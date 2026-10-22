from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from .models import Project, Membership, Entity, Asset, Job
from .intelligence import apply_result


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RequirementWorkbenchTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('requirements-owner')
        self.viewer=get_user_model().objects.create_user('requirements-viewer')
        self.outsider=get_user_model().objects.create_user('requirements-outsider')
        self.project=Project.objects.create(name='Requirements',key='REQ',owner=self.owner)
        self.other=Project.objects.create(name='Other',key='OTHER-REQ',owner=self.outsider)
        Membership.objects.create(project=self.project,user=self.viewer,role='viewer')
        self.client=APIClient();self.client.force_authenticate(self.owner)
        response=self.client.post('/api/entities/',{'project':self.project.pk,'kind':'requirement','name':'登录需求','content':'# 产品\n## 登录\n### 密码登录\n验证凭据。','config':{}},format='json')
        self.assertEqual(response.status_code,201,response.data)
        self.requirement=Entity.objects.get(pk=response.data['id'])
        self.url=f'/api/entities/{self.requirement.pk}/'

    def test_structured_metadata_validation(self):
        valid={'status':'reviewing','priority':'P0','owner':'测试负责人','tags':['登录'],'acceptance_criteria':'合法凭据登录成功'}
        response=self.client.patch(self.url,{'config':valid,'version':1},format='json')
        self.assertEqual(response.status_code,200,response.data)
        for field,value in [('status','unknown'),('priority','high'),('owner',[]),('tags','tag'),('tags',['']),('tags',['x'*41]),('acceptance_criteria',None)]:
            with self.subTest(field=field,value=value):
                response=self.client.patch(self.url,{'config':{**valid,field:value}},format='json')
                self.assertEqual(response.status_code,400)
        self.requirement.refresh_from_db();self.assertEqual(self.requirement.config,valid)

    def test_requirement_parent_kind_and_cycle(self):
        module=Entity.objects.create(project=self.project,kind='module',name='模块',created_by=self.owner)
        self.assertEqual(self.client.patch(self.url,{'parent':module.pk},format='json').status_code,400)
        child=Entity.objects.create(project=self.project,kind='requirement',name='子需求',parent=self.requirement,created_by=self.owner)
        self.assertEqual(self.client.patch(self.url,{'parent':child.pk},format='json').status_code,400)

    def test_context_is_scoped_and_includes_source_versions(self):
        job=Job.objects.create(project=self.project,created_by=self.owner,kind='review',name='评审',mode='simulation',status='completed',payload={'entity_id':self.requirement.pk,'snapshots':[{'id':self.requirement.pk,'kind':'requirement','version':1}]})
        Entity.objects.create(project=self.project,kind='module',name='登录模块',config={'source_requirement_id':self.requirement.pk},created_by=self.owner)
        Entity.objects.create(project=self.project,kind='ui_case',name='登录用例',config={'source_requirement_id':self.requirement.pk},created_by=self.owner)
        Asset.objects.create(project=self.project,kind='case',name='来源任务用例',data={'generation_job':job.pk},created_by=self.owner)
        Entity.objects.create(project=self.other,kind='ui_case',name='其他项目',config={'source_requirement_id':self.requirement.pk},created_by=self.outsider)
        response=self.client.get(self.url+'requirement_context/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['counts'],{'modules':1,'cases':2,'jobs':1})
        self.assertEqual(response.data['jobs'][0]['source_version'],1)
        self.assertNotIn('其他项目',str(response.data))
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(self.url+'requirement_context/').status_code,200)
        self.assertEqual(self.client.patch(self.url,{'name':'禁止修改'},format='json').status_code,403)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url+'requirement_context/').status_code,404)

    def test_context_is_bounded_and_rejects_non_requirement(self):
        Job.objects.bulk_create([Job(project=self.project,created_by=self.owner,kind='review',name=f'评审 {index}',payload={'entity_id':self.requirement.pk}) for index in range(35)])
        data=self.client.get(self.url+'requirement_context/').data
        self.assertEqual(data['counts']['jobs'],35);self.assertEqual(len(data['jobs']),30)
        module=Entity.objects.create(project=self.project,kind='module',name='模块',created_by=self.owner)
        self.assertEqual(self.client.get(f'/api/entities/{module.pk}/requirement_context/').status_code,400)

    def test_restore_does_not_overwrite_concurrent_changes(self):
        self.client.patch(self.url,{'content':'新正文','version':1},format='json')
        response=self.client.post(self.url+'restore/',{'version':1,'expected_version':1},format='json')
        self.assertEqual(response.status_code,400)
        self.requirement.refresh_from_db();self.assertEqual(self.requirement.content,'新正文')
        response=self.client.post(self.url+'restore/',{'version':1,'expected_version':2},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['version'],3)
        self.assertIn('验证凭据',response.data['content'])

    def test_generated_api_preserves_requirement_link(self):
        job=Job.objects.create(project=self.project,created_by=self.owner,kind='generate',name='生成接口',mode='simulation',status='completed',payload={'entity_id':self.requirement.pk,'target_kind':'api'},result={'output':{'cases':[{'name':'健康检查','config':{'method':'GET','url':'/health','assertions':[{'type':'status','expected':200}]}}]}})
        identifiers=apply_result(job)
        generated=Entity.objects.get(pk=identifiers[0])
        self.assertEqual(generated.config['source_requirement_id'],self.requirement.pk)
        self.assertEqual(generated.config['source_generation_job'],job.pk)
        context=self.client.get(self.url+'requirement_context/').data
        self.assertEqual(context['counts']['cases'],1)
