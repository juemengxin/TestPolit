import csv, io, json
from pathlib import Path
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, action, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.throttling import AnonRateThrottle
from .models import Project, Membership, Resource, Asset, Run, Attachment
from .serializers import ProjectSerializer, ResourceSerializer, AssetSerializer, RunSerializer, AttachmentSerializer, MembershipSerializer
from .access import projects_for, require_write

class LoginThrottle(AnonRateThrottle): scope = 'login'

@api_view(['GET'])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def session(request):
    get_token(request)
    if not request.user.is_authenticated: return Response({'user': None})
    return Response({'user': {'id': request.user.id, 'username': request.user.username, 'is_admin': request.user.is_superuser}})

@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
@csrf_protect
def sign_in(request):
    user = authenticate(request, username=request.data.get('username',''), password=request.data.get('password',''))
    if not user: return Response({'detail':'用户名或密码不正确'}, status=400)
    login(request, user)
    return Response({'user': {'id':user.id,'username':user.username,'is_admin':user.is_superuser}})

@api_view(['POST'])
def sign_out(request):
    logout(request)
    return Response({'detail':'已退出'})

class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    def get_queryset(self):
        qs = projects_for(self.request.user).select_related('owner').annotate(
            case_count=Count('entities', filter=Q(entities__kind='ui_case'), distinct=True),
        ).order_by('-updated_at', '-pk')
        search = self.request.query_params.get('search')
        return qs.filter(Q(name__icontains=search) | Q(key__icontains=search)) if search else qs
    def perform_create(self, serializer):
        if getattr(self.request.user,'api_key_project',None): raise PermissionDenied('API Key 不能创建项目')
        serializer.save(owner=self.request.user)
    def perform_update(self, serializer):
        require_write(self.request.user, self.get_object(), owner=True)
        serializer.save()
    def perform_destroy(self, instance):
        require_write(self.request.user, instance, owner=True)
        if instance.runs.filter(status__in=['queued','running']).exists() or instance.jobs.filter(status__in=['queued','running','awaiting_approval']).exists(): raise ValidationError('请先取消正在执行的任务')
        instance.delete()
    @action(detail=True, methods=['get','post','delete'])
    def members(self, request, pk=None):
        project = self.get_object()
        if request.method == 'GET': return Response(MembershipSerializer(project.memberships.select_related('user'), many=True).data)
        require_write(request.user, project, owner=True)
        if not isinstance(request.data.get('username'), str): raise ValidationError('请提供用户名')
        user = get_object_or_404(get_user_model(), username=request.data.get('username'))
        if user.pk == project.owner_id: raise ValidationError('不能修改项目所有者角色')
        if request.method == 'DELETE':
            project.memberships.filter(user=user).delete()
            return Response(status=204)
        role = request.data.get('role')
        if role not in ['editor','viewer']: raise ValidationError('无效角色')
        member, _ = Membership.objects.update_or_create(project=project, user=user, defaults={'role':role})
        return Response(MembershipSerializer(member).data)
    @action(detail=True, methods=['get'])
    def overview(self, request, pk=None):
        project = self.get_object()
        runs = project.runs.all()
        completed_runs = list(runs.filter(status='completed').only('metrics', 'finished_at'))
        passed = sum(run.metrics.get('passed', 0) for run in completed_runs)
        total = sum(run.metrics.get('total', 0) for run in completed_runs)
        trend_by_day = {}
        for run in completed_runs:
            if run.finished_at is None:
                continue
            day = timezone.localtime(run.finished_at).date()
            day_metrics = trend_by_day.setdefault(day, {'passed': 0, 'total': 0, 'runs': 0})
            day_metrics['passed'] += run.metrics.get('passed', 0)
            day_metrics['total'] += run.metrics.get('total', 0)
            day_metrics['runs'] += 1
        dates = []
        for offset in reversed(range(7)):
            day = timezone.localdate() - timezone.timedelta(days=offset)
            day_metrics = trend_by_day.get(day, {'passed': 0, 'total': 0, 'runs': 0})
            dates.append({'date': day.isoformat(), **day_metrics})
        recent_runs = runs.prefetch_related('attachments')[:5]
        return Response({'cases':project.entities.filter(kind='ui_case').count(),'plans':project.assets.filter(kind='plan').count(),'resources':project.resources.count(),'runs':runs.count(),'active_runs':runs.filter(status__in=['queued','running']).count(),'pass_rate':round(passed/total*100,1) if total else 0,'tested_cases':total,'trend':dates,'recent_runs':RunSerializer(recent_runs,many=True).data,'assets_by_kind':list(project.assets.values('kind').annotate(count=Count('id')))})

class ScopedViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        qs = self.queryset.filter(project__in=projects_for(self.request.user))
        for field in ['project','kind','status']:
            value = self.request.query_params.get(field)
            if value and any(f.name == field for f in qs.model._meta.fields):
                if field == 'project' and not value.isdigit(): raise ValidationError('无效项目 ID')
                qs = qs.filter(**{field:value})
        search = self.request.query_params.get('search')
        if search: qs = qs.filter(name__icontains=search)
        return qs
    def perform_create(self, serializer):
        require_write(self.request.user, serializer.validated_data['project'])
        serializer.save()
    def perform_update(self, serializer):
        require_write(self.request.user, self.get_object().project)
        serializer.save()
    def perform_destroy(self, instance):
        require_write(self.request.user, instance.project)
        instance.delete()

class ResourceViewSet(ScopedViewSet):
    def perform_destroy(self, instance):
        for asset in Asset.objects.filter(project=instance.project):
            if asset.data.get('agent_id') == instance.pk:
                raise ValidationError('此 Agent 已关联测试资产，请先移除关联')
        for resource in Resource.objects.filter(project=instance.project, kind='agent').exclude(pk=instance.pk):
            if instance.pk in resource.settings.get('resource_ids', []):
                raise ValidationError('此资源已关联 Agent，请先移除关联')
        super().perform_destroy(instance)
    queryset = Resource.objects.select_related('project').all()
    serializer_class = ResourceSerializer

class AssetViewSet(ScopedViewSet):
    def get_queryset(self):
        qs=super().get_queryset()
        module=self.request.query_params.get('module')
        if module:
            if not module.isdigit(): raise ValidationError('模块 ID 无效')
            qs=qs.filter(data__module_id=int(module))
        return qs
    @action(detail=True,methods=['post'])
    def duplicate(self,request,pk=None):
        item=self.get_object();require_write(request.user,item.project)
        copied=Asset.objects.create(project=item.project,kind=item.kind,name=item.name[:140]+' · 副本',description=item.description,priority=item.priority,status='draft',data=item.data,created_by=request.user)
        return Response(self.get_serializer(copied).data,status=201)
    @action(detail=False,methods=['post'],url_path='import')
    @transaction.atomic
    def import_items(self,request):
        project=get_object_or_404(projects_for(request.user),pk=request.data.get('project'));require_write(request.user,project)
        items=request.data.get('items')
        file=request.FILES.get('file')
        if file:
            if file.size>5*1024*1024: raise ValidationError('导入文件不得超过 5MB')
            try:
                raw=file.read().decode('utf-8-sig')
                items=json.loads(raw) if file.name.endswith('.json') else [{'name':r['名称'],'kind':r.get('类型','case'),'priority':r.get('优先级','P1'),'status':r.get('状态','draft'),'description':r.get('说明',''),'data':json.loads(r.get('测试数据','{}'))} for r in csv.DictReader(io.StringIO(raw))]
            except (ValueError,KeyError,UnicodeDecodeError):raise ValidationError('导入文件格式不正确，请使用平台导出的 CSV 或 JSON')
        if not isinstance(items,list) or not 1<=len(items)<=200:raise ValidationError('请导入 1–200 条用例')
        created=[]
        for item in items:
            payload={k:v for k,v in item.items() if k in ['kind','name','description','priority','status','data']};payload['project']=project.pk
            serializer=self.get_serializer(data=payload);serializer.is_valid(raise_exception=True);serializer.save(created_by=request.user);created.append(serializer.data)
        return Response(created,status=201)
    queryset = Asset.objects.select_related('project','created_by').all()
    serializer_class = AssetSerializer
    def perform_create(self, serializer):
        require_write(self.request.user, serializer.validated_data['project'])
        serializer.save(created_by=self.request.user)
    def perform_destroy(self, instance):
        if instance.kind == 'case':
            for asset in Asset.objects.filter(project=instance.project, kind__in=['plan','flow','suite']):
                if instance.pk in asset.data.get('case_ids',[]): raise ValidationError('此用例已关联计划或流程，请先移除关联')
        super().perform_destroy(instance)
    @action(detail=False, methods=['get'])
    def export(self, request):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="test-assets.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['ID','名称','类型','优先级','状态','说明','测试数据'])
        def safe(value):
            value = str(value)
            return "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value
        for item in self.get_queryset(): writer.writerow([item.pk,safe(item.name),item.kind,item.priority,item.status,safe(item.description),safe(json.dumps(item.data,ensure_ascii=False))])
        return response

class RunViewSet(ScopedViewSet):
    queryset = Run.objects.select_related('project','created_by').prefetch_related('attachments').all()
    serializer_class = RunSerializer
    http_method_names = ['get','post','delete','head','options']
    @transaction.atomic
    def create(self, request):
        if not str(request.data.get('source', '')).isdigit(): raise ValidationError('请提供有效的 source 资产 ID')
        source = get_object_or_404(Asset.objects.filter(project__in=projects_for(request.user)), pk=request.data.get('source'))
        require_write(request.user, source.project)
        if source.kind not in ['case','plan','flow','suite'] or source.status != 'ready': raise ValidationError('仅就绪的用例、计划或流程可以执行')
        ids = [source.pk] if source.kind == 'case' else source.data.get('case_ids',[])
        if not ids: raise ValidationError('请先添加测试用例')
        cases = {c.pk:c for c in Asset.objects.filter(project=source.project, kind='case', status='ready', pk__in=ids)}
        if len(cases) != len(set(ids)): raise ValidationError('关联用例缺失、已归档或尚未就绪')
        snapshot = [{'id':cases[i].id,'name':cases[i].name,'priority':cases[i].priority,'data':cases[i].data} for i in dict.fromkeys(ids)]
        context = {'executor':'内置模拟执行器', 'mode':'simulation', 'source_name':source.name, 'source_kind':source.kind}
        agent_id = source.data.get('agent_id')
        if agent_id:
            agent = Resource.objects.filter(pk=agent_id,project=source.project,kind='agent',enabled=True).first()
            if not agent: raise ValidationError('关联 Agent 已停用或不存在')
            context['agent'] = {'id':agent.pk,'name':agent.name}
            context['resources'] = list(Resource.objects.filter(project=source.project,pk__in=agent.settings.get('resource_ids',[])).values('id','name','kind','mode'))
        now = timezone.now().isoformat()
        run = Run.objects.create(project=source.project, source=source, created_by=request.user, name=source.name, execution_context=context, snapshot=snapshot, history=[{'status':'queued','at':now,'message':'任务已提交 · 模拟模式'}])
        return Response(self.get_serializer(run).data, status=202)
    def perform_destroy(self, instance):
        if instance.status in ['queued','running']: raise ValidationError('请先取消任务再删除报告')
        super().perform_destroy(instance)
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        run = self.get_object()
        require_write(request.user, run.project)
        now = timezone.now()
        history = run.history + [{'status':'cancelled','at':now.isoformat(),'message':'用户取消执行'}]
        changed = Run.objects.filter(pk=run.pk,status__in=['queued','running']).update(status='cancelled',finished_at=now,history=history)
        if not changed: raise ValidationError('任务已结束')
        run.refresh_from_db()
        return Response(self.get_serializer(run).data)
    @action(detail=True, methods=['get'])
    def export(self, request, pk=None):
        run = self.get_object()
        # Report content is produced by the worker and durably stored; exporting only serializes it.
        response = HttpResponse(json.dumps(self.get_serializer(run).data,ensure_ascii=False,indent=2),content_type='application/json; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="simulation-report-{run.pk}.json"'
        return response

class AttachmentViewSet(ScopedViewSet):
    queryset = Attachment.objects.select_related('project').all()
    serializer_class = AttachmentSerializer
    http_method_names = ['get','post','delete','head','options']
    def get_queryset(self):
        qs = super().get_queryset()
        for field in ['asset','run']:
            value = self.request.query_params.get(field)
            if value:
                if not value.isdigit(): raise ValidationError('无效关联 ID')
                qs = qs.filter(**{field:value})
        return qs
    def create(self, request):
        for field in ['project','asset','run']:
            if (field == 'project' or request.data.get(field)) and not str(request.data.get(field, '')).isdigit():
                raise ValidationError('请提供有效的项目和关联 ID')
        project = get_object_or_404(projects_for(request.user), pk=request.data.get('project'))
        require_write(request.user, project)
        file = request.FILES.get('file')
        if not file or file.size > 10*1024*1024: raise ValidationError('请选择不超过 10MB 的文件')
        if Path(file.name).suffix.lower() not in ['.pdf','.txt','.md','.csv','.json','.png','.jpg','.jpeg','.webp','.docx','.xlsx']:
            raise ValidationError('不支持该文件类型')
        asset = get_object_or_404(Asset, project=project, pk=request.data['asset']) if request.data.get('asset') else None
        run = get_object_or_404(Run, project=project, pk=request.data['run']) if request.data.get('run') else None
        if bool(asset) == bool(run): raise ValidationError('附件必须且只能关联一个测试资产或报告')
        case_id = request.data.get('case_id') or None
        step_index = request.data.get('step_index') or None
        if case_id is not None or step_index is not None:
            try: case_id, step_index = int(case_id), int(step_index)
            except (ValueError,TypeError): raise ValidationError('截图关联需同时提供用例 ID 与步骤索引')
            case = next((c for c in run.snapshot if c['id'] == case_id),None) if run else None
            if not case or not 0 <= step_index < len(case['data'].get('steps',[])): raise ValidationError('关联步骤不存在')
        item = Attachment.objects.create(project=project,asset=asset,run=run,case_id=case_id,step_index=step_index,file=file,name=Path(file.name).name,size=file.size,uploaded_by=request.user)
        return Response(self.get_serializer(item).data,status=201)
    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        item = self.get_object()
        inline=request.query_params.get('inline')=='1'
        response = FileResponse(item.file.open('rb'), as_attachment=not inline, filename=item.name)
        response['X-Content-Type-Options'] = 'nosniff'
        return response
