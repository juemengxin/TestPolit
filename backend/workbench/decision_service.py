"""Client for H2O Lightning's constrained Decisions API."""
import httpx

from .security import allowed_url


def _base_endpoint(resource):
    endpoint = resource.endpoint.rstrip('/')
    if endpoint.endswith('/v1'):
        endpoint = endpoint[:-3]
    return endpoint


def _request_url(resource, path):
    url = allowed_url(_base_endpoint(resource) + path)
    return url


def check_health(resource):
    """Check model readiness without returning arbitrary service response data."""
    response = httpx.get(_request_url(resource, '/health'), timeout=15, follow_redirects=False, trust_env=False)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not data.get('ok'):
        raise ValueError('H2O Decisions 健康检查未就绪')
    return {
        'connected': True,
        'provider': 'h2o_decisions',
        'model': str(data.get('model', ''))[:120],
        'attention_mode': str(data.get('attention_mode', ''))[:40],
        'max_state_tokens': data.get('max_state_tokens'),
        'max_options': data.get('max_options'),
        'images': data.get('images'),
    }


def decide(resource, state, questions):
    """Run a bounded named decision request through the documented endpoint."""
    if resource.settings.get('provider_type') != 'h2o_decisions':
        raise ValueError('所选模型不是 H2O Decisions 类型')
    payload = {'state': state, 'questions': questions}
    response = httpx.post(
        _request_url(resource, '/v1/systemone'),
        json=payload,
        headers={'Content-Type': 'application/json'},
        timeout=httpx.Timeout(60, connect=10),
        follow_redirects=False,
        trust_env=False,
    )
    if response.status_code >= 400:
        # Never persist a remote error body: deployments may echo submitted context.
        raise ValueError(f'H2O Decisions 返回 HTTP {response.status_code}')
    result = response.json()
    if not isinstance(result, dict) or not isinstance(result.get('answers'), dict):
        raise ValueError('H2O Decisions 返回结构无效')
    if set(result['answers']) != set(questions):
        raise ValueError('H2O Decisions 返回的问题与请求不一致')
    return {
        'provider': 'h2o_decisions',
        'model': str(result.get('model', ''))[:120],
        'answers': result['answers'],
        'probability_source': str(result.get('probability_source', ''))[:40],
        'effort_used': result.get('effort_used', {}),
        'usage': result.get('usage', {}),
        'attention_mode': str(result.get('attention_mode', ''))[:40],
    }
