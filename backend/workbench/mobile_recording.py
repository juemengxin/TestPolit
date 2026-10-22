"""Persistent, serialized App recording actions executed by the background worker."""
import hashlib
import json
import os
import re
import time

from cryptography.fernet import Fernet
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from math import isfinite
from rest_framework.exceptions import ValidationError

from .access import require_write
from .entities import save_revision
from .executors import validate_config
from .mobile_tools import (MobileRecordingError, call_mobile, capture_screen, connected_devices, response_text,
                           screen_size, assert_app_text, resolve_screen_element, resolve_screen_point)
from .models import AppRecording, Entity, Job
from .security import audit


def cipher():
    return Fernet(os.environ['CREDENTIAL_KEY'].encode())


def device_key(resource, device):
    return hashlib.sha256(f'{resource.endpoint.rstrip("/")}:{device}'.encode()).hexdigest()


def config_for(session, steps):
    return {'platform': 'app', 'executor': 'mobile-mcp', 'device_id': session.device_id,
            'package_name': session.package_name, 'mcp_resource_id': session.resource_id, 'steps': steps}


def recover_session(session):
    """A dead worker must never cause a device gesture to be replayed automatically."""
    if session.status == 'busy' and session.last_job and session.last_job.status in ('failed', 'cancelled'):
        AppRecording.objects.filter(pk=session.pk, status='busy').update(
            status='error', pending_encrypted='', error='后台任务中断；操作可能已执行，请刷新画面后继续')
        session.refresh_from_db()
    return session


def queue_action(session, command):
    """Compare-and-swap, not select_for_update, also serializes SQLite requests."""
    recover_session(session)
    action = command.get('action')
    if action not in ('start', 'refresh'):
        if session.status != 'ready':
            raise ValidationError('请等待当前操作完成；失败后先刷新画面')
        if len(session.steps) >= 100:
            raise ValidationError('已达到执行器 100 步上限，请保存当前录制并新建用例')
        if not session.steps or session.steps[0]['action'] != 'launch_app':
            raise ValidationError('应用尚未成功启动，请重新启动应用')
    if action == 'tap_field':
        locator = command.get('locator')
        allowed = ('resource_id', 'content_desc', 'text')
        if not isinstance(locator, dict) or set(locator) - set(allowed) or any(not isinstance(value, str) or len(value) > 500 for value in locator.values()):
            raise ValidationError('请选择包含资源 ID、无障碍描述或文本的页面元素')
        command['locator'] = {key: value.strip() for key, value in locator.items() if value.strip()}
        if not command['locator']:
            raise ValidationError('请选择包含资源 ID、无障碍描述或文本的页面元素')
    if action == 'tap_screen':
        x, y, version = command.get('x'), command.get('y'), command.get('screen_version')
        if (type(x) not in (int, float) or type(y) not in (int, float) or not isfinite(x) or not isfinite(y)
                or not (0 <= x < session.screen_width and 0 <= y < session.screen_height)):
            raise ValidationError('点击位置超出设备画面，请刷新后重试')
        if type(version) is not int or version != session.screen_version:
            raise ValidationError('设备画面已更新，请在当前画面重新点击')
    if action in ('type_text', 'assert_text', 'swipe', 'press_button', 'wait_seconds', 'tap_field'):
        try:
            validate_config('ui_case', config_for(session, [command]))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if action == 'wait_seconds' and command['value'] > 30:
            raise ValidationError('录制等待时间最多 30 秒')
    elif action not in ('start', 'refresh', 'tap_field', 'tap_screen'):
        raise ValidationError('不支持此录制操作')
    if action == 'type_text' and command.get('sensitive', True):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,49}', command.get('variable', 'recorded_secret')):
            raise ValidationError('敏感输入变量名须为字母开头，最多 50 个字母、数字或下划线')
    encrypted = cipher().encrypt(json.dumps(command, ensure_ascii=False).encode()).decode()
    with transaction.atomic():
        changed = AppRecording.objects.filter(pk=session.pk, status__in=['ready', 'error'], last_job_id=session.last_job_id).update(
            status='busy', pending_encrypted=encrypted, error='', updated_at=timezone.now())
        if not changed:
            raise ValidationError('当前会话正在操作或已结束，请刷新后重试')
        job = Job.objects.create(project=session.project, kind='app_record', name='App 录制 · '+action,
                                 mode='live', payload={'recording_id': session.pk}, created_by=session.created_by)
        AppRecording.objects.filter(pk=session.pk).update(last_job=job)
    session.refresh_from_db()
    return session


def start_session(user, project, resource, device, package):
    """Reserve a device before enqueuing the first real operation."""
    if not isinstance(device, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', device):
        raise ValidationError('设备 ID 无效')
    if not isinstance(package, str) or len(package) > 200 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', package):
        raise ValidationError('请输入有效 Android 应用包名，例如 com.example.app')
    try:
        with transaction.atomic():
            session = AppRecording.objects.create(project=project, resource=resource, device_id=device,
                package_name=package, created_by=user, device_lock=device_key(resource, device))
            queue_action(session, {'action': 'start'})
    except IntegrityError as exc:
        raise ValidationError('此设备已有录制会话，请先结束或取消原会话') from exc
    audit(user, project, 'app.recording_start', session.pk)
    return session


def recorded_step(command):
    """Persist only executable fields; private text is replaced by a runtime variable."""
    step = {key: command[key] for key in ('action', 'value', 'locator', 'submit') if key in command}
    if step['action'] == 'type_text' and command.get('sensitive', True):
        step['value'] = '{{' + command.get('variable', 'recorded_secret') + '}}'
    return step


def execute_recording(job, record):
    session = AppRecording.objects.select_related('resource', 'project', 'created_by').get(pk=job.payload['recording_id'])
    if session.last_job_id != job.pk or session.status != 'busy':
        raise MobileRecordingError('录制任务已失效，未操作设备')
    try:
        require_write(session.created_by, session.project)
        if not session.created_by.is_active or not session.resource.enabled or session.resource.mode != 'live' or session.resource.project_id != session.project_id:
            raise MobileRecordingError('录制权限或真实 Mobile MCP 配置已失效')
        command = json.loads(cipher().decrypt(session.pending_encrypted.encode()))
        action = command['action']
        record('app_record', '正在操作真实 Android 设备：'+action, 20)
        resource, device = session.resource, session.device_id
        step = None
        if action == 'start':
            devices = connected_devices(resource)
            found = next((d for d in devices if d['id'] == device), None)
            if not found:
                raise MobileRecordingError('所选 Android 设备已断开，请检查 USB 调试授权')
            session.device_name = found['name']
            call_mobile(resource, 'mobile_launch_app', {'device': device, 'packageName': session.package_name})
            if not session.steps:
                step = {'action': 'launch_app'}
        elif action == 'tap_field':
            current = response_text(call_mobile(resource, 'mobile_list_elements_on_screen', {'device': device, 'format': 'text'}))
            ref = resolve_screen_element(current, command['locator'])
            call_mobile(resource, 'mobile_click_on_screen_at_coordinates', {'device': device, 'ref': ref})
            step = recorded_step(command)
        elif action == 'tap_screen':
            current = response_text(call_mobile(resource, 'mobile_list_elements_on_screen', {'device': device, 'format': 'json'}))
            element = resolve_screen_point(current, command['x'], command['y'])
            call_mobile(resource, 'mobile_click_on_screen_at_coordinates', {'device': device, 'ref': element['ref']})
            step = {'action': 'tap_field', 'locator': element['locator']}
        elif action == 'type_text':
            call_mobile(resource, 'mobile_type_keys', {'device': device, 'text': command['value'], 'submit': bool(command.get('submit', False))})
            step = recorded_step(command)
        elif action in ('swipe', 'press_button'):
            tool, key = ('mobile_swipe_on_screen', 'direction') if action == 'swipe' else ('mobile_press_button', 'button')
            call_mobile(resource, tool, {'device': device, key: command['value']})
            step = recorded_step(command)
        elif action == 'wait_seconds':
            time.sleep(command['value'])
            step = recorded_step(command)
        elif action == 'assert_text':
            observed = response_text(call_mobile(resource, 'mobile_list_elements_on_screen', {'device': device, 'format': 'text'}))
            assert_app_text(observed, session.package_name, command['value'])
            step = recorded_step(command)
        # Commit successful gestures BEFORE taking a screenshot: a failed capture must not lose them.
        if step:
            session.steps = [*session.steps, step]
        session.pending_encrypted = ''
        session.save(update_fields=['steps', 'device_name', 'pending_encrypted', 'updated_at'])
        image, extension = capture_screen(resource, device)
        width, height = screen_size(resource, device)
        previous = session.screenshot.name
        session.screenshot.save('app-screen.'+extension, ContentFile(image), save=False)
        session.screen_width, session.screen_height = width, height
        session.screen_version += 1
        session.status, session.error = 'ready', ''
        session.save()
        if previous:
            session.screenshot.storage.delete(previous)
        return {'recording_id': session.pk, 'count': len(session.steps), 'screen_version': session.screen_version}
    except Exception as exc:
        message = str(exc) if isinstance(exc, MobileRecordingError) else '设备连接或操作失败；操作可能已生效，请检查 Mobile MCP、白名单及 USB 授权，刷新画面确认后继续'
        AppRecording.objects.filter(pk=session.pk, last_job=job).update(status='error', error=message[:300], pending_encrypted='', updated_at=timezone.now())
        raise MobileRecordingError(message) from exc


def finish_session(session, user, name):
    """Save once, retaining the recording origin and marking execution unverified."""
    recover_session(session)
    if session.status == 'saved' and session.case_id:
        return session.case
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 150:
        raise ValidationError('请填写 1–150 个字符的用例名称')
    if not session.steps:
        raise ValidationError('尚未录制成功的步骤')
    with transaction.atomic():
        if not AppRecording.objects.filter(pk=session.pk, status__in=['ready', 'error']).update(status='saved', device_lock=None):
            raise ValidationError('请等待当前操作完成，或会话已结束')
        config = {**config_for(session, session.steps), 'automation_status': 'ready', 'verification_status': 'unverified',
                  'recording_source': 'mobile-mcp', 'recording_id': session.pk,
                  'notes': '通过 Android 页面元素字段录制；执行时按资源 ID、无障碍描述或文本重新定位。请补充断言、等待与敏感输入变量后执行。'}
        validate_config('ui_case', config)
        case = Entity.objects.create(project=session.project, kind='ui_case', name=name.strip(), config=config, created_by=user)
        save_revision(case, user)
        AppRecording.objects.filter(pk=session.pk).update(case=case, pending_encrypted='')
        audit(user, session.project, 'app.recording_saved', case.pk)
    return case
