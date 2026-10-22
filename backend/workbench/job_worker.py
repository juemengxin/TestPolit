import uuid, threading
from django.db import close_old_connections, transaction
from django.utils import timezone
from .models import Job, Entity, Artifact, Attachment, KnowledgeChunk, Resource, Run
from .entities import parse_document, save_revision, hash_vector
from .security import audit

class Cancelled(Exception):pass


def enqueue_failure_analysis_jobs(job):
    """Queue one model-backed analysis task for each selected execution failure."""
    model_id = job.payload.get('failure_analysis_model_id')
    failed_cases = [case for case in job.result.get('cases', []) if case.get('status') == 'failed']
    if job.kind not in ('api', 'ui') or not model_id or not failed_cases:
        return []

    from .job_submission import create_job
    queued = []
    for case in failed_cases[:10]:
        analysis = create_job(
            project=job.project, kind='analyze', mode='live',
            name=f'AI 失败分析 · {case.get("name", "用例")}'[:150],
            payload={'model_id': model_id, 'source_job': job.pk, 'failure_case_id': case['id']},
            user=job.created_by,
        )
        queued.append({'case_id': case['id'], 'job_id': analysis.pk})
    return queued

SAFE_UI_ERROR_PREFIXES=(
    '缺少依赖变量：','缺少服务端环境密钥引用','目标不在 TESTPILOT_ALLOWED_TARGETS 中',
    'URL 必须为不含凭据的 HTTP(S) 地址','UI base_url 必填','UI 需配置',
    '不支持的 UI 操作','重复回放次数必须','卡顿阈值必须',
    '移动端与浏览器 UI 用例需分开执行','执行前准备数据必须为 JSON 对象',
)

def safe_ui_error(exc):
    """Expose known-safe UI configuration errors without leaking arbitrary exception text."""
    message=str(exc)
    return message[:250] if isinstance(exc,ValueError) and message.startswith(SAFE_UI_ERROR_PREFIXES) else None


def safe_model_status_error(exc):
    """Report provider status codes without persisting response bodies or credentials."""
    status=getattr(exc,'status_code',None)
    if type(exc).__name__ not in {'APIStatusError','AuthenticationError','PermissionDeniedError','NotFoundError','RateLimitError','BadRequestError','InternalServerError'}:
        return None
    guidance={
        400:'请检查模型名称和请求参数',
        401:'请检查 API Key',
        403:'请检查模型权限与可用地域',
        404:'请检查服务地址和模型名称',
        429:'请检查账户额度或请求频率',
    }.get(status,'请检查模型服务状态和连接配置')
    code=f'HTTP {status}' if type(status) is int else 'HTTP 状态未知'
    return f'模型服务返回 {code}；{guidance}。未记录服务响应正文或密钥。'


def claim_job():
    cutoff=timezone.now()-timezone.timedelta(seconds=90)
    # Never blindly replay live external side effects after a worker crash.
    for job in Job.objects.filter(status='running',heartbeat_at__lt=cutoff):
        Job.objects.filter(pk=job.pk,status='running',heartbeat_at=job.heartbeat_at).update(status='failed',finished_at=timezone.now(),result={**job.result,'error':'Worker 中断；为避免重复外部操作，请检查结果后重新提交'})
    for pk in Job.objects.filter(status='queued').order_by('created_at').values_list('pk',flat=True)[:10]:
        token=uuid.uuid4().hex
        if Job.objects.filter(pk=pk,status='queued').update(status='running',worker_id=token,heartbeat_at=timezone.now()):return Job.objects.get(pk=pk)
    return None

def execute_job(job):
    stop=threading.Event()
    def heartbeat():
        while not stop.wait(10):
            close_old_connections()
            Job.objects.filter(pk=job.pk,status='running',worker_id=job.worker_id).update(heartbeat_at=timezone.now())
            close_old_connections()
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    def record(stage,message,progress,values=None):
        if not Job.objects.filter(pk=job.pk,status='running',worker_id=job.worker_id).exists():raise Cancelled()
        job.logs.append({'stage':stage,'message':message,'at':timezone.now().isoformat()})
        job.history.append({'stage':stage,'at':timezone.now().isoformat()})
        if values:job.result.update(values)
        Job.objects.filter(pk=job.pk,status='running',worker_id=job.worker_id).update(logs=job.logs,history=job.history,progress=progress,result=job.result,heartbeat_at=timezone.now())
    try:
        record('start','任务开始 · '+job.mode,3)
        from .intelligence import run_ai, embedding, apply_result
        from .executors import run_api, run_ui, mcp_request
        if job.kind == 'triage':
            from .triage import triage_result
            source = Job.objects.get(pk=job.payload['source_job'], project=job.project, kind__in=['api','ui'], status='completed')
            result = {'triage': triage_result(source.result, source.mode), 'source_job_id': source.pk,
                      'source_report_id': source.result.get('run_report_id')}
        elif job.kind=='app_record':
            from .mobile_recording import execute_recording
            result=execute_recording(job,record)
        elif job.kind=='app_video':
            from .app_video import analyze_app_video
            result=analyze_app_video(job,record)
        elif job.kind=='design':
            from .design import run_design
            result=run_design(job,record)
        elif job.kind in ['generate','optimize','review','split','analyze','chat']:result=run_ai(job,record)
        elif job.kind=='api':result=run_api(job,record)
        elif job.kind=='ui':result=run_ui(job,record)
        elif job.kind=='ingest':
            entity=Entity.objects.get(pk=job.payload['entity_id'],project=job.project)
            artifact=Artifact.objects.get(pk=job.payload['artifact_id'],project=job.project)
            with artifact.file.open('rb') as file:text=parse_document(file)
            record('parse','文档解析完成',35)
            with transaction.atomic():
                entity=Entity.objects.select_for_update().get(pk=entity.pk)
                if entity.version!=job.payload['source_version']:raise ValueError('文档在解析期间已修改，保留原内容；请重新上传')
                entity.content=text
                if entity.kind=='requirement':entity.config={**entity.config,'status':'ready'}
                entity.version+=1;entity.save();save_revision(entity,job.created_by)
            if entity.kind=='knowledge_document':
                chunks=[text[i:i+700] for i in range(0,len(text),600)][:1000]
                resource_id=entity.parent.config.get('embedding_model') if entity.parent else None
                vectors=[]
                for start in range(0,len(chunks),20):
                    record('embedding',f'向量化 {start}/{len(chunks)}',40+int(start/max(len(chunks),1)*50))
                    vectors.extend(embedding(Resource.objects.get(pk=resource_id,project=job.project,enabled=True),chunks[start:start+20]) if resource_id else [hash_vector(t) for t in chunks[start:start+20]])
                with transaction.atomic():
                    entity.chunks.all().delete()
                    KnowledgeChunk.objects.bulk_create([KnowledgeChunk(document=entity,index=i,text=t,vector=v,embedding=f'model:{resource_id}' if resource_id else 'local-hash-256') for i,(t,v) in enumerate(zip(chunks,vectors))])
                result={'characters':len(text),'chunks':len(chunks),'embedding':f'model:{resource_id}' if resource_id else 'local-hash-256','entity_id':entity.pk}
            else:result={'characters':len(text),'entity_id':entity.pk,'version':entity.version}
        elif job.kind in ['mcp','connection','embed_test','rerank_test']:
            resource=Resource.objects.get(pk=job.payload['resource_id'],project=job.project,enabled=True)
            if job.mode=='simulation':result={'mode':'simulation','message':'未连接真实服务；模拟模式不调用外部工具'}
            elif job.kind=='mcp':result=mcp_request(resource,'tools/call',{'name':job.payload['tool'],'arguments':job.payload.get('arguments',{})})
            elif resource.kind=='mcp':result=mcp_request(resource,'tools/list')
            elif resource.settings.get('provider_type') == 'h2o_decisions':
                if job.kind != 'connection':
                    raise ValueError('H2O Decisions 不支持嵌入或 Reranker 测试')
                from .decision_service import check_health
                result=check_health(resource)
            elif job.kind=='embed_test':result={'dimensions':len(embedding(resource,['连接测试'])[0]),'connected':True}
            elif job.kind=='rerank_test':
                import httpx
                from .security import allowed_url,decrypt
                r=httpx.post(allowed_url(resource.endpoint.rstrip('/')+'/rerank'),json={'model':resource.settings.get('model_name'),'query':'测试','documents':['测试规范']},headers=({'Authorization':'Bearer '+decrypt(resource)} if resource.secret_encrypted else {}),timeout=20,follow_redirects=False);r.raise_for_status();result={'connected':bool(r.json().get('results'))}
            else:
                job.payload={**job.payload,'model_id':resource.pk,'model_connection_test':True,'text':'请回复连接成功'};job.kind='chat';result=run_ai(job,record)
        elif job.kind == 'decision':
            from .decision_service import decide
            resource=Resource.objects.get(pk=job.payload['resource_id'],project=job.project,kind='model',enabled=True)
            record('decision','正在调用 H2O 结构化决策接口',35)
            result=decide(resource,job.payload['state'],job.payload['questions'])
        else:raise ValueError('未知任务类型')
        if job.kind in ['connection','chat','decision'] and job.payload.get('resource_id') and job.mode=='live':
            Resource.objects.filter(pk=job.payload['resource_id'],project=job.project).update(mode='live')
        if job.kind in ['api', 'ui']:
            from .triage import triage_result
            result['triage'] = triage_result(result, job.mode)
            record('triage', '失败分诊已完成；建议不改变原始执行结论', 98)
        record('complete','执行与结果持久化完成',100,result)
        finished_at=timezone.now()
        with transaction.atomic():
            updated=Job.objects.filter(pk=job.pk,status='running',worker_id=job.worker_id).update(status='completed',finished_at=finished_at,result=job.result,usage=result.get('usage',{}))
            if updated and job.kind in ['api','ui']:
                report=create_execution_report(job,finished_at)
                job.result['run_report_id']=report.pk
                Job.objects.filter(pk=job.pk).update(result=job.result)
        analysis_model_id=job.payload.get('failure_analysis_model_id')
        failed_cases=[case for case in job.result.get('cases',[]) if case.get('status')=='failed']
        if updated and job.kind in ['api','ui'] and analysis_model_id and failed_cases:
            try:
                job.result['failure_analysis_jobs']=enqueue_failure_analysis_jobs(job)
                if len(failed_cases)>10:
                    job.result['failure_analysis_notice']='自动 AI 分析最多为前 10 条失败用例创建任务'
                Job.objects.filter(pk=job.pk).update(result=job.result)
            except Exception:
                job.result['failure_analysis_error']='AI 失败分析任务未能排队，请在任务页手动提交分析'
                Job.objects.filter(pk=job.pk).update(result=job.result)
        if updated and job.payload.get('auto_apply') and ('cases' in job.result.get('output',{}) or 'requirements' in job.result.get('output',{})):
            try:apply_result(job)
            except ValueError as exc:
                job.result['apply_error']=str(exc);Job.objects.filter(pk=job.pk).update(result=job.result)
        audit(job.created_by,job.project,'job.completed',job.pk)
    except Cancelled:pass
    except Exception as exc:
        safe=type(exc).__name__+'：执行失败，请检查模型、目标白名单、文档版本或运行环境配置'
        model_error=safe_model_status_error(exc)
        if model_error:
            safe=model_error
        ui_error=safe_ui_error(exc) if job.kind=='ui' else None
        if ui_error:
            safe=ui_error
        elif type(exc).__name__ == 'MobileRecordingError':
            safe=str(exc)[:300]
        elif 'Please pass a valid API key' in str(exc):
            safe='Gemini API Key 无效；请在模型管理中重新填写有效密钥后重试'
        elif type(exc).__name__ in {'OpenAIConnectionError','APIConnectionError'}:
            safe='无法连接模型服务；请检查模型地址和本机代理是否已启动且后台 Worker 可访问'
        elif type(exc).__name__ in {'OpenAITimeoutError','APITimeoutError'}:
            safe='模型响应超时；请检查服务响应速度或稍后重试。需求规则将按目标模块分批提取'
        elif isinstance(exc,ValueError) and str(exc).startswith(('模型未返回','模型返回的结构','模型遗漏','模型未能','模型功能','模型连续','模型未生成','用例缺少','用例必须','每条用例','所选需求','所选模型','目标模块','需求文档')):
            safe=str(exc)[:200]
        elif job.kind=='app_video' and isinstance(exc,ValueError) and str(exc).startswith(('视频','无法读取视频','无法从视频','后台 Worker','视觉模型')):
            safe=str(exc)[:250]
        Job.objects.filter(pk=job.pk,status='running',worker_id=job.worker_id).update(status='failed',finished_at=timezone.now(),result={**job.result,'error':safe})
    finally:stop.set();thread.join(timeout=1)


def create_execution_report(job, finished_at):
    """Persist API/UI job results in the searchable test-report workspace."""
    cases=[]
    snapshots={item['id']:item for item in job.payload.get('snapshots',[])}
    for item in job.result.get('cases',[]):
        case_id=item.get('id')
        snapshot=snapshots.get(case_id,{})
        config=snapshot.get('config',{})
        raw_steps=item.get('steps',[])
        case_duration=item.get('duration_ms',item.get('response',{}).get('duration_ms',sum(s.get('duration_ms',0) for s in raw_steps)))
        if job.kind=='api':
            assertions=item.get('assertions',[])
            raw_steps=[{'index':i,'action':f"{item.get('request',{}).get('method','请求')} 接口断言：{check.get('type','')} ",
                        'expected':str(check.get('expected','')),'actual':str(check.get('actual',check.get('detail',item.get('detail','')))),
                        'status':'passed' if check.get('passed') else 'failed'} for i,check in enumerate(assertions)]
            if not raw_steps and item.get('detail'):
                raw_steps=[{'index':0,'action':'接口执行','expected':'请求与断言成功','actual':item['detail'],'status':item.get('status','failed')}]
        normalized_steps=[]
        configured=config.get('steps',[])
        for index,step in enumerate(raw_steps):
            source=configured[index] if index<len(configured) else {}
            normalized={'index':step.get('index',index),'action':step.get('action','执行步骤'),
                        'expected':source.get('expected',source.get('value',step.get('expected',''))),
                        'actual':step.get('error',step.get('actual',step.get('detail','执行完成' if step.get('status')=='passed' else '未执行'))),
                        'status':step.get('status','skipped'),'duration_ms':step.get('duration_ms',0)}
            normalized_steps.append(normalized)
        cases.append({'case_id':case_id,'name':item.get('name',snapshot.get('name',f'用例 #{case_id}')),
                      'priority':'P1','status':item.get('status','skipped'),'steps':normalized_steps,
                      'duration_ms':case_duration,
                      'detail':item.get('detail','')})
    summary=job.result.get('summary',{})
    metrics={'total':summary.get('total',len(cases)),
             'passed':summary.get('passed',sum(c['status']=='passed' for c in cases)),
             'failed':summary.get('failed',sum(c['status']=='failed' for c in cases)),
             'skipped':summary.get('skipped',sum(c['status']=='skipped' for c in cases)),
             'duration_ms':sum(c['duration_ms'] for c in cases)}
    metrics['pass_rate']=round(metrics['passed']/metrics['total']*100,1) if metrics['total'] else 0
    created_at=job.created_at
    run=Run.objects.create(project=job.project,name=job.name,created_by=job.created_by,status='completed',
        mode=job.mode,progress=100,execution_context={'executor':job.result.get('executor','接口执行器' if job.kind=='api' else 'UI 执行器'),
        'source_kind':job.kind,'source_name':job.name,'source_job_id':job.pk,'triage':job.result.get('triage')},
        snapshot=[{'id':item['id'],'name':item.get('name',''),'priority':'P1','data':{'steps':item.get('config',{}).get('steps',[])}} for item in job.payload.get('snapshots',[])],
        results=cases,logs=[{'at':log.get('at',finished_at.isoformat()),'level':'INFO','message':log.get('message',log.get('stage',''))} for log in job.logs],
        history=[{'status':'queued','at':created_at.isoformat(),'message':'执行任务已提交'},
                 {'status':'running','at':(job.heartbeat_at or created_at).isoformat(),'message':'后台执行用例'},
                 {'status':'completed','at':finished_at.isoformat(),'message':'用例执行完成，测试报告已生成'}],
        metrics=metrics,started_at=job.heartbeat_at or created_at,finished_at=finished_at,heartbeat_at=finished_at)
    Run.objects.filter(pk=run.pk).update(created_at=created_at)
    attach_execution_media(job,run)
    return run


def attach_execution_media(job,run):
    """Link every screenshot and video produced by an API/UI execution to its report."""
    artifact_refs=[]
    for case in job.result.get('cases',[]):
        case_id=case.get('id')
        for step_index,step in enumerate(case.get('steps',[])):
            if step.get('screenshot'):
                artifact_refs.append((case_id,step_index,step['screenshot']))
        if case.get('video'):
            artifact_refs.append((case_id,None,case['video']))
    artifacts={a.pk:a for a in Artifact.objects.filter(project=job.project,job=job,category__in=['screenshot','video'],pk__in=[ref[2] for ref in artifact_refs])}
    for case_id,step_index,artifact_id in artifact_refs:
        artifact=artifacts.get(artifact_id)
        if not artifact or Attachment.objects.filter(run=run,case_id=case_id,step_index=step_index,name=artifact.name).exists():
            continue
        Attachment.objects.create(project=job.project,run=run,case_id=case_id,step_index=step_index,name=artifact.name,
            file=artifact.file.name,size=artifact.file.size,uploaded_by=job.created_by)
