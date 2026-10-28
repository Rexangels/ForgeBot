"""Arm proposal tasks on the durable queue (no in-process threads)."""

from __future__ import annotations

from django.db import close_old_connections

from .demo import run_to_review
from .models import Job
from .queue import enqueue


def run_design_proposal_task(job: Job) -> None:
    """Queue handler: run the mission to review using the saved task payload."""
    close_old_connections()
    try:
        payload = job.task_payload or {}
        run_to_review(job, payload.get("mission", {}), payload.get("robot_class", "tabletop_robot_arm"))
    finally:
        close_old_connections()


def start_design_proposal(job_id: int, mission: dict, robot_class: str) -> None:
    job = Job.objects.get(pk=job_id)
    enqueue(job, "design_proposal", {"mission": mission, "robot_class": robot_class})


def start_arm_proposal(job_id: int, mission: dict) -> None:
    """Backward-compatible helper retained for management callers."""
    start_design_proposal(job_id, mission, "tabletop_robot_arm")
