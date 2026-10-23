import json, hashlib, secrets, re
import zipfile
from io import BytesIO
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q, Count
from django.http import HttpResponse, FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, serializers
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response
from .models import Entity, Revision, MachineConnection, Job, Artifact, KnowledgeChunk, Resource, Asset, Project, AccessKey, Organization, Preference, AuditEvent, SystemConfig, Run
from .access import projects_for, require_write
from .views import ScopedViewSet
from .entities import EntitySerializer, save_revision, hash_vector
from .security import audit, redact


_SECRET_FIELD = re.compile(r'password|secret|token|authorization|cookie|api.?key', re.I)


def _preserve_sensitive_config(original, repaired):
    """Keep credential fields server-side while applying a model-generated repair."""
    if isinstance(repaired, list):
        original_items=original if isinstance(original,list) else []
        return [_preserve_sensitive_config(original_items[index] if index<len(original_items) else None,item)
                for index,item in enumerate(repaired)]
    if not isinstance(repaired, dict):
        return repaired
    original = original if isinstance(original, dict) else {}
    result = {}
    for key, value in repaired.items():
        if _SECRET_FIELD.search(str(key)):
            if key in original:
                result[key] = original[key]
            continue
        result[key] = _preserve_sensitive_config(original.get(key), value) if isinstance(value, dict) else value
    for key, value in original.items():
        if _SECRET_FIELD.search(str(key)) and key not in result:
            result[key] = value
    return result

class EntityViewSet(ScopedViewSet):
    queryset=Entity.objects.select_related('project','parent').all()
    serializer_class=EntitySerializer
    @action(detail=False,methods=['get'])
    def recording_access(self,request):
        from .recording import authorize_recording_screen
        authorize_recording_screen(request.user)
        return Response(status=204)
    @action(detail=False,methods=['get'])
    def recording_status(self,request):
        from .recording import recording_status
        return Response(recording_status(request.user))
    @action(detail=False,methods=['post'])
    def recording_start(self,request):
        project=get_object_or_404(projects_for(request.user),pk=request.data.get('project'))
        from .recording import start_recording
        try:result=start_recording(request.user,project,request.data.get('url',''))
        except PermissionDenied:raise
        except PermissionError as exc:raise PermissionDenied(str(exc)) from exc
        except ValueError as exc:raise ValidationError(str(exc)) from exc
        except Exception as exc:raise ValidationError(f'无法启动录制浏览器：{type(exc).__name__}') from exc
        return Response(result,status=201)
    @action(detail=False,methods=['post'])
    def recording_stop(self,request):
        from .recording import finish_recording
        try:item=finish_recording(request.user,str(request.data.get('session_id','')),str(request.data.get('name','')))
        except PermissionDenied:raise
        except PermissionError as exc:raise PermissionDenied(str(exc)) from exc
        except ValueError as exc:raise ValidationError(str(exc)) from exc
        except Exception as exc:raise ValidationError(f'录制结束或用例生成失败：{type(exc).__name__}') from exc
        return Response(self.get_serializer(item).data,status=201)
    @action(detail=False,methods=['post'])
    def recording_cancel(self,request):
        from .recording import cancel_recording
        try:cancel_recording(request.user,str(request.data.get('session_id','')))
        except PermissionDenied:raise
        except PermissionError as exc:raise PermissionDenied(str(exc)) from exc
        except ValueError as exc:raise ValidationError(str(exc)) from exc
        return Response({'status':'cancelled'})
    @action(detail=False,methods=['post'])
    def prepare_data(self,request):
        serializer=self.get_serializer(data={'project':request.data.get('project'),'kind':'data_preparation',
                                             'name':'数据准备预览','config':request.data.get('config',{})})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data['config'])
    @action(detail=False,methods=['post'])
    def import_playwright_recording(self,request):
        """Turn an uploaded Playwright trace archive into a reviewable Web UI case."""
        project=get_object_or_404(projects_for(request.user),pk=request.data.get('project'))
        require_write(request.user,project)
        upload=request.FILES.get('file')
        if not upload or not upload.name.lower().endswith('.zip') or upload.size>100*1024*1024:
            raise ValidationError('请上传不超过 100 MB 的 Playwright Trace ZIP 录制文件')
        try:
            with zipfile.ZipFile(BytesIO(upload.read())) as archive:
                trace_info=next((entry for entry in archive.infolist() if entry.filename.endswith('/trace.trace') or entry.filename=='trace.trace'),None)
                if not trace_info or trace_info.file_size>20*1024*1024:
                    raise ValidationError('录制包中没有有效的 trace.trace，或 Trace 超过 20 MB')
                events=[json.loads(line) for line in archive.read(trace_info).decode('utf-8').splitlines() if line.strip()]
        except (zipfile.BadZipFile,UnicodeDecodeError,json.JSONDecodeError) as exc:
            raise ValidationError('无法读取 Playwright Trace ZIP') from exc
        action_map={'click':'click','fill':'fill','press':'press','check':'check','uncheck':'check','hover':'hover','selectOption':'select','goto':'goto'}
        steps=[];base_url=''
        for event in events:
            if event.get('type')!='before':continue
            api_name=event.get('apiName','').split('.')[-1]
            if api_name not in action_map:continue
            params=event.get('params') or {};action=action_map[api_name]
            step={'action':action}
            if action=='goto':
                target=params.get('url','')
                if not base_url and isinstance(target,str) and target.startswith(('http://','https://')):
                    from urllib.parse import urlsplit
                    parsed=urlsplit(target);base_url=f'{parsed.scheme}://{parsed.netloc}'
                    step['value']=target[len(base_url):] or '/'
                else:step['value']=target
            else:
                selector=params.get('selector')
                if not isinstance(selector,str) or not selector.strip():continue
                step['selector']=selector[:1000]
                if action=='fill' and re.search(r'password|secret|token|credential|api.?key',selector,re.I):
                    step['value']='{{recorded_secret}}'
                elif action in ['fill','press','select']:step['value']=str(params.get('value',params.get('key','')))[:2000]
                if api_name=='uncheck':step['value']='false'
            steps.append(step)
            if len(steps)>=100:break
        if not steps or not base_url:
            raise ValidationError('Trace 中未找到可执行的浏览器操作和目标 URL；请录制至少一次页面导航与交互')
        config={'platform':'web','base_url':base_url,'priority':'P1','test_type':'functional','preconditions':'由 Playwright Trace 录制生成；请复核步骤与断言。',
                'steps':steps,'automation_status':'ready','verification_status':'unverified','recording_source':upload.name,
                'recording_actions':len(steps),'notes':'录制只记录交互操作；请补充可验证的预期结果和断言后再执行。'}
        item=Entity.objects.create(project=project,kind='ui_case',name=f'{Path(upload.name).stem} · 录制用例'[:150],config=config,created_by=request.user)
        save_revision(item,request.user);audit(request.user,project,'ui.recording_import',item.pk)
        return Response(self.get_serializer(item).data,status=201)
    @action(detail=False,methods=['post'],url_path='generate_app_video_case')
    def generate_app_video_case(self,request):
        """Store an Android recording and queue multimodal analysis in the Worker."""
        project=get_object_or_404(projects_for(request.user),pk=request.data.get('project'))
        require_write(request.user,project)
        upload=request.FILES.get('file')
        if not upload or upload.size>100*1024*1024:
            raise ValidationError('请上传不超过 100 MB 的 App 录屏视频')
        suffix=Path(upload.name).suffix.lower()
        if suffix not in {'.mp4','.mov','.m4v','.webm','.mkv'}:
            raise ValidationError('视频格式仅支持 MP4、MOV、M4V、WebM 或 MKV')
        header=upload.read(16);upload.seek(0)
        if suffix in {'.mp4','.mov','.m4v'} and b'ftyp' not in header:
            raise ValidationError('上传文件不是有效的视频容器')
        if suffix in {'.webm','.mkv'} and not header.startswith(b'\x1aE\xdf\xa3'):
            raise ValidationError('上传文件不是有效的视频容器')
        package=request.data.get('package_name','')
        device=request.data.get('device_id','')
        if not isinstance(package,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+',package):
            raise ValidationError('请输入有效 Android 包名')
        if not isinstance(device,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',device):
            raise ValidationError('请选择已连接 Android 设备')
        model=get_object_or_404(Resource,pk=request.data.get('model_id'),project=project,kind='model',enabled=True)
        mobile=get_object_or_404(Resource,pk=request.data.get('resource_id'),project=project,kind='mcp',enabled=True,mode='live')
        with transaction.atomic():
            job=Job.objects.create(project=project,kind='app_video',name='App 录屏识别 · '+Path(upload.name).stem[:120],
                mode='live',payload={'model_id':model.pk,'resource_id':mobile.pk,'device_id':device,'package_name':package},
                created_by=request.user,history=[{'status':'queued','at':timezone.now().isoformat()}])
            artifact=Artifact.objects.create(project=project,job=job,file=upload,name=Path(upload.name).name,category='video')
            job.payload={**job.payload,'artifact_id':artifact.pk}
            job.save(update_fields=['payload'])
        audit(request.user,project,'ui.app_video_analysis_queued',job.pk)
        return Response({'task_id':job.pk,'status':job.status},status=202)
    @action(detail=True,methods=['get'])
    def prepared_output(self,request,pk=None):
        item=self.get_object()
        if item.kind!='data_preparation':raise ValidationError('请选择数据准备方案')
        from .data_preparation import prepare_data
        output_format=request.query_params.get('output_format',item.config.get('output_format','json'))
        try:config=prepare_data({**item.config,'output_format':output_format})
        except (ValueError,RecursionError) as exc:raise ValidationError(str(exc))
        body=config['generated_sql'] if output_format=='sql' else config['generated_json']
        response=HttpResponse(body,content_type='application/sql; charset=utf-8' if output_format=='sql' else 'application/json; charset=utf-8')
        response['Content-Disposition']=f'attachment; filename="test-data-{item.pk}.{output_format}"'
        response['X-Content-Type-Options']='nosniff'
        return response
    def get_queryset(self):
        qs=super().get_queryset()
        if self.request.query_params.get('kind')=='data_preparation':
            qs=qs.order_by('-updated_at','-id')
            output_format=self.request.query_params.get('output_format')
            if output_format in ['json','sql']:qs=qs.filter(config__output_format=output_format)
        if self.request.query_params.get('parent'): qs=qs.filter(parent_id=self.request.query_params['parent'])
        platform=self.request.query_params.get('platform')
        if platform in ['app','web']:
            qs=qs.filter(kind='ui_case',config__platform=platform)
        if self.request.query_params.get('kind')=='ui_case' or platform in ['app','web']:
            # UI 用例的维护顺序按最近编辑时间排列，避免 position 把较旧用例固定在前面。
            qs=qs.order_by('-updated_at','-id')
        return qs
    def perform_create(self,serializer):
        item=serializer.save(created_by=self.request.user)
        audit(self.request.user,item.project,'entity.create',item.pk)
    def perform_update(self,serializer):
        require_write(self.request.user,self.get_object().project)
        item=serializer.save();audit(self.request.user,item.project,'entity.update',item.pk)
    def perform_destroy(self,item):
        require_write(self.request.user,item.project)
        if item.children.exists(): raise ValidationError('请先移动或删除子节点')
        if item.kind=='module' and Asset.objects.filter(project=item.project,data__module_id=item.pk).exists(): raise ValidationError('模块下仍有用例')
        if item.kind=='ui_case' and any(item.pk in flow.config.get('case_ids',[]) for flow in Entity.objects.filter(project=item.project,kind='ui_flow').only('config')):
            raise ValidationError('此 UI 用例已关联测试流程，请先从流程中移除')
        audit(self.request.user,item.project,'entity.delete',item.pk);item.delete()
    @action(detail=True,methods=['post'])
    @transaction.atomic
    def apply_ui_profile(self,request,pk=None):
        from .ui_profiles import apply_ui_profile
        profile=self.get_object()
        require_write(request.user,profile.project)
        result=apply_ui_profile(profile,request.user,request.data.get('case_ids'),platform='app')
        return Response(result)
    @action(detail=True,methods=['post'])
    @transaction.atomic
    def apply_web_profile(self,request,pk=None):
        from .ui_profiles import apply_ui_profile
        profile=self.get_object()
        require_write(request.user,profile.project)
        result=apply_ui_profile(profile,request.user,request.data.get('case_ids'),platform='web')
        return Response(result)
    @action(detail=True,methods=['post'])
    @transaction.atomic
    def make_ui_executable(self,request,pk=None):
        case=self.get_object();require_write(request.user,case.project)
        if case.kind!='ui_case' or case.config.get('platform') not in ['app','web']:raise ValidationError('请选择 App 或 Web UI 用例')
        if case.config.get('automation_status')!='needs_mapping' and not (request.data.get('refresh') is True and case.config.get('verification_status')=='unverified'):
            return Response({'id':case.pk,'status':case.config.get('automation_status'),'converted':False})
        from .ui_steps import compile_app_design_steps,compile_app_video_design_steps,compile_web_design_steps
        config=case.config.copy()
        if config.get('platform')=='app' and config.get('recording_method')=='vision-video-analysis':steps,unmapped=compile_app_video_design_steps(config.get('design_steps',[]))
        elif config.get('platform')=='app':steps,unmapped=compile_app_design_steps(config.get('design_steps',[]))
        else:steps,unmapped=compile_web_design_steps(config.get('design_steps',[]))
        if config.get('recording_method')=='vision-video-analysis' and unmapped:
            raise ValidationError('视频识别步骤仍需补充：'+'；'.join(unmapped[:5]))
        if not steps:raise ValidationError('未能从设计步骤中生成可执行动作')
        config.update({'steps':steps,'verification_status':'unverified','automation_status':'ready','automation_notes':unmapped})
        if config.get('platform')=='app':config['executor']='mobile-mcp'
        if config.get('app_profile_id') and config.get('platform')=='app':
            try:profile=Entity.objects.get(pk=config['app_profile_id'],project=case.project,kind='ui_data',config__data_type='app_profile')
            except Entity.DoesNotExist:raise ValidationError('关联的 App 公共配置已失效')
            app=profile.config.get('app_profile',{})
            config.update({key:app.get(key) for key in ['app_name','package_name','device_id','mcp_resource_id']})
        if config.get('web_profile_id') and config.get('platform')=='web':
            try:profile=Entity.objects.get(pk=config['web_profile_id'],project=case.project,kind='ui_data',config__data_type='web_profile')
            except Entity.DoesNotExist:raise ValidationError('关联的 Web 公共配置已失效')
            config['base_url']=profile.config.get('web_profile',{}).get('base_url','')
        serializer=self.get_serializer(case,data={'config':config,'version':case.version},partial=True)
        serializer.is_valid(raise_exception=True);self.perform_update(serializer)
        audit(request.user,case.project,'ui.steps.compile',case.pk)
        return Response({'id':case.pk,'status':'ready','verification_status':'unverified','step_count':len(steps),'unmapped':unmapped})
    @action(detail=True,methods=['post'])
    @transaction.atomic
    def run_ui_flow(self,request,pk=None):
        flow=self.get_object();require_write(request.user,flow.project)
        if flow.kind!='ui_flow':raise ValidationError('请选择 UI 测试流程')
        ids=flow.config.get('case_ids',[]);platform=flow.config.get('platform')
        if flow.config.get('status')!='ready' or not ids:raise ValidationError('请先保存至少包含一个用例的就绪流程')
        cases=list(Entity.objects.filter(project=flow.project,kind='ui_case',pk__in=ids))
        by_id={case.pk:case for case in cases}
        if len(by_id)!=len(ids):raise ValidationError('流程中存在已删除或不属于当前项目的用例')
        cases=[by_id[case_id] for case_id in ids]
        from .executors import validate_config
        for case in cases:
            if case.config.get('platform')!=platform or case.config.get('automation_status')=='needs_mapping':
                raise ValidationError(f'用例「{case.name}」尚未配置为可执行的 {platform} 用例')
            try:validate_config('ui_case',case.config)
            except ValueError as exc:raise ValidationError({case.name:str(exc)})
            if case.config.get('executor')=='mobile-mcp' and not Resource.objects.filter(project=flow.project,pk=case.config.get('mcp_resource_id'),kind='mcp',enabled=True,mode='live').exists():
                raise ValidationError('流程中的 App 用例需关联已启用的 Mobile MCP 服务')
        environment_id=request.data.get('environment_id',flow.config.get('environment_id'))
        if environment_id is not None and not Entity.objects.filter(project=flow.project,pk=environment_id,kind='ui_environment').exists():
            raise ValidationError('请选择当前项目中的 UI 环境')
        mode=request.data.get('mode','live')
        if mode not in ['live','simulation']:raise ValidationError('UI 流程仅支持真实执行或模拟模式')
        preparation=request.data.get('preparation_data',{})
        if not isinstance(preparation,dict) or len(json.dumps(preparation,ensure_ascii=False))>20000:raise ValidationError('执行准备数据必须为不超过 20KB 的 JSON 对象')
        skill_names=['web-app-test-execution']
        skill_snapshots=list(Resource.objects.filter(project=flow.project,kind='skill',enabled=True,name__in=skill_names).values('id','name','description','settings'))
        payload={'entity_ids':ids,'environment_id':environment_id,'preparation_data':preparation,
                 'snapshots':[{'id':case.pk,'kind':case.kind,'name':case.name,'content':case.content,'config':case.config,'version':case.version} for case in cases],
                 'skill_snapshots':skill_snapshots}
        job=Job.objects.create(project=flow.project,kind='ui',name='UI 测试流程 · '+flow.name,mode=mode,payload=payload,
                               created_by=request.user,status='queued',history=[{'status':'queued','at':timezone.now().isoformat(),'message':f'流程已排队 · {len(cases)} 条 {platform} 用例'}])
        audit(request.user,flow.project,'ui_flow.run',flow.pk)
        return Response(JobSerializer(job,context={'request':request}).data,status=202)
    @action(detail=True,methods=['get'])
    def revisions(self,request,pk=None):
        return Response(list(self.get_object().revisions.values('version','name','content','config','created_at')))
    @action(detail=True,methods=['get'])
    def requirement_context(self,request,pk=None):
        requirement=self.get_object()
        if requirement.kind!='requirement':raise ValidationError('请选择需求文档')
        jobs=Job.objects.filter(project=requirement.project,payload__entity_id=requirement.pk)
        modules=Entity.objects.filter(project=requirement.project,kind='module',config__source_requirement_id=requirement.pk)
        assets=Asset.objects.filter(project=requirement.project,kind='case').filter(
            Q(data__source_requirement_id=requirement.pk)|Q(data__generation_job__in=jobs.values('pk')))
        cases=Entity.objects.filter(project=requirement.project,kind__in=['api','ui_case']).filter(
            Q(config__source_requirement_id=requirement.pk)|Q(config__source_generation_job__in=jobs.values('pk')))
        tasks=[]
        for job in jobs[:30]:
            snapshot=next((item for item in job.payload.get('snapshots',[]) if item.get('id')==requirement.pk and item.get('kind')=='requirement'),{})
            tasks.append({'id':job.pk,'name':job.name,'kind':job.kind,'status':job.status,'mode':job.mode,
                          'created_at':job.created_at,'source_version':snapshot.get('version',job.payload.get('source_version'))})
        linked=[{'id':item.pk,'name':item.name,'kind':item.kind,'resource':'assets','status':item.status} for item in assets[:50]]
        linked.extend({'id':item.pk,'name':item.name,'kind':item.kind,'resource':'entities',
                       'status':item.config.get('automation_status','draft')} for item in cases[:50])
        return Response({'counts':{'modules':modules.count(),'cases':assets.count()+cases.count(),'jobs':jobs.count()},
                         'modules':list(modules[:100].values('id','name','version','config')),
                         'cases':linked,'jobs':tasks,'limits':{'modules':100,'cases_per_type':50,'jobs':30}})
    @action(detail=True,methods=['get'])
    def connection_status(self,request,pk=None):
        item=self.get_object()
        if item.kind!='machine':raise ValidationError('请选择执行机')
        status=MachineConnection.objects.filter(machine=item).first()
        return Response({'checked':bool(status),'connected':status.connected if status else False,
                         'model':status.model if status else '', 'device_id':status.device_id if status else '',
                         'message':status.message if status else '尚未检测',
                         'checked_at':status.checked_at if status else None})
    @action(detail=True,methods=['post'])
    def test_connection(self,request,pk=None):
        item=self.get_object();require_write(request.user,item.project)
        if item.kind!='machine':raise ValidationError('请选择执行机')
        from .machine_connection import probe_machine
        try:result=probe_machine(item.config)
        except ValueError as exc:raise ValidationError(str(exc))
        status,_=MachineConnection.objects.update_or_create(machine=item,defaults=result)
        audit(request.user,item.project,'machine.connection_test',item.pk)
        return Response({'checked':True,**result,'checked_at':status.checked_at})
    @action(detail=True,methods=['post'])
    def restore(self,request,pk=None):
        item=self.get_object();require_write(request.user,item.project)
        revision=get_object_or_404(item.revisions,version=request.data.get('version'))
        data={'name':revision.name,'content':revision.content,'config':revision.config}
        if 'expected_version' in request.data:data['version']=request.data['expected_version']
        serializer=self.get_serializer(item,data=data,partial=True)
        serializer.is_valid(raise_exception=True);self.perform_update(serializer);return Response(serializer.data)
    @action(detail=True,methods=['post'])
    def duplicate(self,request,pk=None):
        item=self.get_object();require_write(request.user,item.project)
        copied=Entity.objects.create(project=item.project,kind=item.kind,name=item.name[:140]+' · 副本',content=item.content,config=item.config,parent=item.parent,created_by=request.user)
        save_revision(copied,request.user);return Response(self.get_serializer(copied).data,status=201)
    @action(detail=True,methods=['post'])
    @transaction.atomic
    def sync_modules(self,request,pk=None):
        requirement=self.get_object();require_write(request.user,requirement.project)
        if requirement.kind!='requirement':raise ValidationError('请选择需求文档')
        from .design import outline_markdown, business_modules
        modules=business_modules(outline_markdown(requirement.content))
        if not requirement.content.strip() or not modules:raise ValidationError('需求文档没有可识别的模块')
        synced=[]
        for index,module in enumerate(modules):
            body='\n\n'.join(f'## {node["name"]}\n{node["source_text"]}' for node in module['functions'] if node['source_text']).strip()
            item=Entity.objects.filter(project=requirement.project,kind='module',config__source_requirement_id=requirement.pk,config__source_heading=module['heading']).first()
            if item:
                if item.content!=body or item.name!=module['name'] or item.config.get('source_version')!=requirement.version:
                    item.name=module['name'];item.content=body;item.config={**item.config,'source_version':requirement.version};item.position=index;item.version+=1;item.save();save_revision(item,request.user)
            else:
                item=Entity.objects.create(project=requirement.project,kind='module',name=module['name'],content=body,
                    config={'source_requirement_id':requirement.pk,'source_heading':module['heading'],'source_version':requirement.version},
                    position=index,created_by=request.user)
                save_revision(item,request.user)
            synced.append({'id':item.pk,'name':item.name,'requirement_id':requirement.pk})
        audit(request.user,requirement.project,'module.sync',requirement.pk)
        return Response({'modules':synced,'count':len(synced)})
    @action(detail=False,methods=['get'])
    def export(self,request):
        if request.query_params.get('export_format')=='xlsx':
            from .spreadsheet_export import XLSX_MIME_TYPE, export_entities_xlsx
            response=HttpResponse(export_entities_xlsx(self.get_queryset()),content_type=XLSX_MIME_TYPE)
            response['Content-Disposition']="attachment; filename*=UTF-8''test-assets.xlsx"
            return response
        response=HttpResponse(json.dumps(self.get_serializer(self.get_queryset(),many=True).data,ensure_ascii=False,indent=2),content_type='application/json')
        response['Content-Disposition']='attachment; filename="assets.json"';return response
    @action(detail=False,methods=['post'],url_path='import')
    @transaction.atomic
    def import_items(self,request):
        project=get_object_or_404(projects_for(request.user),pk=request.data.get('project'));require_write(request.user,project)
        items=request.data.get('items',[])
        if not isinstance(items,list) or not 1<=len(items)<=200: raise ValidationError('一次导入 1–200 条记录')
        created=[]
        for item in items:
            if not isinstance(item,dict): raise ValidationError('导入项必须为对象')
            payload={k:v for k,v in item.items() if k in ['kind','name','content','config','position']};payload['project']=project.pk
            serializer=self.get_serializer(data=payload);serializer.is_valid(raise_exception=True);serializer.save(created_by=request.user);created.append(serializer.data)
        return Response(created,status=201)
    @action(detail=False,methods=['post'])
    @transaction.atomic
    def import_case_template_excel(self,request):
        """Import a spreadsheet as a reusable case-generation template."""
        project=get_object_or_404(projects_for(request.user),pk=request.data.get('project'))
        require_write(request.user,project)
        upload=request.FILES.get('file')
        if not upload or upload.size>10*1024*1024:
            raise ValidationError('用例模板文件需小于 10 MB')
        if Path(upload.name).suffix.lower()!='.xlsx':
            raise ValidationError('仅支持 Excel 工作簿（.xlsx）')
        try:
            from .entities import parse_document
            content=parse_document(upload)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        upload.seek(0)
        if not content.strip():
            raise ValidationError('Excel 文件中没有可导入的用例模板内容')
        name=Path(upload.name).stem.strip()[:150]
        if not name:
            raise ValidationError('Excel 文件名无效')
        headers=[]
        for line in content.splitlines():
            if line.startswith('列名：'):
                headers=[value.strip() for value in line.removeprefix('列名：').split('|') if value.strip()]
                if headers:
                    break
        entity=Entity.objects.create(project=project,kind='case_template',name=name,content=content,
            config={'source_format':'xlsx','required_fields':headers},created_by=request.user)
        save_revision(entity,request.user)
        Artifact.objects.create(project=project,entity=entity,file=upload,name=Path(upload.name).name,category='case_template')
        audit(request.user,project,'case_template.import_excel',entity.pk)
        return Response(self.get_serializer(entity).data,status=201)
    @action(detail=False,methods=['post'])
    @transaction.atomic
    def import_requirement(self,request):
        project=get_object_or_404(projects_for(request.user),pk=request.data.get('project'));require_write(request.user,project)
        file=request.FILES.get('file')
        if not file or file.size>10*1024*1024:raise ValidationError('需求文件需小于 10MB')
        suffix=Path(file.name).suffix.lower()
        if suffix not in ['.md','.doc','.docx']:raise ValidationError('仅支持 Markdown（.md）和 Word（.doc、.docx）需求文件')
        name=Path(file.name).stem.strip()[:150]
        if not name:raise ValidationError('需求文件名无效')
        entity=Entity.objects.create(project=project,kind='requirement',name=name,content='',
                                     config={'status':'parsing','source_format':suffix.lstrip('.')},created_by=request.user)
        save_revision(entity,request.user)
        artifact=Artifact.objects.create(project=project,entity=entity,file=file,name=Path(file.name).name,category='requirement')
        job=Job.objects.create(project=project,kind='ingest',name='导入需求：'+name,mode='local',
                               payload={'entity_id':entity.pk,'artifact_id':artifact.pk,'source_version':entity.version},
                               created_by=request.user)
        audit(request.user,project,'requirement.import',entity.pk)
        return Response({'entity':self.get_serializer(entity).data,'artifact':ArtifactSerializer(artifact,context={'request':request}).data,'job':job.pk},status=202)
    @action(detail=False,methods=['post'])
    @transaction.atomic
    def sync_openapi(self,request):
        project=get_object_or_404(projects_for(request.user),pk=request.data.get('project'));require_write(request.user,project)
        spec=request.data.get('spec',{})
        if not isinstance(spec,dict) or not isinstance(spec.get('paths'),dict): raise ValidationError('请提供 OpenAPI JSON（paths 字段必填）')
        count=0
        for path,methods in spec['paths'].items():
            for method,op in methods.items():
                if method.lower() not in ['get','post','put','patch','delete','head','options']: continue
                if count>=300: raise ValidationError('每次最多同步 300 个接口')
                name=str(op.get('summary') or op.get('operationId') or f'{method.upper()} {path}')[:150]
                config={'method':method.upper(),'url':path,'body':{},'headers':{},'assertions':[{'type':'status','expected':200}],'extract':{},'definition':op}
                item=Entity.objects.filter(project=project,kind='api',config__url=path,config__method=method.upper()).first()
                serializer=self.get_serializer(item,data={'project':project.pk,'kind':'api','name':name,'config':config},partial=bool(item));serializer.is_valid(raise_exception=True);serializer.save(**({} if item else {'created_by':request.user}));count+=1
        return Response({'synced':count})
    @action(detail=True,methods=['post'])
    def search(self,request,pk=None):
        item=self.get_object()
        if item.kind!='knowledge': raise ValidationError('请选择知识库')
        query=request.data.get('query','')
        if not isinstance(query,str) or not query.strip(): raise ValidationError('请输入检索内容')
        from .intelligence import retrieve
        return Response(retrieve(item.project,query,[item.pk],limit=8))
    @action(detail=True,methods=['get'])
    def statistics(self,request,pk=None):
        item=self.get_object();docs=Entity.objects.filter(parent=item,kind='knowledge_document')
        return Response({'documents':docs.count(),'chunks':KnowledgeChunk.objects.filter(document__in=docs).count(),'characters':sum(len(x.content) for x in docs),'embedding':item.config.get('embedding_model') or 'local-hash-256'})
    @action(detail=True,methods=['post'])
    def install(self,request,pk=None):
        source=self.get_object();require_write(request.user,source.project)
        if source.kind!='skill_source': raise ValidationError('请选择技能来源')
        # Declarative skills only: no pip/npm/shell execution from untrusted manifests.
        manifest=request.data.get('manifest') or source.config.get('manifest',{})
        if not isinstance(manifest,dict) or not isinstance(manifest.get('instructions'),str) or not manifest.get('name'): raise ValidationError('技能清单需要 name 与 instructions')
        resource=Resource.objects.create(project=source.project,kind='skill',name=str(manifest['name'])[:100],description='已安装的声明式技能；不执行本地命令',settings={'instructions':manifest['instructions'][:50000]})
        audit(request.user,source.project,'skill.install',resource.pk)
        return Response({'id':resource.pk,'name':resource.name,'mode':'declarative'},status=201)

class ArtifactSerializer(serializers.ModelSerializer):
    url=serializers.SerializerMethodField()
    def get_url(self,obj): return f'/api/artifacts/{obj.pk}/download/'
    class Meta:
        model=Artifact;fields=['id','project','entity','job','name','category','created_at','url']

class ArtifactViewSet(ScopedViewSet):
    queryset=Artifact.objects.all();serializer_class=ArtifactSerializer;http_method_names=['get','post','delete','head','options']
    def get_queryset(self):
        qs=super().get_queryset()
        for field in ['entity','job']:
            if self.request.query_params.get(field): qs=qs.filter(**{field:self.request.query_params[field]})
        return qs
    def create(self,request):
        entity=get_object_or_404(Entity.objects.filter(project__in=projects_for(request.user)),pk=request.data.get('entity'));require_write(request.user,entity.project)
        file=request.FILES.get('file')
        if not file or file.size>10*1024*1024: raise ValidationError('文件需小于 10MB')
        if Path(file.name).suffix.lower() not in ['.txt','.md','.json','.csv','.pdf','.doc','.docx','.xlsx']: raise ValidationError('不支持此文档类型')
        artifact=Artifact.objects.create(project=entity.project,entity=entity,file=file,name=Path(file.name).name)
        job=Job.objects.create(project=entity.project,kind='ingest',name='解析文档：'+entity.name,mode='local',payload={'entity_id':entity.pk,'artifact_id':artifact.pk,'source_version':entity.version},created_by=request.user)
        return Response({'artifact':self.get_serializer(artifact).data,'job':job.pk},status=202)
    @action(detail=True,methods=['get'])
    def download(self,request,pk=None):
        file=self.get_object();inline=request.query_params.get('inline')=='1'
        response=FileResponse(file.file.open('rb'),as_attachment=not inline,filename=file.name)
        response['X-Content-Type-Options']='nosniff'
        return response

class JobSerializer(serializers.ModelSerializer):
    files=ArtifactSerializer(many=True,read_only=True)
    class Meta:
        model=Job;exclude=['worker_id'];read_only_fields=[f.name for f in Job._meta.fields]

class JobViewSet(ScopedViewSet):
    queryset=Job.objects.select_related('project').exclude(kind='app_record');serializer_class=JobSerializer;http_method_names=['get','post','delete','head','options']
    def create(self,request):
        project=get_object_or_404(projects_for(request.user),pk=request.data.get('project'));require_write(request.user,project)
        from .job_submission import create_job
        job=create_job(
            project=project,
            kind=request.data.get('kind'),
            payload=request.data.get('payload',{}),
            mode=request.data.get('mode','simulation'),
            name=request.data.get('name'),
            user=request.user,
        )
        audit(request.user,project,'job.submit',job.pk);return Response(self.get_serializer(job).data,status=202)
    @action(detail=True, methods=['post'])
    def triage(self, request, pk=None):
        source = self.get_object()
        require_write(request.user, source.project)
        if source.kind not in ('api', 'ui') or source.status != 'completed' or source.mode != 'live':
            raise ValidationError('仅已完成的真实接口 / UI 执行任务可提交失败分诊')
        if not any(case.get('status') == 'failed' for case in source.result.get('cases', [])):
            raise ValidationError('该执行任务没有失败用例')
        job = Job.objects.create(project=source.project, kind='triage', name='失败分诊 · ' + source.name[:140],
                                 mode='local', payload={'source_job': source.pk}, created_by=request.user)
        audit(request.user, source.project, 'job.triage', job.pk)
        return Response(self.get_serializer(job).data, status=202)

    @action(detail=True,methods=['post'])
    def generate_report(self,request,pk=None):
        with transaction.atomic():
            job=Job.objects.select_for_update().select_related('project','created_by').get(pk=self.get_object().pk)
            require_write(request.user,job.project)
            if job.kind not in ['api','ui'] or job.status!='completed':raise ValidationError('仅已完成的接口或 UI 用例执行任务可生成测试报告')
            report_id=job.result.get('run_report_id')
            report=Run.objects.filter(project=job.project,execution_context__source_job_id=job.pk).first()
            from .job_worker import attach_execution_media, create_execution_report
            if not report:
                report=create_execution_report(job,job.finished_at or timezone.now())
            else:
                attach_execution_media(job,report)
            if report_id!=report.pk:
                job.result={**job.result,'run_report_id':report.pk}
                job.save(update_fields=['result'])
        return Response({'run_report_id':report.pk,'url':f'/runs/{report.pk}'})
    @action(detail=True,methods=['post'])
    def approve(self,request,pk=None):
        job=self.get_object();require_write(request.user,job.project,owner=True)
        if job.status!='awaiting_approval': raise ValidationError('任务不处于待审批状态')
        accept=request.data.get('approve') is True
        job.status='queued' if accept else 'cancelled';job.history+=[{'status':job.status,'at':timezone.now().isoformat(),'by':request.user.username}];job.save(update_fields=['status','history'])
        audit(request.user,job.project,'tool.approve' if accept else 'tool.reject',job.pk);return Response(self.get_serializer(job).data)
    @action(detail=True,methods=['post'])
    def cancel(self,request,pk=None):
        job=self.get_object();require_write(request.user,job.project)
        if not Job.objects.filter(pk=job.pk,status__in=['queued','running','awaiting_approval']).update(status='cancelled',finished_at=timezone.now()): raise ValidationError('任务已结束')
        return Response({'status':'cancelled'})
    @action(detail=True,methods=['post'])
    def apply(self,request,pk=None):
        job=self.get_object();require_write(request.user,job.project)
        from .intelligence import apply_result
        try: ids=apply_result(job)
        except ValueError as exc: raise ValidationError(str(exc))
        audit(request.user,job.project,'job.apply',job.pk);return Response({'created':ids})
    @action(detail=True,methods=['post'])
    @transaction.atomic
    def repair_retry(self,request,pk=None):
        analysis=self.get_object();require_write(request.user,analysis.project)
        if analysis.kind!='analyze' or analysis.status!='completed' or analysis.mode!='live':
            raise ValidationError('只有已完成的真实模型失败分析可以修复并重试')
        output=analysis.result.get('output',{})
        if output.get('classification')!='test_case_issue' or not isinstance(output.get('repair_config'),dict):
            raise ValidationError('分析结论不是可自动修复的用例问题')
        source=Job.objects.filter(project=analysis.project,pk=analysis.payload.get('source_job'),kind__in=['api','ui'],status='completed',mode='live').first()
        case_id=analysis.payload.get('failure_case_id')
        if not source or not any(case.get('id')==case_id and case.get('status')=='failed' for case in source.result.get('cases',[])):
            raise ValidationError('关联的失败执行记录已失效，不能重试')
        snapshot=next((item for item in source.payload.get('snapshots',[]) if item.get('id')==case_id),None)
        if not snapshot:
            raise ValidationError('执行记录中缺少用例版本快照')
        entity_kind='api' if source.kind=='api' else 'ui_case'
        case=Entity.objects.select_for_update().filter(project=analysis.project,pk=case_id,kind=entity_kind).first()
        if not case or case.version!=snapshot.get('version'):
            raise ValidationError('用例已被其他操作修改，请重新分析最新执行结果')
        repaired_config=_preserve_sensitive_config(case.config,output['repair_config'])
        from .executors import validate_config
        try:validate_config(source.kind,repaired_config)
        except ValueError as exc:raise ValidationError({'repair_config':str(exc)})
        if source.kind=='ui' and repaired_config.get('platform')!=case.config.get('platform'):
            raise ValidationError('修复不得更改 UI 用例平台类型')
        if source.kind=='ui' and repaired_config.get('executor')=='mobile-mcp' and not Resource.objects.filter(
            project=analysis.project,pk=repaired_config.get('mcp_resource_id'),kind='mcp',enabled=True,mode='live'
        ).exists():
            raise ValidationError('修复后的 App 用例需关联已启用的 Mobile MCP 服务')

        case.config=repaired_config;case.version+=1;case.save();save_revision(case,request.user)
        from .job_submission import create_job
        retry_payload={
            'entity_ids':[case.pk],
            'environment_id':source.payload.get('environment_id'),
            'preparation_data':source.payload.get('preparation_data',{}),
            'request_overrides':source.payload.get('request_overrides',{}),
            'ui_platform':case.config.get('platform','web'),
        }
        retry=create_job(project=analysis.project,kind=source.kind,payload=retry_payload,mode=source.mode,
                         name='修复后重试 · '+case.name,user=request.user)
        audit(request.user,analysis.project,'job.repair_retry',retry.pk)
        return Response({'case_id':case.pk,'case_version':case.version,'job':JobSerializer(retry,context={'request':request}).data},status=202)
    @action(detail=True,methods=['get'])
    def export(self,request,pk=None):
        job=self.get_object()
        if job.kind=='design' and request.query_params.get('download')=='markdown':
            from .design import render_markdown
            response=HttpResponse(render_markdown(job.result.get('output',{})),content_type='text/markdown; charset=utf-8')
            response['Content-Disposition']=f'attachment; filename="task-{pk}.md"'
            return response
        response=HttpResponse(json.dumps(self.get_serializer(job).data,ensure_ascii=False,indent=2),content_type='application/json');response['Content-Disposition']=f'attachment; filename="task-{pk}.json"';return response
    def perform_destroy(self,job):
        if job.status in ['queued','running','awaiting_approval']: raise ValidationError('请先取消任务')
        super().perform_destroy(job)

@api_view(['GET','PATCH'])
def preferences(request):
    item,_=Preference.objects.get_or_create(user=request.user)
    if request.method=='PATCH':
        if request.data.get('theme',item.theme) not in ['light','dark'] or request.data.get('language',item.language) not in ['zh','en']: raise ValidationError('无效偏好')
        item.theme=request.data.get('theme',item.theme);item.language=request.data.get('language',item.language);item.save()
    return Response({'theme':item.theme,'language':item.language})

@api_view(['GET','POST','DELETE'])
def keys(request):
    if isinstance(request.auth,AccessKey): raise PermissionDenied('API Key 不能管理凭据')
    if request.method=='GET': return Response(list(AccessKey.objects.filter(user=request.user).values('id','name','project','prefix','read_only','expires_at')))
    if request.method=='DELETE': get_object_or_404(AccessKey,user=request.user,pk=request.data.get('id')).delete();return Response(status=204)
    project=get_object_or_404(projects_for(request.user),pk=request.data.get('project'))
    read_only=request.data.get('read_only',True)
    if not read_only: require_write(request.user,project)
    raw='tp_'+secrets.token_urlsafe(32)
    obj=AccessKey.objects.create(user=request.user,project=project,name=str(request.data.get('name','API Key'))[:100],digest=hashlib.sha256(raw.encode()).hexdigest(),prefix=raw[:12],read_only=bool(read_only),expires_at=timezone.now()+timezone.timedelta(days=30))
    audit(request.user,project,'key.create',obj.pk)
    return Response({'id':obj.pk,'key':raw,'detail':'仅显示一次，有效期 30 天'},status=201)

def admin_only(request):
    if not request.user.is_superuser or isinstance(request.auth,AccessKey): raise PermissionDenied('仅系统管理员可以管理系统')

@api_view(['GET','POST','PATCH'])
def users(request):
    admin_only(request);User=get_user_model()
    if request.method=='GET': return Response(list(User.objects.values('id','username','is_active','is_superuser','date_joined')))
    if request.method=='PATCH':
        user=get_object_or_404(User,pk=request.data.get('id'))
        if user.pk==request.user.pk: raise ValidationError('不能停用自己')
        if type(request.data.get('is_active')) is not bool: raise ValidationError('is_active 必须为布尔值')
        user.is_active=request.data['is_active'];user.save(update_fields=['is_active']);audit(request.user,None,'user.status',user.pk);return Response({'id':user.pk})
    username=request.data.get('username','');password=request.data.get('password','')
    if not isinstance(username,str) or not username.strip() or User.objects.filter(username=username).exists(): raise ValidationError('用户名为空或已存在')
    try: validate_password(password)
    except DjangoValidationError as exc: raise ValidationError(exc.messages)
    user=User.objects.create_user(username=username,password=password);audit(request.user,None,'user.create',user.pk);return Response({'id':user.pk},status=201)

@api_view(['GET','POST','DELETE'])
def organizations(request):
    admin_only(request)
    if request.method=='GET':return Response([{'id':o.pk,'name':o.name,'users':list(o.users.values_list('id',flat=True)),'projects':list(o.projects.values_list('id',flat=True))} for o in Organization.objects.all()])
    if request.method=='DELETE':get_object_or_404(Organization,pk=request.data.get('id')).delete();return Response(status=204)
    name=request.data.get('name','')
    if not name: raise ValidationError('请填写组织名称')
    obj,_=Organization.objects.get_or_create(name=name)
    obj.users.set(get_user_model().objects.filter(pk__in=request.data.get('users',[])));obj.projects.set(Project.objects.filter(pk__in=request.data.get('projects',[])))
    audit(request.user,None,'organization.update',obj.pk);return Response({'id':obj.pk})

@api_view(['GET','PATCH'])
def system(request):
    admin_only(request);item,_=SystemConfig.objects.get_or_create(key='platform',defaults={'value':{'audit_retention_days':90,'available_version':'2.0.0','global_knowledge_ids':[]}})
    if request.method=='PATCH':
        value=request.data
        try: days=int(value.get('audit_retention_days',90))
        except (ValueError,TypeError): raise ValidationError('日志保留天数需为整数')
        if not 7<=days<=3650:raise ValidationError('日志保留时间需在 7–3650 天之间')
        ids=value.get('global_knowledge_ids',[])
        if not isinstance(ids,list) or Entity.objects.filter(kind='knowledge',pk__in=ids).count()!=len(set(ids)):raise ValidationError('无效知识库')
        item.value={'audit_retention_days':days,'available_version':str(value.get('available_version','2.0.0'))[:20],'global_knowledge_ids':ids};item.save();audit(request.user,None,'system.update','platform')
    return Response({'current_version':'2.0.0',**item.value,'adapters':{'llm':'OpenAI-compatible / LangChain','workflow':'LangGraph','api':'httpx','ui':'Playwright Chromium','mcp':'Streamable HTTP JSON-RPC','embedding':'local-hash-256 / OpenAI-compatible'}})

@api_view(['GET'])
def audit_events(request):
    admin_only(request)
    from rest_framework.pagination import PageNumberPagination
    pager=PageNumberPagination();qs=AuditEvent.objects.select_related('user').all()
    if request.query_params.get('search'):qs=qs.filter(Q(action__icontains=request.query_params['search'])|Q(target__icontains=request.query_params['search']))
    page=pager.paginate_queryset(qs,request)
    return pager.get_paginated_response([{'id':e.pk,'user':e.user.username if e.user else 'deleted','project':e.project_id,'action':e.action,'target':e.target,'created_at':e.created_at} for e in page])
