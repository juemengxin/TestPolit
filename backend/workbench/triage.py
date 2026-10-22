"""Evidence-based, local-rule-only test failure triage."""

import re
from django.utils import timezone

CATEGORIES = {
    'environment': ('环境 / 连接', '检查 Worker 网络、目标白名单、服务和设备连接；环境恢复后重新执行。'),
    'authentication': ('鉴权 / 权限', '核对测试账号权限、凭据引用和登录状态，勿将真实密钥写入用例。'),
    'configuration': ('用例配置', '核对依赖变量、断言类型、步骤及定位器；确认问题后使用 AI 失败分析检查修复方案。'),
    'assertion_difference': ('断言差异', '对照需求版本、预期和实际结果，区分产品问题与用例问题，不要直接放宽断言。'),
    'unknown': ('待确认', '证据不足，请查看逐步日志、截图和 Trace，必要时进行真实模型失败分析。'),
}
SIGNALS = {
    'connection_failure': r'ConnectError|ConnectionRefused|无法连接|连接被拒绝|DNS|目标不在|白名单',
    'request_timeout': r'请求目标服务超时|ConnectTimeout|ReadTimeout|网络.*超时',
    'missing_variable': r'缺少依赖变量|缺少服务端环境密钥|变量.*未定义',
    'invalid_assertion': r'未配置断言|断言字段或预期类型无效',
    'locator_issue': r'strict mode violation|selector.*invalid|定位器.*无效|元素.*未找到|无法唯一匹配.*元素字段|刷新元素列表并选择唯一',
    # Mobile executors may fail before the first screen is captured; retain the
    # actionable device/USB diagnosis instead of labeling it as missing evidence.
    'device_issue': r'设备.*(?:未连接|不可用|断开)|设备操作失败|USB\s*授权|device.*(?:not found|disconnected|unavailable)',
}


def failure_evidence(case):
    """Reduce private diagnostics to fixed signals, avoiding arbitrary text transmission."""
    diagnostics = str(case.get('detail', '')) + ' '.join(str(step.get('error', '')) for step in case.get('steps', []))
    diagnostics += ' '.join(str(check.get('detail', '')) for check in case.get('assertions', []))
    evidence = [name for name, pattern in SIGNALS.items() if re.search(pattern, diagnostics, re.I)]
    checks = case.get('assertions', [])
    if any(check.get('passed') is False for check in checks):
        evidence.append('assertion_failed')
    if any(step.get('status') == 'failed' and str(step.get('action', '')).startswith('assert') for step in case.get('steps', [])):
        evidence.append('assertion_failed')
    code = case.get('response', {}).get('status_code')
    if type(code) is not int or not 100 <= code <= 599:
        code = None
    return {'signals': sorted(set(evidence)), 'status_code': code}


def _rule_category(evidence):
    signals = set(evidence['signals'])
    if signals & {'connection_failure', 'request_timeout', 'device_issue'}:
        return 'environment'
    if evidence['status_code'] in (401, 403):
        return 'authentication'
    if signals & {'missing_variable', 'invalid_assertion', 'locator_issue'}:
        return 'configuration'
    if 'assertion_failed' in signals:
        return 'assertion_difference'
    return 'unknown'


def triage_result(result, mode):
    """Produce persistent advisory routing without relabeling passed/failed outcomes."""
    if mode != 'live':
        return {'version': 1, 'status': 'simulation', 'service_status': 'not_called', 'items': [],
                'notice': '模拟执行不进行真实失败分诊。'}
    rows = []
    for case in result.get('cases', []):
        if case.get('status') != 'failed':
            continue
        evidence = failure_evidence(case)
        rows.append({'case_id': case.get('id'), 'name': str(case.get('name', ''))[:200],
                     'evidence': evidence, 'category': _rule_category(evidence),
                     'source': 'local_rules', 'confidence': None, 'needs_review': True})
    for row in rows:
        row['label'], row['recommendation'] = CATEGORIES[row['category']]
    return {'version': 1, 'status': 'completed', 'service_status': 'local_rules',
            'created_at': timezone.now().isoformat(), 'items': rows, 'inference': None,
            'notice': '分诊仅使用本地规则，根据固定信号提供处理建议；不调用外部模型，不修改用例、断言或执行结果。'}
