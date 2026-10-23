import os, json, re, tempfile
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient
from .models import Project,Membership,Entity,Resource,Job,Asset,KnowledgeChunk,Artifact,AccessKey,MachineConnection,Run,Attachment
from .job_worker import claim_job,execute_job,safe_model_status_error,safe_ui_error
from .entities import hash_vector, parse_document
from .security import allowed_url
from .executors import prepare_variables,prepare_api_request,validate_config

@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PlatformTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('owner',password='test-password')
        self.viewer=get_user_model().objects.create_user('reader',password='test-password')
        self.other_user=get_user_model().objects.create_user('other',password='test-password')
        self.project=Project.objects.create(name='项目',key='TEST',owner=self.owner)
        self.other=Project.objects.create(name='其他',key='OTHER',owner=self.other_user)
        Membership.objects.create(project=self.project,user=self.viewer,role='viewer')
        self.client=APIClient();self.client.force_authenticate(self.owner)
        self.tmp=tempfile.TemporaryDirectory();self.override=override_settings(MEDIA_ROOT=self.tmp.name);self.override.enable();self.addCleanup(self.override.disable);self.addCleanup(self.tmp.cleanup)
    def entity(self,kind='requirement',**extra):
        response=self.client.post('/api/entities/',{'project':self.project.pk,'kind':kind,'name':'登录需求','content':'用户使用有效账号登录，失败显示错误信息。','config':{},**extra},format='json')
        self.assertEqual(response.status_code,201,response.data);return response.data
    def job(self,kind='generate',payload=None,mode='simulation'):
        response=self.client.post('/api/jobs/',{'project':self.project.pk,'kind':kind,'mode':mode,'payload':payload or {'text':'用户登录','auto_apply':True}},format='json')
        self.assertEqual(response.status_code,202,response.data);return Job.objects.get(pk=response.data['id'])
    def execute(self,job):
        claimed=claim_job();self.assertEqual(claimed.pk,job.pk);execute_job(claimed);job.refresh_from_db();return job
    def test_execution_data_preparation_overrides_environment_without_logging_values(self):
        records=[]
        job=SimpleNamespace(payload={'preparation_data':{'username':'runner','password':'secret-value'}})
        variables=prepare_variables(job,{'username':'environment-user','base_url':'https://example.test'},lambda *args:records.append(args))
        self.assertEqual(variables['username'],'runner')
        self.assertEqual(variables['base_url'],'https://example.test')
        self.assertNotIn('secret-value',json.dumps(records))
        self.assertEqual(records[0][0],'data_preparation')
        job.payload['request_overrides']={'base_url':'https://override.test','path':'/v2/login','params':{'locale':'zh-CN'}}
        base_url,config=prepare_api_request(job,{'base_url':'https://environment.test'},{'method':'POST','url':'/v1/login','params':{'page':1}})
        self.assertEqual(base_url,'https://override.test')
        self.assertEqual(config['url'],'/v2/login')
        self.assertEqual(config['params'],{'page':1})
        self.assertEqual(config['body'],{'locale':'zh-CN'})

    def test_safe_ui_error_exposes_known_configuration_errors_only(self):
        self.assertEqual(safe_ui_error(ValueError('缺少依赖变量：valid_phone')),'缺少依赖变量：valid_phone')
        self.assertIsNone(safe_ui_error(ValueError('remote response included password=private-value')))
    def test_model_status_error_reports_only_safe_http_status(self):
        class APIStatusError(Exception):
            status_code=401
        self.assertEqual(safe_model_status_error(APIStatusError('authorization=private-value')),
                         '模型服务返回 HTTP 401；请检查 API Key。未记录服务响应正文或密钥。')
        self.assertIsNone(safe_model_status_error(ValueError('private-value')))
    def test_unverified_model_is_blocked_outside_connection_test(self):
        from .model_client import require_verified_model
        resource=SimpleNamespace(mode='simulation')
        with self.assertRaisesRegex(ValueError,'尚未通过真实连接测试'):
            require_verified_model(resource)
        require_verified_model(resource,connection_test=True)
    def test_versions_and_conflict(self):
        e=self.entity();res=self.client.patch(f'/api/entities/{e["id"]}/',{'content':'第二版','version':1},format='json');self.assertEqual(res.status_code,200)
        res=self.client.patch(f'/api/entities/{e["id"]}/',{'content':'覆盖','version':1},format='json');self.assertEqual(res.status_code,400)
        history=self.client.get(f'/api/entities/{e["id"]}/revisions/').data;self.assertEqual(len(history),2)
        self.assertEqual(self.client.post(f'/api/entities/{e["id"]}/restore/',{'version':1},format='json').status_code,200)
    def test_playwright_trace_import_creates_unverified_web_case(self):
        trace=BytesIO()
        with ZipFile(trace,'w') as archive:
            archive.writestr('trace.trace','\n'.join(json.dumps(event) for event in [
                {'type':'before','apiName':'page.goto','params':{'url':'https://example.test/login'}},
                {'type':'before','apiName':'locator.fill','params':{'selector':'#username','value':'tester'}},
                {'type':'before','apiName':'locator.click','params':{'selector':'button[type=submit]'}},
            ]))
        upload=SimpleUploadedFile('login-recording.zip',trace.getvalue(),content_type='application/zip')
        response=self.client.post('/api/entities/import_playwright_recording/',{'project':self.project.pk,'file':upload},format='multipart')
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(response.data['config']['base_url'],'https://example.test')
        self.assertEqual([step['action'] for step in response.data['config']['steps']],['goto','fill','click'])
        self.assertEqual(response.data['config']['verification_status'],'unverified')
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post('/api/entities/import_playwright_recording/',{'project':self.project.pk,'file':SimpleUploadedFile('x.zip',b'x')},format='multipart').status_code,403)
    def test_app_video_upload_creates_private_background_job(self):
        model=Resource.objects.create(project=self.project,kind='model',name='视觉模型',endpoint='http://127.0.0.1:9000/v1')
        mobile=Resource.objects.create(project=self.project,kind='mcp',name='Mobile MCP',endpoint='http://localhost:8933/mcp',mode='live')
        video=SimpleUploadedFile('login.mp4',b'\x00\x00\x00\x18ftypmp42fixture',content_type='video/mp4')
        response=self.client.post('/api/entities/generate_app_video_case/',{
            'project':self.project.pk,'model_id':model.pk,'resource_id':mobile.pk,'device_id':'phone-1',
            'package_name':'com.example.app','file':video},format='multipart')
        self.assertEqual(response.status_code,202,response.data)
        job=Job.objects.get(pk=response.data['task_id'])
        self.assertEqual(job.kind,'app_video');self.assertEqual(job.status,'queued')
        artifact=Artifact.objects.get(job=job,category='video')
        self.assertEqual(artifact.name,'login.mp4')
        self.client.force_authenticate(self.viewer)
        denied=self.client.post('/api/entities/generate_app_video_case/',{
            'project':self.project.pk,'model_id':model.pk,'resource_id':mobile.pk,'device_id':'phone-1',
            'package_name':'com.example.app','file':SimpleUploadedFile('again.mp4',b'\x00\x00\x00\x18ftypmp42')},format='multipart')
        self.assertEqual(denied.status_code,403)
    def test_app_video_worker_persists_only_field_located_unverified_steps(self):
        from .app_video import analyze_app_video
        model=Resource.objects.create(project=self.project,kind='model',name='视觉模型',endpoint='http://127.0.0.1:9000/v1')
        mobile=Resource.objects.create(project=self.project,kind='mcp',name='Mobile MCP',endpoint='http://localhost:8933/mcp',mode='live')
        job=Job.objects.create(project=self.project,kind='app_video',name='视频分析',mode='live',created_by=self.owner,
            payload={'model_id':model.pk,'resource_id':mobile.pk,'device_id':'phone-1','package_name':'com.example.app'})
        artifact=Artifact.objects.create(project=self.project,job=job,name='login.mp4',category='video',file=SimpleUploadedFile('login.mp4',b'video'))
        job.payload={**job.payload,'artifact_id':artifact.pk};job.save(update_fields=['payload'])
        output={'title':'登录流程','steps':[
            {'action':'tap_field','locator':{'text':'登录'},'description':'点击登录'},
            {'action':'type_text','value':'private-password','description':'输入凭据'},
        ],'open_questions':[]}
        with patch('workbench.app_video.extract_video_frames',return_value=(3.0,[{'seconds':0.0,'data':b'frame'}])),\
             patch('workbench.app_video._invoke_vision_model',return_value=(output,{'input_tokens':1,'output_tokens':1,'total_tokens':2,'calls':1})):
            result=analyze_app_video(job,lambda *args,**kwargs:None)
        case=Entity.objects.get(pk=result['case_id'])
        self.assertEqual(case.config['verification_status'],'unverified')
        self.assertEqual(case.config['steps'][1]['action'],'tap_field')
        self.assertNotIn('x',case.config['steps'][1]);self.assertNotIn('y',case.config['steps'][1])
        self.assertEqual(case.config['steps'][2]['value'],'{{recorded_input_1}}')
        self.assertNotIn('private-password',json.dumps(case.config)+json.dumps(result))
    def test_live_recording_requires_editor_and_allowlisted_target(self):
        self.client.force_authenticate(self.viewer)
        response=self.client.post('/api/entities/recording_start/',{'project':self.project.pk,'url':'https://example.test/login'},format='json')
        self.assertEqual(response.status_code,403)
        self.client.force_authenticate(self.owner)
        with patch.dict(os.environ,{'TESTPILOT_ALLOWED_TARGETS':''}):
            response=self.client.post('/api/entities/recording_start/',{'project':self.project.pk,'url':'https://example.test/login'},format='json')
        self.assertEqual(response.status_code,400)
        self.assertIn('TESTPILOT_ALLOWED_TARGETS',str(response.data))
    def test_project_overview_aggregates_completed_runs_by_local_day(self):
        now=timezone.now()
        Run.objects.create(project=self.project,name='历史执行',created_by=self.owner,status='completed',
                           finished_at=now-timedelta(days=8),metrics={'passed':5,'total':5})
        recent_run=Run.objects.create(project=self.project,name='今日执行',created_by=self.owner,status='completed',
                                      finished_at=now,metrics={'passed':1,'total':2})
        Attachment.objects.create(project=self.project,run=recent_run,name='执行截图.png',size=3,
                                  file=SimpleUploadedFile('执行截图.png',b'png'),uploaded_by=self.owner)

        response=self.client.get(f'/api/projects/{self.project.pk}/overview/')

        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['runs'],2)
        self.assertEqual(response.data['tested_cases'],7)
        self.assertEqual(response.data['pass_rate'],85.7)
        self.assertEqual(len(response.data['trend']),7)
        self.assertEqual(response.data['trend'][-1],{
            'date':timezone.localdate().isoformat(),'passed':1,'total':2,'runs':1,
        })
        self.assertEqual(response.data['trend'][0]['runs'],0)
        self.assertEqual(response.data['recent_runs'][0]['attachments'][0]['name'],'执行截图.png')
    def test_failure_analysis_repairs_case_and_queues_live_retry(self):
        original={'method':'GET','url':'/wrong','headers':{'Authorization':'Bearer {{secret:DEMO}}'},'params':{},'body':{},
                  'assertions':[{'type':'status','expected':200}],'extract':{},'before':[],'after':[]}
        repaired={**original,'url':'/health','headers':{'Authorization':'[REDACTED]'}}
        case=self.entity('api',name='健康检查',config=original)
        source=Job.objects.create(project=self.project,kind='api',name='接口执行',mode='live',status='completed',created_by=self.owner,
            payload={'entity_ids':[case['id']],'snapshots':[{'id':case['id'],'kind':'api','name':'健康检查','content':'','config':original,'version':1}]},
            result={'cases':[{'id':case['id'],'status':'failed'}]})
        model=Resource.objects.create(project=self.project,kind='model',name='分析模型')
        created_analysis=self.client.post('/api/jobs/',{'project':self.project.pk,'kind':'analyze','name':'失败分析','mode':'live',
            'payload':{'model_id':model.pk,'source_job':source.pk,'failure_case_id':case['id']}},format='json')
        self.assertEqual(created_analysis.status_code,202,created_analysis.data)
        self.assertEqual(Job.objects.get(pk=created_analysis.data['id']).payload['target_kind'],'api')
        analysis=Job.objects.create(project=self.project,kind='analyze',name='失败分析',mode='live',status='completed',created_by=self.owner,
            payload={'source_job':source.pk,'failure_case_id':case['id'],'target_kind':'api'},
            result={'output':{'classification':'test_case_issue','repair_config':repaired}})

        response=self.client.post(f'/api/jobs/{analysis.pk}/repair_retry/',{},format='json')

        self.assertEqual(response.status_code,202,response.data)
        case_record=Entity.objects.get(pk=case['id'])
        self.assertEqual(case_record.config,{**repaired,'headers':original['headers']})
        self.assertEqual(case_record.version,2)
        retry=Job.objects.get(pk=response.data['job']['id'])
        self.assertEqual(retry.kind,'api')
        self.assertEqual(retry.mode,'live')
        self.assertEqual(retry.status,'queued')
        self.assertEqual(retry.payload['snapshots'][0]['config'],case_record.config)

    def test_api_ai_optimization_applies_related_configs_and_preserves_secrets(self):
        environment=self.entity('environment',name='测试环境',config={
            'base_url':'https://api.example.test','headers':{'X-Token':'{{secret:API_TOKEN}}'},
            'variables':{'account':'old-account','API_TOKEN':'{{secret:API_TOKEN}}'}})
        function=self.entity('function',name='登录前置',config={'before':[{'op':'set','name':'trace','value':'old'}],'after':[]})
        database=self.entity('database',name='只读校验库',config={'path':'fixture.sqlite'})
        original={'method':'GET','url':'/v1/profile','headers':{'Authorization':'Bearer {{secret:API_TOKEN}}'},
                  'params':{'user_id':'{{account_id}}'},'body':{},'assertions':[{'type':'status','expected':200}],
                  'extract':{},'before':[],'after':[],'function_ids':[function['id']]}
        case=self.entity('api',name='读取用户资料',config=original)
        patched={**original,'params':{'user_id':'{{account_id}}','include':'profile'},
                 'assertions':[{'type':'status','expected':200},{'type':'db_equals','database_id':database['id'],'query':'SELECT active FROM users WHERE id = 1','expected':1}],
                 'headers':{'Authorization':'[REDACTED]'},'extract':{'account_id':'data.id'}}
        job=Job.objects.create(project=self.project,kind='optimize',name='AI 修复接口配置',mode='live',status='completed',created_by=self.owner,
            payload={'target_kind':'api','snapshots':[{'id':case['id'],'kind':'api','name':'读取用户资料','content':'','config':original,'version':1}],
                     'related_snapshots':[{'id':environment['id'],'kind':'environment','name':'测试环境','content':'','config':environment['config'],'version':1},
                                          {'id':function['id'],'kind':'function','name':'登录前置','content':'','config':function['config'],'version':1},
                                          {'id':database['id'],'kind':'database','name':'只读校验库','content':'','config':database['config'],'version':1}]},
            result={'output':{'cases':[{'id':case['id'],'name':'读取用户资料','config':patched}],
                              'related_configs':[{'id':environment['id'],'config':{'base_url':'https://api.example.test','headers':{'X-Token':'[REDACTED]'},'variables':{'account':'new-account','API_TOKEN':'[REDACTED]'}}},
                                                 {'id':function['id'],'config':{'before':[{'op':'uuid','name':'trace'}],'after':[]}}]}})

        response=self.client.post(f'/api/jobs/{job.pk}/apply/',{},format='json')

        self.assertEqual(response.status_code,200,response.data)
        case_record=Entity.objects.get(pk=case['id'])
        self.assertEqual(case_record.config['params']['include'],'profile')
        self.assertEqual(case_record.config['headers'],original['headers'])
        self.assertEqual(case_record.config['assertions'][1]['database_id'],database['id'])
        env_record=Entity.objects.get(pk=environment['id'])
        self.assertEqual(env_record.config['variables']['account'],'new-account')
        self.assertEqual(env_record.config['variables']['API_TOKEN'],'{{secret:API_TOKEN}}')
        self.assertEqual(env_record.config['headers']['X-Token'],'{{secret:API_TOKEN}}')
        self.assertEqual(Entity.objects.get(pk=function['id']).config['before'][0]['op'],'uuid')
        self.assertEqual(Entity.objects.get(pk=database['id']).version,1)
        self.assertEqual(job.__class__.objects.get(pk=job.pk).applied,True)

    def test_api_ai_optimization_rejects_stale_related_config_atomically(self):
        environment=self.entity('environment',name='环境',config={'base_url':'https://api.example.test','headers':{},'variables':{}})
        case=self.entity('api',name='健康检查',config={'method':'GET','url':'/health','headers':{},'params':{},'body':{},'assertions':[{'type':'status','expected':200}]})
        job=Job.objects.create(project=self.project,kind='optimize',name='AI 修复接口配置',mode='live',status='completed',created_by=self.owner,
            payload={'target_kind':'api','snapshots':[{'id':case['id'],'kind':'api','name':'健康检查','content':'','config':case['config'],'version':1}],
                     'related_snapshots':[{'id':environment['id'],'kind':'environment','name':'环境','content':'','config':environment['config'],'version':1}]},
            result={'output':{'cases':[{'id':case['id'],'name':'修复健康检查','config':{**case['config'],'url':'/v2/health'}}],
                              'related_configs':[{'id':environment['id'],'config':{'base_url':'https://api.example.test/v2','headers':{},'variables':{}}}]}})
        Entity.objects.filter(pk=environment['id']).update(version=2)

        response=self.client.post(f'/api/jobs/{job.pk}/apply/',{},format='json')

        self.assertEqual(response.status_code,400,response.data)
        self.assertEqual(Entity.objects.get(pk=case['id']).config['url'],'/health')

    def test_api_ai_optimization_cannot_apply_simulated_configuration(self):
        case=self.entity('api',name='健康检查',config={'method':'GET','url':'/health','headers':{},'params':{},'body':{},'assertions':[{'type':'status','expected':200}]})

        response=self.client.post('/api/jobs/',{'project':self.project.pk,'kind':'optimize','name':'AI 编辑接口配置','mode':'simulation',
            'payload':{'entity_id':case['id'],'target_kind':'api','auto_apply':True,'text':'修复配置'}},format='json')

        self.assertEqual(response.status_code,400,response.data)
        self.assertIn('真实模型',str(response.data))

    def test_ui_case_export_can_download_filtered_excel_workbook(self):
        web_case=self.entity('ui_case',name='=网页登录',config={
            'platform':'web','base_url':'https://example.test','preconditions':'访问登录页',
            'steps':[{'action':'goto','value':'/login'},{'action':'assert_text','selector':'h1','value':'登录'}],
            'expected':'显示登录标题'})
        app_case=self.entity('ui_case',name='APP登录',config={
            'platform':'app','automation_status':'needs_mapping','design_steps':[{'action':'点击登录','expected':'进入登录页'}],'steps':[]})

        response=self.client.get('/api/entities/export/',{
            'project':self.project.pk,'kind':'ui_case','platform':'web','export_format':'xlsx'})

        self.assertEqual(response.status_code,200,response.content[:300])
        self.assertEqual(response['Content-Type'],'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('test-assets.xlsx',response['Content-Disposition'])
        with ZipFile(BytesIO(response.content)) as workbook:
            self.assertIn('xl/worksheets/sheet1.xml',workbook.namelist())
            ElementTree.fromstring(workbook.read('xl/workbook.xml'))
            ElementTree.fromstring(workbook.read('xl/worksheets/sheet1.xml'))
            sheet=workbook.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn('=网页登录',sheet)
            self.assertIn('goto',sheet)
            self.assertNotIn('<f>',sheet)
            self.assertNotIn('APP登录',sheet)
            self.assertIn(str(web_case['id']),sheet)

    def test_entity_scope_and_viewer_write(self):
        e=self.entity();self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.patch(f'/api/entities/{e["id"]}/',{'name':'bad'},format='json').status_code,403)
        self.client.force_authenticate(self.other_user)
        self.assertEqual(self.client.get(f'/api/entities/{e["id"]}/').status_code,404)
    def test_machine_connection_records_real_probe_and_enforces_scope(self):
        machine=self.entity('machine',config={'executor':'local-android-adb','serial':''})
        url=f'/api/entities/{machine["id"]}/test_connection/'
        with patch('workbench.machine_connection.probe_machine',return_value={
            'connected':True,'model':'Pixel_8','device_id':'ABC123','message':'ADB 设备已连接'}) as probe:
            response=self.client.post(url)
        self.assertEqual(response.status_code,200,response.data)
        probe.assert_called_once()
        self.assertEqual(response.data['model'],'Pixel_8')
        self.assertTrue(MachineConnection.objects.get(machine_id=machine['id']).connected)
        self.assertEqual(self.client.get(f'/api/entities/{machine["id"]}/connection_status/').data['device_id'],'ABC123')
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post(url).status_code,403)
        self.client.force_authenticate(self.other_user)
        self.assertEqual(self.client.get(f'/api/entities/{machine["id"]}/connection_status/').status_code,404)
    def test_live_mobile_ui_saves_step_evidence(self):
        mcp=Resource.objects.create(project=self.project,kind='mcp',name='Mobile MCP',endpoint='http://localhost:8933/mcp',enabled=True,mode='live')
        case=self.entity('ui_case',name='首页冒烟',config={
            'executor':'mobile-mcp','package_name':'com.example.app','device_id':'device123',
            'mcp_resource_id':mcp.pk,'steps':[{'action':'launch_app'},{'action':'assert_text','value':'首页'}]})
        def fake_mcp(_resource,_method,params,**kwargs):
            name=params['name']
            if name=='mobile_take_screenshot':
                import base64
                return {'content':[{'type':'image','mimeType':'image/png','data':base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()}]}
            if name=='mobile_list_elements_on_screen':
                return {'content':[{'type':'text','text':'@e1 LinearLayout id="com.example.app:id/action_bar_root"\n@e2 TextView text="首页" id="com.example.app:id/title"'}]}
            return {'content':[{'type':'text','text':'Launched app'}]}
        with patch('workbench.executors.mcp_request',side_effect=fake_mcp):
            job=self.execute(self.job('ui',{'entity_ids':[case['id']]},mode='live'))
        self.assertEqual(job.status,'completed',job.result)
        self.assertEqual(job.result['summary']['passed'],1)
        self.assertEqual(len(job.result['cases'][0]['steps']),2)
        self.assertEqual(Artifact.objects.filter(job=job,category='screenshot').count(),2)
        report=Run.objects.get(pk=job.result['run_report_id'])
        self.assertEqual(report.status,'completed')
        self.assertEqual(report.metrics['passed'],1)
        self.assertEqual(len(report.results[0]['steps']),2)
        self.assertEqual(Attachment.objects.filter(run=report,case_id=case['id']).count(),2)
    def test_mobile_ui_executes_supported_interactions_and_records_each_step(self):
        mcp=Resource.objects.create(project=self.project,kind='mcp',name='Mobile MCP',endpoint='http://localhost:8933/mcp',enabled=True,mode='live')
        shared=self.entity('ui_data',name='共享 App 数据',config={'variables':{'shared_input':'hello'}})
        config={'executor':'mobile-mcp','package_name':'com.example.app','device_id':'device123','mcp_resource_id':mcp.pk,
                'data_ids':[shared['id']],
                'steps':[{'action':'launch_app'},{'action':'tap','x':100,'y':200},{'action':'tap','ref':'@e3'},{'action':'tap_text','value':'Continue'},
                         {'action':'type_text','value':'{{shared_input}}'},{'action':'press_button','value':'ENTER'},{'action':'swipe','value':'up'},
                         {'action':'wait_seconds','value':0},{'action':'screenshot'}]}
        case=self.entity('ui_case',name='交互流程',config=config)
        calls=[]
        def fake_mcp(_resource,_method,params,**kwargs):
            name=params['name'];calls.append((name,params['arguments']))
            if name=='mobile_take_screenshot':
                import base64
                return {'content':[{'type':'image','mimeType':'image/png','data':base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()}]}
            if name=='mobile_list_elements_on_screen':
                return {'content':[{'type':'text','text':'@e3 Button text="Continue"'}]}
            return {'content':[{'type':'text','text':'ok'}]}
        from pathlib import Path
        with patch('workbench.executors.mcp_request',side_effect=fake_mcp):
            job=self.execute(self.job('ui',{'entity_ids':[case['id']]},mode='live'))
        self.assertEqual(job.status,'completed',job.result)
        self.assertEqual(job.result['cases'][0]['status'],'passed')
        self.assertEqual([step['status'] for step in job.result['cases'][0]['steps']],['passed']*9)
        self.assertEqual([call[0] for call in calls if call[0]!='mobile_take_screenshot'],[
            'mobile_launch_app','mobile_click_on_screen_at_coordinates','mobile_list_elements_on_screen','mobile_click_on_screen_at_coordinates',
            'mobile_list_elements_on_screen','mobile_click_on_screen_at_coordinates','mobile_type_keys','mobile_press_button','mobile_swipe_on_screen'])
        interactions=[call for call in calls if call[0]!='mobile_take_screenshot']
        self.assertEqual(interactions[1][1],{'device':'device123','x':100,'y':200})
        self.assertEqual(interactions[3][1],{'device':'device123','ref':'@e3'})
        self.assertEqual(interactions[5][1],{'device':'device123','ref':'@e3'})
        self.assertEqual(interactions[6][1]['text'],'hello')
    def test_mobile_ui_rejects_unknown_or_incomplete_actions(self):
        base={'executor':'mobile-mcp','package_name':'com.example.app','device_id':'device123','mcp_resource_id':1}
        for step in [{'action':'invented_action'},{'action':'tap'},{'action':'swipe','value':'diagonal'}]:
            with self.subTest(step=step),self.assertRaises(ValueError):
                validate_config('ui_case',{**base,'steps':[step]})
        validate_config('ui_case',{**base,'steps':[{'action':'launch_app'},{'action':'tap_field','locator':{'resource_id':'com.example.app:id/login'}}]})
        with self.assertRaises(ValueError):
            validate_config('ui_case',{**base,'steps':[{'action':'tap_field','locator':{'text':''}}]})
    def test_mobile_ui_relocates_field_locator_and_clicks_current_ref(self):
        mcp=Resource.objects.create(project=self.project,kind='mcp',name='Mobile field locator',endpoint='http://localhost:8933/mcp',enabled=True,mode='live')
        case=self.entity('ui_case',name='字段定位流程',config={
            'executor':'mobile-mcp','package_name':'com.example.app','device_id':'device123','mcp_resource_id':mcp.pk,
            'steps':[{'action':'launch_app'},{'action':'tap_field','locator':{'resource_id':'com.example.app:id/login'}}]})
        calls=[]
        def fake_mcp(_resource,_method,params,**kwargs):
            name=params['name'];calls.append((name,params['arguments']))
            if name=='mobile_take_screenshot':
                import base64
                return {'content':[{'type':'image','mimeType':'image/png','data':base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()}]}
            if name=='mobile_list_elements_on_screen':
                return {'content':[{'type':'text','text':'@e7 Button text="登录" id="com.example.app:id/login"'}]}
            return {'content':[{'type':'text','text':'ok'}]}
        with patch('workbench.executors.mcp_request',side_effect=fake_mcp):
            job=self.execute(self.job('ui',{'entity_ids':[case['id']]},mode='live'))
        self.assertEqual(job.status,'completed',job.result)
        click=next(arguments for name,arguments in calls if name=='mobile_click_on_screen_at_coordinates')
        self.assertEqual(click,{'device':'device123','ref':'@e7'})
        self.assertFalse({'x','y'} & set(click))
    def test_mobile_ui_compiler_preserves_unverified_status_and_creates_interactions(self):
        from .ui_steps import compile_app_design_steps
        steps,unmapped=compile_app_design_steps([{'action':'1. 点击“手机号登录/注册”\n2. 输入手机号“{{ valid_phone_number }}”\n3. 点击获取验证码','expected':'检查是否显示“验证码已发送”'}])
        self.assertEqual([step['action'] for step in steps],['launch_app','tap_text','type_text','tap_text','assert_text'])
        self.assertEqual(steps[2]['value'],'{{valid_phone_number}}')
        self.assertEqual(steps[-1]['value'],'验证码已发送')
        self.assertEqual(unmapped,[])
    def test_app_video_design_compiler_requires_fields_and_preserves_safe_actions(self):
        from .ui_steps import compile_app_video_design_steps
        steps,unmapped=compile_app_video_design_steps([
            {'action':'点击登录','recorded_action':'tap_field','locator':{'text':'登录'}},
            {'action':'输入凭据','recorded_action':'type_text','value':'{{recorded_input_1}}'},
        ])
        self.assertEqual(steps,[{'action':'launch_app'},{'action':'tap_field','locator':{'text':'登录'}},
                                {'action':'type_text','value':'{{recorded_input_1}}'}])
        self.assertEqual(unmapped,[])
        steps,unmapped=compile_app_video_design_steps([{'action':'点击图标','recorded_action':'tap_field','locator':{'text':''}}])
        self.assertEqual(steps,[{'action':'launch_app'}])
        self.assertTrue(unmapped)
    def test_web_design_compiler_maps_common_playwright_actions(self):
        from .ui_steps import compile_web_design_steps
        steps,notes=compile_web_design_steps([{'action':'1. 进入{{ 程序入口 }}\n2. 在“手机号输入框”中输入有效手机号“{{ valid_phone }}”\n3. 勾选“用户协议”复选框\n4. 点击“获取验证码”按钮','expected':'展示“请输入验证码”'}])
        self.assertEqual([step['action'] for step in steps],['goto','fill','check','click','assert_text'])
        self.assertEqual(steps[1]['selector'],'input[placeholder*="手机号"]:visible')
        self.assertEqual(steps[1]['value'],'{{valid_phone}}')
        self.assertEqual(notes,[])
        validate_config('ui_case',{'platform':'web','base_url':'https://example.test','steps':steps})
        validate_config('ui_case',{'platform':'web','base_url':'https://example.test','steps':[{'action':'hover','selector':'text=帮助'}]})
        validate_config('ui_case',{'platform':'web','base_url':'https://example.test','steps':[{'action':'wait_seconds','value':5}]})
        with self.assertRaises(ValueError):
            validate_config('ui_case',{'platform':'web','base_url':'https://example.test','steps':[{'action':'wait_seconds','value':61}]})
    def test_app_profile_applies_to_selected_cases_and_compiles_as_unverified(self):
        mcp=Resource.objects.create(project=self.project,kind='mcp',name='Mobile MCP',endpoint='http://localhost:8933/mcp',enabled=True,mode='live')
        first=self.entity('ui_case',name='App 登录',config={'platform':'app','automation_status':'needs_mapping','executor':'mobile-mcp',
            'design_steps':[{'action':'1. 点击“手机号登录/注册”\n2. 输入手机号“{{ valid_phone_number }}”','expected':'显示“验证码”'}],'steps':[]})
        second=self.entity('ui_case',name='另一个 App 流程',config={'platform':'app','automation_status':'needs_mapping',
            'design_steps':[{'action':'点击“继续”','expected':'显示“首页”'}],'steps':[]})
        profile=self.entity('ui_data',name='金姆脉知 Android 配置',config={'data_type':'app_profile','app_profile':{
            'app_name':'金姆脉知','package_name':'com.jinmu.pulseoasis','device_id':'6491adcf','mcp_resource_id':mcp.pk},
            'variables':{'valid_phone_number':'13800000000'}})
        response=self.client.post(f'/api/entities/{profile["id"]}/apply_ui_profile/',{'case_ids':[first['id']]},format='json')
        self.assertEqual(response.status_code,200,response.data)
        one=Entity.objects.get(pk=first['id']);two=Entity.objects.get(pk=second['id'])
        self.assertEqual(one.config['app_profile_id'],profile['id']);self.assertEqual(two.config.get('app_profile_id'),None)
        self.assertEqual(one.config['device_id'],'6491adcf')
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post(f'/api/entities/{first["id"]}/make_ui_executable/',{},format='json').status_code,403)
        self.client.force_authenticate(self.owner)
        converted=self.client.post(f'/api/entities/{first["id"]}/make_ui_executable/',{},format='json')
        self.assertEqual(converted.status_code,200,converted.data)
        one.refresh_from_db();self.assertEqual(one.config['automation_status'],'ready')
        self.assertEqual(one.config['verification_status'],'unverified')
        self.assertEqual(one.config['steps'][1],{'action':'tap_text','value':'手机号登录/注册'})
        self.assertEqual(one.config['steps'][2],{'action':'type_text','value':'{{valid_phone_number}}'})
    def test_web_profile_applies_only_selected_cases_and_compiles(self):
        first=self.entity('ui_case',name='Web 登录草稿',config={'platform':'web','automation_status':'needs_mapping','base_url':'',
            'design_steps':[{'action':'1. 进入{{ 程序入口 }}\n2. 在“手机号输入框”输入“{{ valid_phone }}”\n3. 点击“登录”按钮','expected':'显示“手机号登录”'}],'steps':[]})
        second=self.entity('ui_case',name='另一个 Web 草稿',config={'platform':'web','automation_status':'needs_mapping','base_url':'',
            'design_steps':[{'action':'点击“继续”','expected':'显示“下一页”'}],'steps':[]})
        profile=self.entity('ui_data',name='Web 登录环境',config={'data_type':'web_profile','web_profile':{'base_url':'https://example.test/login','browser':'chromium'},'variables':{'valid_phone':'13800000000'}})
        response=self.client.post(f'/api/entities/{profile["id"]}/apply_web_profile/',{'case_ids':[first['id']]},format='json')
        self.assertEqual(response.status_code,200,response.data)
        one=Entity.objects.get(pk=first['id']);two=Entity.objects.get(pk=second['id'])
        self.assertEqual(one.config['web_profile_id'],profile['id']);self.assertNotIn('web_profile_id',two.config)
        self.assertEqual(one.config['base_url'],'https://example.test/login')
        converted=self.client.post(f'/api/entities/{first["id"]}/make_ui_executable/',{},format='json')
        self.assertEqual(converted.status_code,200,converted.data)
        one.refresh_from_db();self.assertEqual(one.config['automation_status'],'ready')
        self.assertEqual(one.config['verification_status'],'unverified')
        self.assertEqual(one.config['steps'][1],{'action':'fill','selector':'input[placeholder*="手机号"]:visible','value':'{{valid_phone}}'})
    def test_ui_flow_runs_ordered_web_cases_as_background_job(self):
        first=self.entity('ui_case',name='登录页',config={'platform':'web','base_url':'https://example.test/login','steps':[{'action':'goto','value':''}]})
        second=self.entity('ui_case',name='忘记密码',config={'platform':'web','base_url':'https://example.test/login','steps':[{'action':'click','selector':'text=忘记密码'}]})
        flow=self.entity('ui_flow',name='登录与找回密码流程',config={'platform':'web','status':'ready','case_ids':[second['id'],first['id']]})
        self.assertEqual(self.client.delete(f'/api/entities/{second["id"]}/').status_code,400)
        response=self.client.post(f'/api/entities/{flow["id"]}/run_ui_flow/',{'mode':'simulation','preparation_data':{'valid_phone':'13800000000'}},format='json')
        self.assertEqual(response.status_code,202,response.data)
        job=Job.objects.get(pk=response.data['id'])
        self.assertEqual(job.kind,'ui');self.assertEqual(job.mode,'simulation')
        self.assertEqual([item['id'] for item in job.payload['snapshots']],[second['id'],first['id']])
        self.assertEqual(job.payload['preparation_data'],{'valid_phone':'13800000000'})
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post(f'/api/entities/{flow["id"]}/run_ui_flow/',{},format='json').status_code,403)
    def test_ui_job_rejects_removed_execution_model_parameter(self):
        case=self.entity('ui_case',name='网页登录',config={'platform':'web','base_url':'https://example.test/login','steps':[{'action':'click','selector':'button','element_label':'登录'}]})
        rejected=self.client.post('/api/jobs/',{'project':self.project.pk,'kind':'ui','mode':'live',
            'payload':{'entity_ids':[case['id']],'execution_model_id':999}},format='json')
        self.assertEqual(rejected.status_code,400)
    def test_completed_ui_job_can_generate_report_once(self):
        case=self.entity('ui_case',name='登录页',config={'base_url':'https://example.test','steps':[{'action':'assert_text','value':'欢迎'}]})
        job=Job.objects.create(project=self.project,kind='ui',name='UI 登录执行',status='completed',mode='live',
            payload={'snapshots':[{'id':case['id'],'name':'登录页','config':{'base_url':'https://example.test','steps':[{'action':'assert_text','value':'欢迎'}]}}]},
            result={'cases':[{'id':case['id'],'name':'登录页','status':'passed','steps':[{'index':0,'action':'assert_text','status':'passed','duration_ms':12}]}],
                    'summary':{'total':1,'passed':1,'failed':0,'skipped':0}},created_by=self.owner,finished_at=timezone.now())
        first=self.client.post(f'/api/jobs/{job.pk}/generate_report/',{},format='json')
        second=self.client.post(f'/api/jobs/{job.pk}/generate_report/',{},format='json')
        self.assertEqual(first.status_code,200,first.data)
        self.assertEqual(first.data['run_report_id'],second.data['run_report_id'])
        self.assertEqual(Run.objects.filter(execution_context__source_job_id=job.pk).count(),1)
        self.assertEqual(Run.objects.get(pk=first.data['run_report_id']).metrics['passed'],1)
        video=Artifact.objects.create(project=self.project,job=job,name='case.webm',category='video',
            file=SimpleUploadedFile('case.webm',b'video fixture',content_type='video/webm'))
        job.result['cases'][0]['video']=video.pk;job.save(update_fields=['result'])
        self.client.post(f'/api/jobs/{job.pk}/generate_report/',{},format='json')
        attachment=Attachment.objects.get(run_id=first.data['run_report_id'],name='case.webm')
        self.assertTrue(self.client.get(f'/api/attachments/{attachment.pk}/download/?inline=1')['Content-Disposition'].startswith('inline;'))
    def test_artifact_preview_is_inline_and_remains_project_scoped(self):
        job=self.job()
        artifact=Artifact.objects.create(project=self.project,job=job,name='step.png',category='screenshot',
            file=SimpleUploadedFile('step.png',b'png fixture',content_type='image/png'))
        url=f'/api/artifacts/{artifact.pk}/download/'
        download=self.client.get(url)
        self.assertEqual(download.status_code,200)
        self.assertTrue(download['Content-Disposition'].startswith('attachment;'))
        preview=self.client.get(url+'?inline=1')
        self.assertEqual(preview.status_code,200)
        self.assertTrue(preview['Content-Disposition'].startswith('inline;'))
        self.assertEqual(preview['X-Content-Type-Options'],'nosniff')
        self.client.force_authenticate(self.other_user)
        self.assertEqual(self.client.get(url+'?inline=1').status_code,404)
    def test_ui_platform_filter_and_design_apply_to_app(self):
        app=self.entity('ui_case',name='App 草稿',config={'platform':'app','automation_status':'needs_mapping',
            'design_steps':[{'action':'打开应用','expected':'显示登录页'}],'steps':[]})
        web=self.entity('ui_case',name='Web 登录',config={'platform':'web','base_url':'http://localhost:5173',
            'steps':[{'action':'goto','value':'/'}]})
        app_rows=self.client.get(f'/api/entities/?project={self.project.pk}&kind=ui_case&platform=app').data['results']
        web_rows=self.client.get(f'/api/entities/?project={self.project.pk}&kind=ui_case&platform=web').data['results']
        self.assertEqual([row['id'] for row in app_rows],[app['id']])
        self.assertEqual([row['id'] for row in web_rows],[web['id']])
        source=self.entity('requirement')
        response=self.client.post('/api/jobs/',{'project':self.project.pk,'kind':'design','mode':'live',
            'payload':{'entity_id':source['id'],'focus_module':'登录','target_kind':'ui_case','ui_platform':'app',
                       'model_id':Resource.objects.create(project=self.project,kind='model',name='Mock',endpoint='https://example.test/v1').pk}},format='json')
        self.assertEqual(response.status_code,202,response.data)
        job=Job.objects.get(pk=response.data['id'])
        job.status='completed';job.result={'output':{'cases':[{'name':'登录引导','steps':[{'action':'打开应用','expected':'显示登录入口'}]}]}}
        job.save(update_fields=['status','result'])
        from .intelligence import apply_result
        created=apply_result(job)
        generated=Entity.objects.get(pk=created[0])
        self.assertEqual(generated.config['platform'],'app')
        self.assertEqual(generated.config['automation_status'],'needs_mapping')
        self.assertEqual(generated.config['design_steps'][0]['expected'],'显示登录入口')
    def test_ui_cases_are_ordered_by_latest_update(self):
        older=self.entity('ui_case',name='较早更新',position=0,config={'platform':'app','automation_status':'needs_mapping',
            'design_steps':[{'action':'打开应用','expected':'显示首页'}],'steps':[]})
        newer=self.entity('ui_case',name='最近更新',position=100,config={'platform':'app','automation_status':'needs_mapping',
            'design_steps':[{'action':'打开应用','expected':'显示首页'}],'steps':[]})
        Entity.objects.filter(pk=older['id']).update(updated_at=timezone.now()-timedelta(days=1))
        response=self.client.get(f'/api/entities/?project={self.project.pk}&kind=ui_case&platform=app')
        self.assertEqual(response.status_code,200)
        self.assertEqual([row['id'] for row in response.data['results']],[newer['id'],older['id']])
    def test_cycle_rejected(self):
        a=self.entity('module');b=self.entity('module',parent=a['id'])
        self.assertEqual(self.client.patch(f'/api/entities/{a["id"]}/',{'parent':b['id']},format='json').status_code,400)
    def test_graph_generates_applies_once(self):
        job=self.execute(self.job(payload={'text':'# 账号体系\n### 用户认证\n#### 注册\n用户使用手机号注册。\n#### 登录\n用户使用密码登录。','auto_apply':True,'count':5}))
        self.assertEqual(job.status,'completed',job.result);self.assertTrue(job.applied)
        self.assertEqual(Asset.objects.filter(project=self.project).count(),5)
        self.assertEqual(job.result['engine'],'LangGraph');self.assertEqual(job.usage['total_tokens'],0)
        self.assertEqual([x['stage'] for x in job.logs],['start','skills','context','features','draft','critique','refine','validate','complete'])
        self.assertEqual([x['name'] for x in job.result['output']['features']],['注册','登录'])
        self.assertTrue(all(case.get('feature') for case in job.result['output']['cases']))
        self.assertEqual({a.data['feature'] for a in Asset.objects.filter(project=self.project)},{'注册','登录'})
        self.assertEqual(self.client.post(f'/api/jobs/{job.pk}/apply/').status_code,400)
    def test_design_workflow_keeps_source_and_never_applies_simulation(self):
        from .design import outline_markdown, select_module
        source='# 产品\n### 账号\n#### 注册/登录\n验证码 10 分钟有效，仅可使用一次。\n### 短信目录\n登录短信含验证码。'
        modules=outline_markdown(source)
        self.assertEqual(select_module(modules,'账号模块')['name'],'账号')
        self.assertIn('10 分钟',modules[0]['functions'][0]['source_text'])
        self.assertEqual(modules[1]['functions'][0]['name'],'模块通用规则')
        e=self.entity(content=source)
        job=self.job('design',{'entity_id':e['id'],'focus_module':'账号','count':40,'auto_apply':True})
        self.assertFalse(job.payload['auto_apply'])
        job=self.execute(job)
        self.assertEqual(job.status,'completed',job.result)
        self.assertEqual(job.result['output']['cases'],[])
        self.assertEqual(job.result['output']['focus_module'],'账号')
        self.assertEqual(Asset.objects.filter(project=self.project).count(),0)
        self.assertEqual(self.client.post(f'/api/jobs/{job.pk}/apply/').status_code,400)
        exported=self.client.get(f'/api/jobs/{job.pk}/export/?download=markdown')
        self.assertEqual(exported.status_code,200)
        self.assertIn('验证码 10 分钟有效',exported.content.decode())
    def test_design_requires_focus_and_same_project_requirement(self):
        e=self.entity()
        for payload in [{'entity_id':e['id']},{'focus_module':'登录'},{'entity_id':e['id'],'focus_module':'登录','count':0}]:
            self.assertEqual(self.client.post('/api/jobs/',{'project':self.project.pk,'kind':'design','payload':payload},format='json').status_code,400)
        other=Entity.objects.create(project=self.other,kind='requirement',name='其他项目',created_by=self.other_user)
        self.assertEqual(self.client.post('/api/jobs/',{'project':self.project.pk,'kind':'design','payload':{'entity_id':other.pk,'focus_module':'登录'}},format='json').status_code,400)
    def test_design_live_generates_traceable_single_step_cases(self):
        source='# 产品\n### 注册/登录\n#### 验证码登录\n验证码 10 分钟有效，仅可使用一次。\n### 支付\n#### 订单付款\n订单需在 15 分钟内付款。'
        e=self.entity(content=source)
        model=Resource.objects.create(project=self.project,kind='model',name='Mock model',endpoint='https://example.test/v1',settings={'model_name':'mock'})
        sequence={'case':0}
        rule_prompts=[]
        def fake_invoke(resource,system,prompt,usage):
            usage['calls']+=1
            if prompt.startswith('逐一从需求原文提取'):
                rule_prompts.append(prompt)
                return {'functions':[{'id':'M1-F1','preconditions':['已获取验证码'],'flow':['输入验证码'],'validation':['10 分钟有效且仅可使用一次'],'errors':[],'special':[],'open_questions':[]}]}
            sequence['case']+=1
            number=sequence['case']
            return {'cases':[{'name':f'验证码规则 {number}','scenario':f'单点场景 {number}',
                              'preconditions':'已获取验证码','steps':['输入验证码并提交'],
                              'expected':f'显示可验证结果 {number}','type':'功能',
                              'requirement_refs':['M1-F1']}]}
        with patch('workbench.design._invoke',side_effect=fake_invoke):
            job=self.execute(self.job('design',{'entity_id':e['id'],'focus_module':'登录','model_id':model.pk,'count':6,'auto_apply':True},mode='live'))
        self.assertEqual(job.status,'completed',job.result)
        self.assertEqual(len(rule_prompts),1)
        self.assertNotIn('订单付款',rule_prompts[0])
        self.assertTrue(job.applied)
        self.assertEqual(len(job.result['output']['cases']),6)
        self.assertEqual(Asset.objects.filter(project=self.project,kind='case').count(),6)
        self.assertTrue(all(len(asset.data['steps'])==1 and asset.data['requirement_refs']==['M1-F1'] for asset in Asset.objects.filter(project=self.project,kind='case')))
        self.assertEqual(Entity.objects.filter(project=self.project,kind='module',name='注册/登录').count(),1)
        self.assertTrue(all(asset.data['module_id'] and asset.data['source_requirement_id']==e['id'] for asset in Asset.objects.filter(project=self.project,kind='case')))
    def test_requirement_module_sync_is_idempotent_and_scoped(self):
        source='# 产品\n### 4.1 登录\n#### 验证码登录\n10 分钟有效。\n### 4.2 会员\n#### 支付\n15 分钟付款。'
        e=self.entity(content=source)
        first=self.client.post(f'/api/entities/{e["id"]}/sync_modules/')
        self.assertEqual(first.status_code,200,first.data)
        self.assertEqual(first.data['count'],2)
        self.assertEqual(self.client.post(f'/api/entities/{e["id"]}/sync_modules/').data['modules'],first.data['modules'])
        self.assertEqual(Entity.objects.filter(project=self.project,kind='module').count(),2)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post(f'/api/entities/{e["id"]}/sync_modules/').status_code,403)
    def test_import_requirement_creates_entity_attachment_and_background_job(self):
        uploaded=SimpleUploadedFile('登录需求.md','# 登录需求\n手机号验证码登录。'.encode(),content_type='text/markdown')
        response=self.client.post('/api/entities/import_requirement/',{'project':self.project.pk,'file':uploaded},format='multipart')
        self.assertEqual(response.status_code,202,response.data)
        entity=Entity.objects.get(pk=response.data['entity']['id'])
        job=Job.objects.get(pk=response.data['job'])
        self.assertEqual(entity.kind,'requirement');self.assertEqual(entity.name,'登录需求')
        self.assertEqual(entity.config['status'],'parsing')
        self.assertEqual(job.kind,'ingest');self.assertEqual(job.payload['entity_id'],entity.pk)
        self.assertEqual(Artifact.objects.get(pk=response.data['artifact']['id']).name,'登录需求.md')
        job=self.execute(job);entity.refresh_from_db()
        self.assertEqual(job.status,'completed',job.result)
        self.assertIn('手机号验证码登录',entity.content)
        self.assertEqual(entity.config['status'],'ready')
        invalid=SimpleUploadedFile('需求.json',b'{}',content_type='application/json')
        self.assertEqual(self.client.post('/api/entities/import_requirement/',{'project':self.project.pk,'file':invalid},format='multipart').status_code,400)
        self.client.force_authenticate(self.viewer)
        denied=SimpleUploadedFile('只读.md',b'# denied',content_type='text/markdown')
        self.assertEqual(self.client.post('/api/entities/import_requirement/',{'project':self.project.pk,'file':denied},format='multipart').status_code,403)
    def test_design_all_modules_writes_cases_to_matching_modules(self):
        source='# 产品\n### 4.1 登录\n#### 密码登录\n正确密码可登录。\n### 4.2 会员\n#### 查看权益\n会员可查看权益。'
        e=self.entity(content=source)
        model=Resource.objects.create(project=self.project,kind='model',name='Mock',endpoint='https://example.test/v1',settings={'model_name':'mock'})
        sequence={'case':0}
        def fake_invoke(resource,system,prompt,usage):
            usage['calls']+=1
            if prompt.startswith('逐一从需求原文提取'):
                ids=re.findall(r'"id": "(M\d+-F\d+)"',prompt)
                return {'functions':[{'id':id,'preconditions':[],'flow':[],'validation':[],'errors':[],'special':[],'open_questions':[]} for id in ids]}
            module='登录' if '仅针对「登录」' in prompt else '会员'
            sequence['case']+=1
            return {'cases':[{'name':module+f'单点用例 {sequence["case"]}','scenario':module+f'单点场景 {sequence["case"]}','preconditions':'已进入页面','steps':['点击确认'],'expected':'页面显示成功','type':'功能','requirement_refs':['M1-F1' if module=='登录' else 'M2-F1']}]}
        with patch('workbench.design._invoke',side_effect=fake_invoke):
            job=self.execute(self.job('design',{'entity_id':e['id'],'focus_module':'全部模块','model_id':model.pk,'count':26,'auto_apply':True},mode='live'))
        self.assertEqual(job.status,'completed',job.result)
        self.assertTrue(job.applied)
        self.assertEqual(job.result['output']['coverage']['function_nodes'],2)
        assets=Asset.objects.filter(project=self.project,kind='case')
        self.assertEqual(assets.count(),52)
        self.assertEqual({a.data['feature'] for a in assets},{'登录','会员'})
        for asset in assets:
            module=Entity.objects.get(pk=asset.data['module_id'])
            self.assertEqual(module.name,asset.data['feature'])
            self.assertEqual(module.config['source_requirement_id'],e['id'])
    def test_design_without_count_covers_each_requirement_node(self):
        e=self.entity(content='# 产品\n### 登录\n#### 密码校验\n正确密码可登录。\n#### 错误提示\n密码错误时提示重试。')
        model=Resource.objects.create(project=self.project,kind='model',name='Mock',endpoint='https://example.test/v1',settings={'model_name':'mock'})
        def fake_invoke(resource,system,prompt,usage):
            usage['calls']+=1
            if prompt.startswith('逐一从需求原文提取'):
                ids=re.findall(r'"id": "(M\d+-F\d+)"',prompt)
                return {'functions':[{'id':id,'preconditions':[],'flow':[],'validation':[],'errors':[],'special':[],'open_questions':[]} for id in ids]}
            node_id=re.search(r'\[(M\d+-F\d+)\]',prompt).group(1)
            return {'cases':[{'name':node_id+' 用例','scenario':node_id+' 场景','preconditions':'已进入登录页','steps':['提交密码'],'expected':'显示对应状态','type':'功能','requirement_refs':[node_id]}], 'not_testable_reason':''}
        with patch('workbench.design._invoke',side_effect=fake_invoke):
            job=self.execute(self.job('design',{'entity_id':e['id'],'focus_module':'登录','model_id':model.pk,'auto_apply':True},mode='live'))
        self.assertEqual(job.status,'completed',job.result)
        self.assertTrue(job.applied)
        self.assertEqual({c['requirement_refs'][0] for c in job.result['output']['cases']},{'M1-F1','M1-F2'})
        self.assertEqual(Asset.objects.filter(project=self.project,kind='case').count(),2)
    def test_qwen_json_generation_disables_default_thinking(self):
        from .model_options import chat_extra_body
        qwen=Resource.objects.create(project=self.project,kind='model',name='Qwen',endpoint='https://dashscope.aliyuncs.com/compatible-mode/v1',settings={'model_name':'qwen3.8-flash'})
        self.assertEqual(chat_extra_body(qwen),{'enable_thinking':False})
        qwen.settings={'model_name':'qwen3-235b-a22b-thinking-2507'}
        self.assertIsNone(chat_extra_body(qwen))
        qwen.endpoint='https://example.test/v1';qwen.settings={'model_name':'qwen3.8-flash'}
        self.assertIsNone(chat_extra_body(qwen))
    def test_design_accepts_json_array_from_compatible_model(self):
        from .design import _result_object, _validate_case, _expand_case_steps
        self.assertEqual(_result_object([{'name':'用例'}],'cases'),{'cases':[{'name':'用例'}]})
        with self.assertRaises(ValueError):_result_object('invalid','cases')
        case=_validate_case({'name':'密码错误提示','steps':['提交错误密码'],'expected':'显示密码错误提示'},'登录',1)
        self.assertEqual(case['scenario'],'密码错误提示')
        self.assertEqual(case['preconditions'],'无')
        expanded=_expand_case_steps({'name':'找回密码','steps':[{'action':'输入手机号','expected':'发送验证码'},
            {'action':'提交验证码','expected':'进入重设密码页'}],'expected':'进入重设密码页'})
        self.assertEqual(len(expanded),2)
        self.assertEqual(expanded[1]['steps'],['提交验证码'])
        self.assertIn('输入手机号',expanded[1]['preconditions'])
    def test_design_moves_setup_steps_to_preconditions_before_apply(self):
        e=self.entity(content='# 产品\n### 登录\n#### 密码登录\n正确密码可登录。')
        model=Resource.objects.create(project=self.project,kind='model',name='Mock',endpoint='https://example.test/v1',settings={'model_name':'mock'})
        calls=[]
        def fake_invoke(resource,system,prompt,usage):
            calls.append(prompt)
            if prompt.startswith('逐一从需求原文提取'):
                return {'functions':[{'id':'M1-F1','preconditions':[],'flow':[],'validation':[],'errors':[],'special':[],'open_questions':[]}]}
            return {'cases':[{'name':'正确密码登录','steps':['输入账号','输入密码','提交'],'expected':'进入首页'}]}
        with patch('workbench.design._invoke',side_effect=fake_invoke):
            job=self.execute(self.job('design',{'entity_id':e['id'],'focus_module':'登录','model_id':model.pk,'auto_apply':True},mode='live'))
        self.assertEqual(job.status,'completed',job.result)
        self.assertFalse(any(prompt.startswith('修复') for prompt in calls))
        asset=Asset.objects.filter(project=self.project,kind='case').first()
        self.assertEqual(asset.data['steps'][0]['action'],'提交')
        self.assertIn('输入账号；输入密码',asset.data['preconditions'])
    def test_design_uses_sms_rules_as_context_without_generating_sms_cases(self):
        e=self.entity(content='# 产品\n### 登录\n#### 验证码登录\n输入验证码后登录。\n### 短信\n#### 验证码\n验证码 10 分钟有效。')
        model=Resource.objects.create(project=self.project,kind='model',name='Mock',endpoint='https://example.test/v1',settings={'model_name':'mock'})
        case_prompts=[]
        def fake_invoke(resource,system,prompt,usage):
            if prompt.startswith('逐一从需求原文提取'):
                ids=re.findall(r'"id": "(M\d+-F\d+)"',prompt)
                return {'functions':[{'id':id,'preconditions':[],'flow':[],'validation':[],'errors':[],'special':[],'open_questions':[]} for id in ids]}
            case_prompts.append(prompt)
            return {'cases':[{'name':'验证码登录','steps':['提交验证码'],'expected':'登录成功'}]}
        with patch('workbench.design._invoke',side_effect=fake_invoke):
            job=self.execute(self.job('design',{'entity_id':e['id'],'focus_module':'登录','model_id':model.pk,'auto_apply':True},mode='live'))
        self.assertEqual(job.status,'completed',job.result)
        self.assertEqual(len(case_prompts),1)
        self.assertIn('验证码 10 分钟有效',case_prompts[0])
        self.assertEqual(len(job.result['output']['cases']),1)
    def test_design_records_invalid_model_cases_without_losing_valid_cases(self):
        e=self.entity(content='# 产品\n### 登录\n#### 验证码登录\n验证码正确可登录。\n#### 密码登录\n密码正确可登录。')
        model=Resource.objects.create(project=self.project,kind='model',name='Mock',endpoint='https://example.test/v1',settings={'model_name':'mock'})
        def fake_invoke(resource,system,prompt,usage):
            if prompt.startswith('逐一从需求原文提取'):
                ids=re.findall(r'"id": "(M\d+-F\d+)"',prompt)
                return {'functions':[{'id':id,'preconditions':[],'flow':[],'validation':[],'errors':[],'special':[],'open_questions':[]} for id in ids]}
            if 'M1-F1' in prompt:
                return {'cases':[{'name':'不完整用例','steps':[],'expected':'登录成功'}]}
            return {'cases':[{'name':'密码登录','steps':['提交密码'],'expected':'登录成功'}]}
        with patch('workbench.design._invoke',side_effect=fake_invoke):
            job=self.execute(self.job('design',{'entity_id':e['id'],'focus_module':'登录','model_id':model.pk,'auto_apply':True},mode='live'))
        self.assertEqual(job.status,'completed',job.result)
        self.assertTrue(job.applied)
        self.assertEqual(Asset.objects.filter(project=self.project,kind='case').count(),1)
        self.assertTrue(any(gap['node_id']=='M1-F1' for gap in job.result['output']['coverage']['not_testable']))
    def test_design_uses_ui_skill_array_schema_and_preserves_valid_cases(self):
        e=self.entity(content='# 产品\n### 登录\n#### 密码登录\n用户输入账号密码后登录。')
        model=Resource.objects.create(project=self.project,kind='model',name='Mock',endpoint='https://example.test/v1',settings={'model_name':'mock'})
        skill=Resource.objects.create(project=self.project,kind='skill',name='ui-testcase-generation',settings={'instructions':'返回 JSON 数组，步骤使用字符串'})
        def fake_invoke(resource,system,prompt,usage):
            if prompt.startswith('逐一从需求原文提取'):
                return {'functions':[{'id':'M1-F1','preconditions':[],'flow':[],'validation':[],'errors':[],'special':[],'open_questions':[]}]}
            if prompt.startswith('修复'):
                return {'cases':[{'name':'仍无效','steps':[],'expected':'登录成功'}]}
            self.assertIn('ui-testcase-generation Skill',prompt)
            return [{'id':1,'name':'密码登录','priority':2,'precondition':'已注册账号',
                     'steps':'1. 进入{{ 程序入口 }}\n2. 输入“账号”和“密码”并提交','expected':'进入首页'},
                    {'id':2,'name':'不完整用例','steps':[],'expected':'登录成功'}]
        with patch('workbench.design._invoke',side_effect=fake_invoke):
            job=self.execute(self.job('design',{'entity_id':e['id'],'focus_module':'登录','model_id':model.pk,'skill_ids':[skill.pk],'auto_apply':True},mode='live'))
        self.assertEqual(job.status,'completed',job.result)
        self.assertEqual(job.result['output']['generation_standard'],'ui-testcase-generation')
        asset=Asset.objects.get(project=self.project,kind='case')
        self.assertEqual(asset.priority,'P0')
        self.assertEqual(asset.data['preconditions'],'已注册账号')
        self.assertIn('{{ 程序入口 }}',asset.data['steps'][0]['action'])
        self.assertTrue(job.result['output']['coverage']['not_testable'])
    def test_job_automatically_routes_to_project_skill(self):
        skill=Resource.objects.create(project=self.project,kind='skill',name='ui-testcase-generation',settings={'instructions':'先分析功能，再生成 UI 用例'})
        job=self.job(payload={'text':'用户登录','target_kind':'case','auto_apply':False})
        self.assertEqual(job.payload['skill_ids'],[skill.pk])
        self.assertEqual(job.payload['skill_snapshots'][0]['name'],'ui-testcase-generation')
        job=self.execute(job)
        self.assertEqual(job.result['skills_used'],['ui-testcase-generation'])
    def test_review_has_snapshot_and_citations(self):
        kb=self.entity('knowledge');doc=self.entity('knowledge_document',parent=kb['id'])
        KnowledgeChunk.objects.create(document_id=doc['id'],index=0,text='用户登录权限规则',vector=hash_vector('用户登录权限规则'))
        req=self.entity();job=self.job('review',{'entity_id':req['id'],'knowledge_ids':[kb['id']]});Entity.objects.filter(pk=req['id']).update(content='新内容')
        job=self.execute(job);self.assertEqual(job.status,'completed',job.result);self.assertTrue(job.result['citations']);self.assertEqual(job.payload['snapshots'][0]['version'],1)
    def test_parse_document_and_index_background(self):
        kb=self.entity('knowledge');doc=self.entity('knowledge_document',parent=kb['id'])
        res=self.client.post('/api/artifacts/',{'entity':doc['id'],'file':SimpleUploadedFile('rules.txt','登录用户必须有项目权限。'.encode())},format='multipart')
        self.assertEqual(res.status_code,202,res.data)
        job=self.execute(Job.objects.get(pk=res.data['job']));self.assertEqual(job.status,'completed',job.result)
        self.assertEqual(KnowledgeChunk.objects.filter(document_id=doc['id']).count(),1)
        self.assertEqual(Entity.objects.get(pk=doc['id']).version,2)
        result=self.client.post(f'/api/entities/{kb["id"]}/search/',{'query':'登录权限'},format='json');self.assertTrue(result.data)
    def test_excel_case_template_upload_is_parsed_and_selectable_for_generation(self):
        template=self.entity('case_template',name='企业 Excel 用例模板',content='用例编号、场景、前置条件、操作步骤、预期结果')
        template_entity=Entity.objects.get(pk=template['id'])
        from .spreadsheet_export import export_entities_xlsx
        workbook=export_entities_xlsx([template_entity])
        response=self.client.post('/api/artifacts/',{
            'entity':template['id'],'file':SimpleUploadedFile('case-template.xlsx',workbook,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},format='multipart')
        self.assertEqual(response.status_code,202,response.data)
        job=self.execute(Job.objects.get(pk=response.data['job']))
        self.assertEqual(job.status,'completed',job.result)
        parsed=Entity.objects.get(pk=template['id'])
        self.assertIn('工作表：测试用例',parsed.content)
        self.assertIn('前置条件',parsed.content)
        generation=self.job(payload={'text':'用户登录需求','template_id':template['id']})
        self.assertEqual(generation.payload['template_id'],template['id'])
        self.assertEqual(generation.payload['template_snapshot']['version'],parsed.version)
        self.assertIn('前置条件',generation.payload['template_snapshot']['content'])
        wrong_kind=self.entity('prompt',name='不是用例模板')
        invalid=self.client.post('/api/jobs/',{'project':self.project.pk,'kind':'generate','payload':{
            'text':'用户登录需求','template_id':wrong_kind['id']}},format='json')
        self.assertEqual(invalid.status_code,400,invalid.data)
    def test_excel_import_creates_case_template_and_checks_project_write_access(self):
        from .spreadsheet_export import export_entities_xlsx
        source=self.entity('case_template',name='登录模板',content='用例名称、前置条件、步骤、预期结果')
        workbook=export_entities_xlsx([Entity.objects.get(pk=source['id'])])
        response=self.client.post('/api/entities/import_case_template_excel/',{
            'project':self.project.pk,
            'file':SimpleUploadedFile('团队用例模板.xlsx',workbook,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},format='multipart')
        self.assertEqual(response.status_code,201,response.data)
        imported=Entity.objects.get(pk=response.data['id'])
        self.assertEqual(imported.kind,'case_template')
        self.assertEqual(imported.name,'团队用例模板')
        self.assertEqual(imported.config['source_format'],'xlsx')
        self.assertIn('工作表：测试用例',imported.content)
        self.assertTrue(imported.files.filter(category='case_template').exists())
        self.assertEqual(imported.revisions.count(),1)

        self.client.force_authenticate(self.viewer)
        forbidden=self.client.post('/api/entities/import_case_template_excel/',{
            'project':self.project.pk,
            'file':SimpleUploadedFile('forbidden.xlsx',workbook,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},format='multipart')
        self.assertEqual(forbidden.status_code,403,forbidden.data)
    def test_parse_legacy_doc_with_linux_antiword(self):
        upload=SimpleUploadedFile('legacy.doc',b'legacy Word document')
        completed=SimpleNamespace(returncode=0,stdout='登录需求正文'.encode())
        with patch('workbench.entities.shutil.which',side_effect=lambda name:'/usr/bin/antiword' if name=='antiword' else None),\
             patch('workbench.entities.Path.is_file',return_value=False),\
             patch('workbench.entities.subprocess.run',return_value=completed) as run:
            self.assertEqual(parse_document(upload),'登录需求正文')
        self.assertEqual(run.call_args.args[0][0],'/usr/bin/antiword')
    def test_mcp_requires_owner_approval(self):
        resource=Resource.objects.create(project=self.project,kind='mcp',name='MCP')
        job=self.job('mcp',{'resource_id':resource.pk,'tool':'lookup','arguments':{}},'live')
        self.assertEqual(job.status,'awaiting_approval');self.assertIsNone(claim_job())
        self.client.force_authenticate(self.viewer);self.assertEqual(self.client.post(f'/api/jobs/{job.pk}/approve/',{'approve':True},format='json').status_code,403)
        self.client.force_authenticate(self.owner);self.assertEqual(self.client.post(f'/api/jobs/{job.pk}/approve/',{'approve':False},format='json').status_code,200);job.refresh_from_db();self.assertEqual(job.status,'cancelled')
    def test_api_simulation_is_not_real_pass(self):
        e=self.entity('api',config={'method':'GET','url':'http://example.test','assertions':[{'type':'status','expected':200}]})
        job=self.execute(self.job('api',{'entity_ids':[e['id']]}));self.assertEqual(job.status,'completed',job.result);self.assertEqual(job.result['cases'][0]['status'],'skipped')
    def test_cancel_and_stale_jobs_not_replayed(self):
        job=self.job();claimed=claim_job();self.client.post(f'/api/jobs/{job.pk}/cancel/');execute_job(claimed);job.refresh_from_db();self.assertEqual(job.status,'cancelled')
        job=self.job();claim_job();Job.objects.filter(pk=job.pk).update(heartbeat_at=timezone.now()-timezone.timedelta(seconds=200));self.assertIsNone(claim_job());job.refresh_from_db();self.assertEqual(job.status,'failed')
    def test_malformed_job_inputs(self):
        for payload in [{'model_id':[]},{'count':'bad'},{'target_kind':'shell'},{'entity_ids':[999]}]:
            res=self.client.post('/api/jobs/',{'project':self.project.pk,'kind':'generate','payload':payload},format='json');self.assertEqual(res.status_code,400,res.data)
    def test_api_key_scoped_and_hashed(self):
        res=self.client.post('/api/access-keys/',{'project':self.project.pk,'name':'CI','read_only':True},format='json');self.assertEqual(res.status_code,201)
        token=res.data['key'];self.assertNotIn(token,AccessKey.objects.get().digest)
        client=APIClient();client.credentials(HTTP_AUTHORIZATION='Bearer '+token)
        self.assertEqual(client.get('/api/projects/').data['count'],1)
        self.assertEqual(client.get(f'/api/projects/{self.other.pk}/').status_code,404)
        self.assertEqual(client.post('/api/jobs/',{}).status_code,403)
        self.assertEqual(client.get('/api/access-keys/').status_code,403)
    def test_admin_routes_block_regular_users(self):
        for url in ['users','organizations','config','audit']:self.assertEqual(self.client.get('/api/system/'+url+'/').status_code,403)
    def test_secret_config_and_arbitrary_scripts_rejected(self):
        res=self.client.post('/api/entities/',{'project':self.project.pk,'kind':'environment','name':'unsafe','config':{'headers':{'Authorization':'Bearer raw-secret'}}},format='json');self.assertEqual(res.status_code,400)
        res=self.client.post('/api/entities/',{'project':self.project.pk,'kind':'api','name':'unsafe','config':{'url':'/','before':[{'op':'eval','code':'bad'}]}},format='json');self.assertEqual(res.status_code,400)
    def test_allowed_target_exact_match(self):
        with patch.dict(os.environ,{'TESTPILOT_ALLOWED_TARGETS':'localhost:8088'}):
            self.assertEqual(allowed_url('http://localhost:8088/health'),'http://localhost:8088/health')
            for url in ['http://localhost:8089/','http://localhost:8088.evil/','file:///etc/passwd','http://u:p@localhost:8088/']:
                with self.assertRaises(ValueError):allowed_url(url)
    def test_export_and_openapi_sync(self):
        res=self.client.post('/api/entities/sync_openapi/',{'project':self.project.pk,'spec':{'paths':{'/health':{'get':{'summary':'健康检查'}}}}},format='json');self.assertEqual(res.status_code,200,res.data)
        res=self.client.post('/api/entities/sync_openapi/',{'project':self.project.pk,'spec':{'paths':{'/health':{'get':{'summary':'健康检查 v2'}}}}},format='json');self.assertEqual(res.status_code,200)
        self.assertEqual(Entity.objects.get(kind='api').version,2)
        self.assertEqual(self.client.get('/api/entities/export/').status_code,200)
    def test_case_copy_import_module(self):
        module=self.entity('module');case=Asset.objects.create(project=self.project,kind='case',name='用例',created_by=self.owner,data={'module_id':module['id']})
        self.assertEqual(self.client.post(f'/api/assets/{case.pk}/duplicate/').status_code,201)
        self.assertEqual(self.client.get('/api/assets/',{'module':module['id']}).data['count'],2)
        res=self.client.post('/api/assets/import/',{'project':self.project.pk,'items':[{'kind':'case','name':'导入用例','data':{}}]},format='json');self.assertEqual(res.status_code,201,res.data)
