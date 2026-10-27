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
        for a in job.artifacts.order_by("-id")
    ]
    mission_artifact = job.artifacts.filter(kind="mission").first()
    robot_class = "differential-drive-rover"
    if job.provider == settings.MODEL_API_PROVIDER:
        robot_class = "tabletop_robot_arm"
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


@require_POST
def create_image_design(request, project_id: int):
    """Queue image-guided robot concept generation without retaining the image."""
    from .image_design import _num
    from .vision import _image_format_allowed
    from .image_design_worker import start_image_design

    denied = _forbidden(request)
    if denied:
        return denied
    project = _project_owned(request, project_id)
    image_file = request.FILES.get("image")
    if image_file is None:
        return JsonResponse({"error": "Choose a robot reference image first."}, status=400)
    if not upload_size_ok(image_file.size):
        return JsonResponse({"error": f"Image must be no larger than {settings.FORGEBOT_MAX_UPLOAD_MB:g} MB."}, status=413)
    image = image_file.read()
    content_type = image_file.content_type or ""
    if not _image_format_allowed(content_type, image):
        return JsonResponse({"error": "Upload a valid JPEG, PNG, or WebP reference image."}, status=400)
    brief = (request.POST.get("brief") or "").strip()
    constraints = (request.POST.get("constraints") or "").strip()
    if not brief or len(brief) > 2000 or len(constraints) > 2000:
        return JsonResponse({"error": "Add a design brief (up to 2,000 characters) and keep constraints under 2,000 characters."}, status=400)
    try:
        height = _num(float(request.POST.get("target_height_m", "")), "Target height", 0.05, 3.0)
    except (ValueError, TypeError):
        return JsonResponse({"error": "Target height must be between 0.05 m and 3 m."}, status=400)
    key = (request.POST.get("idempotency_key") or "").strip()
    if not key:
        return JsonResponse({"error": "idempotency_key is required."}, status=400)
    mission = {"robot_class": "image_robot_design", "brief": brief, "target_height_m": height, "constraints": constraints}
    job, created = Job.objects.get_or_create(
        project=project,
        idempotency_key=key,
        defaults={"provider": settings.MODEL_API_PROVIDER, "model": settings.MODEL_API_NAME},
    )
    if not created:
        return JsonResponse({"id": job.id, "state": job.state, "created": False}, status=200)
    store_artifact(job, "mission", json.dumps(mission, indent=2), "mission/v1")
    JobEvent.objects.create(job=job, state=job.state, detail={"stage": "queued", "message": "Waiting to design a robot concept from the reference image."})
    start_image_design(job.id, image, content_type, brief, height, constraints)
    return JsonResponse({"id": job.id, "state": states.QUEUED, "created": True}, status=202)


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
            "provider": settings.MODEL_API_PROVIDER if robot_class in ("tabletop_robot_arm", "research_humanoid") else "local-demo",
            "model": settings.MODEL_API_NAME if robot_class in ("tabletop_robot_arm", "research_humanoid") else "deterministic-rover-v1",
        },
    )
    if not created:
        return JsonResponse({"id": job.id, "state": job.state, "created": False})
    # Make the selected platform available to the first workbench poll, before
    # the local proposal worker starts writing the same immutable mission.
    mission_record = {**(mission if isinstance(mission, dict) else {}), "robot_class": robot_class}
    store_artifact(job, "mission", json.dumps(mission_record, indent=2), "mission/v1")
    if robot_class in ("tabletop_robot_arm", "research_humanoid"):
        from .arm_worker import start_design_proposal

        JobEvent.objects.create(
            job=job,
            state=job.state,
            detail={"stage": "queued", "message": "Waiting to generate the robot design proposal."},
        )
        start_design_proposal(job.id, mission, robot_class)
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


@require_POST
def rebuild_cad_package(request, job_id: int):
    """Rebuild an image-design CAD package from its saved, reviewed specification."""
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    spec_artifact = job.artifacts.filter(kind="spec").order_by("-id").first()
    if spec_artifact is None:
        return JsonResponse({"error": "This design has no saved specification to rebuild from."}, status=409)
    try:
        spec = json.loads(spec_artifact.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "The saved design specification is invalid."}, status=409)
    if not isinstance(spec, dict) or spec.get("robot_class") != "image_robot_design":
        return JsonResponse({"error": "CAD package rebuild is available for image-guided robot designs."}, status=409)

    from .image_design import generate_cad_package
    from .freecad_export import FreeCADError, run_freecad_export

    package = generate_cad_package(spec)
    cad_artifact = store_artifact(job, "cad", json.dumps(package, indent=2), "forgebot-cad-package/v4")
    try:
        freecad_result = run_freecad_export(job.id, package)
    except FreeCADError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    freecad_artifact = store_artifact(job, "cad_export", json.dumps(freecad_result, indent=2), "forgebot-freecad-export/v1")
    validation = json.loads(package["files"]["assembly_validation.json"])
    return JsonResponse({
        "artifact_id": cad_artifact.id,
        "freecad_artifact_id": freecad_artifact.id,
        "freecad_status": freecad_result["status"],
        "schema_version": cad_artifact.schema_version,
        "validation": {"status": validation.get("status"), "finding_count": len(validation.get("findings", []))},
    })


@require_POST
def inspect_design_component(request, job_id: int):
    """Ask Meta to explain a component using only the saved structured robot model."""
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    spec_artifact = job.artifacts.filter(kind="spec").order_by("-id").first()
    if spec_artifact is None:
        return JsonResponse({"error": "This design has no saved specification."}, status=409)
    try:
        payload = json.loads(request.body or b"{}")
        spec = json.loads(spec_artifact.body)
        if spec.get("robot_class") != "image_robot_design":
            return JsonResponse({"error": "Component questions are currently available for image-guided designs."}, status=409)
        from .image_design import explain_design_component
        answer = explain_design_component(spec, payload.get("component_id"), payload.get("question", ""))
    except (json.JSONDecodeError, TypeError, AttributeError):
        return JsonResponse({"error": "Request body must be a JSON object."}, status=400)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        from .meta_model import MetaModelError
        if isinstance(exc, MetaModelError):
            return JsonResponse({"error": str(exc)}, status=502)
        raise
    return JsonResponse(answer)


@require_POST
def propose_cad_sketch_edit(request, job_id: int):
    """Ask the configured model to propose a bounded, reviewable sketch edit."""
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    spec_artifact = job.artifacts.filter(kind="spec").order_by("-id").first()
    if spec_artifact is None:
        return JsonResponse({"error": "This design has no saved specification."}, status=409)
    try:
        payload = json.loads(request.body or b"{}")
        spec = json.loads(spec_artifact.body)
        if spec.get("robot_class") != "image_robot_design":
            return JsonResponse({"error": "AI sketch proposals are currently available for image-guided robot designs."}, status=409)
        from .image_design import propose_component_sketch_edit
        component_id = payload.get("component_id")
        kernel_findings = []
        export_artifact = job.artifacts.filter(kind="cad_export").order_by("-id").first()
        if export_artifact is not None:
            try:
                export_doc = json.loads(export_artifact.body)
                validation = export_doc.get("export_report", {}).get("solid_kernel_validation", {})
                kernel_findings = [item for item in validation.get("intersections", []) if component_id in item.get("component_ids", [])]
            except (json.JSONDecodeError, AttributeError, TypeError):
                kernel_findings = []
        proposal = propose_component_sketch_edit(spec, component_id, payload.get("instruction"), kernel_findings)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return JsonResponse({"error": "Request body must be a JSON object."}, status=400)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        from .meta_model import MetaModelError
        if isinstance(exc, MetaModelError):
            return JsonResponse({"error": str(exc)}, status=502)
        raise
    return JsonResponse(proposal)


@require_POST
def edit_cad_sketches(request, job_id: int):
    """Save reviewed component envelope edits and rebuild their FreeCAD sketches."""
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    spec_artifact = job.artifacts.filter(kind="spec").order_by("-id").first()
    if spec_artifact is None:
        return JsonResponse({"error": "This design has no saved specification."}, status=409)
    try:
        payload = json.loads(request.body or b"{}")
        edits = payload.get("components")
        if not isinstance(edits, list) or not edits:
            raise ValueError("Provide one or more component dimension edits.")
        spec = json.loads(spec_artifact.body)
        if spec.get("robot_class") != "image_robot_design":
            return JsonResponse({"error": "Sketch editing is currently available for image-guided robot designs."}, status=409)
        components = {component["id"]: component for component in spec.get("components", [])}
        for edit in edits:
            component_id = edit.get("id")
            dimensions = edit.get("dimensions_m")
            if component_id not in components or not isinstance(dimensions, list) or len(dimensions) != 3:
                raise ValueError("Each edit must identify a known component and provide width, depth, and height.")
            values = [float(value) for value in dimensions]
            if any(not (0.005 <= value <= 5.0) for value in values):
                raise ValueError("Sketch dimensions must be between 5 mm and 5 m.")
            if components[component_id]["geometry"] == "cylinder":
                diameter = (values[0] + values[1]) / 2
                values = [diameter, diameter, values[2]]
            elif components[component_id]["geometry"] == "sphere":
                values = [values[0], values[0], values[0]]
            components[component_id]["dimensions_m"] = values
        spec.setdefault("design_history", []).append({"action": "component_envelope_edited", "component_ids": [item["id"] for item in edits]})
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    from .image_design import generate_cad_package, generate_image_robot_urdf
    from .freecad_export import FreeCADError, run_freecad_export

    package = generate_cad_package(spec)
    try:
        freecad_result = run_freecad_export(job.id, package)
    except FreeCADError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    spec_artifact = store_artifact(job, "spec", json.dumps(spec, indent=2), spec_artifact.schema_version)
    urdf_artifact = store_artifact(job, "urdf", generate_image_robot_urdf(spec), "urdf/v1")
    cad_artifact = store_artifact(job, "cad", json.dumps(package, indent=2), "forgebot-cad-package/v4")
    freecad_artifact = store_artifact(job, "cad_export", json.dumps(freecad_result, indent=2), "forgebot-freecad-export/v1")
    JobEvent.objects.create(job=job, state=job.state, detail={"stage": "sketch_edit", "status": "completed", "component_ids": [item["id"] for item in edits]})
    return JsonResponse({
        "spec_artifact_id": spec_artifact.id,
        "urdf_artifact_id": urdf_artifact.id,
        "artifact_id": cad_artifact.id,
        "freecad_artifact_id": freecad_artifact.id,
        "freecad_status": freecad_result["status"],
        "freecad_version": freecad_result.get("freecad_version"),
    })


@require_POST
def simulate_tabletop_arm(request, job_id: int):
    """Run and persist the reproducible MuJoCo tabletop-arm task."""
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    if job.state != states.SUCCEEDED:
        return JsonResponse({"error": "Approve and build the robot package before simulation."}, status=409)
    spec_artifact = job.artifacts.filter(kind="spec").order_by("-id").first()
    if spec_artifact is None:
        return JsonResponse({"error": "This design has no saved specification."}, status=409)
    try:
        payload = json.loads(request.body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        spec = json.loads(spec_artifact.body)
        if spec.get("robot_class") != "tabletop_robot_arm":
            return JsonResponse({"error": "Physics simulation is currently available for tabletop arms."}, status=409)
        train = payload.get("train_controller", False)
        if not isinstance(train, bool):
            raise ValueError("train_controller must be true or false.")
        from .arm_simulation import run_arm_simulation
        result = run_arm_simulation(
            spec,
            pick_target=payload.get("pick_target_m"),
            place_target=payload.get("place_target_m"),
            train=train,
        )
        result["spec_artifact_id"] = spec_artifact.id
    except Exception as exc:
        from .arm_simulation import ArmSimulationError
        if isinstance(exc, ArmSimulationError):
            return JsonResponse({"error": str(exc)}, status=422)
        if isinstance(exc, (json.JSONDecodeError, TypeError, ValueError)):
            return JsonResponse({"error": str(exc)}, status=400)
        raise
    artifact = store_artifact(job, "sim", json.dumps(result, indent=2), "sim/mujoco-arm/v1")
    JobEvent.objects.create(job=job, state=job.state, detail={"stage": "physics_simulation", "artifact_id": artifact.id, "passed": result["passed"]})
    return JsonResponse({"artifact_id": artifact.id, "schema_version": artifact.schema_version, "passed": result["passed"], "engine": result["engine"], "engine_version": result["engine_version"]})


@require_POST
def benchmark_project_arm_designs(request, job_id: int):
    """Run the same deterministic pick/place acceptance task on recent project arms."""
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    if job.state != states.SUCCEEDED:
        return JsonResponse({"error": "Approve and build the robot package before benchmarking."}, status=409)
    candidates = list(job.project.jobs.filter(state=states.SUCCEEDED).order_by("-updated_at")[:8])
    if job not in candidates:
        candidates = [job, *candidates[:7]]
    results = []
    for candidate in candidates:
        spec_artifact = candidate.artifacts.filter(kind="spec").order_by("-id").first()
        if spec_artifact is None:
            continue
        try:
            spec = json.loads(spec_artifact.body)
            if spec.get("robot_class") != "tabletop_robot_arm":
                continue
            from .arm_simulation import run_arm_simulation
            simulation = run_arm_simulation(spec)
            results.append({
                "job_id": candidate.id,
                "design_name": spec.get("name") or f"Arm design #{candidate.id}",
                "spec_artifact_id": spec_artifact.id,
                "passed": simulation["passed"],
                "task": simulation["task"],
                "acceptance_checks": simulation["acceptance_checks"],
                "failure_reasons": simulation["failure_reasons"],
                "metrics": simulation["metrics"],
            })
        except Exception as exc:
            results.append({"job_id": candidate.id, "design_name": f"Arm design #{candidate.id}", "passed": False, "failure_reasons": [str(exc)], "acceptance_checks": {}})
    if not results:
        return JsonResponse({"error": "No approved tabletop-arm designs were found in this project."}, status=409)
    record = {
        "schema_version": "forgebot-arm-benchmark/v1",
        "benchmark": "same default tabletop pick-and-place acceptance task",
        "design_count": len(results),
        "passed_count": sum(1 for result in results if result["passed"]),
        "results": results,
        "limits": ["Only the eight most recently updated successful project jobs are considered.", "Primitive geometry and estimated inertias are used; the benchmark is not hardware qualification."],
    }
    artifact = store_artifact(job, "sim_benchmark", json.dumps(record, indent=2), record["schema_version"])
    JobEvent.objects.create(job=job, state=job.state, detail={"stage": "arm_design_benchmark", "artifact_id": artifact.id, "design_count": len(results), "passed_count": record["passed_count"]})
    return JsonResponse({"artifact_id": artifact.id, "benchmark": record})


@require_GET
def tabletop_arm_simulation_defaults(request, job_id: int):
    """Return a reachable default target pair derived from the saved arm spec."""
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    spec_artifact = job.artifacts.filter(kind="spec").order_by("-id").first()
    if spec_artifact is None:
        return JsonResponse({"error": "This design has no saved specification."}, status=409)
    try:
        spec = json.loads(spec_artifact.body)
        if spec.get("robot_class") != "tabletop_robot_arm":
            return JsonResponse({"error": "Physics simulation is currently available for tabletop arms."}, status=409)
        from .arm_simulation import default_task_targets
        pick, place = default_task_targets(spec)
    except Exception as exc:
        from .arm_simulation import ArmSimulationError
        if isinstance(exc, ArmSimulationError):
            return JsonResponse({"error": str(exc)}, status=422)
        if isinstance(exc, (json.JSONDecodeError, TypeError, ValueError)):
            return JsonResponse({"error": str(exc)}, status=400)
        raise
    return JsonResponse({"pick_target_m": list(pick), "place_target_m": list(place), "spec_artifact_id": spec_artifact.id})


@require_POST
def propose_arm_simulation_repair(request, job_id: int):
    """Ask Meta for one bounded arm-spec change using the latest local trial."""
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    spec_artifact = job.artifacts.filter(kind="spec").order_by("-id").first()
    sim_artifact = job.artifacts.filter(kind="sim").order_by("-id").first()
    if spec_artifact is None or sim_artifact is None:
        return JsonResponse({"error": "Run a MuJoCo task before asking for a simulation repair."}, status=409)
    try:
        spec = json.loads(spec_artifact.body)
        simulation = json.loads(sim_artifact.body)
        if spec.get("robot_class") != "tabletop_robot_arm" or simulation.get("schema_version") != "forgebot-arm-simulation/v1":
            return JsonResponse({"error": "A tabletop-arm MuJoCo result is required."}, status=409)
        if simulation.get("passed"):
            return JsonResponse({"error": "This simulation passed its acceptance gate; no repair is needed."}, status=409)
        if simulation.get("spec_artifact_id") != spec_artifact.id:
            return JsonResponse({"error": "The latest simulation belongs to an older design. Rerun it before requesting a repair."}, status=409)
        from .robot_arm import propose_arm_repair
        proposal = propose_arm_repair(spec, simulation)
        proposal_artifact = store_artifact(job, "repair_proposal", json.dumps(proposal, indent=2), "arm-repair-proposal/v1")
    except (json.JSONDecodeError, TypeError, AttributeError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        from .meta_model import MetaModelError
        if isinstance(exc, MetaModelError):
            return JsonResponse({"error": str(exc)}, status=502)
        raise
    return JsonResponse({**proposal, "proposal_artifact_id": proposal_artifact.id})


@require_POST
def apply_arm_simulation_repair(request, job_id: int):
    """Apply a user-reviewed one-parameter repair and invalidate old simulation evidence."""
    denied = _forbidden(request)
    if denied:
        return denied
    job = get_object_or_404(Job, id=job_id, project__owner=request.user)
    if job.state != states.SUCCEEDED:
        return JsonResponse({"error": "Robot files must be built before applying a repair."}, status=409)
    spec_artifact = job.artifacts.filter(kind="spec").order_by("-id").first()
    sim_artifact = job.artifacts.filter(kind="sim").order_by("-id").first()
    if spec_artifact is None or sim_artifact is None:
        return JsonResponse({"error": "Run a MuJoCo task before applying its repair."}, status=409)
    try:
        proposal = json.loads(request.body or b"{}")
        spec = json.loads(spec_artifact.body)
        simulation = json.loads(sim_artifact.body)
        proposal_artifact = job.artifacts.filter(kind="repair_proposal").order_by("-id").first()
        if spec.get("robot_class") != "tabletop_robot_arm" or simulation.get("schema_version") != "forgebot-arm-simulation/v1" or simulation.get("spec_artifact_id") != spec_artifact.id:
            return JsonResponse({"error": "The latest simulation is stale; rerun the current design before applying a repair."}, status=409)
        if simulation.get("passed"):
            return JsonResponse({"error": "This simulation passed its acceptance gate; no repair is needed."}, status=409)
        if proposal_artifact is None or proposal.get("proposal_artifact_id") != proposal_artifact.id:
            return JsonResponse({"error": "Request a current AI repair proposal before applying a change."}, status=409)
        saved_proposal = json.loads(proposal_artifact.body)
        if any(proposal.get(key) != saved_proposal.get(key) for key in ("parameter", "joint_name", "value", "rationale", "review_notes")):
            return JsonResponse({"error": "Submitted repair differs from the saved proposal; request a fresh proposal."}, status=409)
        from .robot_arm import apply_arm_repair, generate_arm_starter_code, generate_arm_urdf
        updated = apply_arm_repair(spec, proposal)
        urdf = generate_arm_urdf(updated)
        code = generate_arm_starter_code(updated)
        from .validation import check_generated_code, validate_urdf
        findings = [{"severity": finding.severity, "path": finding.artifact_path, "message": finding.message} for finding in (validate_urdf(urdf) + check_generated_code(code))]
        blockers = [finding for finding in findings if finding["severity"] == "blocker"]
        if blockers:
            raise ValueError("Repaired files failed local validation: " + "; ".join(item["message"] for item in blockers))
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    spec_artifact = store_artifact(job, "spec", json.dumps(updated, indent=2), spec_artifact.schema_version)
    urdf_artifact = store_artifact(job, "urdf", urdf, "urdf/v1")
    code_artifact = store_artifact(job, "code", code, "code/v1")
    report_artifact = store_artifact(job, "report", json.dumps({"findings": findings, "repair": {key: proposal.get(key) for key in ("parameter", "joint_name", "value", "rationale")}, "requires_resimulation": True}, indent=2), "report/v1")
    stale_sim = {"status": "stale", "reason": "The arm specification changed after the last simulation. Run MuJoCo again before treating the repair as validated.", "spec_artifact_id": spec_artifact.id}
    sim_artifact = store_artifact(job, "sim", json.dumps(stale_sim, indent=2), "sim/mujoco-arm/v1")
    JobEvent.objects.create(job=job, state=job.state, detail={"stage": "arm_simulation_repair", "parameter": proposal.get("parameter"), "requires_resimulation": True})
    return JsonResponse({"spec_artifact_id": spec_artifact.id, "urdf_artifact_id": urdf_artifact.id, "code_artifact_id": code_artifact.id, "report_artifact_id": report_artifact.id, "simulation_artifact_id": sim_artifact.id})


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
