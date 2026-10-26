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
from .models import Job
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
    if spec_doc.get("robot_class") == "tabletop_robot_arm":
        urdf = generate_arm_urdf(spec_doc)
        code = generate_arm_starter_code(spec_doc)
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

    if spec_doc.get("robot_class") == "tabletop_robot_arm":
        manifest = build_manifest(
            input_revision=job.input_revision,
            provider=job.provider,
            model=job.model,
            artifact_versions={
                "spec": ARM_SPEC_SCHEMA_VERSION,
                "bom": ARM_BOM_SCHEMA_VERSION,
                "validators": VALIDATOR_VERSION,
            },
            validator_version=VALIDATOR_VERSION,
            sim_config={
                "status": "not_run",
                "reason": "Arm physics simulation is not implemented yet.",
            },
        )
        store_artifact(job, "manifest", manifest_json(manifest), "export/v1")
        job.transition(states.SUCCEEDED)
        return {"passed": True, "blockers": 0, "simulation": "not implemented"}

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
