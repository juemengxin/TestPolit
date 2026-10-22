"""Recorder contracts use explicit tool fixtures; they do not claim a real-device pass."""
import base64
import json
import os
import tempfile
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .job_worker import execute_job
from .mobile_recording import execute_recording
from .mobile_tools import MobileRecordingError, capture_screen, parse_screen_elements, resolve_screen_element, resolve_screen_point
from .models import AppRecording, Entity, Job, Membership, Project, Resource


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AppRecordingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('owner')
        self.other = get_user_model().objects.create_user('other')
        self.viewer = get_user_model().objects.create_user('viewer')
        self.project = Project.objects.create(name='App recording', key='APPREC', owner=self.owner)
        Membership.objects.create(project=self.project, user=self.other, role='editor')
        Membership.objects.create(project=self.project, user=self.viewer, role='viewer')
        self.resource = Resource.objects.create(project=self.project, kind='mcp', name='Mobile', endpoint='http://localhost:8933/mcp', mode='live')
        self.client = APIClient(); self.client.force_authenticate(self.owner)
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        override = override_settings(MEDIA_ROOT=directory.name); override.enable(); self.addCleanup(override.disable)
        env = patch.dict(os.environ, {'CREDENTIAL_KEY': Fernet.generate_key().decode()}); env.start(); self.addCleanup(env.stop)
        self.calls = []
        mocked = patch('workbench.executors.mcp_request', side_effect=self.tool); mocked.start(); self.addCleanup(mocked.stop)

    def tool(self, resource, method, params, **kwargs):
        name, arguments = params['name'], params['arguments']; self.calls.append((name, arguments))
        text = 'ok'
        if name == 'mobile_list_available_devices':
            text = json.dumps({'devices':[{'id':'phone1','name':'Android phone','platform':'android','state':'online'}]})
        if name == 'mobile_take_screenshot':
            return {'content':[{'type':'image','mimeType':'image/png','data':base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()}]}
        if name == 'mobile_get_screen_size': text = 'Screen size is 1080x2400 pixels'
        if name == 'mobile_list_elements_on_screen':
            if arguments.get('format') == 'json':
                text = 'Found these elements on screen: '+json.dumps([{'ref':'@e1','type':'TextView','text':'首页',
                    'identifier':'com.example.app:id/title','coordinates':{'x':100,'y':200,'width':300,'height':100}}],ensure_ascii=False)
            else:
                text = '@e1 text="首页" id="com.example.app:id/title"'
        return {'content':[{'type':'text','text':text}]}

    def start(self):
        response = self.client.post('/api/app-recordings/', {'project':self.project.pk,'resource':self.resource.pk,'device_id':'phone1','package_name':'com.example.app'}, format='json')
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(self.calls, [])
        return AppRecording.objects.get(pk=response.data['id'])

    def work(self, session):
        session.refresh_from_db(); job = session.last_job
        job.status='running';job.worker_id='fixture';job.save()
        execute_job(job);job.refresh_from_db();session.refresh_from_db()
        return job

    def command(self, session, **command):
        return self.client.post(f'/api/app-recordings/{session.pk}/command/', command, format='json')

    def test_record_and_save_app_case_once_with_revision(self):
        session = self.start(); job = self.work(session)
        self.assertEqual(job.status, 'completed', job.result)
        self.assertEqual(session.status, 'ready')
        self.assertEqual(self.command(session, action='tap_field', locator={'resource_id':'com.example.app:id/title'}).status_code, 202)
        self.work(session)
        response = self.client.get(f'/api/app-recordings/{session.pk}/')
        self.assertEqual(response.data['steps'][-1], {'action':'tap_field','locator':{'resource_id':'com.example.app:id/title'}})
        result = self.client.post(f'/api/app-recordings/{session.pk}/finish/', {'name':'登录流程'})
        self.assertEqual(result.status_code, 200, result.data)
        case = Entity.objects.get(pk=result.data['case_id'])
        self.assertEqual(case.config['platform'], 'app')
        self.assertEqual(case.config['verification_status'], 'unverified')
        self.assertEqual(case.revisions.count(), 1)
        again = self.client.post(f'/api/app-recordings/{session.pk}/finish/', {'name':'登录流程'})
        self.assertEqual(again.data['case_id'], case.pk)
        session.refresh_from_db();self.assertIsNone(session.device_lock)

    def test_field_based_recording_and_replay_never_uses_coordinates(self):
        session=self.start();self.work(session)
        fields=self.client.get(f'/api/app-recordings/{session.pk}/elements/')
        self.assertEqual(fields.status_code,200,fields.data)
        self.assertEqual(fields.data[0]['locator'],{'resource_id':'com.example.app:id/title','text':'首页'})
        self.assertEqual(self.command(session,action='tap_field',locator=fields.data[0]['locator']).status_code,202)
        self.work(session)
        session.refresh_from_db()
        self.assertEqual(session.steps[-1],{'action':'tap_field','locator':{'resource_id':'com.example.app:id/title','text':'首页'}})
        self.assertIn(('mobile_click_on_screen_at_coordinates',{'device':'phone1','ref':'@e1'}),self.calls)
        self.assertFalse(any('x' in arguments or 'y' in arguments for name,arguments in self.calls if name=='mobile_click_on_screen_at_coordinates'))
        self.client.post(f'/api/app-recordings/{session.pk}/finish/',{'name':'字段定位录制'})
        case=Entity.objects.get(name='字段定位录制')
        self.assertEqual(case.config['steps'][1]['action'],'tap_field')
        self.assertNotIn('x',case.config['steps'][1]);self.assertNotIn('y',case.config['steps'][1])

    def test_tapping_device_preview_auto_resolves_and_saves_element_fields(self):
        session=self.start();self.work(session)
        response=self.command(session,action='tap_screen',x=200,y=240,screen_version=session.screen_version)
        self.assertEqual(response.status_code,202,response.data)
        job=self.work(session)
        self.assertEqual(job.status,'completed',job.result)
        session.refresh_from_db()
        self.assertEqual(session.steps[-1],{'action':'tap_field','locator':{'resource_id':'com.example.app:id/title','text':'首页'}})
        self.assertIn(('mobile_click_on_screen_at_coordinates',{'device':'phone1','ref':'@e1'}),self.calls)

    def test_device_preview_click_rejects_stale_or_unrecognized_positions(self):
        session=self.start();self.work(session)
        stale=self.command(session,action='tap_screen',x=200,y=240,screen_version=session.screen_version-1)
        self.assertEqual(stale.status_code,400)
        outside=self.command(session,action='tap_screen',x=session.screen_width+1,y=240,screen_version=session.screen_version)
        self.assertEqual(outside.status_code,400)

    def test_element_parser_handles_accessibility_fields_and_ambiguous_matches(self):
        source='@e1 text="登录" id="com.example:id/login" content-desc="提交按钮" class="Button"\n@e2 text="密码" id="com.example:id/password" class="android.widget.EditText password"'
        elements=parse_screen_elements(source)
        self.assertEqual(elements[0]['locator'],{'resource_id':'com.example:id/login','content_desc':'提交按钮','text':'登录'})
        self.assertEqual(elements[1]['text'],'')
        self.assertEqual(resolve_screen_element(source,{'resource_id':'com.example:id/login'}),'@e1')
        with self.assertRaises(MobileRecordingError):
            resolve_screen_element(source+'\n@e3 text="登录" id="com.example:id/login"',{'resource_id':'com.example:id/login'})

    def test_mobile_mcp_json_element_bounds_support_hit_testing(self):
        source='Found these elements on screen: '+json.dumps([
            {'ref':'@e1','type':'View','identifier':'container','coordinates':{'x':0,'y':0,'width':500,'height':500}},
            {'ref':'@e2','type':'Button','text':'登录','identifier':'com.example:id/login','coordinates':{'x':100,'y':100,'width':120,'height':60}},
        ],ensure_ascii=False)
        element=resolve_screen_point(source,120,120)
        self.assertEqual(element['ref'],'@e2')
        self.assertEqual(element['locator'],{'resource_id':'com.example:id/login','text':'登录'})
        self.assertEqual(resolve_screen_point('@e2 Button text="登录" id="com.example:id/login" at=100,100 size=120x60',110,110)['ref'],'@e2')
        with self.assertRaises(MobileRecordingError):
            resolve_screen_point(source,600,600)

    def test_private_text_encrypted_pending_then_variable_only(self):
        session=self.start();self.work(session)
        self.assertEqual(self.command(session, action='type_text', value='private-password', sensitive=True, variable='login_password').status_code,202)
        session.refresh_from_db();self.assertNotIn('private-password', session.pending_encrypted)
        job=self.work(session)
        self.assertEqual(session.steps[-1]['value'], '{{login_password}}')
        self.assertEqual(session.pending_encrypted,'')
        self.assertNotIn('private-password', json.dumps(job.payload)+json.dumps(job.logs)+json.dumps(job.result))
        self.assertNotIn('private-password', json.dumps(self.client.get(f'/api/app-recordings/{session.pk}/').data))
        self.assertIn(('mobile_type_keys', {'device':'phone1','text':'private-password','submit':False}), self.calls)

    def test_busy_invalid_and_stale_commands_do_not_touch_device(self):
        session=self.start()
        self.assertEqual(self.command(session, action='refresh').status_code,400)
        self.work(session);self.calls.clear()
        for command in [{'action':'tap','x':10,'y':10,'screen_version':0}, {'action':'tap','x':1080,'y':0,'screen_version':1}, {'action':'tap','x':True,'y':0,'screen_version':1}, {'action':'type_text','value':'x','sensitive':'false'}, {'action':'wait_seconds','value':31}, {'action':'shell','value':'rm'}]:
            self.assertEqual(self.command(session, **command).status_code,400)
        self.assertEqual(self.calls,[])

    def test_permissions_and_real_resource_checks(self):
        session=self.start();self.work(session)
        for user,code in [(self.viewer,403),(self.other,404)]:
            self.client.force_authenticate(user)
            response=self.client.get(f'/api/app-recordings/{session.pk}/screen/')
            # Owner scoping hides a viewer's unrelated session before role checking.
            self.assertIn(response.status_code,[403,404])
            self.assertEqual(self.command(session, action='refresh').status_code,404)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post('/api/app-recordings/devices/', {'project':self.project.pk,'resource':self.resource.pk}).status_code,403)
        self.client.force_authenticate(self.owner)
        self.resource.mode='simulation';self.resource.save()
        self.assertEqual(self.client.post('/api/app-recordings/', {'project':self.project.pk,'resource':self.resource.pk,'device_id':'other','package_name':'com.example.app'}).status_code,404)
        self.assertEqual(self.client.post('/api/jobs/', {'project':self.project.pk,'kind':'app_record','mode':'live','payload':{'recording_id':session.pk}},format='json').status_code,400)
        self.assertEqual(self.client.post(f'/api/jobs/{session.last_job_id}/cancel/').status_code,404)

    def test_device_lease_and_cancel_release(self):
        session=self.start();self.work(session)
        response=self.client.post('/api/app-recordings/', {'project':self.project.pk,'resource':self.resource.pk,'device_id':'phone1','package_name':'com.example.other'})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.client.post(f'/api/app-recordings/{session.pk}/cancel/').status_code,204)
        session.refresh_from_db();self.assertIsNone(session.device_lock);self.assertFalse(session.screenshot)

    def test_capture_failure_preserves_successful_action_and_requires_refresh(self):
        session=self.start();self.work(session)
        self.command(session, action='press_button',value='BACK')
        with patch('workbench.mobile_recording.capture_screen',side_effect=MobileRecordingError('截图失败')):
            job=self.work(session)
        self.assertEqual(job.status,'failed');self.assertEqual(session.status,'error')
        self.assertEqual(session.steps[-1],{'action':'press_button','value':'BACK'})
        self.assertEqual(self.command(session,action='press_button',value='BACK').status_code,400)
        self.assertEqual(self.command(session,action='refresh').status_code,202)
        self.work(session);self.assertEqual(session.status,'ready');self.assertEqual(len(session.steps),2)

    def test_failed_gesture_is_not_recorded_or_retried(self):
        session=self.start();self.work(session)
        self.command(session,action='swipe',value='up')
        with patch('workbench.mobile_recording.call_mobile',side_effect=TimeoutError('private-value')):
            job=self.work(session)
        self.assertEqual(len(session.steps),1);self.assertNotIn('private-value',job.result['error'])
        self.assertEqual(session.pending_encrypted,'')

    def test_assertion_failure_is_not_saved(self):
        session=self.start();self.work(session)
        self.command(session,action='assert_text',value='不存在')
        self.work(session);self.assertEqual(len(session.steps),1);self.assertEqual(session.status,'error')

    def test_worker_interruption_recovers_without_replaying(self):
        session=self.start();Job.objects.filter(pk=session.last_job_id).update(status='failed')
        data=self.client.get(f'/api/app-recordings/{session.pk}/').data
        self.assertEqual(data['status'],'error');self.assertEqual(self.calls,[])
        session.refresh_from_db();self.assertEqual(session.pending_encrypted,'')

    def test_revoked_permission_stops_queued_command(self):
        session=self.start();self.project.owner=self.other;self.project.save()
        job=self.work(session);self.assertEqual(job.status,'failed');self.assertEqual(self.calls,[])
