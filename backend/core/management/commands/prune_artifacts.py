"""Delete aged artifacts, keeping the newest artifact of each kind per job."""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils import timezone

from core.models import Artifact


class Command(BaseCommand):
    help = "Delete artifacts older than FORGEBOT_ARTIFACT_RETENTION_DAYS (newest per kind kept)."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=settings.FORGEBOT_ARTIFACT_RETENTION_DAYS)
        newest_ids = (
            Artifact.objects.values("job_id", "kind")
            .annotate(newest=Max("id"))
            .values_list("newest", flat=True)
        )
        deleted, _ = (
            Artifact.objects.filter(created_at__lt=cutoff)
            .exclude(pk__in=list(newest_ids))
            .delete()
        )
        self.stdout.write(f"Deleted {deleted} artifacts older than {cutoff.date()}.")
