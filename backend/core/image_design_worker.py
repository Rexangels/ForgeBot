"""Prototype background worker for image-guided robot concept proposals."""

from __future__ import annotations

import json
import threading

from django.db import close_old_connections

from . import states
from .artifacts import store_artifact
from .image_design import IMAGE_DESIGN_VERSION, propose_image_robot
from .meta_model import MetaModelError
from .models import Job


def start_image_design(job_id: int, image: bytes, content_type: str, brief: str, height_m: float, constraints: str) -> None:
    thread = threading.Thread(
        target=_run,
        args=(job_id, image, content_type, brief, height_m, constraints),
        daemon=True,
        name=f"forgebot-image-design-{job_id}",
    )
    thread.start()


def _run(job_id: int, image: bytes, content_type: str, brief: str, height_m: float, constraints: str) -> None:
    close_old_connections()
    try:
        job = Job.objects.get(id=job_id)
        job.transition(states.SPECIFYING)
        design = propose_image_robot(image, content_type, brief, height_m, constraints)
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
    except (MetaModelError, ValueError) as exc:
        _fail(job_id, str(exc))
    except Exception:
        _fail(job_id, "The image-design workflow failed unexpectedly. Check the backend log and retry.")
    finally:
        close_old_connections()


def _fail(job_id: int, message: str) -> None:
    job = Job.objects.filter(id=job_id).first()
    if not job or job.state in states.TERMINAL:
        return
    store_artifact(job, "report", json.dumps({"findings": [{"severity": "blocker", "message": message}]}, indent=2), "report/v1")
    job.transition(states.FAILED)
