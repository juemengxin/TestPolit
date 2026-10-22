"""Recorder lifecycle, ownership and manual-action regression tests."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from . import recording
from .models import Entity, Membership, Project, Revision
from .recording_browser import ActionRecorder, blocked_request_warning


class ActionRecorderTests(SimpleTestCase):
    def test_blocked_request_warning_shows_only_allowlist_authority(self):
        warning = blocked_request_warning('https://api.example.test:8443/v1/login?token=private')
        self.assertIn('api.example.test:8443', warning)
        self.assertIn('TESTPILOT_ALLOWED_TARGETS', warning)
        for private_value in ('/v1/login', 'token', 'private'):
            self.assertNotIn(private_value, warning)

    def test_typing_is_coalesced_without_losing_click_order(self):
        recorder = ActionRecorder('https://example.test/login')
        recorder.append({'action': 'click', 'selector': '#search', 'element_label': '搜索输入框'})
        for value in ['t', 'te', 'test']:
            recorder.append({'action': 'fill', 'selector': '#search', 'value': value, 'element_label': '搜索'})
        recorder.append({'action': 'click', 'selector': '#submit', 'element_label': '登录'})
        self.assertEqual([step['action'] for step in recorder.steps], ['goto', 'click', 'fill', 'click'])
        self.assertEqual(recorder.steps[2]['value'], 'test')
        self.assertEqual(recorder.steps[3]['element_label'], '登录')
        self.assertEqual(recorder.preview()[-1], {'action':'click','selector':'#submit','element_label':'登录'})
        self.assertNotIn('test', json.dumps(recorder.preview()))

    def test_password_and_limit_are_never_silently_saved(self):
        recorder = ActionRecorder('https://example.test')
        recorder.append({'action': 'fill', 'selector': '[name=password]', 'value': 'private-value'})
        self.assertNotIn('private-value', json.dumps(recorder.result()))
        self.assertEqual(recorder.steps[-1]['value'], '{{recorded_secret}}')
        for _ in range(100):
            recorder.append({'action': 'click', 'selector': '#next'})
        self.assertEqual(len(recorder.steps), 100)
        self.assertIn('100', recorder.error)

    def test_sensitive_field_label_also_protects_typed_value(self):
        recorder = ActionRecorder('https://example.test/login')
        recorder.append({'action':'fill','selector':'input:nth-of-type(2)','element_label':'登录密码','value':'private-value'})
        self.assertEqual(recorder.steps[-1]['value'],'{{recorded_secret}}')
        self.assertNotIn('private-value',json.dumps(recorder.result()))

    def test_recorded_account_variable_is_preserved_and_warned(self):
        recorder = ActionRecorder('https://example.test/login')
        recorder.append({'action':'fill','selector':'[placeholder="请输入账号"]','element_label':'请输入账号','value':'jinmu'})
        self.assertEqual(recorder.steps[-1]['value'],'{{recorded_username}}')
        self.assertIn('{{recorded_username}}',recorder.warnings[0])
        self.assertNotIn('jinmu',json.dumps(recorder.result()))

    def test_unsupported_gestures_are_visible(self):
        recorder = ActionRecorder('https://example.test')
        recorder.append({'unsupported': 'iframe 操作需复核'})
        self.assertEqual(recorder.warnings, ['iframe 操作需复核'])
        self.assertEqual(len(recorder.steps), 1)


class RecordingSessionTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('recorder-owner')
        self.other = get_user_model().objects.create_user('recorder-other')
        self.project = Project.objects.create(name='录制测试', key='RECORDER', owner=self.owner)
        self.directory = tempfile.TemporaryDirectory()
        self.process = Mock()
        self.process.is_alive.return_value = True
        self.channel = Mock()
        self.channel.poll.return_value = True
        self.session = {'id': 'session-test', 'project_id': self.project.pk, 'user_id': self.owner.pk,
                        'process': self.process, 'channel': self.channel, 'directory': self.directory,
                        'target_url': 'https://example.test/login'}
        self.session_patch = patch.object(recording, '_session', self.session)
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(lambda: setattr(recording, '_session', None))

    def test_status_hides_session_from_other_users(self):
        self.assertEqual(recording.recording_status(self.other), {'active': True, 'session_id': None})
        self.channel.send.assert_not_called()
        with self.assertRaises(PermissionError):
            recording.cancel_recording(self.other, 'session-test')
        self.process.terminate.assert_not_called()

    def test_recording_screen_requires_authenticated_owner(self):
        client = APIClient()
        self.assertEqual(client.get('/api/entities/recording_access/').status_code, 403)
        client.force_authenticate(self.other)
        self.assertEqual(client.get('/api/entities/recording_access/').status_code, 403)
        client.force_authenticate(self.owner)
        self.assertEqual(client.get('/api/entities/recording_access/').status_code, 204)
        self.process.is_alive.return_value = False
        self.assertEqual(client.get('/api/entities/recording_access/').status_code, 403)

    def test_status_and_stop_enforce_revoked_project_permissions(self):
        self.project.owner = self.other
        self.project.save()
        Membership.objects.create(project=self.project, user=self.owner, role='viewer')
        with self.assertRaises(PermissionDenied):
            recording.finish_recording(self.owner, 'session-test')
        self.channel.send.assert_not_called()

    def test_stale_browser_is_cleaned_and_can_be_restarted(self):
        self.process.is_alive.return_value = False
        self.assertFalse(recording.recording_status(self.owner)['active'])
        self.assertIsNone(recording._session)
        self.assertFalse(Path(self.directory.name).exists())

    def test_stop_persists_real_actions_and_revision_then_cleans_trace(self):
        capture = {'steps': [{'action': 'goto', 'value': 'https://example.test/login'},
                             {'action': 'fill', 'selector': '#name', 'value': 'tester'},
                             {'action': 'click', 'selector': '#submit'}], 'warnings': [], 'error': ''}
        Path(self.directory.name, 'actions.json').write_text(json.dumps(capture))
        Path(self.directory.name, 'trace.zip').write_bytes(b'temporary raw trace')
        self.channel.recv.return_value = {'status': 'stopped'}
        self.process.is_alive.return_value = False
        with patch.dict(os.environ, {'TESTPILOT_ALLOWED_TARGETS': 'example.test'}):
            item = recording.finish_recording(self.owner, 'session-test', '登录流程')
        item.refresh_from_db()
        self.assertEqual(item.config['steps'], capture['steps'])
        self.assertEqual(item.config['verification_status'], 'unverified')
        self.assertTrue(Revision.objects.filter(entity=item).exists())
        self.assertIsNone(recording._session)
        self.assertFalse(Path(self.directory.name).exists())

    def test_stop_error_does_not_create_partial_case(self):
        self.channel.recv.return_value = {'status': 'error', 'error': '浏览器已关闭'}
        self.process.is_alive.return_value = False
        with self.assertRaisesMessage(ValueError, '浏览器已关闭'):
            recording.finish_recording(self.owner, 'session-test')
        self.assertFalse(Entity.objects.exists())
        self.assertIsNone(recording._session)

    def test_cancel_never_creates_case(self):
        self.process.is_alive.return_value = False
        recording.cancel_recording(self.owner, 'session-test')
        self.assertFalse(Entity.objects.exists())
        self.assertIsNone(recording._session)
