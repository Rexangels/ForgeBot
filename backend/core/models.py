"""Persistence: projects, jobs, artifacts, and ordered job events."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from . import states


class Project(models.Model):
    name = models.CharField(max_length=200)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="projects"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Job(models.Model):
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="jobs"
    )
    state = models.CharField(max_length=32, default=states.QUEUED)
    idempotency_key = models.CharField(max_length=128)
    input_revision = models.CharField(max_length=64, default="rev-1")
    provider = models.CharField(max_length=128, default="unconfigured")
    model = models.CharField(max_length=128, default="unconfigured")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "idempotency_key"], name="unique_job_key"
            )
        ]

    def transition(self, target: str) -> None:
        states.check_transition(self.state, target)
        self.state = target
        self.updated_at = timezone.now()
        self.save(update_fields=["state", "updated_at"])
        JobEvent.objects.create(job=self, state=target, detail={})


class JobEvent(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="events")
    state = models.CharField(max_length=32)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class Artifact(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="artifacts")
    kind = models.CharField(max_length=64)  # spec, bom, urdf, code, report, sim
    schema_version = models.CharField(max_length=64, default="")
    content_hash = models.CharField(max_length=64)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
