import time
from django.core.management.base import BaseCommand
from django.db import OperationalError, close_old_connections
from workbench.worker import recover_stale, claim, execute
from workbench.job_worker import claim_job, execute_job
class Command(BaseCommand):
    help = 'Run durable simulation task worker; stale jobs recover after 60 seconds'
    def add_arguments(self, parser): parser.add_argument('--once',action='store_true')
    def handle(self, *args, **options):
        self.stdout.write('TestPilot worker started (durable run + intelligence/automation queues)')
        try:
            while True:
                close_old_connections()
                try:
                    recover_stale()
                    run = claim()
                    if run: execute(run)
                    job=claim_job()
                    if job: execute_job(job)
                except OperationalError:
                    time.sleep(1)
                    continue
                if options['once']: break
                if not run and not job: time.sleep(1)
        except KeyboardInterrupt: self.stdout.write('Worker stopped; unfinished tasks recover automatically.')
