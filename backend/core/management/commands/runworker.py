"""Durable background worker: claim queued jobs and run them to completion."""

import socket
import uuid

from django.core.management.base import BaseCommand

from core.queue import POLL_INTERVAL_S, SIM_ONLY_KINDS, run_forever


class Command(BaseCommand):
    help = "Claim QUEUED jobs from the database queue and run their tasks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sim-only",
            action="store_true",
            help="Restrict this worker to simulation task kinds.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Exit after the queue stays empty for one poll interval (scripts and checks).",
        )

    def handle(self, *args, **options):
        worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        kinds = sorted(SIM_ONLY_KINDS) if options["sim_only"] else None
        scope = "simulation tasks" if options["sim_only"] else "all task kinds"
        self.stdout.write(f"ForgeBot worker {worker_id} started for {scope}.")
        stop_after = POLL_INTERVAL_S if options["once"] else None
        run_forever(worker_id, kinds=kinds, stop_after_idle_s=stop_after)
