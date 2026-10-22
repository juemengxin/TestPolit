"""Narrow Mobile MCP adapter shared by recording and remote screenshot capture."""
import base64
import json
import re



class MobileRecordingError(ValueError):
    """An actionable message safe to display without remote response contents."""


def call_mobile(resource, name, arguments, *, images=False):
    """Some MCP versions report actionable failures as text without isError."""
    from .executors import mcp_request
    response = mcp_request(resource, 'tools/call', {'name': name, 'arguments': arguments}, include_images=images)
    text = '\n'.join(part.get('text', '') for part in response.get('content', []) if part.get('type') == 'text')
    if response.get('isError') or 'Please fix the issue and try again' in text or text.startswith('Error:'):
        raise MobileRecordingError('设备操作失败，请检查手机是否解锁、USB 授权和 Mobile MCP 服务；刷新画面确认实际状态')
    return response


def response_text(response):
    return '\n'.join(part.get('text', '') for part in response.get('content', []) if part.get('type') == 'text')


def parse_screen_elements(source):
    """Parse Mobile MCP text or JSON elements into safe locators and hit-test bounds."""
    raw = source.strip()
    marker = 'Found these elements on screen:'
    if marker in raw:
        raw = raw.split(marker, 1)[1].strip()

    records = None
    try:
        records = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        start = min((index for index in (raw.find('['), raw.find('{')) if index >= 0), default=-1)
        if start >= 0:
            try:
                records, _ = json.JSONDecoder().raw_decode(raw[start:])
            except json.JSONDecodeError:
                records = None
    if isinstance(records, dict):
        records = records.get('elements', records.get('items', []))

    parsed = []
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            coords = record.get('coordinates', record.get('rect', {}))
            if not isinstance(coords, dict):
                coords = {}
            item = {
                'ref': record.get('ref', ''),
                'text': record.get('text') or record.get('value') or '',
                'resource_id': record.get('identifier') or record.get('id') or '',
                'content_desc': record.get('label') or record.get('name') or record.get('content_desc') or '',
                'class_name': record.get('type') or record.get('class') or '',
                'x': coords.get('x'), 'y': coords.get('y'),
                'width': coords.get('width'), 'height': coords.get('height'),
            }
            parsed.append(item)
    else:
        for line in source.splitlines():
            ref = re.search(r'(?<![A-Za-z0-9_])(@e[0-9]+)(?=\s|$)', line)
            if not ref:
                continue
            fields = {key.lower().replace('_', '-'): value for key, value in
                      re.findall(r'([A-Za-z][A-Za-z0-9_-]*)="((?:\\.|[^"])*)"', line)}
            position = re.search(r'\bat=(-?\d+),(-?\d+)\b', line)
            size = re.search(r'\bsize=(\d+)x(\d+)\b', line)
            parsed.append({
                'ref': ref.group(1), 'text': fields.get('text', fields.get('value', '')),
                'resource_id': fields.get('id', fields.get('resource-id', '')),
                'content_desc': fields.get('label', fields.get('content-desc', fields.get('name', ''))),
                'class_name': fields.get('class', ''),
                'x': int(position.group(1)) if position else None,
                'y': int(position.group(2)) if position else None,
                'width': int(size.group(1)) if size else None,
                'height': int(size.group(2)) if size else None,
            })

    elements = []
    for item in parsed:
        if not isinstance(item['ref'], str) or not re.fullmatch(r'@e\d+', item['ref']):
            continue
        if 'password' in (str(item['class_name']) + ' ' + str(item['resource_id'])).lower():
            item['text'] = ''
        item['locator'] = {key: item[key] for key in ('resource_id', 'content_desc', 'text')
                           if isinstance(item[key], str) and item[key].strip()}
        if item['locator']:
            elements.append(item)
    return elements


def resolve_screen_point(source, x, y):
    """Resolve a screen tap to the smallest labeled accessibility element at that point."""
    matches = []
    for item in parse_screen_elements(source):
        bounds = (item.get('x'), item.get('y'), item.get('width'), item.get('height'))
        if any(type(value) not in (int, float) for value in bounds):
            continue
        left, top, width, height = bounds
        if width <= 0 or height <= 0 or not (left <= x <= left + width and top <= y <= top + height):
            continue
        matches.append((width * height, item))
    if not matches:
        raise MobileRecordingError('点击位置未匹配到可识别的页面文字或元素，请点击控件文字区域后重试')
    matches.sort(key=lambda match: match[0])
    return matches[0][1]


def resolve_screen_element(source, locator):
    """Resolve a recorded accessibility locator against the latest UI tree, rejecting ambiguity."""
    locator = {key: value for key, value in locator.items() if isinstance(value, str) and value.strip()}
    if not locator:
        raise MobileRecordingError('页面元素定位字段为空，请填写资源 ID、无障碍描述或文本')
    candidates = [item for item in parse_screen_elements(source)
                  if all(item.get(key, '') == value for key, value in locator.items())]
    if len(candidates) != 1:
        raise MobileRecordingError('当前页面无法唯一匹配所选元素字段，请刷新元素列表并选择唯一的资源 ID、无障碍描述或文本')
    return candidates[0]['ref']


def connected_devices(resource):
    """Only advertise the Android devices supported by the current case executor."""
    response = call_mobile(resource, 'mobile_list_available_devices', {})
    try:
        value = json.loads(response_text(response))
        devices = value['devices'] if isinstance(value, dict) else value
        return [{'id': str(d['id']), 'name': str(d.get('name', d['id']))[:150], 'platform': 'android'}
                for d in devices if d.get('platform') == 'android' and d.get('state', 'online') == 'online']
    except (ValueError, KeyError, TypeError) as exc:
        raise MobileRecordingError('Mobile MCP 未返回可识别的设备列表，请检查服务版本') from exc


def capture_screen(resource, device):
    """Transport the screenshot bytes; host-side paths are inaccessible in Docker."""
    response = call_mobile(resource, 'mobile_take_screenshot', {'device': device, 'maxSize': 1200}, images=True)
    for part in response.get('content', []):
        if part.get('type') != 'image' or part.get('mimeType') not in ('image/png', 'image/jpeg'):
            continue
        try:
            data = base64.b64decode(part['data'], validate=True)
        except (ValueError, KeyError) as exc:
            raise MobileRecordingError('设备截图数据不完整，请刷新画面') from exc
        if part['mimeType'] == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n'):
            return data, 'png'
        if part['mimeType'] == 'image/jpeg' and data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
            return data, 'jpg'
    raise MobileRecordingError('Mobile MCP 未返回有效截图，请检查设备连接')


def screen_size(resource, device):
    text = response_text(call_mobile(resource, 'mobile_get_screen_size', {'device': device}))
    match = re.search(r'(\d+)\s*x\s*(\d+)', text)
    if not match or not all(0 < int(v) <= 20000 for v in match.groups()):
        raise MobileRecordingError('无法获取设备屏幕尺寸，请刷新画面')
    return tuple(map(int, match.groups()))


def assert_app_text(observed, package, expected):
    """Match visible text only beneath Android's own app root, never system UI."""
    lines = observed.splitlines()
    marker = f'id="{package}:id/action_bar_root"'
    app_start = next((index for index, line in enumerate(lines) if marker in line), None)
    if app_start is None or not any(f'text="{expected}"' in line for line in lines[app_start + 1:]):
        raise MobileRecordingError('未在被测 App 页面找到预期文案')
