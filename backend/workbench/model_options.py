from urllib.parse import urlsplit


def chat_extra_body(resource):
    """Keep JSON-generation calls out of Qwen's default deep-thinking mode."""
    host = urlsplit(resource.endpoint).hostname
    name = str(resource.settings.get('model_name') or '').lower()
    if host == 'dashscope.aliyuncs.com' and name.startswith('qwen3') and 'thinking' not in name:
        return {'enable_thinking': False}
    return None


def require_chat_compatible(resource):
    """Reject decision-only services before calling a chat-completions client."""
    if resource.settings.get('provider_type') == 'h2o_decisions':
        raise ValueError('H2O Lightning 是结构化决策服务；请使用结构化决策任务，不能用于通用聊天、用例生成或视频解析')
