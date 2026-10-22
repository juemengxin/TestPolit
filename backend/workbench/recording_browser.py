"""Browser-owned recorder process and bounded manual-action capture."""

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

MAX_STEPS = 100
MAX_DURATION_SECONDS = 30 * 60
def recorded_input_variable(selector, element_label):
    """Keep authentication and personal-input values out of persisted recordings."""
    descriptor = f'{selector} {element_label}'
    if re.search(r'password|secret|token|credential|api.?key|密码|密钥|令牌', descriptor, re.I):
        return 'recorded_secret'
    if re.search(r'username|user.?name|account|login|用户名|账号|登录名', descriptor, re.I):
        return 'recorded_username'
    if re.search(r'email|邮箱', descriptor, re.I):
        return 'recorded_email'
    if re.search(r'phone|mobile|手机号|电话', descriptor, re.I):
        return 'recorded_phone'
    return ''


def blocked_request_warning(request_url):
    """Name the blocked authority without exposing paths, query values, or credentials."""
    try:
        parsed = urlsplit(str(request_url))
        hostname = parsed.hostname or '未知主机'
        port = parsed.port
    except ValueError:
        hostname, port = '无法解析的主机', None
    authority = f'[{hostname}]' if ':' in hostname and not hostname.startswith('[') else hostname
    if port is not None:
        authority = f'{authority}:{port}'
    return f'请求域名 {authority} 不在白名单中，已阻止；请由管理员将此精确主机及端口加入 TESTPILOT_ALLOWED_TARGETS。'


class ActionRecorder:
    """Coalesce typing and reject unsupported input instead of silently dropping steps."""

    def __init__(self, target_url):
        self.steps = [{'action': 'goto', 'value': target_url, 'element_label': '录制起始页面'}]
        self.warnings = []
        self.error = ''

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def append(self, payload):
        if not isinstance(payload, dict) or self.error:
            return
        if payload.get('unsupported'):
            self.warn(str(payload['unsupported'])[:120])
            return
        action, selector = payload.get('action'), payload.get('selector')
        if action not in ('click', 'fill', 'press', 'select', 'check') or not isinstance(selector, str) or not selector:
            self.error = '录制操作格式无效，请重新录制'
            return
        value = str(payload.get('value', ''))
        element_label = str(payload.get('element_label', '')).strip()[:160]
        if len(selector) > 1000 or len(value) > 2000:
            self.error = '定位器或输入值超过执行器支持长度，请拆分录制'
            return
        step = {'action': action, 'selector': selector}
        if element_label:
            step['element_label'] = element_label
        if action != 'click':
            variable = recorded_input_variable(selector, element_label) if action == 'fill' else ''
            step['value'] = '{{' + variable + '}}' if variable else value
        variable_reference = re.fullmatch(r'\{\{(recorded_[A-Za-z0-9_]+)\}\}', step.get('value', ''))
        if variable_reference:
            name = variable_reference.group(1)
            self.warn(f'敏感输入已替换为 {{{{{name}}}}}，执行前请在测试数据中配置')
        previous = self.steps[-1]
        if action == 'fill' and previous['action'] == 'fill' and previous.get('selector') == selector:
            self.steps[-1] = step
        elif len(self.steps) >= MAX_STEPS:
            self.error = '录制超过 100 个步骤，请取消后按业务流程分段录制'
        else:
            self.steps.append(step)

    def result(self):
        return {'steps': self.steps, 'warnings': self.warnings, 'error': self.error}

    def preview(self):
        """Return operation names and element labels without exposing typed values."""
        return [{'action': step['action'], 'selector': step.get('selector', ''),
                 'element_label': step.get('element_label', '')} for step in self.steps]


def run_browser_session(channel, target_url, directory):
    """Keep Playwright's event loop alive between HTTP requests, with a bounded lifetime."""
    import django
    django.setup()
    from playwright.sync_api import sync_playwright
    from .security import allowed_url

    recorder = ActionRecorder(target_url)
    try:
        with sync_playwright() as playwright:
            executable = os.getenv('TESTPILOT_BROWSER_EXECUTABLE', '')
            browser = playwright.chromium.launch(
                headless=False, **({'executable_path': executable} if executable else {}),
                args=['--no-sandbox', '--disable-dev-shm-usage', '--window-size=1440,900'],
            )
            context = browser.new_context(viewport={'width': 1360, 'height': 820}, service_workers='block', accept_downloads=False)
            page = context.new_page()

            def allowlisted_request(route):
                try:
                    allowed_url(route.request.url)
                except ValueError:
                    recorder.warn(blocked_request_warning(route.request.url))
                    route.abort()
                else:
                    route.continue_()

            def capture(source, payload):
                if source['page'] != page or source['frame'] != page.main_frame:
                    recorder.warn('iframe 或新窗口的操作需要手动补充步骤')
                    return
                recorder.append(payload)

            def reject_popup(popup):
                recorder.warn('新窗口已阻止，跨窗口流程需要手动配置')
                popup.close()

            context.route('**/*', allowlisted_request)
            context.route_web_socket('**/*', lambda socket: socket.close())
            context.expose_binding('__testpilotRecord', capture)
            context.add_init_script(path=str(Path(__file__).with_name('recording_events.js')))
            context.on('page', reject_popup)
            context.tracing.start(screenshots=True, snapshots=True, sources=False)
            page.goto(target_url, wait_until='domcontentloaded', timeout=30000)
            channel.send({'status': 'ready'})
            deadline = time.monotonic() + MAX_DURATION_SECONDS
            while time.monotonic() < deadline and not page.is_closed():
                # This call dispatches routing and binding callbacks while the user operates via VNC.
                page.wait_for_timeout(100)
                if not channel.poll():
                    continue
                command = channel.recv()
                if command == 'status':
                    channel.send({'status': 'recording', 'count': len(recorder.steps), 'steps': recorder.preview(),
                                  'warnings': recorder.warnings, 'error': recorder.error})
                elif command == 'cancel':
                    break
                elif command == 'stop':
                    page.wait_for_timeout(200)
                    context.tracing.stop(path=str(Path(directory) / 'trace.zip'))
                    Path(directory, 'actions.json').write_text(json.dumps(recorder.result(), ensure_ascii=False), encoding='utf-8')
                    channel.send({'status': 'stopped'})
                    break
            else:
                channel.send({'status': 'error', 'error': '录制浏览器已关闭或超过 30 分钟，请重新开始'})
            context.close()
            browser.close()
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            channel.send({'status': 'error', 'error': f'录制浏览器异常：{type(exc).__name__}'})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        channel.close()
