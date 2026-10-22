import uuid
from django.conf import settings
from django.db import models

class Project(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    key = models.SlugField(max_length=20, unique=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='owned_projects')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-updated_at']

class Membership(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=[('editor','编辑者'), ('viewer','观察者')])
    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'user'], name='unique_project_member')]

class Resource(models.Model):
    KINDS = [('model','模型'), ('skill','Skill'), ('mcp','MCP'), ('agent','Agent')]
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='resources')
    kind = models.CharField(max_length=10, choices=KINDS)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    endpoint = models.URLField(blank=True)
    secret_encrypted = models.TextField(blank=True)
    settings = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    mode = models.CharField(max_length=20, default='simulation', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-updated_at']

class Asset(models.Model):
    KINDS = [('document','测试文档'), ('case','测试用例'), ('plan','测试计划'), ('flow','测试流程'), ('suite','测试套件')]
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='assets')
    kind = models.CharField(max_length=10, choices=KINDS)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=[('draft','草稿'), ('ready','就绪'), ('archived','已归档')], default='draft')
    priority = models.CharField(max_length=3, choices=[('P0','P0'),('P1','P1'),('P2','P2'),('P3','P3')], default='P1')
    data = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-updated_at']

class Run(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='runs')
    name = models.CharField(max_length=150)
    source = models.ForeignKey(Asset, null=True, on_delete=models.SET_NULL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=15, default='queued', choices=[('queued','排队中'), ('running','执行中'), ('completed','已完成'), ('failed','失败'), ('cancelled','已取消')])
    mode = models.CharField(max_length=20, default='simulation')
    progress = models.PositiveIntegerField(default=0)
    execution_context = models.JSONField(default=dict)
    snapshot = models.JSONField(default=list)
    results = models.JSONField(default=list)
    logs = models.JSONField(default=list)
    history = models.JSONField(default=list)
    metrics = models.JSONField(default=dict)
    worker_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    heartbeat_at = models.DateTimeField(null=True)
    class Meta:
        ordering = ['-created_at']

def upload_path(instance, filename):
    from pathlib import Path
    return f'{instance.project_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'

class Attachment(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.CASCADE, related_name='attachments')
    run = models.ForeignKey(Run, null=True, blank=True, on_delete=models.CASCADE, related_name='attachments')
    case_id = models.PositiveIntegerField(null=True, blank=True)
    step_index = models.PositiveIntegerField(null=True, blank=True)
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to=upload_path)
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']

class Entity(models.Model):
    KINDS = [(x,x) for x in ['requirement','knowledge','knowledge_document','prompt','case_template','strategy','module','api','environment','database','function','tag','ui_case','ui_flow','machine','ui_environment','page','ui_data','data_preparation','skill_source','conversation']]
    project = models.ForeignKey(Project,on_delete=models.CASCADE,related_name='entities')
    kind = models.CharField(max_length=30,choices=KINDS)
    name = models.CharField(max_length=150)
    content = models.TextField(blank=True)
    config = models.JSONField(default=dict,blank=True)
    parent = models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='children')
    position = models.IntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ['position','-updated_at']

class Revision(models.Model):
    entity = models.ForeignKey(Entity,on_delete=models.CASCADE,related_name='revisions')
    version = models.PositiveIntegerField()
    content = models.TextField(blank=True)
    config = models.JSONField(default=dict)
    name = models.CharField(max_length=150)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering=['-version']
        constraints=[models.UniqueConstraint(fields=['entity','version'],name='entity_version_unique')]

class MachineConnection(models.Model):
    machine = models.OneToOneField(Entity, on_delete=models.CASCADE, related_name='connection')
    connected = models.BooleanField(default=False)
    model = models.CharField(max_length=150, blank=True)
    device_id = models.CharField(max_length=150, blank=True)
    message = models.CharField(max_length=300, blank=True)
    checked_at = models.DateTimeField(auto_now=True)

class KnowledgeChunk(models.Model):
    document = models.ForeignKey(Entity,on_delete=models.CASCADE,related_name='chunks')
    index = models.PositiveIntegerField()
    text = models.TextField()
    vector = models.JSONField(default=list)
    embedding = models.CharField(max_length=100,default='local-hash-256')
    class Meta: ordering=['index']

class Job(models.Model):
    KINDS=[(x,x) for x in ['generate','design','optimize','review','split','analyze','chat','ingest','api','ui','mcp','connection','embed_test','rerank_test','triage','app_record','app_video','decision']]
    project=models.ForeignKey(Project,on_delete=models.CASCADE,related_name='jobs')
    kind=models.CharField(max_length=20,choices=KINDS)
    name=models.CharField(max_length=150)
    status=models.CharField(max_length=20,default='queued')
    mode=models.CharField(max_length=20,default='simulation')
    payload=models.JSONField(default=dict)
    result=models.JSONField(default=dict)
    logs=models.JSONField(default=list)
    history=models.JSONField(default=list)
    progress=models.PositiveIntegerField(default=0)
    usage=models.JSONField(default=dict)
    applied=models.BooleanField(default=False)
    worker_id=models.CharField(max_length=64,blank=True)
    heartbeat_at=models.DateTimeField(null=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)
    finished_at=models.DateTimeField(null=True)
    class Meta: ordering=['-created_at']

class Artifact(models.Model):
    project=models.ForeignKey(Project,on_delete=models.CASCADE)
    entity=models.ForeignKey(Entity,null=True,blank=True,on_delete=models.CASCADE,related_name='files')
    job=models.ForeignKey(Job,null=True,blank=True,on_delete=models.CASCADE,related_name='files')
    file=models.FileField(upload_to=upload_path)
    name=models.CharField(max_length=255)
    category=models.CharField(max_length=30,default='document')
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']


class AppRecording(models.Model):
    """Durable owner-only recording; a unique device lease serializes interactions."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    device_id = models.CharField(max_length=100)
    device_name = models.CharField(max_length=150, blank=True)
    package_name = models.CharField(max_length=200)
    device_lock = models.CharField(max_length=64, unique=True, null=True)
    status = models.CharField(max_length=20, default='ready')
    steps = models.JSONField(default=list)
    error = models.CharField(max_length=300, blank=True)
    pending_encrypted = models.TextField(blank=True)
    screenshot = models.FileField(upload_to=upload_path, blank=True)
    screen_width = models.PositiveIntegerField(default=0)
    screen_height = models.PositiveIntegerField(default=0)
    screen_version = models.PositiveIntegerField(default=0)
    last_job = models.ForeignKey(Job, null=True, on_delete=models.SET_NULL)
    case = models.ForeignKey(Entity, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class Organization(models.Model):
    name=models.CharField(max_length=100,unique=True)
    users=models.ManyToManyField(settings.AUTH_USER_MODEL,blank=True)
    projects=models.ManyToManyField(Project,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)

class AccessKey(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    project=models.ForeignKey(Project,on_delete=models.CASCADE)
    name=models.CharField(max_length=100)
    digest=models.CharField(max_length=64,unique=True)
    prefix=models.CharField(max_length=16)
    read_only=models.BooleanField(default=True)
    expires_at=models.DateTimeField()
    created_at=models.DateTimeField(auto_now_add=True)

class AuditEvent(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    project=models.ForeignKey(Project,null=True,on_delete=models.SET_NULL)
    action=models.CharField(max_length=100)
    target=models.CharField(max_length=200)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']

class Preference(models.Model):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    theme=models.CharField(max_length=10,default='light')
    language=models.CharField(max_length=10,default='zh')

class SystemConfig(models.Model):
    key=models.CharField(max_length=50,unique=True)
    value=models.JSONField(default=dict)
