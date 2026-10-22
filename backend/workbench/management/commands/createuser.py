import getpass
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
class Command(BaseCommand):
    help='Create a regular user interactively; assign project membership in the UI'
    def add_arguments(self,parser): parser.add_argument('username')
    def handle(self,*args,**options):
        if get_user_model().objects.filter(username=options['username']).exists(): raise CommandError('User already exists')
        password=getpass.getpass('Password: ')
        if password!=getpass.getpass('Password (again): '): raise CommandError('Passwords do not match')
        try: validate_password(password)
        except ValidationError as exc: raise CommandError('; '.join(exc.messages))
        get_user_model().objects.create_user(username=options['username'],password=password)
        self.stdout.write('User created. Assign a project role from 项目管理 → 成员.')
