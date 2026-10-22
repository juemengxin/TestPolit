"""Manage one local recording session; database writes stay in the API process."""

import atexit
import json
import multiprocessing
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from django.db import transaction

from .access import require_write
from .entities import save_revision
from .models import Entity, Project
from .recording_browser import run_browser_session
from .security import allowed_url, audit

_lock = threading.RLock()
_session = None


def _receive(session, timeout=45):
    """Bound IPC waits and expose only safe error messages from the recorder."""
    channel = session['channel']
    try:
        if not channel.poll(timeout):
            raise ValueError('录制浏览器响应超时，请重新开始录制')
        result = channel.recv()
    except (EOFError, BrokenPipeError, OSError) as exc:
        raise ValueError('录制浏览器已断开，请重新开始录制') from exc
    if result.get('status') == 'error':
        raise ValueError(result['error'])
    return result


def _close_session(session):
    """Gracefully close Chromium first, then reclaim the process and temporary Trace."""
    process, channel = session['process'], session['channel']
    try:
        proxy = session.get('display_proxy')
        if proxy:
            # Closing the websocket server disconnects every viewer before another owner can record.
            try:
                os.killpg(proxy.pid, signal.SIGTERM)
                proxy.wait(timeout=3)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(proxy.pid, signal.SIGKILL)
                proxy.wait(timeout=2)
        if process.is_alive():
            try:
                channel.send('cancel')
            except (BrokenPipeError, EOFError, OSError):
                pass
            process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
    finally:
        channel.close()
        session['directory'].cleanup()


def _start_display_proxy(session):
    """Expose the recording desktop only behind the authenticated frontend proxy."""
    proxy = subprocess.Popen(
        ['/opt/bin/noVNC/utils/novnc_proxy', '--listen', '7900', '--vnc', 'localhost:5900'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    session['display_proxy'] = proxy
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and proxy.poll() is None:
        try:
            with socket.create_connection(('127.0.0.1', 7900), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise ValueError('录制画面服务启动失败，请检查 Docker 浏览器服务')


def _discard_session():
    global _session
    session, _session = _session, None
    if session:
        _close_session(session)


atexit.register(_discard_session)


def start_recording(user, project: Project, target_url: str) -> dict:
    """Launch an isolated owner-only session after permission and target checks."""
    global _session
    require_write(user, project)
    target_url = allowed_url(target_url)
    with _lock:
        if _session and not _session['process'].is_alive():
            _discard_session()
        if _session:
            raise ValueError('已有录制会话正在运行，请先结束或取消当前录制')
        # Spawn avoids inheriting Gunicorn sockets, database connections and Playwright greenlets.
        context = multiprocessing.get_context('spawn')
        parent_channel, child_channel = context.Pipe()
        directory = tempfile.TemporaryDirectory(prefix='testpilot-recording-')
        process = context.Process(target=run_browser_session, args=(child_channel, target_url, directory.name), daemon=True)
        session = {
            'id': uuid4().hex, 'user_id': user.pk, 'project_id': project.pk,
            'process': process, 'channel': parent_channel, 'directory': directory, 'target_url': target_url,
        }
        try:
            process.start()
            child_channel.close()
            if _receive(session).get('status') != 'ready':
                raise ValueError('录制浏览器启动失败')
            _start_display_proxy(session)
            audit(user, project, 'ui.recording_start', session['id'])
            _session = session
            return {'session_id': session['id'], 'target_url': target_url, 'status': 'recording'}
        except Exception:
            child_channel.close()
            _close_session(session)
            raise


def _get_session(user, session_id):
    if not _session or _session['id'] != session_id:
        raise ValueError('录制会话不存在或已结束，请重新开始')
    if _session['user_id'] != user.pk:
        raise PermissionError('无权操作此录制会话')
    require_write(user, Project.objects.get(pk=_session['project_id']))
    return _session


def recording_status(user):
    """Restore only the owner's authorized session and report capture errors honestly."""
    with _lock:
        if not _session:
            return {'active': False}
        if not _session['process'].is_alive():
            _discard_session()
            return {'active': False, 'error': '录制会话已结束，请重新开始'}
        if _session['user_id'] != user.pk:
            return {'active': True, 'session_id': None}
        session = _get_session(user, _session['id'])
        try:
            session['channel'].send('status')
            status = _receive(session, timeout=5)
        except (ValueError, OSError):
            _discard_session()
            return {'active': False, 'error': '录制浏览器已断开，请重新开始'}
        return {**status, 'active': True, 'session_id': session['id'], 'target_url': session['target_url'], 'project_id': session['project_id']}


def authorize_recording_screen(user):
    """Nginx checks this for both desktop assets and the websocket upgrade."""
    from rest_framework.exceptions import PermissionDenied
    with _lock:
        if not _session or _session['user_id'] != user.pk or not _session['process'].is_alive():
            raise PermissionDenied('无权访问此录制画面')
        require_write(user, Project.objects.get(pk=_session['project_id']))


def _create_case(project, user, directory, name):
    """Persist captured manual actions, rather than treating Trace API calls as user gestures."""
    from .executors import validate_config
    capture = json.loads(Path(directory, 'actions.json').read_text(encoding='utf-8'))
    if capture.get('error'):
        raise ValueError(capture['error'])
    steps = capture['steps']
    if len(steps) < 2:
        raise ValueError('未录制到页面交互，请至少完成一次点击或输入后再生成用例')
    base_url = allowed_url(steps[0]['value'])
    warnings = capture.get('warnings', [])
    config = {
        'platform': 'web', 'base_url': base_url, 'priority': 'P1', 'test_type': 'functional',
        'preconditions': '由平台 Playwright 录制生成；请复核步骤与断言。', 'steps': steps,
        'automation_status': 'ready', 'verification_status': 'unverified',
        'recording_source': 'platform-playwright-recorder', 'recording_actions': len(steps),
        'recording_warnings': warnings,
        'notes': '\n'.join(['录制不代表测试通过；请补充预期结果与断言，复核步骤后执行。', *warnings]),
    }
    validate_config('ui_case', config)
    with transaction.atomic():
        item = Entity.objects.create(project=project, kind='ui_case', name=name[:150], config=config, created_by=user)
        save_revision(item, user)
        audit(user, project, 'ui.recording_finish', item.pk)
    return item


def finish_recording(user, session_id, case_name=''):
    """Finalize the capture, create its first version and always discard raw recording files."""
    with _lock:
        session = _get_session(user, session_id)
        try:
            session['channel'].send('stop')
            if _receive(session).get('status') != 'stopped':
                raise ValueError('录制尚未结束，请重试')
            return _create_case(Project.objects.get(pk=session['project_id']), user, session['directory'].name,
                                case_name.strip() or f'Web 录制用例 {session_id[:8]}')
        finally:
            _discard_session()


def cancel_recording(user, session_id):
    """Stop only the caller's own session without saving a case."""
    with _lock:
        session = _get_session(user, session_id)
        project = Project.objects.get(pk=session['project_id'])
        _discard_session()
        audit(user, project, 'ui.recording_cancel', session_id)
