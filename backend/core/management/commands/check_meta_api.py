"""Check Meta Model API authentication without printing the key or reply."""

from django.core.management.base import BaseCommand, CommandError

from core.meta_model import MetaModelError, check_connection
from django.conf import settings


class Command(BaseCommand):
    help = "Verify the configured Meta Model API key and endpoint."

    def handle(self, *args, **options):
        try:
            check_connection()
        except MetaModelError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            self.style.SUCCESS(
                f"Meta Model API connection succeeded (model: {settings.MODEL_API_NAME})."
            )
        )
