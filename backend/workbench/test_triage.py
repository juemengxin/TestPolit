"""Contract, privacy and persistence checks for advisory failure decisions."""
import copy
import os
from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from .job_worker import claim_job, execute_job, enqueue_failure_analysis_jobs
from .models import Entity, Job, Membership, Project, Resource, Run
from .triage import triage_result


def failure(**changes):
    return {'id': 11, 'name': '测试用例', 'status': 'failed', 'detail': '', 'assertions': [], **changes}


class DecisionTests(SimpleTestCase):
    def test_local_rule_routing_ignores_external_model_configuration(self):
        cases = [
            failure(detail='无法连接目标服务'),
            failure(response={'status_code':401}),
            failure(detail='缺少依赖变量：tenant_id'),
            failure(assertions=[{'type':'json_equals','passed':False}]),
            failure(assertions=[{'type':'json_equals','passed':False,'detail':'断言字段或预期类型无效'}]),
            failure(detail='TimeoutError'),
            failure(steps=[{'status':'failed','action':'tap_field','error':'当前页面无法唯一匹配所选元素字段，请刷新元素列表并选择唯一的资源 ID、无障碍描述或文本'}]),
            failure(steps=[{'status':'failed','action':'launch_app','error':'设备操作失败，请检查手机是否解锁、USB 授权和 Mobile MCP 服务；刷新画面确认实际状态'}]),
            failure(status='passed'),
        ]
        source = {'cases':cases}
        before = copy.deepcopy(source)
        with patch.dict(os.environ, {'TESTPILOT_LAYA_URL':'https://unused.example', 'TESTPILOT_LAYA_API_KEY':'unused-secret'}), \
             patch('httpx.Client') as outbound_client:
            result = triage_result(source, 'live')
        self.assertEqual([item['category'] for item in result['items']],
                         ['environment','authentication','configuration','assertion_difference','configuration','unknown','configuration','environment'])
        self.assertEqual(result['service_status'], 'local_rules')
        self.assertTrue(all(item['confidence'] is None for item in result['items']))
        self.assertTrue(all(item['source'] == 'local_rules' for item in result['items']))
        self.assertIsNone(result['inference'])
        self.assertIn('不调用外部模型', result['notice'])
        outbound_client.assert_not_called()
        self.assertEqual(source, before)

    def test_simulation_and_no_failures_skip_triage(self):
        with patch('httpx.Client') as outbound_client:
            self.assertEqual(triage_result({'cases':[failure()]},'simulation')['status'], 'simulation')
            self.assertEqual(triage_result({'cases':[]},'live')['items'], [])
        outbound_client.assert_not_called()


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class TriageWorkflowTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('owner')
        self.viewer=get_user_model().objects.create_user('viewer')
        self.outsider=get_user_model().objects.create_user('outsider')
        self.project=Project.objects.create(name='测试项目',key='TRIAGE',owner=self.owner)
        Membership.objects.create(project=self.project,user=self.viewer,role='viewer')
        self.client=APIClient()
        self.client.force_authenticate(self.owner)
        self.source=Job.objects.create(project=self.project,kind='api',mode='live',name='失败请求',status='completed',
                                       created_by=self.owner,result={'cases':[failure(detail='未配置断言，不能判定通过')]})

    def test_background_triage_does_not_execute_source_or_modify_cases(self):
        original=copy.deepcopy(self.source.result)
        response=self.client.post(f'/api/jobs/{self.source.pk}/triage/',{},format='json')
        self.assertEqual(response.status_code,202)
        task=Job.objects.get(pk=response.data['id'])
        self.assertEqual(task.status,'queued')
        with patch('workbench.executors.run_api') as execute:
            execute_job(claim_job())
        execute.assert_not_called()
        task.refresh_from_db();self.source.refresh_from_db()
        self.assertEqual(task.status,'completed',task.result)
        self.assertEqual(task.result['triage']['items'][0]['category'],'configuration')
        self.assertEqual(self.source.result,original)
        self.assertEqual(Run.objects.count(),0)
        self.assertEqual(task.result['source_job_id'],self.source.pk)

    def test_selected_model_queues_async_analysis_for_each_failed_case(self):
        model=Resource.objects.create(project=self.project,kind='model',name='Local Test Model',
                                      endpoint='http://host.docker.internal:11434/v1',enabled=True)
        self.source.payload={'snapshots':[{'id':11,'kind':'ui_case','version':1,'config':{'platform':'web'}}]}
        self.source.result={'cases':[failure(id=11),failure(id=12,name='第二条失败用例')]}
        self.source.save(update_fields=['payload','result'])
        self.source.payload['failure_analysis_model_id']=model.pk
        queued=enqueue_failure_analysis_jobs(self.source)
        self.assertEqual([item['case_id'] for item in queued],[11,12])
        analyses=list(Job.objects.filter(kind='analyze',project=self.project).order_by('id'))
        self.assertEqual(len(analyses),2)
        self.assertTrue(all(item.status=='queued' and item.mode=='live' for item in analyses))
        self.assertEqual([item.payload['model_id'] for item in analyses],[model.pk,model.pk])
        self.assertEqual([item.payload['failure_case_id'] for item in analyses],[11,12])

    def test_submission_authorization_and_modes(self):
        for user,status in [(self.viewer,403),(self.outsider,404)]:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.post(f'/api/jobs/{self.source.pk}/triage/').status_code,status)
        self.client.force_authenticate(self.owner)
        self.source.mode='simulation';self.source.save()
        self.assertEqual(self.client.post(f'/api/jobs/{self.source.pk}/triage/').status_code,400)
        response=self.client.post('/api/jobs/',{'project':self.project.pk,'kind':'triage','mode':'live','payload':{'source_job':self.source.pk}},format='json')
        self.assertEqual(response.status_code,400)

    def test_automatic_execution_persists_triage_in_report(self):
        result={'cases':[failure(detail='无法连接目标服务')],
                'summary':{'total':1,'passed':0,'failed':1,'skipped':0}}
        job=Job.objects.create(project=self.project,kind='api',mode='live',name='执行',created_by=self.owner,
                               payload={'snapshots':[]})
        with patch('workbench.executors.run_api',return_value=result):
            execute_job(claim_job())
        job.refresh_from_db()
        self.assertEqual(job.status,'completed')
        report=Run.objects.get(pk=job.result['run_report_id'])
        self.assertEqual(report.execution_context['triage'],job.result['triage'])
        self.assertEqual(report.metrics['failed'],1)
        self.assertEqual(job.result['cases'][0]['status'],'failed')
