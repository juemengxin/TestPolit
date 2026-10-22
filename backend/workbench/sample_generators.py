"""Generate synthetic values from explicit examples without model or network calls."""

import calendar
import re
import string
from uuid import UUID


CHINESE_CHARACTERS = '明华文安宁嘉欣晨宇思雨林清志远乐天小晓美丽芳静敏伟强平海云'
SURNAMES = '赵钱孙李周吴郑王冯陈杨刘张黄林徐朱马胡郭何高罗梁宋唐许韩曹邓'


def validate_example(field):
    """Only accept bounded, unambiguous patterns that can retain their type."""
    example = field.get('value', '')
    if not isinstance(example, str) or not example.strip() or len(example) > 256:
        raise ValueError('按示例随机时请填写 1–256 字符的示例数据')
    if '{{secret:' in example:
        raise ValueError('密钥引用不可作为随机示例')
    if field.get('type', 'string') == 'number' and not re.fullmatch(r'-?\d+(?:\.\d{1,12})?', example.strip()):
        raise ValueError('小数示例须为普通十进制数字，最多 12 位小数')
    return example


def _digits(example, generator, nonzero_first=False):
    return ''.join(generator.choice('123456789' if nonzero_first and index == 0 else string.digits)
                   for index in range(len(example)))


def sample_value(field, generator):
    """Keep structural characters and recognizable formats, varying only data parts."""
    example, kind = field['value'], field['type']
    if kind == 'boolean':
        return bool(generator.getrandbits(1))
    if kind in ('integer', 'number'):
        text = example.strip()
        sign = '-' if text.startswith('-') else ''
        whole, dot, fraction = text.lstrip('-').partition('.')
        whole = _digits(whole, generator, nonzero_first=len(whole) > 1)
        result = sign + whole + (dot + _digits(fraction, generator) if dot else '')
        # Validation already enforces JSON-safe examples; preserve that bound too.
        if kind == 'integer':
            return min(max(int(result), -9007199254740991), 9007199254740991)
        return float(result)
    if re.fullmatch(r'1[3-9]\d{9}', example):
        return example[:3] + _digits(example[3:], generator)
    email = re.fullmatch(r'([^@\s]+)@([^@\s]+\.[^@\s]+)', example)
    if email:
        return _pattern(email[1], generator) + '@' + email[2]
    date = re.fullmatch(r'(\d{4})([-/])(\d{2})\2(\d{2})(?:([ T])(\d{2}):(\d{2}):(\d{2}))?', example)
    if date:
        from datetime import datetime
        try:
            datetime(int(date[1]), int(date[3]), int(date[4]), int(date[6] or 0), int(date[7] or 0), int(date[8] or 0))
        except ValueError as exc:
            raise ValueError('日期示例不是有效日期或时间') from exc
        year, month = generator.randint(2000, 2035), generator.randint(1, 12)
        day = generator.randint(1, calendar.monthrange(year, month)[1])
        result = f'{year:04d}{date[2]}{month:02d}{date[2]}{day:02d}'
        if date[5]:
            result += f'{date[5]}{generator.randrange(24):02d}:{generator.randrange(60):02d}:{generator.randrange(60):02d}'
        return result
    if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', example):
        result = str(UUID(int=generator.getrandbits(128), version=4))
        return result.upper() if example == example.upper() else result
    if re.search(r'name|姓名', field['name'], re.I) and re.fullmatch(r'[\u4e00-\u9fff]{2,4}', example):
        return generator.choice(SURNAMES) + ''.join(generator.choices(CHINESE_CHARACTERS, k=len(example)-1))
    return _pattern(example, generator)


def _pattern(example, generator):
    """Preserve identifier prefixes before digits, punctuation and character classes."""
    first_digit = next((index for index, char in enumerate(example) if char in string.digits), None)
    prefix = example[:first_digit] if first_digit is not None else ''
    result = prefix
    for char in example[len(prefix):]:
        if char in string.digits:
            result += generator.choice(string.digits)
        elif char in string.ascii_lowercase:
            result += generator.choice(string.ascii_lowercase)
        elif char in string.ascii_uppercase:
            result += generator.choice(string.ascii_uppercase)
        elif '\u4e00' <= char <= '\u9fff':
            result += generator.choice(CHINESE_CHARACTERS)
        else:
            result += char
    return result
