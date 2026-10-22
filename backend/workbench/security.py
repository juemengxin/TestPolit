import hashlib, os, re
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from .models import AccessKey, AuditEvent

def decrypt(resource):
    return Fernet(os.environ['CREDENTIAL_KEY'].encode()).decrypt(resource.secret_encrypted.encode()).decode() if resource.secret_encrypted else ''

def allowed_url(url):
    """Only administrator-specified exact hosts/ports may be contacted. No redirects."""
    parsed=urlsplit(url)
    allowed={x.strip().lower() for x in os.getenv('TESTPILOT_ALLOWED_TARGETS','').split(',') if x.strip()}
    if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('URL 必须为不含凭据的 HTTP(S) 地址')
    authority=parsed.netloc.lower()
    if authority not in allowed:
        raise ValueError('目标不在 TESTPILOT_ALLOWED_TARGETS 中，请由管理员添加精确主机及端口')
    return url

def redact(value):
    """Redact common credential fields, including structured headers and URLs."""
    if isinstance(value,dict):
        return {k:('[REDACTED]' if re.search(r'password|secret|token|authorization|cookie|api.?key',str(k),re.I) else redact(v)) for k,v in value.items()}
    if isinstance(value,list): return [redact(x) for x in value]
    if isinstance(value,str):
        return re.sub(r'(?i)((?:api_key|token|password|secret)=)[^&\s]+',r'\1[REDACTED]',value)[:100000]
    return value

def audit(user,project,action,target):
    AuditEvent.objects.create(user=user,project=project,action=action,target=str(target)[:200])

class ProjectKeyAuthentication(BaseAuthentication):
    def authenticate(self,request):
        header=request.headers.get('Authorization','')
        if not header.startswith('Bearer tp_'): return None
        digest=hashlib.sha256(header[7:].encode()).hexdigest()
        key=AccessKey.objects.select_related('user','project').filter(digest=digest,expires_at__gt=timezone.now(),user__is_active=True).first()
        if not key: raise AuthenticationFailed('API Key 无效或已过期')
        if key.read_only and request.method not in ['GET','HEAD','OPTIONS']: raise PermissionDenied('只读 API Key')
        request._request.api_key_project=key.project_id
        # All project queryset and serializer checks are scoped through the authenticated user wrapper.
        key.user.api_key_project=key.project_id
        return key.user,key
