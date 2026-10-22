from django.core.management.base import BaseCommand
from django.utils import timezone
from workbench.models import SystemConfig,AuditEvent
class Command(BaseCommand):
    help='Clean audit records older than the configured retention; defaults to dry-run'
    def add_arguments(self,parser):parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        config=SystemConfig.objects.filter(key='platform').values_list('value',flat=True).first() or {}
        days=config.get('audit_retention_days',90);qs=AuditEvent.objects.filter(created_at__lt=timezone.now()-timezone.timedelta(days=days));count=qs.count()
        if options['apply']:qs.delete()
        self.stdout.write(f'{count} records older than {days} days; '+('deleted' if options['apply'] else 'dry-run; use --apply to delete'))
