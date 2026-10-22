import os
import re
import shutil
import subprocess


def probe_machine(config):
    executor = config.get('executor')
    if executor == 'local-playwright-chromium':
        try:
            from playwright.sync_api import sync_playwright
            options = {'headless': True}
            if os.getenv('TESTPILOT_BROWSER_EXECUTABLE'):
                options['executable_path'] = os.environ['TESTPILOT_BROWSER_EXECUTABLE']
            elif os.getenv('TESTPILOT_BROWSER_CHANNEL'):
                options['channel'] = os.environ['TESTPILOT_BROWSER_CHANNEL']
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**options)
                try:
                    version = browser.version
                finally:
                    browser.close()
            return {'connected': True, 'model': f'Chromium {version}', 'device_id': 'local-browser', 'message': '本地 Chromium 启动成功'}
        except Exception:
            return {'connected': False, 'model': 'Chromium', 'device_id': '', 'message': '本地 Chromium 启动失败，请检查 Playwright 浏览器安装及运行权限'}
    if executor == 'local-android-adb':
        adb = shutil.which('adb')
        if not adb:
            return {'connected': False, 'model': '', 'device_id': '', 'message': '未安装 Android Debug Bridge (adb)'}
        serial = config.get('serial', '')
        if not isinstance(serial, str) or (serial and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', serial)):
            raise ValueError('设备序列号格式无效')
        try:
            result = subprocess.run([adb, 'devices', '-l'], capture_output=True, text=True, timeout=8, check=False)
        except (subprocess.TimeoutExpired, OSError):
            return {'connected': False, 'model': '', 'device_id': serial, 'message': 'ADB 连接超时或不可用'}
        if result.returncode:
            return {'connected': False, 'model': '', 'device_id': serial, 'message': 'ADB 服务启动失败，请检查本机权限和 USB 调试'}
        devices = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            details = dict(part.split(':', 1) for part in parts[2:] if ':' in part)
            devices.append({'serial': parts[0], 'state': parts[1], 'model': details.get('model', '')})
        match = [device for device in devices if device['serial'] == serial] if serial else devices
        if not match:
            return {'connected': False, 'model': '', 'device_id': serial, 'message': '未发现本地 Android 设备'}
        if len(match) > 1:
            return {'connected': False, 'model': '', 'device_id': '', 'message': '发现多台设备，请在执行机配置中填写 serial'}
        device = match[0]
        if device['state'] != 'device':
            return {'connected': False, 'model': device['model'], 'device_id': device['serial'], 'message': f'设备状态：{device["state"]}，请检查 USB 调试授权'}
        return {'connected': True, 'model': device['model'] or 'Android（型号未上报）', 'device_id': device['serial'], 'message': 'ADB 设备已连接；当前 UI 执行器仅支持浏览器用例'}
    raise ValueError('仅支持本地 Chromium 或 Android ADB 测试机')
