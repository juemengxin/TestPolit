from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from workbench.entities import save_revision
from workbench.models import Asset, Attachment, Entity, Job, Run


class Command(BaseCommand):
    help = '将指定生成任务的手工用例迁入 UI 自动化列表，保留设计内容并标记待配置执行'

    def add_arguments(self, parser):
        parser.add_argument('job_id', type=int)

    @transaction.atomic
    def handle(self, *args, **options):
        job = Job.objects.select_for_update().filter(pk=options['job_id'], kind='design', status='completed').first()
        if not job:
            raise CommandError('未找到已完成的需求用例生成任务')
        cases = list(Asset.objects.select_for_update().filter(project=job.project, kind='case', data__generation_job=job.pk))
        if not cases:
            existing = Entity.objects.filter(project=job.project, kind='ui_case', config__source_generation_job=job.pk).count()
            self.stdout.write(f'无需迁移；现有 UI 用例 {existing} 条')
            return
        ids = [case.pk for case in cases]
        if Attachment.objects.filter(asset_id__in=ids).exists() or Run.objects.filter(source_id__in=ids).exists():
            raise CommandError('部分用例已关联附件或执行记录，停止迁移以免丢失历史')
        created = 0
        for case in cases:
            if Entity.objects.filter(project=job.project, kind='ui_case', config__source_asset_id=case.pk).exists():
                continue
            data = case.data
            entity = Entity.objects.create(
                project=job.project, kind='ui_case', name=case.name, created_by=case.created_by,
                content=case.description,
                config={
                    'automation_status': 'needs_mapping', 'base_url': '', 'steps': [],
                    'design_steps': data.get('steps') or [], 'preconditions': data.get('preconditions') or '',
                    'expected': data.get('expected') or '', 'priority': case.priority,
                    'case_id': data.get('case_id') or '', 'feature': data.get('feature') or '',
                    'module_id': data.get('module_id'), 'source_requirement_id': data.get('source_requirement_id'),
                    'requirement_refs': data.get('requirement_refs') or [],
                    'source_generation_job': job.pk, 'source_asset_id': case.pk,
                },
            )
            save_revision(entity, case.created_by)
            created += 1
        cases_deleted = Asset.objects.filter(pk__in=ids).delete()[0]
        self.stdout.write(self.style.SUCCESS(f'迁移 {created} 条 UI 用例，移出手工列表 {cases_deleted} 条'))
