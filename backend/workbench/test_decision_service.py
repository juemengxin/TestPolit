from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from .decision_service import check_health, decide
from .model_options import require_chat_compatible
from .job_submission import create_job
from .models import Project, Resource


class H2ODecisionServiceTests(SimpleTestCase):
    def setUp(self):
        self.resource = Mock(endpoint='http://192.168.5.195:18741', settings={'provider_type': 'h2o_decisions'})

    @patch('workbench.decision_service.httpx.get')
    @patch('workbench.decision_service.allowed_url', side_effect=lambda value: value)
    def test_health_check_returns_only_service_metadata(self, allow_url, get):
        get.return_value.json.return_value = {
            'ok': True, 'model': 'h2oai/h2o-lightning-4b', 'attention_mode': 'bidirectional',
            'max_state_tokens': 6144, 'max_options': 255, 'images': {'max_count': 1},
            'secret': 'must-not-escape',
        }
        result = check_health(self.resource)
        self.assertEqual(result['model'], 'h2oai/h2o-lightning-4b')
        self.assertNotIn('secret', result)
        get.assert_called_once()
        allow_url.assert_called_once_with('http://192.168.5.195:18741/health')

    @patch('workbench.decision_service.httpx.post')
    @patch('workbench.decision_service.allowed_url', side_effect=lambda value: value)
    def test_decision_request_uses_named_question_payload(self, allow_url, post):
        answers = {'ready': {'type': 'noul', 'noul': 0.94}}
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            'model': 'h2oai/h2o-lightning-4b', 'answers': answers,
            'usage': {'input_tokens': 10, 'output_tokens': 1}, 'attention_mode': 'bidirectional',
        }
        state = '页面显示登录表单'
        questions = {'ready': {'type': 'noul', 'instructions': '页面是否可输入？', 'criteria': {'true': '可输入', 'false': '不可输入'}}}
        result = decide(self.resource, state, questions)
        self.assertEqual(result['answers'], answers)
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs['json'], {'state': state, 'questions': questions})
        self.assertFalse(kwargs['follow_redirects'])
        self.assertEqual(allow_url.call_args.args[0], 'http://192.168.5.195:18741/v1/systemone')

    def test_decision_model_cannot_be_misused_as_chat_model(self):
        with self.assertRaisesRegex(ValueError, '结构化决策服务'):
            require_chat_compatible(self.resource)

    @patch('workbench.decision_service.httpx.post')
    @patch('workbench.decision_service.allowed_url', side_effect=lambda value: value)
    def test_remote_error_body_is_not_exposed(self, _allow_url, post):
        post.return_value.status_code = 400
        post.return_value.text = 'submitted private context'
        with self.assertRaisesRegex(ValueError, 'HTTP 400') as error:
            decide(self.resource, 'private context', {'ready': {'type': 'noul', 'instructions': 'ready?', 'criteria': {'true': 'yes', 'false': 'no'}}})
        self.assertNotIn('private context', str(error.exception))


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class H2ODecisionJobTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('decision-user', password='test')
        self.project = Project.objects.create(name='Decision tests', key='DECISION', owner=self.user)
        self.resource = Resource.objects.create(
            project=self.project,
            kind='model',
            name='H2O Lightning',
            endpoint='http://192.168.5.195:18741',
            settings={'provider_type': 'h2o_decisions', 'model_name': 'h2oai/h2o-lightning-4b'},
        )

    def test_decision_job_is_persisted_with_the_question_snapshot(self):
        questions = {'ready': {'type': 'noul', 'instructions': '页面是否就绪？', 'criteria': {'true': '是', 'false': '否'}}}
        job = create_job(self.project, 'decision', {
            'resource_id': self.resource.pk, 'state': '登录页已打开', 'questions': questions,
        }, 'live', 'H2O decision test', self.user)
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.payload['questions'], questions)

    def test_decision_job_rejects_non_h2o_model(self):
        self.resource.settings = {'provider_type': 'openai_compatible'}
        self.resource.save(update_fields=['settings'])
        with self.assertRaisesRegex(Exception, 'H2O Decisions'):
            create_job(self.project, 'decision', {
                'resource_id': self.resource.pk, 'state': 'test',
                'questions': {'ready': {'type': 'noul', 'instructions': 'ready?', 'criteria': {'true': 'yes', 'false': 'no'}}},
            }, 'live', 'Invalid decision', self.user)

