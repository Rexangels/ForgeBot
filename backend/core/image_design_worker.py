"""Image-design tasks on the durable queue (no in-process threads)."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections

from . import states
from .artifacts import store_artifact
from .image_design import IMAGE_DESIGN_VERSION, propose_image_robot
from .meta_model import MetaModelError
from .models import Job
from .queue import FatalTaskError, enqueue


def _inbox_path(job_id: int, index: int = 0) -> Path:
    suffix = "" if index == 0 else f"-view-{index + 1}"
    return Path(settings.MEDIA_ROOT) / "forgebot" / "inbox" / f"job-{job_id}{suffix}.bin"


def start_image_design(job_id: int, image: bytes, content_type: str, brief: str, height_m: float, constraints: str, extra_views: list[tuple] | None = None, view_label: str = "front", *, local_only: bool = False) -> None:
    """Persist the reference bytes and enqueue the design task for runworker.

    `extra_views` holds (bytes, content_type, view_label) tuples for the
    second and third reference viewpoints. With `local_only=True` the worker
    probes the reference bytes on this machine and the model designs from a
    text brief; no pixel is uploaded.
    """
    from .image_design import _clean_view_label

    job = Job.objects.get(id=job_id)
    views = [{"content_type": content_type, "view": _clean_view_label(view_label), "index": 0}]
    _inbox_path(job_id, 0).parent.mkdir(parents=True, exist_ok=True)
    _inbox_path(job_id, 0).write_bytes(image)
    for position, (extra_image, extra_type, extra_view) in enumerate(extra_views or [], start=1):
        _inbox_path(job_id, position).write_bytes(extra_image)
        views.append({"content_type": extra_type, "view": extra_view, "index": position})
    enqueue(job, "image_design", {
        "views": views,
        "brief": brief,
        "height_m": height_m,
        "constraints": constraints,
        "local_only": bool(local_only),
    })


def run_image_design_task(job: Job) -> None:
    """Queue handler: propose, validate, and store the image-guided concept."""
    close_old_connections()
    try:
        job.transition(states.SPECIFYING)
        payload = job.task_payload or {}
        view_metas = payload.get("views") or [{"content_type": payload.get("content_type", ""), "view": "other", "index": 0}]
        views = []
        for meta in view_metas:
            inbox = _inbox_path(job.id, int(meta.get("index", 0)))
            if not inbox.is_file():
                raise ValueError("A reference image for this job is no longer available; resubmit the design request.")
            views.append({"image": inbox.read_bytes(), "content_type": meta.get("content_type", ""), "view": meta.get("view", "other")})
        from .image_design import propose_image_robot_from_views
        design = propose_image_robot_from_views(
            views,
            payload.get("brief", ""),
            float(payload.get("height_m", 0.5)),
            payload.get("constraints", ""),
            local_only=bool(payload.get("local_only", False)),
        )
        if job.cancel_requested:
            job.transition(states.CANCELLED)
            return
        bom = {
            "schema_version": "bom/v1",
            "items": [
                {"name": component["name"], "quantity": 1, "availability": "unverified", "role": component["role"], "geometry_only": True}
                for component in design["components"]
            ],
            "note": "Conceptual geometry inventory only; no purchasable parts or verified hardware are inferred from the image.",
        }
        store_artifact(job, "spec", json.dumps(design, indent=2), IMAGE_DESIGN_VERSION)
        store_artifact(job, "bom", json.dumps(bom, indent=2), "bom/v1")
        job.events.create(state=states.SPECIFYING, detail={"stage": "proposal_ready", "message": "Image-guided concept is ready for review."})
        job.transition(states.AWAITING_REVIEW)
    except ValueError as exc:
        _fail_task(job.id, str(exc))
        raise FatalTaskError(str(exc)) from exc
    except MetaModelError as exc:
        _fail_task(job.id, str(exc))
        raise FatalTaskError(str(exc)) from exc
    except Exception as exc:
        _fail_task(job.id, f"The image-design workflow failed unexpectedly: {exc}")
        raise FatalTaskError(str(exc)) from exc
    finally:
        try:
            for index in range(3):
                _inbox_path(job.id, index).unlink(missing_ok=True)
        except OSError:
            pass
        close_old_connections()


def _fail_task(job_id: int, message: str) -> None:
    job = Job.objects.filter(id=job_id).first()
    if not job or job.state in states.TERMINAL:
        return
    # Terminal transition happens in queue.run_claimed after retries run out;
    # record the report now so every attempt leaves evidence.
    store_artifact(job, "report", json.dumps({"findings": [{"severity": "blocker", "message": message}]}, indent=2), "report/v1")
