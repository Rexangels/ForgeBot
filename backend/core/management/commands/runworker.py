"""Demo worker entry point (queue-backed execution arrives next)."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the ForgeBot background worker (demo: synchronous stub)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sim-only",
            action="store_true",
            help="Restrict this worker to simulation steps.",
        )

    def handle(self, *args, **options):
        mode = "simulation" if options["sim_only"] else "general"
        self.stdout.write(
            f"ForgeBot {mode} worker started (demo stub; "
            "connect a Redis-backed queue for durable execution)."
        )
