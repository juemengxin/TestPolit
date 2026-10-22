from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('workbench', '0011_alter_job_kind_apprecording')]

    operations = [
        migrations.AlterField(
            model_name='job',
            name='kind',
            field=models.CharField(choices=[(value, value) for value in [
                'generate', 'design', 'optimize', 'review', 'split', 'analyze', 'chat',
                'ingest', 'api', 'ui', 'mcp', 'connection', 'embed_test', 'rerank_test',
                'triage', 'app_record', 'app_video',
            ]], max_length=20),
        ),
    ]
