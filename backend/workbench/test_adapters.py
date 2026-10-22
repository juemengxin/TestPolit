"""Explicit local protocol fixtures, never claimed as an actual model quality test.
Run fixture_server.py then TESTPILOT_INTEGRATION=1 manage.py test workbench.test_adapters.
"""
import os
from unittest import skipUnless
from unittest.mock import patch
from django.test import TransactionTestCase,override_settings
from django.contrib.auth import get_user_model
from .models import Project,Entity,Resource,Job,Artifact
from .job_worker import execute_job
from .executors import mcp_request
from .intelligence import embedding
import tempfile

@skipUnless(os.getenv('TESTPILOT_INTEGRATION')=='1','requires explicit local fixture server')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class NetworkAdapterTests(TransactionTestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('fixture',password='fixture')
        self.project=Project.objects.create(name='Local protocol fixture, not real LLM',key='FIXTURE',owner=self.owner)
        self.env=patch.dict(os.environ,{'TESTPILOT_ALLOWED_TARGETS':'127.0.0.1:8766'});self.env.start();self.addCleanup(self.env.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.override=override_settings(MEDIA_ROOT=self.tmp.name);self.override.enable();self.addCleanup(self.override.disable)
    def run_job(self,kind,payload):
        job=Job.objects.create(project=self.project,created_by=self.owner,name='LOCAL PROTOCOL FIXTURE — NOT A REAL MODEL',kind=kind,mode='live',payload=payload,status='running',worker_id='fixture')
        execute_job(job);job.refresh_from_db();self.assertEqual(job.status,'completed',job.result);return job
    def test_live_http_assertions_and_variable_extraction(self):
        job=self.run_job('api',{'snapshots':[{'id':1,'name':'fixture health','config':{'method':'GET','url':'http://127.0.0.1:8766/health','assertions':[{'type':'status','expected':200},{'type':'json_equals','path':'status','expected':'ok'}],'extract':{'service':'service'}}}]})
        self.assertEqual(job.result['summary']['passed'],1);self.assertEqual(job.result['variables']['service'],'testpilot-fixture')
    def test_live_failed_assertion_is_recorded(self):
        job=self.run_job('api',{'snapshots':[{'id':1,'name':'expected failure','config':{'method':'GET','url':'http://127.0.0.1:8766/health','assertions':[{'type':'status','expected':404}]}}]})
        self.assertEqual(job.result['summary']['failed'],1)
    def test_langchain_three_round_graph_protocol(self):
        model=Resource.objects.create(project=self.project,kind='model',name='Fixture, not real LLM',endpoint='http://127.0.0.1:8766/v1',settings={'model_name':'fixture-only'})
        job=self.run_job('generate',{'text':'Fixture contract test','model_id':model.pk,'target_kind':'case','auto_apply':False})
        self.assertEqual(job.result['engine'],'LangGraph');self.assertEqual(job.usage['calls'],3);self.assertEqual(job.usage['total_tokens'],90);self.assertEqual(job.result['output']['cases'][0]['name'],'Fixture generated case')
    def test_mcp_initialize_list_call(self):
        resource=Resource.objects.create(project=self.project,kind='mcp',name='MCP fixture',endpoint='http://127.0.0.1:8766/mcp')
        self.assertEqual(mcp_request(resource,'tools/list')['tools'][0]['name'],'echo')
        self.assertIn('hello',str(mcp_request(resource,'tools/call',{'name':'echo','arguments':{'message':'hello'}})))
    def test_embedding_protocol(self):
        resource=Resource.objects.create(project=self.project,kind='model',name='Embedding fixture',endpoint='http://127.0.0.1:8766/v1',settings={'model_name':'fixture'})
        self.assertEqual(embedding(resource,['test']),[[1.0,0.0,0.0]])
    def test_playwright_real_browser_evidence(self):
        job=self.run_job('ui',{'repeat_count':2,'stall_threshold_ms':5000,'snapshots':[{'id':1,'name':'Actual local browser test','config':{'base_url':'http://127.0.0.1:8766','steps':[{'action':'goto','value':'/'},{'action':'fill','selector':'#name','value':'TestPilot'},{'action':'click','selector':'#submit'},{'action':'assert_text','selector':'#result','value':'Hello TestPilot'}]}}]})
        self.assertEqual(job.result['summary']['passed'],2,job.result)
        self.assertEqual([case['iteration'] for case in job.result['cases']],[1,2])
        self.assertEqual(job.result['stability'][0]['pass_rate'],100)
        self.assertTrue(job.result['stability'][0]['stable'])
        self.assertEqual(job.files.filter(category='screenshot').count(),8)
        self.assertEqual(job.files.filter(category='trace').count(),2);self.assertEqual(job.files.filter(category='video').count(),2)
