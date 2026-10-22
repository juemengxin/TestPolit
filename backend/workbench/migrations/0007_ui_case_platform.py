from django.db import migrations


def classify_ui_cases(apps, schema_editor):
    Entity = apps.get_model('workbench', 'Entity')
    Revision = apps.get_model('workbench', 'Revision')
    for item in Entity.objects.filter(kind='ui_case').iterator():
        config = dict(item.config or {})
        if config.get('platform') in ('app', 'web'):
            continue
        requirement = Entity.objects.filter(pk=config.get('source_requirement_id'), kind='requirement').first() if config.get('source_requirement_id') else None
        config['platform'] = 'app' if config.get('executor') == 'mobile-mcp' or (requirement and 'app' in requirement.content.lower()) else 'web'
        item.config = config
        item.version += 1
        item.save(update_fields=['config', 'version', 'updated_at'])
        Revision.objects.create(entity_id=item.pk, version=item.version, name=item.name,
                                content=item.content, config=config, created_by_id=item.created_by_id)


class Migration(migrations.Migration):
    dependencies = [('workbench', '0006_machineconnection')]
    operations = [migrations.RunPython(classify_ui_cases, migrations.RunPython.noop)]
