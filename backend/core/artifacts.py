"""Content-addressed artifact writes shared by views and the demo worker."""

from __future__ import annotations

from .exports import content_hash
from .models import Artifact, Job


def store_artifact(job: Job, kind: str, body: str, schema_version: str = "") -> Artifact:
    """Idempotent-by-content artifact write used by the pipeline."""
    digest = content_hash(body.encode("utf-8"))
    existing = job.artifacts.filter(kind=kind, content_hash=digest).first()
    if existing:
        return existing
    return Artifact.objects.create(
        job=job,
        kind=kind,
        schema_version=schema_version,
        content_hash=digest,
        body=body,
    )
