"""Job-creation quotas: predictable local budgets before paid-provider spend.

Two caps, both configurable through environment variables:
- active jobs per project (non-terminal states);
- jobs created per user per rolling 24 hours.

Idempotent replays (same idempotency key) never consume quota: callers check
quotas only when they are about to create a new row.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import states


class QuotaExceeded(ValueError):
    """A job-creation budget refused the request."""


def _max_active_per_project() -> int:
    try:
        return max(1, int(getattr(settings, "FORGEBOT_MAX_ACTIVE_JOBS_PER_PROJECT", 10)))
    except (TypeError, ValueError):
        return 10


def _max_daily_per_user() -> int:
    try:
        return max(1, int(getattr(settings, "FORGEBOT_MAX_JOBS_PER_USER_PER_DAY", 100)))
    except (TypeError, ValueError):
        return 100


def check_job_quotas(project, user) -> None:
    """Raise QuotaExceeded when a new job would breach either budget."""
    from .models import Job

    active = Job.objects.filter(project=project).exclude(state__in=states.TERMINAL).count()
    if active >= _max_active_per_project():
        raise QuotaExceeded(
            f"Project {project.name!r} already has {active} active jobs "
            f"(limit {_max_active_per_project()}); finish or cancel one first."
        )
    since = timezone.now() - timedelta(hours=24)
    daily = Job.objects.filter(project__owner=user, created_at__gte=since).count()
    if daily >= _max_daily_per_user():
        raise QuotaExceeded(
            f"Account {user.username!r} already created {daily} jobs in the last 24 hours "
            f"(limit {_max_daily_per_user()})."
        )
