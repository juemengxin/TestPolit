import json, os, re, time, uuid, sqlite3
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from django.conf import settings
from django.core.files import File
from .models import Entity, Artifact, Resource
from .security import allowed_url, decrypt, redact

def validate_config(kind,config):
    if not isinstance(config,dict):raise ValueError('配置必须为对象')
    if kind=='api':
        method=config.get('method','GET')
        if not isinstance(method,str) or method.upper() not in ['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS']:raise ValueError('不支持的 HTTP 方法')
        if not isinstance(config.get('url'),str) or not config['url'].strip():raise ValueError('请求 URL 必填')
        for field in ['headers','params','body','extract']:
            if not isinstance(config.get(field,{}),dict):raise ValueError(f'{field} 必须为 JSON 对象')
        assertions=config.get('assertions',[])
        if not isinstance(assertions,list) or len(assertions)>100:raise ValueError('assertions 必须为最多 100 条的数组')
        for assertion in assertions:
            if not isinstance(assertion,dict) or assertion.get('type') not in ['status','json_equals','contains','duration_lt','db_equals']:raise ValueError('不支持的断言类型')
            if assertion['type']=='db_equals' and (type(assertion.get('database_id')) is not int or not isinstance(assertion.get('query'),str) or not assertion['query'].strip().lower().startswith('select ') or ';' in assertion['query']):
                raise ValueError('数据库断言需引用数据库并使用单条只读 SELECT')
            if assertion['type']=='json_equals' and not isinstance(assertion.get('path'),str):raise ValueError('JSON 断言需提供字段路径')
        if 'function_ids' in config and (not isinstance(config['function_ids'],list) or len(config['function_ids'])>50 or any(type(value) is not int for value in config['function_ids'])):
            raise ValueError('function_ids 必须是整数数组')
        for field in ['before','after']:
            operations=config.get(field,[])
            if not isinstance(operations,list) or len(operations)>100:raise ValueError(f'{field} 必须为最多 100 条的数组')
            _validate_operations(operations)
    if kind=='function':
        for field in ['before','after']:
            operations=config.get(field,[])
            if not isinstance(operations,list) or len(operations)>100:raise ValueError(f'{field} 必须为最多 100 条的数组')
            _validate_operations(operations)
    if kind=='ui_case':
        if config.get('executor')=='mobile-mcp':
            if not isinstance(config.get('package_name'),str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+',config['package_name']):raise ValueError('移动 UI 用例需配置有效 package_name')
            if not isinstance(config.get('device_id'),str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',config['device_id']):raise ValueError('移动 UI 用例需配置 device_id')
            if type(config.get('mcp_resource_id')) is not int:raise ValueError('移动 UI 用例需配置 Mobile MCP 资源 ID')
            steps=config.get('steps',[])
            if not isinstance(steps,list) or not 1<=len(steps)<=100:raise ValueError('移动 UI 需配置 1–100 个步骤')
            for step in steps:
                if not isinstance(step,dict) or step.get('action') not in ['launch_app','tap','tap_field','tap_text','type_text','press_button','swipe','wait_seconds','assert_text','screenshot']:
                    raise ValueError('移动 UI 步骤类型无效或暂不支持')
                if step['action']=='tap_text' and (not isinstance(step.get('value'),str) or not step['value'].strip() or len(step['value'])>200):
                    raise ValueError('按文案点击需填写 1–200 个字符的可见文本')
                if step['action']=='tap_field':
                    locator=step.get('locator')
                    if (not isinstance(locator,dict) or set(locator)-{'resource_id','content_desc','text'} or
                            any(not isinstance(value,str) or len(value)>500 for value in locator.values()) or
                            not any(value.strip() for value in locator.values())):
                        raise ValueError('字段定位需填写资源 ID、无障碍描述或文本')
                if step['action']=='assert_text' and (not isinstance(step.get('value'),str) or not step['value'].strip() or len(step['value'])>200):
                    raise ValueError('文本断言需填写预期文案')
                if step['action']=='tap':
                    ref=step.get('ref','').strip() if isinstance(step.get('ref',''),str) else ''
                    x,y=step.get('x'),step.get('y')
                    if not ref and (type(x) not in (int,float) or type(y) not in (int,float) or x<0 or y<0):raise ValueError('点击步骤需填写有效元素 ref 或非负坐标 x、y')
                    if ref and (len(ref)>30 or not re.fullmatch(r'@e[0-9]+',ref)):raise ValueError('元素 ref 格式无效，请使用 @e1 形式')
                if step['action']=='type_text' and (not isinstance(step.get('value'),str) or not step['value'] or len(step['value'])>2000):
                    raise ValueError('输入步骤需填写 1–2000 个字符')
                if step['action']=='press_button' and step.get('value') not in ['BACK','HOME','ENTER','VOLUME_UP','VOLUME_DOWN']:
                    raise ValueError('按键仅支持 BACK、HOME、ENTER、VOLUME_UP、VOLUME_DOWN')
                if step['action']=='swipe' and step.get('value') not in ['up','down','left','right']:
                    raise ValueError('滑动方向仅支持上、下、左、右')
                if step['action']=='wait_seconds' and (type(step.get('value')) is not int or not 0<=step['value']<=600):
                    raise ValueError('等待时长须为 0–600 秒')
            return
        if not isinstance(config.get('base_url'),str):raise ValueError('UI base_url 必填')
        steps=config.get('steps',[])
        if not isinstance(steps,list) or not 1<=len(steps)<=100:raise ValueError('UI 需配置 1–100 个步骤')
        for step in steps:
            if not isinstance(step,dict) or step.get('action') not in ['goto','click','fill','press','wait_visible','wait_seconds','assert_text','assert_visible','assert_url','select','check','hover','screenshot']:raise ValueError('不支持的 UI 操作，不允许执行任意代码')
            if step['action']=='wait_seconds' and (type(step.get('value')) is not int or not 0<=step['value']<=60):raise ValueError('等待时长须为 0–60 秒')

def _validate_operations(operations):
    """Keep AI-edited hooks within the executor's declarative, non-code operation set."""
    for operation in operations:
        if not isinstance(operation,dict) or operation.get('op') not in ['set','uuid','timestamp','concat']:
            raise ValueError('前后置函数仅支持 set/uuid/timestamp/concat 声明式操作')
        if not isinstance(operation.get('name'),str) or not operation['name'].strip() or len(operation['name'])>100:
            raise ValueError('函数变量名无效')
        if operation['op']=='concat' and (not isinstance(operation.get('values'),list) or len(operation['values'])>100):
            raise ValueError('concat 函数需提供最多 100 项 values 数组')

def json_path(data,path):
    for part in str(path).removeprefix('$.').split('.'):
        if part: data=data[int(part)] if isinstance(data,list) else data[part]
    return data

def interpolate(value,variables):
    if isinstance(value,dict):return {k:interpolate(v,variables) for k,v in value.items()}
    if isinstance(value,list):return [interpolate(v,variables) for v in value]
    if not isinstance(value,str):return value
    def replace(match):
        name=match.group(1)
        if name.startswith('secret:'):
            env_name='TESTPILOT_SECRET_'+name[7:]
            if env_name not in os.environ:raise ValueError('缺少服务端环境密钥引用')
            return os.environ[env_name]
        if name not in variables:raise ValueError('缺少依赖变量：'+name)
        return str(variables[name])
    return re.sub(r'\{\{([\w:.-]+)\}\}',replace,value)

def functions(ops,variables):
    for op in ops:
        name=op.get('name','')
        if not name or len(name)>100:raise ValueError('函数变量名无效')
        if op['op']=='uuid':variables[name]=str(uuid.uuid4())
        elif op['op']=='timestamp':variables[name]=int(time.time())
        elif op['op']=='concat':variables[name]=''.join(str(interpolate(x,variables)) for x in op.get('values',[]))
        else:variables[name]=interpolate(op.get('value',''),variables)

def clean(value):
    output=json.dumps(redact(value),ensure_ascii=False)
    for key,secret in os.environ.items():
        if key.startswith('TESTPILOT_SECRET_') and secret:output=output.replace(secret,'[REDACTED]')
    return json.loads(output)

def prepare_variables(job,base,record):
    """Merge per-run test data without persisting values in execution logs."""
    supplied=job.payload.get('preparation_data',{})
    if not isinstance(supplied,dict):raise ValueError('执行前准备数据必须为 JSON 对象')
    variables={**base,**supplied}
    names=sorted(str(name) for name in supplied)[:100]
    message='执行前数据准备完成'+(f'：已加载 {len(names)} 个变量' if names else '：未填写临时变量，使用环境默认数据')
    record('data_preparation',message,10,{'data_preparation':{'variable_names':names,'count':len(names)}})
    return variables

def prepare_api_request(job,environment,config,variables=None):
    overrides=job.payload.get('request_overrides',{})
    if not isinstance(overrides,dict):raise ValueError('执行前请求配置必须为 JSON 对象')
    params=overrides.get('params',{})
    if not isinstance(params,dict):raise ValueError('执行前请求参数必须为 JSON 对象')
    base_url=str(overrides.get('base_url') or environment.get('base_url','')).strip()
    path=str(overrides.get('path') or config.get('url','')).strip()
    if not path:raise ValueError('请求路径不能为空')
    target='params' if config.get('method','GET').upper() in ['GET','HEAD','DELETE'] else 'body'
    return base_url,{**config,'url':path,target:{**config.get(target,{}),**params}}

def database_value(job,assertion):
    config=Entity.objects.get(pk=assertion.get('database_id'),project=job.project,kind='database').config
    root=(settings.BASE_DIR/'test_databases').resolve();path=(root/str(config.get('path',''))).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('数据库必须位于 backend/test_databases 目录')
    sql=str(assertion.get('query','')).strip()
    if not sql.lower().startswith('select ') or ';' in sql:raise ValueError('数据库校验仅允许单条 SELECT')
    with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
        deadline=time.monotonic()+2
        db.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
        row=db.execute(sql).fetchone()
        return row[0] if row else None

def run_api(job,record):
    import httpx
    skill_names=[s['name'] for s in job.payload.get('skill_snapshots',[])]
    record('skills','通过 Skill 执行接口断言：'+('、'.join(skill_names) if skill_names else '内置接口断言规则'),5,{'skills_used':skill_names})
    env=Entity.objects.get(pk=job.payload['environment_id'],project=job.project,kind='environment').config if job.payload.get('environment_id') else {}
    variables=prepare_variables(job,dict(env.get('variables',{})),record);results=[]
    for index,item in enumerate(job.payload['snapshots']):
        record('request',f'执行接口 {index+1} / {len(job.payload["snapshots"])}',int(index/max(len(job.payload['snapshots']),1)*90))
        raw=item['config'];validate_config('api',raw)
        for fn in Entity.objects.filter(project=job.project,kind='function',pk__in=raw.get('function_ids',[])):functions(fn.config.get('before',[]),variables)
        functions(raw.get('before',[]),variables);base_url,prepared=prepare_api_request(job,env,raw,variables)
        config=interpolate(prepared,variables);base_url=interpolate(base_url,variables)
        url=urljoin(base_url,config['url']);started=time.monotonic();checks=[];response_data={}
        if job.mode=='simulation':
            results.append({'id':item['id'],'name':item['name'],'status':'skipped','detail':'模拟模式未发送 HTTP 请求','assertions':[]});continue
        try:
            allowed_url(url)
            with httpx.Client(timeout=min(float(config.get('timeout',15)),30),follow_redirects=False,trust_env=False) as client:
                with client.stream(config.get('method','GET'),url,headers={**interpolate(env.get('headers',{}),variables),**config.get('headers',{})},params=config.get('params'),json=config.get('body') if config.get('method','GET') not in ['GET','HEAD'] else None) as response:
                    body=b''
                    for piece in response.iter_bytes():
                        body+=piece
                        if len(body)>2*1024*1024:raise ValueError('响应超过 2MB')
                    text=body.decode('utf-8',errors='replace')
                    try:data=json.loads(text)
                    except ValueError:data={}
                    duration=round((time.monotonic()-started)*1000)
                    for rule in config.get('assertions',[]):
                        try:
                            actual=response.status_code if rule['type']=='status' else json_path(data,rule.get('path','')) if rule['type']=='json_equals' else text if rule['type']=='contains' else duration if rule['type']=='duration_lt' else database_value(job,rule)
                            expected=rule.get('expected');passed=str(expected) in actual if rule['type']=='contains' else actual<float(expected) if rule['type']=='duration_lt' else actual==expected
                            checks.append({'type':rule['type'],'passed':passed,'expected':expected,'actual':actual if not isinstance(actual,str) else actual[:1000]})
                        except (KeyError,TypeError,ValueError,IndexError):checks.append({'type':rule['type'],'passed':False,'detail':'断言字段或预期类型无效'})
                    for name,path in config.get('extract',{}).items():variables[name]=json_path(data,path)
                    functions(config.get('after',[]),variables)
                    response_data={'status_code':response.status_code,'headers':dict(response.headers),'body':data if data else text[:50000],'duration_ms':duration}
            results.append(clean({'id':item['id'],'name':item['name'],'status':'passed' if checks and all(c['passed'] for c in checks) else 'failed','assertions':checks,'response':response_data,'request':{'method':config.get('method','GET'),'url':url},'detail':'' if checks else '未配置断言，不能判定通过'}))
        except Exception as exc:
            if isinstance(exc,ValueError):detail=str(exc)
            elif isinstance(exc,httpx.ConnectError):detail='无法连接目标服务：请检查 Worker 的 DNS/网络出口、防火墙、代理和目标白名单'
            elif isinstance(exc,httpx.TimeoutException):detail='请求目标服务超时：请检查 Worker 到目标服务的网络延迟和服务状态'
            else:detail=f'{type(exc).__name__}：请求未成功，请检查环境、依赖变量或连接配置'
            results.append({'id':item['id'],'name':item['name'],'status':'failed','assertions':checks,'detail':detail})
        record('response','接口结果已保存',int((index+1)/len(job.payload['snapshots'])*95),{'cases':results})
    return {'cases':results,'variables':clean(variables),'mode':job.mode,'summary':summary(results),'skills_used':skill_names}

def summary(results):return {'total':len(results),'passed':sum(x['status']=='passed' for x in results),'failed':sum(x['status']=='failed' for x in results),'skipped':sum(x['status']=='skipped' for x in results)}

def save_artifact(job,path,category):
    with open(path,'rb') as file:
        obj=Artifact(project=job.project,job=job,name=Path(path).name,category=category);obj.file.save(Path(path).name,File(file),save=True)
    return obj.pk

def run_ui(job,record):
    skill_names=[s['name'] for s in job.payload.get('skill_snapshots',[])]
    record('skills','通过 Skill 编排 UI 执行：'+('、'.join(skill_names) if skill_names else '内置 UI 执行规则'),5,{'skills_used':skill_names})
    mobile_cases=[x for x in job.payload['snapshots'] if x['config'].get('executor')=='mobile-mcp']
    if mobile_cases:
        if len(mobile_cases)!=len(job.payload['snapshots']):raise ValueError('移动端与浏览器 UI 用例需分开执行')
        env=Entity.objects.get(project=job.project,pk=job.payload['environment_id'],kind='ui_environment').config if job.payload.get('environment_id') else {}
        for item in mobile_cases:
            variables=dict(env.get('variables',{}))
            for data in Entity.objects.filter(project=job.project,kind='ui_data',pk__in=item['config'].get('data_ids',[])):
                variables.update(data.config.get('variables',{}))
                app_profile=data.config.get('app_profile') if data.config.get('data_type')=='app_profile' else None
                if app_profile:
                    item['config'].update({key:app_profile.get(key) for key in ['app_name','package_name','device_id','mcp_resource_id']})
            variables=prepare_variables(job,variables,record)
            item['config']=interpolate(item['config'],variables)
        if job.mode=='simulation':return {'mode':'simulation','skills_used':skill_names,'cases':[{'id':x['id'],'name':x['name'],'status':'skipped','detail':'模拟模式未连接移动设备'} for x in job.payload['snapshots']]}
        return run_mobile_ui(job,record,skill_names)
    env=Entity.objects.get(project=job.project,pk=job.payload['environment_id'],kind='ui_environment').config if job.payload.get('environment_id') else {}
    repeat_count=job.payload.get('repeat_count',1)
    stall_threshold_ms=job.payload.get('stall_threshold_ms',5000)
    if type(repeat_count) is not int or not 1<=repeat_count<=20:raise ValueError('重复回放次数必须为 1–20')
    if type(stall_threshold_ms) is not int or not 500<=stall_threshold_ms<=60000:raise ValueError('卡顿阈值必须为 500–60000 毫秒')
    variables=dict(env.get('variables',{}))
    for item in job.payload['snapshots']:
        for data in Entity.objects.filter(project=job.project,kind='ui_data',pk__in=item['config'].get('data_ids',[])):
            variables.update(data.config.get('variables',{}))
            web_profile=data.config.get('web_profile') if data.config.get('data_type')=='web_profile' else None
            if web_profile:item['config']['base_url']=web_profile.get('base_url',item['config'].get('base_url',''))
    variables=prepare_variables(job,variables,record)
    if job.mode=='simulation':return {'mode':'simulation','skills_used':skill_names,'data_preparation':{'variable_names':sorted(job.payload.get('preparation_data',{}))},'cases':[{'id':x['id'],'name':x['name'],'status':'skipped','detail':'模拟模式未启动浏览器'} for x in job.payload['snapshots']]}
    import tempfile
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections
    from playwright.sync_api import sync_playwright
    results=[]
    page_objects={item.pk:item.config for item in Entity.objects.filter(project=job.project,kind='page')}
    original_record=record
    def database_call(function,*args):
        def invoke():
            close_old_connections()
            try:return function(*args)
            finally:close_old_connections()
        return database_pool.submit(invoke).result()
    def record(*args):return database_call(original_record,*args)
    def artifact(path,category):return database_call(save_artifact,job,path,category)
    with ThreadPoolExecutor(max_workers=1) as database_pool, tempfile.TemporaryDirectory(prefix='testpilot-ui-') as tmp, sync_playwright() as p:
        launch_options={'headless':True}
        if os.getenv('TESTPILOT_BROWSER_EXECUTABLE'):
            launch_options['executable_path']=os.environ['TESTPILOT_BROWSER_EXECUTABLE']
        elif os.getenv('TESTPILOT_BROWSER_CHANNEL'):
            launch_options['channel']=os.environ['TESTPILOT_BROWSER_CHANNEL']
        browser=p.chromium.launch(**launch_options)
        try:
            replay_items=[{**item,'_replay_iteration':iteration} for iteration in range(1,repeat_count+1) for item in job.payload['snapshots']]
            for index,item in enumerate(replay_items):
                config=interpolate(item['config'],variables);validate_config('ui_case',config)
                base=env.get('base_url') or config['base_url'];allowed_url(base)
                context=browser.new_context(record_video_dir=tmp,viewport={'width':1280,'height':800},service_workers='block')
                context.set_default_timeout(min(int(env.get('timeout',10000)),30000))
                def route_request(route):
                    try:allowed_url(route.request.url);route.continue_()
                    except ValueError:route.abort()
                context.route('**/*',route_request)
                context.route_web_socket('**/*',lambda ws:ws.close())
                context.tracing.start(screenshots=True,snapshots=True,sources=True)
                page=context.new_page();steps=[];console=[];page_errors=[]
                page.on('console',lambda msg:console.append({'level':msg.type,'message':clean(msg.text)[:1000]}))
                page.on('pageerror',lambda exc:page_errors.append(clean(str(exc))[:1000]))
                trace=Path(tmp)/f'trace-{item["id"]}.zip'
                try:
                    for n,step in enumerate(config['steps']):
                        record('ui',f'用例 {item["id"]} 第 {item["_replay_iteration"]} 轮 · 步骤 {n+1}',min(90,int((index+n/len(config['steps']))/len(replay_items)*90)))
                        start=time.monotonic();action=step['action'];selector=step.get('selector','');value=str(step.get('value',''))
                        if not selector and step.get('page_id'):
                            selector=page_objects.get(step['page_id'],{}).get('elements',{}).get(step.get('element'),'')
                        locator=page.locator(selector) if selector else None
                        try:
                            if action=='goto':page.goto(allowed_url(urljoin(base,value)),wait_until='domcontentloaded')
                            elif action=='click':locator.click()
                            elif action=='wait_seconds':page.wait_for_timeout(int(value)*1000)
                            elif action=='hover':locator.hover()
                            elif action=='fill':locator.fill(value)
                            elif action=='press':locator.press(value)
                            elif action=='select':locator.select_option(value)
                            elif action=='check':locator.uncheck() if value.lower()=='false' else locator.check()
                            elif action=='wait_visible':locator.wait_for(state='visible')
                            elif action=='assert_visible':
                                if not locator.is_visible():raise AssertionError('元素不可见')
                            elif action=='assert_text':
                                from playwright.sync_api import expect
                                expect(locator).to_contain_text(value)
                            elif action=='assert_url':
                                if value not in page.url:raise AssertionError('URL 与预期不符')
                            screenshot=Path(tmp)/f'case-{item["id"]}-step-{n+1}.png';page.screenshot(path=str(screenshot),full_page=True)
                            duration_ms=round((time.monotonic()-start)*1000)
                            steps.append({'index':n,'action':action,'selector':selector,'status':'passed','duration_ms':duration_ms,
                                          'stalled':duration_ms>=stall_threshold_ms,'screenshot':artifact(screenshot,'screenshot')})
                        except Exception as exc:
                            duration_ms=round((time.monotonic()-start)*1000)
                            screenshot_id=None
                            try:
                                screenshot=Path(tmp)/f'failure-{item["id"]}-round-{item["_replay_iteration"]}-{n+1}.png'
                                page.screenshot(path=str(screenshot));screenshot_id=artifact(screenshot,'screenshot')
                            except Exception:
                                pass
                            failed_step={'index':n,'action':action,'selector':selector,'status':'failed','duration_ms':duration_ms,
                                         'stalled':duration_ms>=stall_threshold_ms,'error':type(exc).__name__+'：'+redact(clean(str(exc)))[:1000]}
                            if screenshot_id:failed_step['screenshot']=screenshot_id
                            steps.append(failed_step);break
                finally:
                    context.tracing.stop(path=str(trace));trace_id=artifact(trace,'trace');video=page.video;context.close()
                    video_id=artifact(video.path(),'video') if video else None
                results.append({'id':item['id'],'name':item['name'],'iteration':item['_replay_iteration'],'repeat_count':repeat_count,
                                'status':'failed' if any(x['status']=='failed' for x in steps) or page_errors else 'passed','steps':steps,
                                'trace':trace_id,'video':video_id,'console':console[:100], 'page_errors':page_errors[:100],
                                'stalls':[{'step':step['index']+1,'duration_ms':step['duration_ms']} for step in steps if step.get('stalled')]})
                record('ui_result','UI 回放产物已保存',min(95,int((index+1)/len(replay_items)*95)),{'cases':results})
        finally:browser.close()
    stability=[]
    for case_id in dict.fromkeys(item['id'] for item in results):
        replay_results=[item for item in results if item['id']==case_id]
        passed=sum(item['status']=='passed' for item in replay_results)
        stability.append({'case_id':case_id,'repeat_count':len(replay_results),'passed':passed,'failed':len(replay_results)-passed,
                          'pass_rate':round(passed/len(replay_results)*100,1),'stable':passed==len(replay_results),
                          'stall_count':sum(len(item['stalls']) for item in replay_results),
                          'page_error_count':sum(len(item['page_errors']) for item in replay_results)})
    return {'cases':results,'summary':summary(results),'stability':stability,'mode':'live','executor':'local-playwright-chromium',
            'repeat_count':repeat_count,'stall_threshold_ms':stall_threshold_ms,'skills_used':skill_names}


def run_mobile_ui(job,record,skill_names):
    import tempfile
    from .mobile_tools import capture_screen, assert_app_text, resolve_screen_element
    results=[]
    with tempfile.TemporaryDirectory(prefix='testpilot-mobile-ui-') as tmp:
        for index,item in enumerate(job.payload['snapshots']):
            config=item['config'];validate_config('ui_case',config)
            resource=Resource.objects.get(pk=config['mcp_resource_id'],project=job.project,kind='mcp',enabled=True,mode='live')
            device=config['device_id'];package=config['package_name'];steps=[]
            for number,step in enumerate(config['steps']):
                action=step['action'];started=time.monotonic()
                record('mobile_ui',f'用例 {item["id"]} 步骤 {number+1}：{action}',min(90,int((index+number/len(config['steps']))/len(job.payload['snapshots'])*90)))
                result={'index':number,'action':action,'status':'running'}
                try:
                    if action=='launch_app':
                        response=mcp_request(resource,'tools/call',{'name':'mobile_launch_app','arguments':{'device':device,'packageName':package}})
                        if response.get('isError'):raise ValueError('应用启动失败')
                    elif action=='tap':
                        args={'device':device}
                        if step.get('ref'):
                            current=mcp_request(resource,'tools/call',{'name':'mobile_list_elements_on_screen','arguments':{'device':device,'format':'text'}})
                            current_text='\n'.join(part.get('text','') for part in current.get('content',[]) if part.get('type')=='text')
                            if current.get('isError') or not re.search(rf'(?<![A-Za-z0-9_]){re.escape(step["ref"])}(?=\s|$)',current_text):
                                raise ValueError('目标元素 ref 已失效或当前页面不可见，请重新读取页面元素')
                            args['ref']=step['ref']
                        else:
                            args.update({'x':step['x'],'y':step['y']})
                        response=mcp_request(resource,'tools/call',{'name':'mobile_click_on_screen_at_coordinates','arguments':args})
                        if response.get('isError'):raise ValueError('点击操作失败')
                    elif action=='tap_text':
                        current=mcp_request(resource,'tools/call',{'name':'mobile_list_elements_on_screen','arguments':{'device':device,'format':'text'}})
                        current_text='\n'.join(part.get('text','') for part in current.get('content',[]) if part.get('type')=='text')
                        if current.get('isError'):raise ValueError('页面结构读取失败')
                        target=step['value'];ref=None
                        for line in current_text.splitlines():
                            if target in line:
                                match=re.search(r'@e[0-9]+',line)
                                if match:ref=match.group(0);break
                        if not ref:raise ValueError(f'当前页面未找到可点击文案：{target}')
                        response=mcp_request(resource,'tools/call',{'name':'mobile_click_on_screen_at_coordinates','arguments':{'device':device,'ref':ref}})
                        if response.get('isError'):raise ValueError('点击操作失败')
                    elif action=='tap_field':
                        current=mcp_request(resource,'tools/call',{'name':'mobile_list_elements_on_screen','arguments':{'device':device,'format':'text'}})
                        current_text='\n'.join(part.get('text','') for part in current.get('content',[]) if part.get('type')=='text')
                        if current.get('isError'):raise ValueError('页面结构读取失败')
                        ref=resolve_screen_element(current_text,step['locator'])
                        response=mcp_request(resource,'tools/call',{'name':'mobile_click_on_screen_at_coordinates','arguments':{'device':device,'ref':ref}})
                        if response.get('isError'):raise ValueError('点击操作失败')
                    elif action=='type_text':
                        response=mcp_request(resource,'tools/call',{'name':'mobile_type_keys','arguments':{'device':device,'text':step['value'],'submit':bool(step.get('submit',False))}})
                        if response.get('isError'):raise ValueError('文本输入失败')
                    elif action=='press_button':
                        response=mcp_request(resource,'tools/call',{'name':'mobile_press_button','arguments':{'device':device,'button':step['value']}})
                        if response.get('isError'):raise ValueError('设备按键操作失败')
                    elif action=='swipe':
                        response=mcp_request(resource,'tools/call',{'name':'mobile_swipe_on_screen','arguments':{'device':device,'direction':step['value']}})
                        if response.get('isError'):raise ValueError('页面滑动失败')
                    elif action=='wait_seconds':
                        time.sleep(step['value'])
                    elif action=='assert_text':
                        response=mcp_request(resource,'tools/call',{'name':'mobile_list_elements_on_screen','arguments':{'device':device,'format':'text'}})
                        if response.get('isError'):raise ValueError('页面结构读取失败')
                        observed='\n'.join(part.get('text','') for part in response.get('content',[]) if part.get('type')=='text')
                        expected=step['value'];result['expected']=expected
                        try:
                            assert_app_text(observed, package, expected)
                        except ValueError as exc:
                            raise AssertionError(str(exc)) from exc
                    elif action=='screenshot':
                        pass
                    else:
                        raise ValueError('遇到未实现的移动端操作，已停止执行')
                    screenshot=Path(tmp)/f'mobile-{item["id"]}-{number+1}.png'
                    image,extension=capture_screen(resource,device)
                    screenshot=screenshot.with_suffix('.'+extension)
                    screenshot.write_bytes(image)
                    result['screenshot']=save_artifact(job,screenshot,'screenshot')
                    result['status']='passed'
                except Exception as exc:
                    result['status']='failed';result['error']=str(exc)[:300] if isinstance(exc,(ValueError,AssertionError)) else '移动设备操作失败'
                    screenshot=Path(tmp)/f'mobile-failure-{item["id"]}-{number+1}.png'
                    try:
                        image,extension=capture_screen(resource,device)
                        screenshot=screenshot.with_suffix('.'+extension)
                        screenshot.write_bytes(image)
                        result['screenshot']=save_artifact(job,screenshot,'screenshot')
                    except Exception:pass
                result['duration_ms']=round((time.monotonic()-started)*1000)
                steps.append(result)
                if result['status']=='failed':break
            results.append({'id':item['id'],'name':item['name'],'status':'failed' if any(step['status']=='failed' for step in steps) else 'passed',
                            'verification_status':config.get('verification_status','unverified'),'device_id':device,'package_name':package,'steps':steps})
            record('mobile_ui_result','移动端 UI 执行结果已保存',min(95,int((index+1)/len(job.payload['snapshots'])*95)),{'cases':results})
    return {'cases':results,'summary':summary(results),'mode':'live','executor':'mobile-mcp','skills_used':skill_names}

def mcp_request(resource,method,params=None,*,include_images=False):
    import httpx
    allowed_url(resource.endpoint)
    headers={'Accept':'application/json, text/event-stream','Content-Type':'application/json'}
    host=urlsplit(resource.endpoint)
    # The authenticated host MCP is bound to loopback with DNS-rebinding protection;
    # Docker reaches it through the gateway but must retain its approved localhost Host.
    if host.hostname=='host.docker.internal' and host.port==8933:
        headers['Host']='localhost:8933'
    if resource.secret_encrypted:headers['Authorization']='Bearer '+decrypt(resource)
    def parse(response):
        response.raise_for_status()
        if 'text/event-stream' in response.headers.get('content-type',''):
            values=[line[5:].strip() for line in response.text.splitlines() if line.startswith('data:')]
            return json.loads(values[-1])
        return response.json()
    with httpx.Client(timeout=25,follow_redirects=False,trust_env=False) as client:
        initialized=client.post(resource.endpoint,headers=headers,json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'TestPilot','version':'2.0.0'}}})
        data=parse(initialized)
        if data.get('error'):raise ValueError('MCP 初始化失败')
        if initialized.headers.get('mcp-session-id'):headers['Mcp-Session-Id']=initialized.headers['mcp-session-id']
        headers['MCP-Protocol-Version']=data.get('result',{}).get('protocolVersion','2025-03-26')
        client.post(resource.endpoint,headers=headers,json={'jsonrpc':'2.0','method':'notifications/initialized'})
        result=parse(client.post(resource.endpoint,headers=headers,json={'jsonrpc':'2.0','id':2,'method':method,'params':params or {}}))
        if result.get('error'):raise ValueError('MCP 服务返回工具错误')
        raw=result.get('result',{})
        safe=redact(raw)
        if include_images:
            # Screenshot bytes are not log text: truncation would corrupt the image.
            for source,target in zip(raw.get('content',[]),safe.get('content',[])):
                if source.get('type')=='image' and source.get('mimeType') in ('image/png','image/jpeg'):
                    data=source.get('data','')
                    if not isinstance(data,str) or len(data)>16*1024*1024:raise ValueError('设备截图过大')
                    target['data']=data
        return safe
