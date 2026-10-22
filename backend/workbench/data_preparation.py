"""Build deterministic test payloads without executing SQL or resolving secrets."""

import json
import math
import re
import random
import secrets
import string

from .sample_generators import sample_value, validate_example


IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,62}\Z')
SECRET_FIELD = re.compile(r'password|secret|token|authorization|cookie|api.?key|^key$', re.I)
SECRET_REFERENCE = re.compile(r'\{\{secret:[A-Za-z0-9_]+\}\}\Z')
FIELD_TYPES = {'string', 'integer', 'number', 'boolean', 'null', 'json'}


def _invalid_constant(value):
    raise ValueError(f'JSON 不支持 {value}')


def _check_json_values(value, depth=0):
    if depth > 30:
        raise ValueError('JSON 嵌套最多 30 层')
    if isinstance(value, str):
        try:value.encode('utf-8')
        except UnicodeError as exc:raise ValueError('字段包含无效 Unicode 字符') from exc
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('JSON 数值必须为有限数值')
    elif type(value) is int and abs(value) > 9007199254740991:
        raise ValueError('JSON 整数超出安全范围；大编号请使用字符串')
    elif isinstance(value, dict):
        for name, item in value.items():
            _check_json_values(name, depth+1)
            _check_json_values(item, depth+1)
    elif isinstance(value, list):
        for item in value:_check_json_values(item, depth+1)


def _check_secrets(value):
    """Apply the existing configuration credential policy to generated objects too."""
    if isinstance(value, dict):
        for name, item in value.items():
            if SECRET_FIELD.search(name) and item not in ('', None):
                if not isinstance(item, str) or not SECRET_REFERENCE.fullmatch(item):
                    raise ValueError(f'敏感字段 {name} 请使用 {{{{secret:NAME}}}} 引用，不填写明文凭据')
            _check_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _check_secrets(item)


def _field_value(field):
    kind = field.get('type', 'string')
    if not isinstance(kind, str) or kind not in FIELD_TYPES:
        raise ValueError('字段类型仅支持 string、integer、number、boolean、null、json')
    raw = field.get('value', '')
    if not isinstance(raw, str) or len(raw) > 20000:
        raise ValueError('字段值须为不超过 20,000 字符的文本')
    if kind == 'null':
        return None
    if kind == 'string':
        return raw
    value = raw.strip()
    if kind == 'integer':
        if not re.fullmatch(r'-?[0-9]{1,16}', value):
            raise ValueError('整数格式不正确；大编号请使用字符串类型')
        result = int(value)
        if abs(result) > 9007199254740991:
            raise ValueError('整数超出 JSON 安全整数范围；大编号请使用字符串类型')
        return result
    if kind == 'number':
        if len(value) > 64:
            raise ValueError('小数格式不正确')
        try:
            result = float(value)
        except ValueError as exc:
            raise ValueError('小数格式不正确') from exc
        if not math.isfinite(result):
            raise ValueError('小数必须是有限数值，不能为 NaN 或 Infinity')
        return result
    if kind == 'boolean':
        if value not in ('true', 'false'):
            raise ValueError('布尔值须为 true 或 false')
        return value == 'true'
    try:
        result = json.loads(raw, parse_constant=_invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise ValueError('JSON 字段须填写有效 JSON 对象或数组') from exc
    if not isinstance(result, (dict, list)):
        raise ValueError('JSON 字段须填写对象或数组；标量请使用对应字段类型')
    return result


def _random_options(field):
    """Validate bounded generators before allocating a batch of rows."""
    mode = field.get('random', False)
    if not isinstance(mode, bool):
        raise ValueError('随机配置须为布尔值')
    if not mode:
        return {}
    kind = field.get('type', 'string')
    if kind not in ('string', 'integer', 'number', 'boolean'):
        raise ValueError('随机生成仅支持字符串、整数、小数和布尔值')
    if SECRET_FIELD.search(field['name']):
        raise ValueError('敏感字段不可随机生成，请使用服务端密钥引用')
    random_mode = field.get('random_mode', 'rules')
    if random_mode not in ('example', 'rules'):
        raise ValueError('随机方式无效')
    if random_mode == 'example':
        validate_example(field)
        _field_value(field)
        return {'random_mode': 'example'}
    if kind == 'string':
        length = field.get('length', 12)
        if type(length) is not int or not 1 <= length <= 256:
            raise ValueError('随机字符串长度须为 1–256 的整数')
        return {'length': length}
    if kind == 'boolean':
        return {}
    lower, upper = field.get('min', 0), field.get('max', 100)
    for value in (lower, upper):
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1_000_000_000:
            raise ValueError('随机数范围须为 ±1,000,000,000 以内的有限数值')
        if kind == 'integer' and type(value) is not int:
            raise ValueError('随机整数上下限须为整数')
    if lower > upper:
        raise ValueError('随机数最小值不能大于最大值')
    return {'min': lower, 'max': upper}


def _random_value(field, generator):
    kind = field['type']
    if field.get('random_mode') == 'example':
        return sample_value(field, generator)
    if kind == 'string':
        return ''.join(generator.choices(string.ascii_letters + string.digits, k=field['length']))
    if kind == 'boolean':
        return bool(generator.getrandbits(1))
    if kind == 'integer':
        return generator.randint(field['min'], field['max'])
    # Keep fractional ranges intact instead of rounding beyond their bounds.
    return generator.uniform(field['min'], field['max'])


def _sql_literal(value, dialect):
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, (int, float)):
        return json.dumps(value, allow_nan=False)
    text = json.dumps(value, ensure_ascii=False, allow_nan=False) if isinstance(value, (dict, list)) else value
    if '\x00' in text:
        raise ValueError('SQL 文本不能包含 NUL 字符')
    if dialect == 'mysql':
        # Hex UTF-8 literals are independent of MySQL NO_BACKSLASH_ESCAPES mode.
        return f"CONVERT(X'{text.encode('utf-8').hex()}' USING utf8mb4)"
    escaped = text.replace("'", "''")
    if dialect == 'postgresql':
        return "E'" + escaped.replace('\\', '\\\\') + "'"
    return "'" + escaped + "'"


def prepare_data(config):
    """Normalize inputs and rebuild all derived values on every preview/save/export."""
    if not isinstance(config, dict):
        raise ValueError('配置必须为对象')
    output_format = config.get('output_format', 'json')
    dialect = config.get('dialect', 'sqlite')
    shape = config.get('json_shape', 'object')
    if output_format not in ('json', 'sql') or dialect not in ('sqlite', 'mysql', 'postgresql') or shape not in ('object', 'array'):
        raise ValueError('输出格式、SQL 方言或 JSON 结构无效')
    fields = config.get('fields', [])
    if not isinstance(fields, list) or not 1 <= len(fields) <= 200:
        raise ValueError('请填写 1–200 个字段')
    count = config.get('count', 1)
    if type(count) is not int or not 1 <= count <= 1000:
        raise ValueError('生成数量须为 1–1000 的整数')
    seed = config.get('random_seed', secrets.token_hex(16))
    if not isinstance(seed, str) or not re.fullmatch(r'[0-9a-f]{32}', seed):
        raise ValueError('随机批次标识无效')
    if not any(isinstance(field, dict) and field.get('random') is True for field in fields):
        seed = '0' * 32
    generator = random.Random(seed)
    variables = {}
    normalized = []
    for index, field in enumerate(fields, 1):
        if not isinstance(field, dict):
            raise ValueError(f'第 {index} 个字段格式无效')
        name = field.get('name', '')
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError(f'第 {index} 个字段名称须为 1–128 字符')
        name = name.strip()
        if name in variables:
            raise ValueError(f'字段名称重复：{name}')
        try:
            options = _random_options({**field, 'name': name})
            variables[name] = None if field.get('random', False) else _field_value(field)
        except ValueError as exc:
            raise ValueError(f'字段 {name}：{exc}') from exc
        normalized.append({'name': name, 'type': field.get('type', 'string'),
                           'value': '' if field.get('type') == 'null' or (field.get('random', False) and options.get('random_mode') != 'example') else field.get('value', ''),
                           'random': field.get('random', False), **options})
    estimated_row_size = sum(len(json.dumps(field['name'])) +
                             ((len(field['value']) * 6 + 32 if field.get('random_mode') == 'example' else field.get('length', 32)) if field['random'] else len(json.dumps(variables[field['name']]))) + 8
                             for field in normalized)
    if estimated_row_size * count > 80000:
        raise ValueError('生成结果过大，请减少生成数量、字段或字段长度')
    rows = []
    for _ in range(count):
        rows.append({field['name']: _random_value(field, generator) if field['random'] else variables[field['name']]
                     for field in normalized})
    variables = rows[0]
    if count > 1:
        shape = 'array'
    try:
        _check_json_values(variables)
        _check_secrets(variables)
    except RecursionError as exc:
        raise ValueError('JSON 嵌套过深') from exc
    table = config.get('table_name', '')
    if not isinstance(table, str) or len(table) > 63:
        raise ValueError('表名最多 63 字符')
    table = table.strip()
    sql = ''
    if output_format == 'sql':
        if not IDENTIFIER.fullmatch(table) or any(not IDENTIFIER.fullmatch(name) for name in variables):
            raise ValueError('SQL 表名和字段名须以英文字母或下划线开头，仅含字母、数字、下划线，最多 63 字符')
        if dialect in ('sqlite','mysql') and len({name.lower() for name in variables})!=len(variables):
            raise ValueError('SQLite / MySQL 字段名不能仅大小写不同')
        quote = '`' if dialect == 'mysql' else '"'
        columns = ', '.join(f'{quote}{name}{quote}' for name in variables)
        statements = []
        for row in rows:
            values = ', '.join(_sql_literal(value, dialect) for value in row.values())
            statements.append(f'INSERT INTO {quote}{table}{quote} ({columns}) VALUES ({values});')
        sql = '\n'.join(statements)
    try:
        result = {
            'count': count, 'random_seed': seed, 'fields': normalized, 'output_format': output_format, 'dialect': dialect,
            'json_shape': shape, 'table_name': table, 'variables': variables,
            'generated_json': json.dumps(rows if shape == 'array' else variables, ensure_ascii=False, indent=2, allow_nan=False),
            'generated_sql': sql,
        }
        if len(json.dumps(result, allow_nan=False)) > 200000:
            raise ValueError('生成结果过大，请减少字段或缩短字段值')
    except (RecursionError, OverflowError) as exc:
        raise ValueError('JSON 嵌套过深或数值过大') from exc
    return result
