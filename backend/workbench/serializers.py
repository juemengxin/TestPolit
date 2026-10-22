import os
from cryptography.fernet import Fernet
from django.db.models import Count
from rest_framework import serializers
from .models import Project, Resource, Asset, Run, Attachment, Membership
from .access import projects_for, role_for, require_write

class ProjectSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    owner_name = serializers.CharField(source='owner.username', read_only=True)
    case_count = serializers.SerializerMethodField()
    class Meta:
        model = Project
        fields = ['id','name','key','description','owner_name','role','case_count','created_at','updated_at']
    def get_role(self, obj): return role_for(self.context['request'].user, obj)
    def get_case_count(self, obj):
        # Project list queries annotate this value to avoid one count query per row.
        if hasattr(obj, 'case_count'):
            return obj.case_count
        return obj.entities.filter(kind='ui_case').count()

class ScopedSerializer(serializers.ModelSerializer):
    def validate_project(self, project):
        if not projects_for(self.context['request'].user).filter(pk=project.pk).exists():
            raise serializers.ValidationError('项目不存在或无权限')
        require_write(self.context['request'].user, project)
        if self.instance and self.instance.project_id != project.id:
            raise serializers.ValidationError('不能迁移记录到其他项目')
        return project
    def validate_kind(self, kind):
        if self.instance and self.instance.kind != kind:
            raise serializers.ValidationError('不能修改资源类型')
        return kind

class ResourceSerializer(ScopedSerializer):
    secret = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=4096)
    has_secret = serializers.SerializerMethodField()
    class Meta:
        model = Resource
        fields = ['id','project','kind','name','description','endpoint','settings','enabled','mode','secret','has_secret','created_at','updated_at']
        read_only_fields = ['mode']
    def get_has_secret(self, obj): return bool(obj.secret_encrypted)
    def validate_endpoint(self, value):
        from urllib.parse import urlsplit
        parts = urlsplit(value)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise serializers.ValidationError('服务地址不能包含账号、密码、查询参数或片段；凭据请使用密钥字段')
        return value
    def validate(self, attrs):
        config = attrs.get('settings', {})
        if not isinstance(config, dict): raise serializers.ValidationError({'settings':'请输入 JSON 对象'})
        # Credentials have a dedicated encrypted write-only field; arbitrary config cannot hold secrets.
        allowed = {'model_name','temperature','instructions','transport','command','timeout','resource_ids','provider_type'}
        if set(config) - allowed: raise serializers.ValidationError({'settings':'仅支持 model_name、temperature、instructions、transport、command、timeout、resource_ids、provider_type；密钥请使用专用输入框'})
        if config.get('provider_type', 'openai_compatible') not in {'openai_compatible', 'h2o_decisions'}:
            raise serializers.ValidationError({'settings':'不支持的模型服务类型'})
        if config.get('provider_type') == 'h2o_decisions' and attrs.get('secret'):
            raise serializers.ValidationError({'secret':'H2O Decisions 当前部署不使用 API Key'})
        project = attrs.get('project', getattr(self.instance, 'project', None))
        ids = config.get('resource_ids', [])
        if not isinstance(ids, list) or any(type(i) is not int for i in ids): raise serializers.ValidationError({'settings':'resource_ids 必须为整数数组'})
        if Resource.objects.filter(project=project, pk__in=ids).count() != len(set(ids)): raise serializers.ValidationError({'settings':'关联资源必须属于同一项目'})
        if 'secret' in attrs:
            value = attrs.pop('secret')
            key = os.getenv('CREDENTIAL_KEY')
            if value:
                if not key: raise serializers.ValidationError({'secret':'服务端未配置 CREDENTIAL_KEY，不能保存密钥'})
                try: attrs['secret_encrypted'] = Fernet(key.encode()).encrypt(value.encode()).decode()
                except (ValueError, TypeError): raise serializers.ValidationError({'secret':'服务端加密配置无效'})
            else: attrs['secret_encrypted'] = ''
        return attrs

class AssetSerializer(ScopedSerializer):
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    class Meta:
        model = Asset
        fields = ['id','project','kind','name','description','status','priority','data','created_by_name','created_at','updated_at']
    def validate(self, attrs):
        data = attrs.get('data', getattr(self.instance, 'data', {}))
        kind = attrs.get('kind', getattr(self.instance, 'kind', None))
        project = attrs.get('project', getattr(self.instance, 'project', None))
        if not isinstance(data, dict): raise serializers.ValidationError({'data':'必须为 JSON 对象'})
        if data.get('module_id'):
            from .models import Entity
            if type(data['module_id']) is not int or not Entity.objects.filter(pk=data['module_id'],project=project,kind='module').exists():
                raise serializers.ValidationError({'data':'模块不存在或不属于当前项目'})
        agent_id = data.get('agent_id')
        if agent_id is not None:
            if type(agent_id) is not int or not Resource.objects.filter(pk=agent_id,project=project,kind='agent').exists():
                raise serializers.ValidationError({'data':'Agent 不存在或不属于当前项目'})
        if kind == 'case':
            steps = data.get('steps', [])
            if not isinstance(steps, list) or len(steps) > 100: raise serializers.ValidationError({'data':'步骤必须为数组，最多 100 步'})
            for step in steps:
                if not isinstance(step, dict) or not isinstance(step.get('action'), str) or not step['action'].strip() or not isinstance(step.get('expected'), str) or not step['expected'].strip():
                    raise serializers.ValidationError({'data':'每个步骤需填写 action 和 expected'})
            if data.get('simulate_result','passed') not in ['passed','failed','skipped']:
                raise serializers.ValidationError({'data':'模拟结果无效'})
            if attrs.get('status', getattr(self.instance,'status','draft')) == 'ready' and not steps:
                raise serializers.ValidationError({'data':'就绪用例至少需要一个步骤'})
        if kind in ['plan','flow','suite']:
            ids = data.get('case_ids', [])
            if not isinstance(ids, list) or any(type(i) is not int for i in ids) or len(ids) > 1000:
                raise serializers.ValidationError({'data':'case_ids 必须为整数数组，最多 1000 条'})
            if Asset.objects.filter(project=project, kind='case', pk__in=ids).count() != len(set(ids)):
                raise serializers.ValidationError({'data':'关联用例不存在或不属于当前项目'})
        return attrs

class RunSerializer(serializers.ModelSerializer):
    attachments = serializers.SerializerMethodField()
    def get_attachments(self, obj): return AttachmentSerializer(obj.attachments.all(), many=True).data
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    class Meta:
        model = Run
        exclude = ['worker_id','snapshot']
        read_only_fields = [f.name for f in Run._meta.fields]

class AttachmentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    class Meta:
        model = Attachment
        fields = ['id','project','asset','run','case_id','step_index','name','size','created_at','download_url']
        read_only_fields = fields
    def get_download_url(self, obj): return f'/api/attachments/{obj.pk}/download/'

class MembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    class Meta:
        model = Membership
        fields = ['id','username','role']
