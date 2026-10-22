"""Small, shared client for project-scoped OpenAI-compatible chat models."""

import json
import os
import re
from urllib.parse import urlsplit

from .model_options import chat_extra_body, require_chat_compatible
from .security import allowed_url, decrypt


def require_verified_model(resource, *, connection_test=False):
    """Prevent live analysis from using a model that has never passed a connection test."""
    if resource.mode != 'live' and not connection_test:
        raise ValueError('所选模型尚未通过真实连接测试；请在模型管理中验证连接后再执行')


def complete_json(resource, system_prompt, user_prompt, *, max_tokens=1200, timeout=35, temperature=0.2):
    """Call an explicitly selected model and parse its JSON-only response."""
    from langchain_openai import ChatOpenAI
    import httpx

    require_chat_compatible(resource)
    allowed_url(resource.endpoint)
    proxy = None
    if resource.endpoint.startswith('https://generativelanguage.googleapis.com/'):
        configured = os.getenv('TESTPILOT_GEMINI_PROXY', '')
        if configured:
            parsed = urlsplit(configured)
            if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'} or not parsed.port or parsed.username or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
                raise ValueError('Gemini 代理仅允许无凭据的本机 HTTP 地址')
            proxy = configured

    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False, proxy=proxy) as http:
        model = ChatOpenAI(
            model=resource.settings.get('model_name') or 'gpt-4o-mini',
            base_url=resource.endpoint,
            api_key=decrypt(resource) or 'no-key',
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=0,
            http_client=http,
            extra_body=chat_extra_body(resource),
        )
        response = model.invoke([('system', system_prompt), ('human', user_prompt)])

    content = response.content if isinstance(response.content, str) else json.dumps(response.content)
    content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content.strip())
    if not content:
        raise ValueError('模型未返回正文；请检查模型输出配置')
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError('模型未返回有效 JSON，请检查模型输出格式') from exc
    if not isinstance(result, dict):
        raise ValueError('模型返回结果必须为 JSON 对象')

    metadata = response.usage_metadata or {}
    usage = {key: metadata.get(key, 0) for key in ('input_tokens', 'output_tokens', 'total_tokens')}
    usage['calls'] = 1
    return result, usage
