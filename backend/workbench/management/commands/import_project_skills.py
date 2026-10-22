from pathlib import Path
import re

from django.core.management.base import BaseCommand, CommandError

from workbench.models import Project, Resource


class Command(BaseCommand):
    help = 'Import */SKILL.md files as enabled project Skill resources.'

    def add_arguments(self, parser):
        parser.add_argument('directory')
        parser.add_argument('--project-key', required=True)

    def handle(self, *args, **options):
        root = Path(options['directory']).expanduser().resolve()
        if not root.is_dir():
            raise CommandError(f'目录不存在：{root}')
        try:
            project = Project.objects.get(key=options['project_key'])
        except Project.DoesNotExist as exc:
            raise CommandError('项目不存在') from exc
        files = sorted(root.glob('*/SKILL.md'))
        if not files:
            raise CommandError('未找到 */SKILL.md')
        created = updated = 0
        for path in files:
            text = path.read_text(encoding='utf-8')
            header = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.S)
            meta = header.group(1) if header else ''
            name_match = re.search(r'^name:\s*(.+)$', meta, re.M)
            description_match = re.search(r'^description:\s*(.+)$', meta, re.M)
            name = (name_match.group(1).strip() if name_match else path.parent.name)[:100]
            description = (description_match.group(1).strip() if description_match else '')[:5000]
            _, was_created = Resource.objects.update_or_create(
                project=project,
                kind='skill',
                name=name,
                defaults={
                    'description':description,
                    'settings':{'instructions':text},
                    'enabled':True,
                },
            )
            created += int(was_created)
            updated += int(not was_created)
        self.stdout.write(self.style.SUCCESS(
            f'导入完成：新增 {created}，更新 {updated}，项目 {project.name}'
        ))
