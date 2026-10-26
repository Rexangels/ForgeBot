"""Local development worker for Meta-backed arm proposals.

This thread makes the prototype UI responsive. It is intentionally a local
bridge; replace it with the durable Redis worker before production deployment.
"""

from __future__ import annotations

import json
from threading import Thread

from django.db import close_old_connections

from . import states
from .artifacts import store_artifact
from .demo import run_to_review
from .models import Job


def _run_proposal(job_id: int, mission: dict) -> None:
    close_old_connections()
    try:
        job = Job.objects.get(pk=job_id)
        run_to_review(job, mission, "tabletop_robot_arm")
    except Job.DoesNotExist:
        return
    except Exception:
        # Keep API/provider internals out of user-visible job reports.
        try:
            job.refresh_from_db()
            if job.state not in states.TERMINAL:
                store_artifact(
                    job,
                    "report",
                    json.dumps({"errors": ["Arm design generation stopped unexpectedly."]}, indent=2),
                    "report/v1",
                )
                job.transition(states.FAILED)
        except Exception:
            pass
    finally:
        close_old_connections()


def start_arm_proposal(job_id: int, mission: dict) -> None:
    thread = Thread(target=_run_proposal, args=(job_id, mission), daemon=True)
    thread.start()
