from django.db.models import Q
from rest_framework.exceptions import PermissionDenied
from .models import Project

def projects_for(user):
    key_project=getattr(user,'api_key_project',None)
    if key_project:
        qs=Project.objects.filter(pk=key_project)
        return qs if user.is_superuser else qs.filter(Q(owner=user)|Q(memberships__user=user)).distinct()
    if user.is_superuser:
        return Project.objects.all()
    return Project.objects.filter(Q(owner=user) | Q(memberships__user=user)).distinct()

def role_for(user, project):
    if getattr(user,'api_key_project',project.pk) != project.pk: return None
    if user.is_superuser or project.owner_id == user.id:
        return 'owner'
    return project.memberships.filter(user=user).values_list('role', flat=True).first()

def require_write(user, project, owner=False):
    role = role_for(user, project)
    if role != 'owner' and (owner or role != 'editor'):
        raise PermissionDenied('没有此项目的操作权限')
