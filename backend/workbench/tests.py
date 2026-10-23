import os, tempfile
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient
from .models import Project, Membership, Asset, Run, Resource, Attachment, Entity
from .worker import claim, execute, recover_stale

class APITests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.temp.name)
        self.settings_override.enable()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.settings_override.disable)
        User=get_user_model()
        self.owner=User.objects.create_user('owner',password='test-password-109')
        self.viewer=User.objects.create_user('viewer',password='test-password-109')
        self.editor=User.objects.create_user('editor',password='test-password-109')
        self.outsider=User.objects.create_user('outsider',password='test-password-109')
        self.project=Project.objects.create(name='Alpha',key='ALPHA',owner=self.owner)
        self.other=Project.objects.create(name='Beta',key='BETA',owner=self.outsider)
        Membership.objects.create(project=self.project,user=self.viewer,role='viewer')
        Membership.objects.create(project=self.project,user=self.editor,role='editor')
        self.case=Asset.objects.create(project=self.project,kind='case',name='登录测试',status='ready',created_by=self.owner,data={'steps':[{'action':'打开页面','expected':'显示页面'}],'simulate_result':'passed'})
        self.client=APIClient()
        self.client.force_authenticate(self.owner)
    def payload(self,**extra):
        return {'project':self.project.id,'kind':'case','name':'新用例','status':'ready','data':{'steps':[{'action':'点击','expected':'显示'}]},**extra}
    def start(self):
        response=self.client.post('/api/runs/',{'source':self.case.pk},format='json')
        self.assertEqual(response.status_code,202,response.data)
        return Run.objects.get(pk=response.data['id'])
    def test_auth_required(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/projects/').status_code,403)
    def test_project_isolation(self):
        Entity.objects.create(project=self.project,kind='ui_case',name='首页用例',created_by=self.owner)
        data=self.client.get('/api/projects/').data
        self.assertEqual(data['count'],1)
        self.assertEqual(data['results'][0]['case_count'],1)
        self.assertEqual(self.client.get(f'/api/projects/{self.other.pk}/').status_code,404)
    def test_viewer_read_only(self):
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get('/api/assets/').data['count'],1)
        self.assertEqual(self.client.post('/api/assets/',self.payload(),format='json').status_code,403)
        self.assertEqual(self.client.patch(f'/api/assets/{self.case.pk}/',{'name':'bad'},format='json').status_code,403)
        self.assertEqual(self.client.delete(f'/api/assets/{self.case.pk}/').status_code,403)
        self.assertEqual(self.client.post('/api/runs/',{'source':self.case.pk},format='json').status_code,403)
    def test_editor_cannot_manage_members_or_project(self):
        self.client.force_authenticate(self.editor)
        self.assertEqual(self.client.post('/api/assets/',self.payload(),format='json').status_code,201)
        self.assertEqual(self.client.patch(f'/api/projects/{self.project.pk}/',{'name':'bad'},format='json').status_code,403)
        self.assertEqual(self.client.post(f'/api/projects/{self.project.pk}/members/',{'username':'outsider','role':'editor'},format='json').status_code,403)
    def test_cross_project_creation_and_reparent_denied(self):
        self.assertIn(self.client.post('/api/assets/',self.payload(project=self.other.pk),format='json').status_code,[400,403])
        self.assertIn(self.client.patch(f'/api/assets/{self.case.pk}/',{'project':self.other.pk},format='json').status_code,[400,403])
    def test_steps_and_associations_validated(self):
        self.assertEqual(self.client.post('/api/assets/',self.payload(data={'steps':[{'action':'missing expected'}]}),format='json').status_code,400)
        foreign=Asset.objects.create(project=self.other,kind='case',name='foreign',created_by=self.outsider)
        self.assertEqual(self.client.post('/api/assets/',self.payload(kind='plan',data={'case_ids':[foreign.pk]}),format='json').status_code,400)
    def test_crud_filter_pagination_and_export(self):
        for i in range(12): self.client.post('/api/assets/',self.payload(name=f'批次-{i}'),format='json')
        res=self.client.get('/api/assets/',{'project':self.project.pk,'kind':'case','search':'批次'})
        self.assertEqual(res.data['count'],12); self.assertEqual(len(res.data['results']),10)
        self.assertEqual(len(self.client.get('/api/assets/',{'page':2}).data['results']),3)
        self.assertEqual(self.client.patch(f'/api/assets/{self.case.pk}/',{'name':'新名称'},format='json').status_code,200)
        export=self.client.get('/api/assets/export/',{'project':self.project.pk})
        self.assertIn('新名称',export.content.decode())
        self.assertEqual(self.client.delete(f'/api/assets/{self.case.pk}/').status_code,204)
    def test_secret_encrypted_and_never_returned(self):
        secret='never-return-this-secret'
        with patch.dict(os.environ,{'CREDENTIAL_KEY':Fernet.generate_key().decode()}):
            res=self.client.post('/api/resources/',{'project':self.project.pk,'kind':'model','name':'Model','secret':secret},format='json')
        self.assertEqual(res.status_code,201,res.data)
        self.assertTrue(res.data['has_secret']); self.assertNotIn(secret,str(res.data))
        obj=Resource.objects.get(pk=res.data['id'])
        self.assertNotIn(secret,obj.secret_encrypted)
        self.assertNotIn('secret_encrypted',res.data)
        res=self.client.patch(f'/api/resources/{obj.pk}/',{'name':'Renamed'},format='json')
        self.assertTrue(res.data['has_secret'])
        self.assertEqual(res.data['mode'],'simulation')
    def test_resource_apply_to_projects_copies_encrypted_config_and_checks_target_permissions(self):
        model=Resource.objects.create(project=self.project,kind='model',name='Shared Model',endpoint='https://model.example/v1',secret_encrypted='encrypted-value',settings={'model_name':'test-model'})
        response=self.client.post(f'/api/resources/{model.pk}/apply-to-projects/',{'project_ids':[self.other.pk]},format='json')
        self.assertEqual(response.status_code,400)  # target project is not accessible
        self.assertFalse(Resource.objects.filter(project=self.other, name='Shared Model').exists())

        target=Project.objects.create(name='Gamma',key='GAMMA',owner=self.owner)
        response=self.client.post(f'/api/resources/{model.pk}/apply-to-projects/',{'project_ids':[target.pk]},format='json')
        self.assertEqual(response.status_code,200,response.data)
        clone=Resource.objects.get(project=target,name='Shared Model')
        self.assertEqual(clone.secret_encrypted,'encrypted-value')
        self.assertEqual(clone.settings,model.settings)
        self.assertNotIn('encrypted-value',str(response.data))
        self.assertTrue(response.data['results'][0]['created'])

        response=self.client.post(f'/api/resources/{model.pk}/apply-to-projects/',{'project_ids':[target.pk]},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(Resource.objects.filter(project=target,name='Shared Model').count(),1)
        self.assertFalse(response.data['results'][0]['created'])

    def test_agent_apply_copies_and_remaps_linked_resources_and_requires_editor_target(self):
        model=Resource.objects.create(project=self.project,kind='model',name='Agent Model')
        skill=Resource.objects.create(project=self.project,kind='skill',name='Agent Skill')
        agent=Resource.objects.create(project=self.project,kind='agent',name='Agent',settings={'instructions':'run','resource_ids':[model.pk,skill.pk]})
        target=Project.objects.create(name='Gamma',key='GAMMA',owner=self.owner)
        response=self.client.post(f'/api/resources/{agent.pk}/apply-to-projects/',{'project_ids':[target.pk]},format='json')
        self.assertEqual(response.status_code,200,response.data)
        copied_agent=Resource.objects.get(project=target,kind='agent',name='Agent')
        copied_ids=copied_agent.settings['resource_ids']
        self.assertEqual(len(copied_ids),2)
        self.assertEqual(set(Resource.objects.filter(project=target,pk__in=copied_ids).values_list('kind',flat=True)),{'model','skill'})

        editor_target=Project.objects.create(name='Editor Target',key='EDTARGET',owner=self.outsider)
        Membership.objects.create(project=editor_target,user=self.owner,role='viewer')
        denied=self.client.post(f'/api/resources/{agent.pk}/apply-to-projects/',{'project_ids':[editor_target.pk]},format='json')
        self.assertEqual(denied.status_code,403)
    def test_plaintext_secret_settings_rejected(self):
        res=self.client.post('/api/resources/',{'project':self.project.pk,'kind':'model','name':'Unsafe','settings':{'api_key':'sensitive'}},format='json')
        self.assertEqual(res.status_code,400)
    def test_snapshot_results_and_durable_report(self):
        run=self.start()
        self.assertEqual(run.status,'queued');self.assertEqual(run.results,[])
        self.case.name='Changed after submission';self.case.save()
        claimed=claim();self.assertIsNotNone(claimed);execute(claimed,delay=0)
        self.assertIsNone(claim())
        persisted=Run.objects.get(pk=run.pk)
        self.assertEqual(persisted.status,'completed');self.assertEqual(persisted.progress,100)
        self.assertEqual(persisted.results[0]['name'],'登录测试')
        self.assertEqual(persisted.metrics['passed'],1)
        self.assertEqual(persisted.results[0]['steps'][0]['action'],'打开页面')
        self.assertEqual(len(persisted.history),3)
        other_client=APIClient();other_client.force_authenticate(self.owner)
        report=other_client.get(f'/api/runs/{run.pk}/').data
        self.assertEqual(report['results'],persisted.results)
        self.assertIn('SIMULATION',report['logs'][0]['message'])
        self.assertEqual(other_client.get(f'/api/runs/{run.pk}/export/').status_code,200)
    def test_failed_and_skipped_simulated_results(self):
        for outcome in ['failed','skipped']:
            self.case.data['simulate_result']=outcome;self.case.save()
            run=self.start();execute(claim(),delay=0);run.refresh_from_db()
            self.assertEqual(run.metrics[outcome],1)
            self.assertEqual(run.status,'completed')
    def test_cancel_prevents_worker_overwrite(self):
        run=self.start();claimed=claim()
        self.assertEqual(self.client.post(f'/api/runs/{run.pk}/cancel/').status_code,200)
        execute(claimed,delay=0);run.refresh_from_db()
        self.assertEqual(run.status,'cancelled');self.assertEqual(run.results,[])
    def test_stale_worker_recovers(self):
        run=self.start();claimed=claim()
        Run.objects.filter(pk=run.pk).update(heartbeat_at=timezone.now()-timezone.timedelta(seconds=90))
        recover_stale();run.refresh_from_db();self.assertEqual(run.status,'queued')
        replacement=claim();self.assertNotEqual(replacement.worker_id,claimed.worker_id)
        execute(claimed,delay=0);run.refresh_from_db();self.assertEqual(run.status,'running')
        execute(replacement,delay=0);run.refresh_from_db();self.assertEqual(run.status,'completed')
    def test_upload_download_permission_and_path(self):
        file=SimpleUploadedFile('notes.txt',b'private test document',content_type='text/plain')
        res=self.client.post('/api/attachments/',{'project':self.project.pk,'asset':self.case.pk,'file':file},format='multipart')
        self.assertEqual(res.status_code,201,res.data)
        self.assertNotIn('file',res.data)
        self.assertEqual(self.client.get(res.data['download_url']).status_code,200)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(res.data['download_url']).status_code,404)
        self.assertEqual(self.client.delete(f'/api/attachments/{res.data["id"]}/').status_code,404)
    def test_attachment_step_link_validated(self):
        run=self.start()
        res=self.client.post('/api/attachments/',{'project':self.project.pk,'run':run.pk,'case_id':self.case.pk,'step_index':0,'file':SimpleUploadedFile('step.png',b'fake')},format='multipart')
        self.assertEqual(res.status_code,201,res.data)
        self.assertEqual(res.data['step_index'],0)
        res=self.client.post('/api/attachments/',{'project':self.project.pk,'run':run.pk,'case_id':self.case.pk,'step_index':99,'file':SimpleUploadedFile('step.png',b'fake')},format='multipart')
        self.assertEqual(res.status_code,400)
    def test_draft_case_cannot_execute(self):
        self.case.status='draft';self.case.save()
        self.assertEqual(self.client.post('/api/runs/',{'source':self.case.pk},format='json').status_code,400)
    def test_login_csrf_and_session_cookie(self):
        client=APIClient(enforce_csrf_checks=True)
        self.assertEqual(client.post('/api/auth/login/',{'username':'owner','password':'test-password-109'}).status_code,403)
        client.get('/api/auth/session/')
        token=client.cookies['csrftoken'].value
        res=client.post('/api/auth/login/',{'username':'owner','password':'test-password-109'},HTTP_X_CSRFTOKEN=token)
        self.assertEqual(res.status_code,200)
        self.assertTrue(client.cookies['sessionid']['httponly'])
        self.assertEqual(client.get('/api/projects/').status_code,200)
    def test_membership_revoke_takes_effect(self):
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(f'/api/assets/{self.case.pk}/').status_code,200)
        Membership.objects.filter(project=self.project,user=self.viewer).delete()
        self.assertEqual(self.client.get(f'/api/assets/{self.case.pk}/').status_code,404)
    def test_agent_reference_and_safe_execution_context(self):
        model=Resource.objects.create(project=self.project,kind='model',name='Model',secret_encrypted='not-for-response')
        agent=Resource.objects.create(project=self.project,kind='agent',name='Agent',settings={'resource_ids':[model.pk]})
        response=self.client.patch(f'/api/assets/{self.case.pk}/',{'data':{**self.case.data,'agent_id':agent.pk}},format='json')
        self.assertEqual(response.status_code,200,response.data)
        run=self.start()
        self.assertEqual(run.execution_context['agent']['name'],'Agent')
        self.assertNotIn('secret',str(run.execution_context))
        self.assertEqual(self.client.delete(f'/api/resources/{agent.pk}/').status_code,400)
        self.assertEqual(self.client.delete(f'/api/resources/{model.pk}/').status_code,400)
        foreign=Resource.objects.create(project=self.other,kind='agent',name='Foreign')
        response=self.client.patch(f'/api/assets/{self.case.pk}/',{'data':{**self.case.data,'agent_id':foreign.pk}},format='json')
        self.assertEqual(response.status_code,400)
    def test_export_includes_attachment_and_secret_in_url_rejected(self):
        run=self.start()
        upload=self.client.post('/api/attachments/',{'project':self.project.pk,'run':run.pk,'file':SimpleUploadedFile('notes.txt',b'note')},format='multipart')
        self.assertEqual(upload.status_code,201)
        report=self.client.get(f'/api/runs/{run.pk}/export/').json()
        self.assertEqual(report['attachments'][0]['id'],upload.data['id'])
        self.assertNotIn('file',report['attachments'][0])
        res=self.client.post('/api/resources/',{'project':self.project.pk,'kind':'mcp','name':'Unsafe','endpoint':'https://example.com/api?api_key=secret'},format='json')
        self.assertEqual(res.status_code,400)
    def test_malformed_ids_rejected(self):
        self.assertEqual(self.client.post('/api/runs/',{'source':'bad'},format='json').status_code,400)
        self.assertEqual(self.client.post('/api/attachments/',{'project':'bad'},format='json').status_code,400)
