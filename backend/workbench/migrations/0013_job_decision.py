from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('workbench', '0012_job_app_video')]

    operations = [
        migrations.AlterField(
            model_name='job',
            name='kind',
            field=models.CharField(choices=[(value, value) for value in [
                'generate', 'design', 'optimize', 'review', 'split', 'analyze', 'chat',
                'ingest', 'api', 'ui', 'mcp', 'connection', 'embed_test', 'rerank_test',
                'triage', 'app_record', 'app_video', 'decision',
            ]], max_length=20),
        ),
    ]
