"""Durable database-backed job queue for ForgeBot Phase 1.

Jobs waiting in QUEUED with a task_kind are work, not thread-local state: any
`manage.py runworker` process can claim them, so a restarted API or worker
resumes where it left off. Claiming is a single conditional UPDATE, which is
atomic on both SQLite and PostgreSQL. Transient failures release the claim for
a later pass up to MAX_ATTEMPTS; cancellation is cooperative through
cancel_requested.
"""

from __future__ import annotations

import json
import time

from django.db import close_old_connections
from django.utils import timezone

from . import states
from .artifacts import store_artifact
from .models import Job, JobEvent

MAX_ATTEMPTS = 3
POLL_INTERVAL_S = 1.0


class FatalTaskError(RuntimeError):
    """A task failure that must not be retried (invalid input, missing files)."""

# Task kinds a simulation-only worker may claim. Simulation currently runs
# synchronously inside request handlers, so this set stays empty until a
# simulation task is ported to the queue.
SIM_ONLY_KINDS = frozenset()


def enqueue(job: Job, kind: str, payload: dict | None = None) -> Job:
    """Mark a QUEUED job as claimable work for runworker processes."""
    job.task_kind = kind
    job.task_payload = dict(payload or {})
    job.attempts = 0
    job.claimed_by = ""
    job.cancel_requested = False
    job.save(update_fields=["task_kind", "task_payload", "attempts", "claimed_by", "cancel_requested", "updated_at"])
    JobEvent.objects.create(job=job, state=job.state, detail={"stage": "queued", "task_kind": kind})
STALE_CLAIM_TIMEOUT_S = 600.0


def reap_stale_jobs(timeout_s: float = STALE_CLAIM_TIMEOUT_S) -> int:
    """Find non-terminal jobs whose claim has not updated in timeout_s and fail them."""
    stale_cutoff = timezone.now() - timezone.timedelta(seconds=timeout_s)
    stale_jobs = Job.objects.filter(
        state__in=[states.SPECIFYING, states.GENERATING, states.VALIDATING, states.SIMULATING],
        updated_at__lt=stale_cutoff,
    ).exclude(claimed_by="")
    reaped = 0
    for job in stale_jobs:
        _fail(job, f"Job timed out: no heartbeat from worker '{job.claimed_by}' in over {int(timeout_s / 60)} minutes.")
        release(job)
        reaped += 1
    return reaped


def claim_next(worker_id: str, kinds: list[str] | None = None) -> Job | None:
    """Atomically claim the oldest waiting job; None when the queue is empty."""
    reap_stale_jobs()
    candidate = (
        Job.objects.filter(state=states.QUEUED, claimed_by="", cancel_requested=False)
        .exclude(task_kind="")
    )
    if kinds is not None:
        candidate = candidate.filter(task_kind__in=kinds)
    candidate = candidate.order_by("id").first()
    if candidate is None:
        return None
    claimed = Job.objects.filter(pk=candidate.pk, state=states.QUEUED, claimed_by="", cancel_requested=False).update(
        claimed_by=worker_id, updated_at=timezone.now()
    )
    if not claimed:
        return None
    candidate.refresh_from_db()
    return candidate


def release(job: Job) -> None:
    job.claimed_by = ""
    job.save(update_fields=["claimed_by", "updated_at"])


def _fail(job: Job, message: str) -> None:
    store_artifact(job, "report", json.dumps({"errors": [message]}, indent=2), "report/v1")
    if job.state not in states.TERMINAL:
        try:
            job.transition(states.FAILED)
        except ValueError:
            pass


def run_claimed(job: Job) -> str:
    """Execute one claimed job. Returns completed, retrying, cancelled, or failed."""
    close_old_connections()
    try:
        job.refresh_from_db()
        if job.cancel_requested or job.state == states.CANCELLED:
            if job.state not in states.TERMINAL:
                try:
                    job.transition(states.CANCELLED)
                except ValueError:
                    pass
            release(job)
            return "cancelled"
        job.attempts += 1
        job.save(update_fields=["attempts", "updated_at"])
        handler = _handlers().get(job.task_kind)
        if handler is None:
            _fail(job, f"No worker handles task {job.task_kind!r}.")
            release(job)
            return "failed"
        handler(job)
        release(job)
        return "completed"
    except FatalTaskError as exc:
        try:
            job.refresh_from_db()
            _fail(job, str(exc))
        except Exception:
            pass
        release(job)
        return "failed"
    except Exception as exc:
        try:
            job.refresh_from_db()
        except Exception:
            return "failed"
        if job.attempts >= MAX_ATTEMPTS:
            _fail(job, f"{job.task_kind} stopped unexpectedly after {job.attempts} attempts.")
            release(job)
            return "failed"
        JobEvent.objects.create(
            job=job,
            state=job.state,
            detail={"stage": "retry", "attempt": job.attempts, "error": type(exc).__name__},
        )
        release(job)
        return "retrying"
    finally:
        close_old_connections()


def run_forever(worker_id: str, kinds: list[str] | None = None, stop_after_idle_s: float | None = None) -> None:
    """Poll, claim, and run jobs until the process stops."""
    idle_since: float | None = None
    while True:
        job = claim_next(worker_id, kinds)
        if job is None:
            if stop_after_idle_s is not None:
                now = time.monotonic()
                idle_since = idle_since if idle_since is not None else now
                if now - idle_since >= stop_after_idle_s:
                    return
            time.sleep(POLL_INTERVAL_S)
            continue
        idle_since = None
        run_claimed(job)


def _handlers() -> dict:
    from . import arm_worker, image_design_worker

    return {
        "design_proposal": arm_worker.run_design_proposal_task,
        "image_design": image_design_worker.run_image_design_task,
    }
