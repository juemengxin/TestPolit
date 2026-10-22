from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import Attachment, Artifact
@receiver(post_delete, sender=Artifact)
@receiver(post_delete, sender=Attachment)
def remove_file_after_commit(sender, instance, **kwargs):
    if instance.file:
        storage, name = instance.file.storage, instance.file.name
        transaction.on_commit(lambda: storage.delete(name))
