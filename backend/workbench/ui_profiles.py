"""Application services for applying reusable UI execution profiles."""

from __future__ import annotations

from urllib.parse import urlsplit

from rest_framework.exceptions import ValidationError

from .entities import save_revision
from .executors import validate_config
from .models import Entity, Resource
from .security import audit


def _selected_cases(profile: Entity, case_ids: object, platform: str) -> list[Entity]:
    """Validate and lock the requested cases inside the profile's project."""
    if (
        not isinstance(case_ids, list)
        or not case_ids
        or len(case_ids) > 200
        or any(type(case_id) is not int for case_id in case_ids)
    ):
        raise ValidationError('请选择 1–200 条用例')

    unique_case_ids = set(case_ids)
    cases = list(
        Entity.objects.select_for_update().filter(
            project=profile.project,
            kind='ui_case',
            config__platform=platform,
            pk__in=unique_case_ids,
        )
    )
    if len(cases) != len(unique_case_ids):
        platform_name = 'App' if platform == 'app' else 'Web'
        raise ValidationError(f'部分用例不属于当前项目或不是 {platform_name} 用例')
    return cases


def _validate_profile_kind(profile: Entity, platform: str) -> None:
    """Reject a profile whose persisted subtype does not match the action."""
    expected_data_type = f'{platform}_profile'
    if profile.kind != 'ui_data' or profile.config.get('data_type') != expected_data_type:
        platform_name = 'App' if platform == 'app' else 'Web'
        raise ValidationError(f'请选择 {platform_name} 公共配置')


def _merge_profile_reference(case: Entity, profile: Entity) -> dict:
    """Add the profile to a case's reusable data links without duplicating IDs."""
    return {
        **case.config,
        'data_ids': list(dict.fromkeys([*case.config.get('data_ids', []), profile.pk])),
    }


def _app_profile_config(profile: Entity) -> tuple[dict, Resource]:
    """Validate App device settings and resolve its enabled live Mobile MCP."""
    app_config = profile.config.get('app_profile', {})
    required_fields = ['app_name', 'package_name', 'device_id', 'mcp_resource_id']
    if any(not app_config.get(field) for field in required_fields):
        raise ValidationError('公共配置须包含 App 名称、包名、设备 ID 和 Mobile MCP 资源')

    try:
        resource = Resource.objects.get(
            pk=app_config['mcp_resource_id'],
            project=profile.project,
            kind='mcp',
            enabled=True,
            mode='live',
        )
    except (Resource.DoesNotExist, ValueError, TypeError) as exc:
        raise ValidationError('公共配置引用的 Mobile MCP 资源不可用') from exc
    return app_config, resource


def _apply_app_profile(case: Entity, profile: Entity, app_config: dict, resource: Resource, user) -> None:
    """Copy App connection details and keep executable cases serializer-valid."""
    case_config = {
        **case.config,
        'app_name': str(app_config['app_name']),
        'package_name': str(app_config['package_name']),
        'device_id': str(app_config['device_id']),
        'mcp_resource_id': resource.pk,
        'executor': 'mobile-mcp',
        'app_profile_id': profile.pk,
    }
    case_config['data_ids'] = list(
        dict.fromkeys([*case_config.get('data_ids', []), profile.pk])
    )

    if case_config.get('automation_status') != 'needs_mapping':
        try:
            validate_config('ui_case', case_config)
        except ValueError as exc:
            raise ValidationError({str(case.pk): str(exc)}) from exc
    _save_case_profile(case, case_config, user)


def _apply_web_profile(case: Entity, profile: Entity, user) -> None:
    """Copy the Web landing URL while retaining the existing case settings."""
    base_url = str(profile.config.get('web_profile', {}).get('base_url', '')).strip()
    parsed_url = urlsplit(base_url)
    if parsed_url.scheme not in ['http', 'https'] or not parsed_url.netloc:
        raise ValidationError('Web 公共配置须填写有效的 HTTP/HTTPS 地址')

    case_config = {
        **case.config,
        'base_url': base_url,
        'web_profile_id': profile.pk,
    }
    case_config['data_ids'] = list(
        dict.fromkeys([*case_config.get('data_ids', []), profile.pk])
    )
    _save_case_profile(case, case_config, user)


def _save_case_profile(case: Entity, config: dict, user) -> None:
    """Version and snapshot the case after profile settings have been applied."""
    case.config = config
    case.version += 1
    case.save(update_fields=['config', 'version', 'updated_at'])
    save_revision(case, user)


def apply_ui_profile(profile: Entity, user, case_ids: object, platform: str) -> dict:
    """Apply one project-scoped App or Web profile and return the API payload."""
    if platform not in {'app', 'web'}:
        raise ValueError('platform must be app or web')

    _validate_profile_kind(profile, platform)
    cases = _selected_cases(profile, case_ids, platform)

    if platform == 'app':
        app_config, resource = _app_profile_config(profile)
        for case in cases:
            _apply_app_profile(case, profile, app_config, resource, user)
        audit(user, profile.project, 'ui.app_profile.apply', profile.pk)
    else:
        for case in cases:
            _apply_web_profile(case, profile, user)
        audit(user, profile.project, 'ui.web_profile.apply', profile.pk)

    return {
        'applied': len(cases),
        'profile_id': profile.pk,
        'case_ids': [case.pk for case in cases],
    }
