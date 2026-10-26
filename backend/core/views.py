"""Phase 1 JSON API: projects, jobs, events, artifacts."""

from __future__ import annotations

import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from . import states
from .artifacts import store_artifact
from .models import Artifact, Job, JobEvent, Project
from .schemas import SUPPORTED_ROBOT_CLASSES


def _forbidden(request) -> JsonResponse | None:
    if not request.user.is_authenticated:
        return JsonResponse({"error": "sign-in required"}, status=401)
    return None


def _project_owned(request, project_id: int) -> Project:
    return get_object_or_404(Project, id=project_id, owner=request.user)


@require_POST
def create_project(request):
    denied = _forbidden(request)
    if denied:
        return denied
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    name = (payload.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "name is required"}, status=400)
    project = Project.objects.create(name=name, owner=request.user)
    return JsonResponse({"id": project.id, "name": project.name}, status=201)


@require_POST
def create_job(request, project_id: int):
    denied = _forbidden(request)
    if denied:
        return denied
    project = _project_owned(request, project_id)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    key = (payload.get("idempotency_key") or "").strip()
    if not key:
        return JsonResponse({"error": "idempotency_key is required"}, status=400)
    job, created = Job.objects.get_or_create(
        project=project,
        idempotency_key=key,
        defaults={
            "input_revision": str(payload.get("input_revision", "rev-1")),
            "provider": str(payload.get("provider", "unconfigured")),
            "model": str(payload.get("model", "unconfigured")),
        },
    )
    if created:
        JobEvent.objects.create(job=job, state=job.state, detail={})
    return JsonResponse(
        {"id": job.id, "state": job.state, "created": created},
        status=201 if created else 200,
    )


@require_GET
def get_job(request, job_id: int):
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    artifacts = [
        {"id": a.id, "kind": a.kind, "schema_version": a.schema_version}
        for a in job.artifacts.all()
    ]
    mission_artifact = job.artifacts.filter(kind="mission").first()
    robot_class = (
        "tabletop_robot_arm"
        if job.provider == settings.MODEL_API_PROVIDER
        else "differential-drive-rover"
    )
    if mission_artifact:
        try:
            robot_class = json.loads(mission_artifact.body).get("robot_class", robot_class)
        except (json.JSONDecodeError, AttributeError):
            pass
    return JsonResponse(
        {
            "id": job.id,
            "state": job.state,
            "robot_class": robot_class,
            "provider": job.provider,
            "model": job.model,
            "artifacts": artifacts,
        }
    )


@require_POST
def cancel_job(request, job_id: int):
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    if job.state in states.TERMINAL:
        return JsonResponse({"id": job.id, "state": job.state})
    try:
        job.transition(states.CANCELLED)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"id": job.id, "state": job.state})


@require_GET
def job_events(request, job_id: int):
    """Ordered events; ?after_id=N resumes, plain GET polls (fallback)."""
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    try:
        after = int(request.GET.get("after_id", "0"))
    except ValueError:
        after = 0
    events = job.events.filter(id__gt=after).order_by("id")[:200]
    return JsonResponse(
        {
            "job_id": job.id,
            "state": job.state,
            "events": [
                {
                    "id": e.id,
                    "state": e.state,
                    "created_at": e.created_at.isoformat(),
                    "detail": e.detail,
                }
                for e in events
            ],
        }
    )


@require_GET
def get_artifact(request, artifact_id: int):
    denied = _forbidden(request)
    if denied:
        return denied
    artifact = get_object_or_404(
        Artifact, id=artifact_id, job__project__owner=request.user
    )
    return JsonResponse(
        {
            "id": artifact.id,
            "kind": artifact.kind,
            "schema_version": artifact.schema_version,
            "content_hash": artifact.content_hash,
            "body": artifact.body,
        }
    )


def upload_size_ok(num_bytes: int) -> bool:
    limit = settings.FORGEBOT_MAX_UPLOAD_MB * 1024 * 1024
    return num_bytes <= limit


@require_POST
def analyze_scene(request):
    """Analyze one user-selected still image without retaining the image."""
    from .meta_model import MetaModelError
    from .vision import analyze_scene_image

    denied = _forbidden(request)
    if denied:
        return denied
    image_file = request.FILES.get("image")
    if image_file is None:
        return JsonResponse({"error": "Choose or capture an image first."}, status=400)
    if not upload_size_ok(image_file.size):
        return JsonResponse({"error": f"Image must be no larger than {settings.FORGEBOT_MAX_UPLOAD_MB:g} MB."}, status=413)
    try:
        result = analyze_scene_image(image_file.read(), image_file.content_type or "")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except MetaModelError as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse({"provider": settings.MODEL_API_PROVIDER, "model": settings.MODEL_API_NAME, "observation": result})


@require_GET
def list_projects(request):
    denied = _forbidden(request)
    if denied:
        return denied
    projects = Project.objects.filter(owner=request.user).order_by("-id")
    return JsonResponse(
        {"projects": [{"id": p.id, "name": p.name} for p in projects]}
    )


@require_GET
def list_jobs(request, project_id: int):
    denied = _forbidden(request)
    if denied:
        return denied
    project = _project_owned(request, project_id)
    jobs = project.jobs.order_by("-id")
    return JsonResponse(
        {"jobs": [{"id": j.id, "state": j.state} for j in jobs]}
    )


@require_POST
def create_demo_job(request, project_id: int):
    """Run the demo pipeline synchronously up to awaiting_review."""
    from .demo import run_to_review

    denied = _forbidden(request)
    if denied:
        return denied
    project = _project_owned(request, project_id)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    key = (payload.get("idempotency_key") or "").strip()
    mission = payload.get("mission") or {}
    robot_class = payload.get("robot_class", "differential-drive-rover")
    if not key:
        return JsonResponse({"error": "idempotency_key is required"}, status=400)
    if robot_class not in SUPPORTED_ROBOT_CLASSES:
        return JsonResponse(
            {"error": f"robot_class must be one of {', '.join(SUPPORTED_ROBOT_CLASSES)}"},
            status=400,
        )
    job, created = Job.objects.get_or_create(
        project=project,
        idempotency_key=key,
        defaults={
            "input_revision": str(payload.get("input_revision", "rev-1")),
            "provider": settings.MODEL_API_PROVIDER if robot_class == "tabletop_robot_arm" else "local-demo",
            "model": settings.MODEL_API_NAME if robot_class == "tabletop_robot_arm" else "deterministic-rover-v1",
        },
    )
    if not created:
        return JsonResponse({"id": job.id, "state": job.state, "created": False})
    if robot_class == "tabletop_robot_arm":
        from .arm_worker import start_arm_proposal

        JobEvent.objects.create(
            job=job,
            state=job.state,
            detail={"stage": "queued", "message": "Waiting to generate the arm proposal."},
        )
        start_arm_proposal(job.id, mission)
        return JsonResponse(
            {"id": job.id, "state": states.QUEUED, "created": True}, status=202
        )
    errors = run_to_review(job, mission, robot_class)
    job.refresh_from_db()
    return JsonResponse(
        {"id": job.id, "state": job.state, "mission_errors": errors}, status=201
    )


@require_POST
def approve_job(request, job_id: int):
    """Approve assumptions; generate, validate, and simulate synchronously."""
    from .demo import run_from_approval

    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    if job.state != states.AWAITING_REVIEW:
        return JsonResponse(
            {"error": f"job is {job.state}, not awaiting_review"}, status=409
        )
    summary = run_from_approval(job)
    job.refresh_from_db()
    return JsonResponse({"id": job.id, "state": job.state, "summary": summary})


@require_GET
def csrf_token(request):
    from django.middleware.csrf import get_token

    return JsonResponse({"csrfToken": get_token(request)})


@require_POST
def login_view(request):
    from django.contrib.auth import authenticate, login

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    user = authenticate(
        request,
        username=payload.get("username", ""),
        password=payload.get("password", ""),
    )
    if user is None:
        return JsonResponse({"error": "invalid credentials"}, status=401)
    login(request, user)
    return JsonResponse({"username": user.username})


@require_POST
def logout_view(request):
    from django.contrib.auth import logout

    logout(request)
    return JsonResponse({"ok": True})


@require_GET
def me_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"username": None})
    return JsonResponse({"username": request.user.username})
