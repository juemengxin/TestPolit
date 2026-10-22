"""Validation and snapshot creation for asynchronous job submissions."""

from __future__ import annotations

import json

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Asset, Entity, Job, Resource


JOB_REFERENCE_FIELDS = (
    'entity_id', 'model_id', 'failure_analysis_model_id', 'agent_id', 'environment_id', 'prompt_id',
    'template_id', 'strategy_id', 'source_job', 'resource_id', 'failure_case_id',
)
ALLOWED_PAYLOAD_FIELDS = {
    'text', 'entity_id', 'entity_ids', 'case_ids', 'skill_ids', 'model_id',
    'agent_id', 'environment_id', 'knowledge_ids', 'prompt_id', 'template_id',
    'strategy_id', 'source_job', 'resource_id', 'tool', 'arguments', 'count',
    'target_kind', 'ui_platform', 'auto_apply', 'messages', 'specialty',
    'focus_module', 'preparation_data', 'request_overrides', 'failure_case_id',
    'related_entity_ids',
    'repeat_count', 'stall_threshold_ms',
    'failure_analysis_model_id',
    'state', 'questions',
}


def _validate_basic_fields(kind: str, payload: dict, mode: str) -> None:
    """Validate the general job contract before querying referenced records."""
    if kind not in dict(Job.KINDS) or kind in ('ingest', 'triage', 'app_record', 'app_video'):
        raise ValidationError('无效任务类型')
    if mode not in ['simulation', 'live', 'local']:
        raise ValidationError('无效运行模式')

    for field in JOB_REFERENCE_FIELDS:
        if payload.get(field) is not None and type(payload[field]) is not int:
            raise ValidationError(f'{field} 必须为整数')

    max_count = None if kind == 'design' else 30
    count = payload.get('count', 5)
    if type(count) is not int or count < 1 or (max_count is not None and count > max_count):
        message = '每模块生成数量必须为正整数' if kind == 'design' else f'生成数量应为 1–{max_count}'
        raise ValidationError(message)
    if payload.get('target_kind', 'case') not in ['case', 'api', 'ui_case']:
        raise ValidationError('无效目标类型')
    if payload.get('ui_platform', 'web') not in ['app', 'web']:
        raise ValidationError('UI 用例类型仅支持 App 或 Web')
    if kind=='ui':
        if type(payload.get('repeat_count',1)) is not int or not 1<=payload.get('repeat_count',1)<=20:
            raise ValidationError('UI 重复回放次数必须为 1–20')
        if type(payload.get('stall_threshold_ms',5000)) is not int or not 500<=payload.get('stall_threshold_ms',5000)<=60000:
            raise ValidationError('UI 卡顿阈值必须为 500–60000 毫秒')
    if not isinstance(payload.get('text', ''), str) or len(payload.get('text', '')) > 40000:
        raise ValidationError('输入文本过长')
    messages = payload.get('messages', [])
    if not isinstance(messages, list) or len(messages) > 20:
        raise ValidationError('对话上下文最多 20 条')
    if set(payload) - ALLOWED_PAYLOAD_FIELDS:
        raise ValidationError('包含不支持的任务参数')

    for field in ('preparation_data', 'request_overrides'):
        value = payload.get(field, {})
        if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False)) > 20000:
            raise ValidationError(f'{field} 必须为不超过 20KB 的 JSON 对象')
    if kind == 'decision':
        state, questions = payload.get('state'), payload.get('questions')
        if mode != 'live':
            raise ValidationError('结构化决策仅支持真实 H2O 服务')
        if not isinstance(state, (str, dict, list)) or len(json.dumps(state, ensure_ascii=False)) > 30000:
            raise ValidationError('决策上下文必须是 30KB 以内的文本或 JSON')
        if not isinstance(questions, dict) or not questions or len(questions) > 20:
            raise ValidationError('请提供 1–20 个命名决策问题')
        for name, question in questions.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(question, dict):
                raise ValidationError('决策问题格式无效')
            if question.get('type') not in {'noul', 'choice', 'score'} or not isinstance(question.get('instructions'), str) or not question['instructions'].strip():
                raise ValidationError('每个问题都需要有效类型与 instructions')
            criteria = question.get('criteria')
            if question['type'] == 'noul' and (not isinstance(criteria, dict) or not {'true', 'false'} <= set(criteria)):
                raise ValidationError('noul 问题的 criteria 必须包含 true 和 false')
            if question['type'] == 'choice' and (not isinstance(criteria, dict) or not criteria or len(criteria) > 255):
                raise ValidationError('choice 问题需要 1–255 个命名选项')
            if question['type'] == 'score' and (not isinstance(criteria, list) or not criteria or len(criteria) > 255):
                raise ValidationError('score 问题需要 1–255 个分级标签')


def _validate_design_fields(payload: dict, mode: str) -> None:
    """Enforce the requirement-analysis contract and source document scope."""
    if not isinstance(payload.get('focus_module'), str) or not payload['focus_module'].strip() or len(payload['focus_module']) > 100:
        raise ValidationError('请选择或填写目标模块')
    if not payload.get('entity_id') and not payload.get('text', '').strip():
        raise ValidationError('请选择需求文档或输入需求正文')
    if payload.get('target_kind', 'case') not in ['case', 'ui_case']:
        raise ValidationError('需求拆解任务仅生成测试用例或 UI 用例草稿')
    if mode not in ['simulation', 'live']:
        raise ValidationError('需求拆解仅支持模拟或真实模型模式')
    if mode != 'live':
        payload['auto_apply'] = False
def _validate_entity_record_references(project, payload: dict) -> None:
    """Confirm scalar entity references belong to the selected project."""
    for field in ('entity_id', 'environment_id', 'prompt_id', 'template_id', 'strategy_id'):
        if payload.get(field) and not Entity.objects.filter(project=project, pk=payload[field]).exists():
            raise ValidationError(f'{field} 不属于当前项目')


def _validate_design_source(project, payload: dict, kind: str) -> None:
    """Require a requirement entity for design jobs after generic scope checks."""
    if kind == 'design' and payload.get('entity_id') and not Entity.objects.filter(
        project=project, pk=payload['entity_id'], kind='requirement'
    ).exists():
        raise ValidationError('请选择需求文档')


def _validate_api_optimization_scope(project, payload: dict, kind: str, mode: str) -> None:
    """Keep API configuration optimization restricted to API cases and related test assets."""
    if kind != 'optimize' or payload.get('target_kind') != 'api':
        if payload.get('related_entity_ids'):
            raise ValidationError('关联配置仅支持接口配置优化任务')
        return
    if mode != 'live':
        raise ValidationError('接口配置 AI 编辑与修复必须使用真实模型模式')
    if not payload.get('entity_id') or not Entity.objects.filter(
        project=project, pk=payload['entity_id'], kind='api'
    ).exists():
        raise ValidationError('请选择要编辑的接口用例')
    related_ids=payload.get('related_entity_ids', [])
    if related_ids and Entity.objects.filter(project=project,pk__in=related_ids).exclude(
        kind__in=['environment','database','function']
    ).exists():
        raise ValidationError('接口配置只能关联环境、数据库或公共函数')


def _validate_collection_references(project, payload: dict) -> None:
    """Validate selected entity, knowledge, case, and skill collections."""
    for field, model in (('entity_ids', Entity), ('knowledge_ids', Entity), ('case_ids', Asset)):
        ids = payload.get(field, [])
        invalid = (
            not isinstance(ids, list)
            or len(ids) > 100
            or any(type(item_id) is not int for item_id in ids)
        )
        if invalid or model.objects.filter(project=project, pk__in=ids).count() != len(set(ids)):
            raise ValidationError(f'{field} 引用无效')

    skill_ids = payload.get('skill_ids', [])
    if not isinstance(skill_ids, list) or len(skill_ids) > 20 or any(type(item_id) is not int for item_id in skill_ids):
        raise ValidationError('skill_ids 引用无效')
    if skill_ids and Resource.objects.filter(
        project=project, kind='skill', enabled=True, pk__in=skill_ids
    ).count() != len(set(skill_ids)):
        raise ValidationError('Skill 不存在、未启用或不属于当前项目')

    related_ids = payload.get('related_entity_ids', [])
    if (
        not isinstance(related_ids, list)
        or len(related_ids) > 100
        or any(type(item_id) is not int for item_id in related_ids)
        or Entity.objects.filter(project=project, pk__in=related_ids).count() != len(set(related_ids))
    ):
        raise ValidationError('related_entity_ids 引用无效')


def _validate_resource_references(project, payload: dict) -> None:
    """Confirm model, agent, tool resource, and source job references."""
    for field in ('model_id', 'agent_id', 'resource_id'):
        if payload.get(field) and not Resource.objects.filter(project=project, pk=payload[field]).exists():
            raise ValidationError(f'{field} 引用无效')
    analysis_model_id = payload.get('failure_analysis_model_id')
    if analysis_model_id is not None:
        analysis_model = Resource.objects.filter(
            project=project, pk=analysis_model_id, kind='model', enabled=True,
        ).first()
        if not analysis_model or analysis_model.settings.get('provider_type', 'openai_compatible') != 'openai_compatible':
            raise ValidationError('失败分析模型必须是当前项目已启用的聊天模型')
    if payload.get('source_job') and not Job.objects.filter(project=project, pk=payload['source_job']).exists():
        raise ValidationError('原任务无效')


def _validate_failure_analysis(project, payload: dict, kind: str, mode: str) -> None:
    """Bind execution analysis to one failed live API/UI case."""
    if kind != 'analyze' or not payload.get('source_job'):
        return
    source_job = Job.objects.filter(
        project=project, pk=payload['source_job'], kind__in=['api', 'ui'], status='completed', mode='live'
    ).first()
    if not source_job:
        raise ValidationError('失败分析需关联同项目已完成的真实接口或 UI 执行任务')
    case_id = payload.get('failure_case_id')
    failed_ids = {
        case.get('id') for case in source_job.result.get('cases', [])
        if case.get('status') == 'failed'
    }
    if case_id not in failed_ids:
        raise ValidationError('请选择该执行任务中失败的用例')
    if mode != 'live':
        raise ValidationError('执行失败分析需使用真实模型模式')
    payload['target_kind'] = 'api' if source_job.kind == 'api' else 'ui_case'


def _validate_automation_job(project, payload: dict, kind: str, mode: str) -> None:
    """Validate API/UI case selection, executor mode, and environment type."""
    expected_kind = 'api' if kind == 'api' else 'ui_case'
    entity_ids = payload.get('entity_ids', [])
    if not entity_ids or Entity.objects.filter(
        project=project, kind=expected_kind, pk__in=entity_ids
    ).count() != len(set(entity_ids)):
        raise ValidationError('请选择正确类型的自动化用例')

    if kind == 'ui':
        if Entity.objects.filter(
            project=project,
            pk__in=entity_ids,
            config__automation_status='needs_mapping',
        ).exists():
            raise ValidationError('所选 UI 用例仍待配置可执行步骤，不能执行')
        selected_configs = list(
            Entity.objects.filter(project=project, pk__in=entity_ids).values_list('config', flat=True)
        )
        if len({config.get('executor') == 'mobile-mcp' for config in selected_configs}) > 1:
            raise ValidationError('移动端与浏览器 UI 用例需分开执行')
        for config in selected_configs:
            if config.get('executor') == 'mobile-mcp' and not Resource.objects.filter(
                project=project,
                pk=config.get('mcp_resource_id'),
                kind='mcp',
                enabled=True,
                mode='live',
            ).exists():
                raise ValidationError('移动端 UI 用例需使用已验证的 Mobile MCP 服务')
        if payload.get('repeat_count',1)>1 and any(config.get('executor')=='mobile-mcp' for config in selected_configs):
            raise ValidationError('循环回放当前仅支持 Playwright Web 用例')

    environment_kind = 'environment' if kind == 'api' else 'ui_environment'
    environment_id = payload.get('environment_id')
    if environment_id and not Entity.objects.filter(
        project=project, pk=environment_id, kind=environment_kind
    ).exists():
        raise ValidationError('环境类型不正确')


def _resolve_skills(project, payload: dict, kind: str) -> None:
    """Apply task-specific default skills and freeze their submitted snapshots."""
    if not payload.get('skill_ids'):
        target = payload.get('target_kind', 'case')
        if kind == 'review':
            names = ['api-doc-review' if target == 'api' else 'requirements-review']
        elif kind == 'analyze':
            names = ['api-test-assertion' if target == 'api' else 'web-app-test-execution']
        elif kind == 'design':
            names = ['requirements-review', 'ui-testcase-generation']
        elif kind in ['generate', 'optimize']:
            names = ['api-testcase-generation' if target == 'api' else 'ui-testcase-generation']
        elif kind == 'api':
            names = ['api-test-assertion']
        elif kind == 'ui':
            names = ['web-app-test-execution']
        else:
            names = []
        payload['skill_ids'] = list(
            Resource.objects.filter(
                project=project, kind='skill', enabled=True, name__in=names
            ).values_list('id', flat=True)
        )

    payload['skill_snapshots'] = list(
        Resource.objects.filter(
            project=project,
            kind='skill',
            enabled=True,
            pk__in=payload.get('skill_ids', []),
        ).values('id', 'name', 'description', 'settings')
    )


def _capture_submission_snapshots(project, payload: dict) -> None:
    """Freeze selected source entities and cases so queued work is reproducible."""
    entity_ids = payload.get('entity_ids', [])
    if payload.get('entity_id'):
        entity_ids = entity_ids + [payload['entity_id']]
    snapshots_by_id = {
        item['id']: item
        for item in Entity.objects.filter(project=project, pk__in=entity_ids).values(
            'id', 'kind', 'name', 'content', 'config', 'version'
        )
    }
    payload['snapshots'] = [
        snapshots_by_id[item_id] for item_id in dict.fromkeys(entity_ids)
    ]
    related_ids = payload.get('related_entity_ids', [])
    payload['related_snapshots'] = list(
        Entity.objects.filter(project=project, pk__in=related_ids)
        .values('id', 'kind', 'name', 'content', 'config', 'version')
    )
    payload['cases'] = list(
        Asset.objects.filter(project=project, pk__in=payload.get('case_ids', [])).values(
            'id', 'name', 'description', 'priority', 'data', 'updated_at'
        )
    )
    for case in payload['cases']:
        case['updated_at'] = case['updated_at'].isoformat()


def build_job_submission(project, kind: str, payload: object, mode: str) -> tuple[dict, str, str]:
    """Validate a job request and build its immutable payload and initial status."""
    if kind not in dict(Job.KINDS) or kind in ('ingest', 'triage', 'app_record', 'app_video'):
        raise ValidationError('无效任务类型')
    if mode not in ['simulation', 'live', 'local']:
        raise ValidationError('无效运行模式')
    if not isinstance(payload, dict):
        raise ValidationError('payload 必须为对象')

    _validate_basic_fields(kind, payload, mode)
    if kind == 'design':
        _validate_design_fields(payload, mode)
    _validate_entity_record_references(project, payload)
    _validate_design_source(project, payload, kind)
    _validate_collection_references(project, payload)
    _validate_api_optimization_scope(project, payload, kind, mode)
    _validate_resource_references(project, payload)
    if payload.get('failure_analysis_model_id') and kind not in ('api', 'ui'):
        raise ValidationError('失败分析模型仅用于接口或 UI 自动化任务')
    if kind == 'decision':
        resource = Resource.objects.filter(project=project, pk=payload.get('resource_id'), kind='model', enabled=True).first()
        if not resource or resource.settings.get('provider_type') != 'h2o_decisions':
            raise ValidationError('请选择当前项目中已启用的 H2O Decisions 模型')
    _validate_failure_analysis(project, payload, kind, mode)

    if kind in ['api', 'ui'] and mode == 'local':
        mode = 'live'
    if kind in ['generate', 'design', 'optimize', 'review', 'split', 'analyze', 'chat'] and mode == 'live' and not payload.get('model_id'):
        raise ValidationError('真实 AI 任务必须选择模型')
    if kind in ['api', 'ui']:
        _validate_automation_job(project, payload, kind, mode)
    if kind == 'mcp' and (not payload.get('resource_id') or not payload.get('tool')):
        raise ValidationError('MCP 调用需指定服务与工具')

    _resolve_skills(project, payload, kind)
    _capture_submission_snapshots(project, payload)
    initial_status = 'awaiting_approval' if kind == 'mcp' and mode == 'live' else 'queued'
    return payload, mode, initial_status


def create_job(project, kind: str, payload: object, mode: str, name: object, user) -> Job:
    """Persist a validated queued job and its initial history entry."""
    submission_payload, execution_mode, initial_status = build_job_submission(
        project, kind, payload, mode
    )
    return Job.objects.create(
        project=project,
        kind=kind,
        name=str(name or kind + ' 任务')[:150],
        mode=execution_mode,
        payload=submission_payload,
        status=initial_status,
        created_by=user,
        history=[{'status': initial_status, 'at': timezone.now().isoformat()}],
    )
