"""Synchronous prototype pipeline (stand-in for the queue worker).

Runs rover and tabletop-arm workflows in-process. Arm specifications come from
Meta; URDF and starter control artifacts are generated locally after review.
The rover retains its deterministic waypoint simulation. A failed gate blocks
downstream steps while earlier artifacts stay reviewable.
"""

from __future__ import annotations

import json

from . import states
from .artifacts import store_artifact
from .exports import build_manifest, manifest_json
from .meta_model import MetaModelError
from .models import Job, JobEvent
from .rover import (
    MAX_MOTOR_EFFORT,
    MAX_SPEED_M_S,
    generate_rover_bom,
    generate_rover_spec,
    generate_rover_urdf,
    generate_starter_code,
)
from .robot_arm import (
    ARM_BOM_SCHEMA_VERSION,
    ARM_SPEC_SCHEMA_VERSION,
    arm_bom,
    generate_arm_starter_code,
    generate_arm_urdf,
    propose_tabletop_arm,
    validate_arm_design,
)
from .humanoid import (
    HUMANOID_BOM_VERSION,
    HUMANOID_SPEC_VERSION,
    generate_humanoid_starter_code,
    generate_humanoid_urdf,
    humanoid_bom,
    propose_humanoid,
    validate_humanoid_bom,
    validate_humanoid_design,
)
from .schemas import (
    BOM_SCHEMA_VERSION,
    SPEC_SCHEMA_VERSION,
    validate_bom,
    validate_mission,
    validate_spec,
)
from .simulation import ENGINE_NAME, ENGINE_VERSION, run_waypoint_route
from .validation import SEVERITY_BLOCKER, check_generated_code, validate_urdf

VALIDATOR_VERSION = "validators/v1"
DEMO_WAYPOINTS = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]


def run_to_review(
    job: Job, mission: dict, robot_class: str = "differential-drive-rover"
) -> list[str]:
    """Advance a fresh job to awaiting_review. Returns mission errors."""
    job.transition(states.SPECIFYING)
    if not isinstance(mission, dict):
        mission = {}
    mission = {**mission, "robot_class": robot_class}
    store_artifact(job, "mission", json.dumps(mission, indent=2), "mission/v1")
    errors = validate_mission(mission)
    if errors:
        store_artifact(
            job, "report", json.dumps({"errors": errors}, indent=2), "report/v1"
        )
        job.transition(states.FAILED)
        return errors
    if robot_class == "tabletop_robot_arm":
        try:
            spec = propose_tabletop_arm(mission)
        except (MetaModelError, ValueError) as exc:
            errors = [str(exc)]
            store_artifact(
                job, "report", json.dumps({"errors": errors}, indent=2), "report/v1"
            )
            job.transition(states.FAILED)
            return errors
        bom = arm_bom(spec)
        spec_version, bom_version = ARM_SPEC_SCHEMA_VERSION, ARM_BOM_SCHEMA_VERSION
    elif robot_class == "research_humanoid":
        try:
            spec = propose_humanoid(mission)
        except (MetaModelError, ValueError) as exc:
            errors = [str(exc)]
            store_artifact(
                job, "report", json.dumps({"errors": errors}, indent=2), "report/v1"
            )
            job.transition(states.FAILED)
            return errors
        bom = humanoid_bom(spec)
        spec_version, bom_version = HUMANOID_SPEC_VERSION, HUMANOID_BOM_VERSION
    else:
        spec = generate_rover_spec()
        bom = generate_rover_bom()
        spec_version, bom_version = SPEC_SCHEMA_VERSION, BOM_SCHEMA_VERSION
    store_artifact(job, "spec", json.dumps(spec, indent=2), spec_version)
    store_artifact(job, "bom", json.dumps(bom, indent=2), bom_version)
    job.transition(states.AWAITING_REVIEW)
    return []


def run_from_approval(job: Job) -> dict:
    """Generate artifacts, validate, simulate. Returns the run summary."""
    job.transition(states.GENERATING)
    bom_doc = json.loads(job.artifacts.get(kind="bom").body)
    spec_doc = json.loads(job.artifacts.get(kind="spec").body)
    if spec_doc.get("robot_class") == "image_robot_design":
        from .image_design import (
            generate_cad_package,
            generate_image_robot_firmware_artifact,
            generate_image_robot_urdf,
            simulate_image_robot,
        )

        urdf = generate_image_robot_urdf(spec_doc)
        store_artifact(job, "urdf", urdf, "urdf/v1")
        cad_package = generate_cad_package(spec_doc)
        store_artifact(job, "cad", json.dumps(cad_package, indent=2), "forgebot-cad-package/v4")
        assembly_validation = json.loads(cad_package["files"]["assembly_validation.json"])
        job.transition(states.VALIDATING)
        findings = [
            {"severity": item.severity, "path": item.artifact_path, "message": item.message, "remediation": item.remediation}
            for item in validate_urdf(urdf, "image_robot.urdf")
        ]
        findings.extend({
            "severity": item["severity"], "path": f"cad/assembly/{item.get('code', 'check')}",
            "message": item["message"], "remediation": "Review the CAD assembly interfaces and component placement before using the concept.",
        } for item in assembly_validation["findings"] if item["severity"] in ("blocker", "warning"))
        blockers = [item for item in findings if item["severity"] == SEVERITY_BLOCKER]
        store_artifact(job, "report", json.dumps({"findings": findings, "assembly_validation": assembly_validation, "label": "conceptual geometry only"}, indent=2), "report/v1")
        simulation = simulate_image_robot(spec_doc)
        store_artifact(job, "sim", json.dumps(simulation, indent=2), "sim/v1")
        firmware_artifact_body = generate_image_robot_firmware_artifact(spec_doc, simulation)
        store_artifact(job, "code", firmware_artifact_body, "forgebot-mechatronics-firmware/v1")
        if blockers:
            freecad_result = {"schema_version": "forgebot-freecad-export/v1", "status": "blocked", "message": "FreeCAD export was skipped because the URDF or design validation found blockers."}
        else:
            from .freecad_export import FreeCADError, run_freecad_export

            try:
                freecad_result = run_freecad_export(job.id, cad_package)
            except FreeCADError as exc:
                freecad_result = {
                    "schema_version": "forgebot-freecad-export/v1",
                    "status": "unavailable" if "not found" in str(exc).lower() else "failed",
                    "message": str(exc),
                }
        store_artifact(job, "cad_export", json.dumps(freecad_result, indent=2), "forgebot-freecad-export/v1")
        from .fabrication import estimate_3d_print_and_fabrication_costs
        fab_report = estimate_3d_print_and_fabrication_costs(
            spec_doc,
            cad_export=freecad_result if freecad_result.get("status") == "completed" else None,
            mechatronics=simulation.get("mechatronics"),
        )
        enhanced_bom = {
            "schema_version": "forgebot-bom/v2",
            "items": bom_doc.get("items", []),
            "fabrication_estimate": fab_report,
        }
        store_artifact(job, "bom", json.dumps(enhanced_bom, indent=2), "forgebot-bom/v2")
        JobEvent.objects.create(
            job=job,
            state=job.state,
            detail={
                "stage": "freecad_export",
                "status": freecad_result["status"],
                "message": "FreeCAD created the native CAD assembly." if freecad_result["status"] == "completed" else freecad_result.get("message", "FreeCAD export did not run."),
            },
        )
        if blockers:
            job.transition(states.FAILED)
            return {"passed": False, "blockers": len(blockers), "assembly_validation": assembly_validation["status"], "freecad_export": freecad_result["status"], "simulation": "not run"}
        manifest = build_manifest(
            input_revision=job.input_revision,
            provider=job.provider,
            model=job.model,
            artifact_versions={"spec": "image-robot-concept/v1", "urdf": "urdf/v1", "cad": "forgebot-cad-package/v4", "validators": VALIDATOR_VERSION},
            validator_version=VALIDATOR_VERSION,
            sim_config=simulation,
        )
        store_artifact(job, "manifest", manifest_json(manifest), "export/v1")
        job.transition(states.SUCCEEDED)
        return {"passed": bool(simulation.get("passed", True)), "blockers": 0, "assembly_validation": assembly_validation["status"], "freecad_export": freecad_result["status"], "simulation": "simulated"}
    if spec_doc.get("robot_class") == "tabletop_robot_arm":
        urdf = generate_arm_urdf(spec_doc)
        code = generate_arm_starter_code(spec_doc)
    elif spec_doc.get("robot_class") == "research_humanoid":
        urdf = generate_humanoid_urdf(spec_doc)
        code = generate_humanoid_starter_code(spec_doc)
    else:
        urdf = generate_rover_urdf()
        code = generate_starter_code()
    store_artifact(job, "urdf", urdf, "urdf/v1")
    store_artifact(job, "code", code, "code/v1")

    job.transition(states.VALIDATING)
    findings = [
        {"severity": f.severity, "path": f.artifact_path, "message": f.message}
        for f in (
            validate_urdf(urdf)
            + check_generated_code(code)
        )
    ]
    if spec_doc.get("robot_class") == "tabletop_robot_arm":
        validators = (("spec", spec_doc, validate_arm_design),)
    elif spec_doc.get("robot_class") == "research_humanoid":
        validators = (
            ("spec", spec_doc, validate_humanoid_design),
            ("bom", bom_doc, validate_humanoid_bom),
        )
    else:
        validators = (("spec", spec_doc, validate_spec), ("bom", bom_doc, validate_bom))
    for label, doc, validator in validators:
        for err in validator(doc):
            findings.append({"severity": SEVERITY_BLOCKER, "path": label, "message": err})
    blockers = [f for f in findings if f["severity"] == SEVERITY_BLOCKER]
    store_artifact(
        job, "report", json.dumps({"findings": findings}, indent=2), "report/v1"
    )
    if blockers:
        job.transition(states.FAILED)
        return {"passed": False, "blockers": len(blockers)}

    if spec_doc.get("robot_class") in ("tabletop_robot_arm", "research_humanoid"):
        is_humanoid = spec_doc.get("robot_class") == "research_humanoid"
        spec_version = HUMANOID_SPEC_VERSION if is_humanoid else ARM_SPEC_SCHEMA_VERSION
        bom_version = HUMANOID_BOM_VERSION if is_humanoid else ARM_BOM_SCHEMA_VERSION
        simulation_status = {
            "status": "not_run",
            "reason": "Humanoid physics simulation is the next milestone; this run produced a URDF kinematic concept only."
            if is_humanoid else "Arm physics simulation is not implemented yet.",
        }
        manifest = build_manifest(
            input_revision=job.input_revision,
            provider=job.provider,
            model=job.model,
            artifact_versions={
                "spec": spec_version,
                "bom": bom_version,
                "validators": VALIDATOR_VERSION,
            },
            validator_version=VALIDATOR_VERSION,
            sim_config=simulation_status,
        )
        if is_humanoid:
            store_artifact(job, "sim", json.dumps(simulation_status, indent=2), "sim/v1")
        store_artifact(job, "manifest", manifest_json(manifest), "export/v1")
        job.transition(states.SUCCEEDED)
        return {
            "passed": True,
            "blockers": 0,
            "simulation": "not implemented" if not is_humanoid else "not run; physics integration is next",
        }

    job.transition(states.SIMULATING)
    sim = run_waypoint_route(
        DEMO_WAYPOINTS,
        speed_m_s=0.8,
        max_speed_m_s=MAX_SPEED_M_S,
        max_motor_effort=MAX_MOTOR_EFFORT,
    )
    sim_doc = {
        "engine": f"{ENGINE_NAME} {ENGINE_VERSION}",
        "waypoints": DEMO_WAYPOINTS,
        "passed": sim.passed,
        "distance_m": sim.distance_m,
        "collisions": sim.collisions,
        "violations": sim.violations,
        "assumptions": sim.assumptions,
        "label": "simulated; requires engineering review",
    }
    store_artifact(job, "sim", json.dumps(sim_doc, indent=2), "sim/v1")
    manifest = build_manifest(
        input_revision=job.input_revision,
        provider=job.provider,
        model=job.model,
        artifact_versions={
            "spec": SPEC_SCHEMA_VERSION,
            "bom": BOM_SCHEMA_VERSION,
            "validators": VALIDATOR_VERSION,
        },
        validator_version=VALIDATOR_VERSION,
        sim_config=sim_doc,
    )
    store_artifact(job, "manifest", manifest_json(manifest), "export/v1")
    job.transition(states.SUCCEEDED if sim.passed else states.FAILED)
    return {"passed": sim.passed, "blockers": 0}
