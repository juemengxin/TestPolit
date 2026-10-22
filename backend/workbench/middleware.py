from .models import AuditEvent
class AuditMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        response=self.get_response(request)
        if request.path.startswith('/api/') and request.method in ['POST','PUT','PATCH','DELETE'] and response.status_code<400 and getattr(request,'user',None) and request.user.is_authenticated:
            AuditEvent.objects.create(user=request.user,action=request.method,target=request.path[:200])
        return response
