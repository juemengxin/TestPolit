"""Compile natural-language UI designs into the platform's declarative steps."""

from __future__ import annotations

import re
from typing import Any


QUOTED_TEXT_PATTERN = re.compile(r'[“"「『](.+?)[”"」』]')
VARIABLE_PATTERN = re.compile(r'\{\{\s*([\w.-]+)\s*\}\}')
STEP_NUMBER_PATTERN = re.compile(r'^\d+[.、)）]\s*')


def _split_sentences(value: Any) -> list[str]:
    """Split a design action into executable clauses while preserving decimals."""
    text = str(value or '').replace('\r', '\n')
    return [
        clause.strip(' \t-•')
        for clause in re.split(r'\n+|(?<!\d)[；;](?!\d)', text)
        if clause.strip(' \t-•')
    ]


def _clean_step_text(sentence: str) -> str:
    """Remove a leading ordered-list marker before matching the action wording."""
    return STEP_NUMBER_PATTERN.sub('', sentence).strip()


def _infer_app_input_variable(sentence: str) -> str:
    """Choose the established shared-data key for an unspecified App input."""
    if '密码' in sentence:
        return 'valid_password'
    if '手机号' in sentence or '手机号码' in sentence:
        return 'valid_phone_number'
    if '验证码' in sentence:
        return 'valid_sms_code'
    return 'app_test_input'


def _compile_app_input(sentence: str, unmapped: list[str]) -> dict[str, str]:
    """Map an App input clause and report values that need shared test data."""
    variable_match = VARIABLE_PATTERN.search(sentence)
    quoted_match = QUOTED_TEXT_PATTERN.search(sentence)
    inferred_variable = _infer_app_input_variable(sentence)

    if variable_match:
        value = '{{' + variable_match.group(1).strip() + '}}'
    elif not quoted_match or VARIABLE_PATTERN.fullmatch(quoted_match.group(1).strip()):
        value = '{{' + inferred_variable + '}}'
    else:
        value = quoted_match.group(1)

    if not variable_match and not quoted_match:
        unmapped.append(sentence + f'（需在公共数据补充 {inferred_variable}）')
    return {'action': 'type_text', 'value': value}


def _compile_app_click(sentence: str, unmapped: list[str]) -> dict[str, str]:
    """Map an App tap clause, retaining the existing fallback labels."""
    target_match = QUOTED_TEXT_PATTERN.search(sentence)
    if target_match:
        return {'action': 'tap_text', 'value': target_match.group(1).strip()}
    if re.search(r'返回键', sentence):
        return {'action': 'press_button', 'value': 'BACK'}
    if re.search(r'获取验证码', sentence):
        return {'action': 'tap_text', 'value': '获取验证码'}
    if re.search(r'授权', sentence):
        return {'action': 'tap_text', 'value': '授权'}

    submit_match = re.search(r'(?:点击|单击|选择|按下)(.{1,30})', sentence)
    if re.search(r'提交|确认|完成注册|登录/注册', sentence):
        label = submit_match.group(1).strip('“”"「」 ') if submit_match else sentence[-12:]
        return {'action': 'tap_text', 'value': label}

    unmapped.append(sentence)
    return {'action': 'screenshot'}


def _compile_app_swipe(sentence: str, unmapped: list[str]) -> dict[str, Any]:
    """Translate a directional App swipe or mark an unknown direction for review."""
    direction_patterns = (
        ('up', r'向上|上滑'),
        ('down', r'向下|下滑'),
        ('left', r'向左|左滑'),
        ('right', r'向右|右滑'),
    )
    direction = next(
        (value for value, pattern in direction_patterns if re.search(pattern, sentence)),
        None,
    )
    if direction:
        return {'action': 'swipe', 'value': direction}
    unmapped.append(sentence)
    return {'action': 'screenshot'}


def _compile_app_wait(sentence: str, unmapped: list[str]) -> dict[str, Any]:
    """Convert a natural-language wait to seconds, retaining the 10-minute cap."""
    match = re.search(r'等待.{0,10}(\d+)\s*(秒|分钟)', sentence)
    if not match:
        return {'action': 'screenshot'}

    seconds = int(match.group(1)) * (60 if match.group(2) == '分钟' else 1)
    if seconds > 600:
        unmapped.append(sentence + '（等待时间已限制为 10 分钟）')
    return {'action': 'wait_seconds', 'value': min(seconds, 600)}


def _compile_app_sentence(sentence: str, unmapped: list[str]) -> dict[str, Any]:
    """Apply the existing App-language precedence to one cleaned design clause."""
    if re.search(r'桌面图标|启动(?:应用|app)|进入\s*\{\{\s*程序入口', sentence, re.I):
        return {'action': 'launch_app'}
    if re.search(r'点击|单击|选择|按下|确认授权', sentence):
        return _compile_app_click(sentence, unmapped)
    if re.search(r'输入|填写|键入|设置.{0,8}密码', sentence):
        return _compile_app_input(sentence, unmapped)
    if re.search(r'返回键', sentence):
        return {'action': 'press_button', 'value': 'BACK'}
    if re.search(r'滑动', sentence):
        return _compile_app_swipe(sentence, unmapped)
    if re.search(r'等待.{0,10}(\d+)\s*(秒|分钟)', sentence):
        return _compile_app_wait(sentence, unmapped)
    if re.search(r'进入.{0,12}(找回密码|手机号登录|登录注册)', sentence):
        target_match = re.search(r'(找回密码|手机号登录(?:/注册)?|登录/注册)', sentence)
        return {'action': 'tap_text', 'value': target_match.group(1)}
    if re.search(r'观察|查看|检查', sentence) and QUOTED_TEXT_PATTERN.search(sentence):
        target = QUOTED_TEXT_PATTERN.search(sentence)
        return {'action': 'assert_text', 'value': target.group(1).strip()}

    unmapped.append(sentence)
    return {'action': 'screenshot'}


def _append_expected_assertions(design: dict[str, Any], output: list[dict[str, Any]], *, web: bool) -> None:
    """Add quoted acceptance text once, preserving each platform's step schema."""
    expected_texts = QUOTED_TEXT_PATTERN.findall(str(design.get('expected') or ''))
    for expected_text in expected_texts:
        expected_text = expected_text.strip()
        already_asserted = any(
            step.get('action') == 'assert_text' and step.get('value') == expected_text
            for step in output
        )
        if expected_text and not already_asserted:
            assertion = {'action': 'assert_text', 'value': expected_text}
            if web:
                assertion['selector'] = 'body'
            output.append(assertion)


def compile_app_design_steps(design_steps: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Compile mobile design clauses and return actions plus human-review notes."""
    output: list[dict[str, Any]] = []
    unmapped: list[str] = []

    for design in design_steps if isinstance(design_steps, list) else []:
        if not isinstance(design, dict):
            unmapped.append('设计步骤格式无效')
            continue
        for sentence in _split_sentences(design.get('action')):
            cleaned_sentence = _clean_step_text(sentence)
            if cleaned_sentence:
                output.append(_compile_app_sentence(cleaned_sentence, unmapped))
        _append_expected_assertions(design, output, web=False)

    if output and output[0]['action'] != 'launch_app':
        output.insert(0, {'action': 'launch_app'})
    return output[:100], unmapped[:100]


def compile_app_video_design_steps(designs: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Compile reviewed video-analysis fields without converting gestures into coordinates."""
    output: list[dict[str, Any]] = [{'action': 'launch_app'}]
    unmapped: list[str] = []
    for index, design in enumerate(designs if isinstance(designs, list) else [], 1):
        if not isinstance(design, dict):
            unmapped.append(f'第 {index} 步结构无效')
            continue
        action = design.get('recorded_action')
        step: dict[str, Any] = {'action': action} if action else {}
        if action == 'tap_field':
            locator = design.get('locator', {})
            if isinstance(locator, dict):
                locator = {key: value.strip() for key, value in locator.items()
                           if key in ('resource_id', 'content_desc', 'text') and isinstance(value, str) and value.strip()}
            if not locator:
                unmapped.append(f'第 {index} 步缺少资源 ID、无障碍描述或可见文本定位')
                continue
            step['locator'] = locator
        elif action == 'type_text':
            value = design.get('value')
            if not isinstance(value, str) or not value.startswith('{{') or not value.endswith('}}'):
                unmapped.append(f'第 {index} 步需配置运行时输入变量')
                continue
            step['value'] = value
        elif action == 'swipe' and design.get('value') in ('up', 'down', 'left', 'right'):
            step['value'] = design['value']
        elif action == 'press_button' and design.get('value') in ('BACK', 'HOME', 'ENTER', 'VOLUME_UP', 'VOLUME_DOWN'):
            step['value'] = design['value']
        elif action == 'wait_seconds' and type(design.get('value')) is int and 0 <= design['value'] <= 600:
            step['value'] = design['value']
        elif action == 'assert_text' and isinstance(design.get('value'), str) and design['value'].strip():
            step['value'] = design['value'].strip()[:200]
        else:
            unmapped.append(f'第 {index} 步操作类型或参数仍需补充')
            continue
        if isinstance(design.get('expected'), str) and design['expected'].strip():
            step['expected'] = design['expected'].strip()[:500]
        output.append(step)
    return output[:100], unmapped[:100]


def _web_selector(label: str) -> str:
    """Select the same semantic locator used by the existing Web compiler."""
    label = str(label or '')
    selectors = (
        (('手机号', '手机号码'), 'input[placeholder*="手机号"]:visible'),
        (('验证码',), 'input[placeholder*="验证码"]:visible'),
        (('密码',), 'input[type="password"]:visible'),
        (('协议',), 'input[type="checkbox"]:visible'),
        (('账号', '用户名'), 'input[placeholder*="账号"]:visible'),
    )
    for keywords, selector in selectors:
        if any(keyword in label for keyword in keywords):
            return selector
    return 'body'


def _quoted_input_label(quotes: list[str]) -> str:
    """Find a quoted label that identifies an input control."""
    return next(
        (quote for quote in quotes if any(
            marker in quote for marker in ('输入框', '手机号', '验证码', '密码', '账号')
        )),
        '',
    )


def _compile_web_input(sentence: str, quotes: list[str], variables: list[str], notes: list[str]) -> dict[str, str]:
    """Build a Web fill action and record when its test value is unspecified."""
    label = _quoted_input_label(quotes)
    if variables:
        value = '{{' + variables[-1].strip() + '}}'
    else:
        value = '{{web_test_input}}'
        notes.append(sentence + '（需在公共数据补充 web_test_input）')
    return {'action': 'fill', 'selector': _web_selector(label or sentence), 'value': value}


def _compile_web_click(sentence: str, quotes: list[str], notes: list[str]) -> dict[str, str]:
    """Build a text-based Web click or preserve an unmapped clause as a screenshot."""
    label = quotes[-1] if quotes else ''
    if not label:
        match = re.search(r'(?:点击|单击)(.{1,24}?)(?:按钮|超链接|链接|区域|$)', sentence)
        label = match.group(1).strip(' ：:“”"') if match else ''
    if label:
        return {'action': 'click', 'selector': f'text={label}'}
    notes.append(sentence)
    return {'action': 'screenshot'}


def _compile_web_observation(sentence: str, quotes: list[str], notes: list[str]) -> dict[str, str]:
    """Compile a Web wait/assertion when its visible target is explicit."""
    label = quotes[-1] if quotes else ''
    if label and '输入框' in label:
        return {'action': 'wait_visible', 'selector': _web_selector(label)}
    if label:
        return {'action': 'assert_text', 'selector': 'body', 'value': label}
    notes.append(sentence)
    return {'action': 'screenshot'}


def _compile_web_sentence(sentence: str, notes: list[str]) -> list[dict[str, str]]:
    """Apply the existing Web-language precedence to one cleaned design clause."""
    quotes = QUOTED_TEXT_PATTERN.findall(sentence)
    variables = VARIABLE_PATTERN.findall(sentence)

    if re.search(r'刷新|按\s*F5', sentence, re.I):
        return [{'action': 'press', 'selector': 'body', 'value': 'F5'}]
    if re.search(r'悬浮|hover', sentence, re.I):
        if quotes:
            return [{'action': 'hover', 'selector': f'text={quotes[-1]}'}]
        notes.append(sentence)
        return [{'action': 'screenshot'}]
    if re.search(r'勾选|选中.*复选框', sentence):
        return [{'action': 'check', 'selector': 'input[type="checkbox"]:visible'}]
    if re.search(r'输入|填写|键入', sentence):
        fill_action = _compile_web_input(sentence, quotes, variables, notes)
        if re.search(r'勾选|选中.*复选框', sentence):
            return [fill_action, {'action': 'check', 'selector': 'input[type="checkbox"]:visible'}]
        return [fill_action]
    if re.search(r'点击|单击|尝试点击', sentence):
        return [_compile_web_click(sentence, quotes, notes)]
    if re.search(r'等待|观察|查看|检查|确保', sentence):
        return [_compile_web_observation(sentence, quotes, notes)]
    if re.search(r'进入\s*\{\{|打开登录页|访问.*登录', sentence):
        # The caller's configured landing URL already supplies this navigation.
        return []

    notes.append(sentence)
    return [{'action': 'screenshot'}]


def compile_web_design_steps(design_steps: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Compile Web design clauses into Playwright actions and review notes."""
    output: list[dict[str, Any]] = [{'action': 'goto', 'value': ''}]
    notes: list[str] = []

    for design in design_steps if isinstance(design_steps, list) else []:
        if not isinstance(design, dict):
            notes.append('设计步骤格式无效')
            continue
        for sentence in _split_sentences(design.get('action')):
            cleaned_sentence = _clean_step_text(sentence)
            if cleaned_sentence:
                output.extend(_compile_web_sentence(cleaned_sentence, notes))

        _append_expected_assertions(design, output, web=True)
        expected = str(design.get('expected') or '')
        if expected and not QUOTED_TEXT_PATTERN.search(expected):
            notes.append('预期结果缺少可直接断言的页面文案：' + expected[:120])

    return output[:100], notes[:100]
