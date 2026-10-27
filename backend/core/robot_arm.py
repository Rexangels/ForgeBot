"""Typed first-pass design generation for a tabletop 4-DOF arm."""

from __future__ import annotations

import json
import math

from .meta_model import generate_structured_json

ARM_SPEC_SCHEMA_VERSION = "robot-arm-spec/v1"
ARM_BOM_SCHEMA_VERSION = "robot-arm-bom/v1"
JOINT_NAMES = ("base_yaw", "shoulder_pitch", "elbow_pitch", "wrist_pitch")

ARM_DESIGN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "robot_class", "name", "summary", "geometry",
        "masses", "payload_kg", "joint_limits", "gripper", "bom",
        "assumptions", "open_questions",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": [ARM_SPEC_SCHEMA_VERSION]},
        "robot_class": {"type": "string", "enum": ["tabletop_robot_arm"]},
        "name": {"type": "string"},
        "summary": {"type": "string"},
        "geometry": {
            "type": "object", "additionalProperties": False,
            "required": ["base_diameter_m", "base_height_m", "upper_arm_length_m", "forearm_length_m", "wrist_length_m"],
            "properties": {
                "base_diameter_m": {"type": "number", "minimum": 0.06, "maximum": 0.18},
                "base_height_m": {"type": "number", "minimum": 0.03, "maximum": 0.12},
                "upper_arm_length_m": {"type": "number", "minimum": 0.08, "maximum": 0.25},
                "forearm_length_m": {"type": "number", "minimum": 0.08, "maximum": 0.25},
                "wrist_length_m": {"type": "number", "minimum": 0.04, "maximum": 0.16},
            },
        },
        "masses": {
            "type": "object", "additionalProperties": False,
            "required": ["base_kg", "shoulder_kg", "upper_arm_kg", "forearm_kg", "wrist_kg", "tool_kg"],
            "properties": {
                "base_kg": {"type": "number", "minimum": 0.2, "maximum": 3.0},
                "shoulder_kg": {"type": "number", "minimum": 0.05, "maximum": 1.0},
                "upper_arm_kg": {"type": "number", "minimum": 0.05, "maximum": 1.0},
                "forearm_kg": {"type": "number", "minimum": 0.05, "maximum": 1.0},
                "wrist_kg": {"type": "number", "minimum": 0.03, "maximum": 0.5},
                "tool_kg": {"type": "number", "minimum": 0.03, "maximum": 0.5},
            },
        },
        "payload_kg": {"type": "number", "minimum": 0.02, "maximum": 0.25},
        "joint_limits": {
            "type": "array", "minItems": 4, "maxItems": 4,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "lower_deg", "upper_deg", "effort_nm", "velocity_rad_s"],
                "properties": {
                    "name": {"type": "string", "enum": list(JOINT_NAMES)},
                    "lower_deg": {"type": "number", "minimum": -180, "maximum": 180},
                    "upper_deg": {"type": "number", "minimum": -180, "maximum": 180},
                    "effort_nm": {"type": "number", "minimum": 0.2, "maximum": 15},
                    "velocity_rad_s": {"type": "number", "minimum": 0.1, "maximum": 2},
                },
            },
        },
        "gripper": {
            "type": "object", "additionalProperties": False,
            "required": ["type", "jaw_opening_m", "grip_force_n"],
            "properties": {
                "type": {"type": "string", "enum": ["parallel_jaw"]},
                "jaw_opening_m": {"type": "number", "minimum": 0.01, "maximum": 0.08},
                "grip_force_n": {"type": "number", "minimum": 1, "maximum": 20},
            },
        },
        "bom": {
            "type": "array", "minItems": 4, "maxItems": 12,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["component", "purpose", "availability"],
                "properties": {
                    "component": {"type": "string"},
                    "purpose": {"type": "string"},
                    "availability": {"type": "string", "enum": ["unverified"]},
                },
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}


def propose_tabletop_arm(mission: dict) -> dict:
    """Ask Meta for a constrained conceptual design; verify it locally."""
    prompt = {
        "mission": mission.get("task", ""),
        "environment": mission.get("environment", ""),
        "units": mission.get("units", "si"),
        "design_boundary": {
            "robot_class": "tabletop_robot_arm",
            "degrees_of_freedom": len(JOINT_NAMES),
            "joint_order": list(JOINT_NAMES),
            "maximum_reach_m": 0.8,
            "maximum_payload_kg": 0.25,
            "purpose": "small tabletop pick-and-place; no human collaboration or safety certification",
        },
    }
    spec = generate_structured_json(
        system_prompt=(
            "You are a conceptual robotics design assistant. Propose a plausible, "
            "conservative tabletop 4-DOF arm for small-object pick-and-place. "
            "Return only the requested schema. Do not claim a component is verified, "
            "available, safe for human collaboration, or manufacturing-ready. Use SI "
            "units. Keep the link lengths and masses physically plausible. Mark every "
            "BOM item unverified. Put unknowns and engineering assumptions in the "
            "provided arrays. This proposal will be reviewed and checked by software, "
            "but software checks are not engineering certification."
        ),
        user_prompt=json.dumps(prompt),
        schema_name="forgebot_tabletop_arm_design",
        schema=ARM_DESIGN_SCHEMA,
    )
    errors = validate_arm_design(spec)
    if errors:
        raise ValueError("Generated arm design did not pass validation: " + "; ".join(errors))
    return spec


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_arm_design(spec: dict) -> list[str]:
    """Semantic checks that complement Meta's constrained JSON response."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["design must be an object"]
    if spec.get("schema_version") != ARM_SPEC_SCHEMA_VERSION:
        errors.append(f"schema_version must be {ARM_SPEC_SCHEMA_VERSION}")
    if spec.get("robot_class") != "tabletop_robot_arm":
        errors.append("robot_class must be tabletop_robot_arm")
    geometry = spec.get("geometry")
    if not isinstance(geometry, dict):
        errors.append("geometry must be an object")
        geometry = {}
    else:
        for key in ("base_diameter_m", "base_height_m", "upper_arm_length_m", "forearm_length_m", "wrist_length_m"):
            if not _finite_number(geometry.get(key)) or geometry[key] <= 0:
                errors.append(f"geometry.{key} must be a finite positive number")
    lengths = [geometry.get(key) for key in ("upper_arm_length_m", "forearm_length_m", "wrist_length_m")]
    if all(_finite_number(item) for item in lengths) and sum(lengths) > 0.8:
        errors.append("maximum link reach must not exceed 0.8 m")
    masses = spec.get("masses")
    if not isinstance(masses, dict):
        errors.append("masses must be an object")
    elif all(_finite_number(value) for value in masses.values()) and sum(masses.values()) > 5:
        errors.append("estimated robot mass must not exceed 5 kg")
    if not _finite_number(spec.get("payload_kg")) or spec["payload_kg"] <= 0:
        errors.append("payload_kg must be a finite positive number")
    elif spec["payload_kg"] > 0.25:
        errors.append("payload must not exceed the 0.25 kg MVP limit")
    joint_limits = spec.get("joint_limits")
    if not isinstance(joint_limits, list) or len(joint_limits) != len(JOINT_NAMES):
        errors.append("joint_limits must define the four supported joints")
    else:
        names = [joint.get("name") if isinstance(joint, dict) else None for joint in joint_limits]
        if names != list(JOINT_NAMES):
            errors.append("joint_limits must appear once in the supported joint order")
        for index, joint in enumerate(joint_limits):
            if not isinstance(joint, dict):
                continue
            lower, upper = joint.get("lower_deg"), joint.get("upper_deg")
            if _finite_number(lower) and _finite_number(upper) and lower >= upper:
                errors.append(f"joint_limits[{index}] lower_deg must be less than upper_deg")
            for key in ("effort_nm", "velocity_rad_s"):
                if not _finite_number(joint.get(key)) or joint[key] <= 0:
                    errors.append(f"joint_limits[{index}].{key} must be a finite positive number")
            for key in ("lower_deg", "upper_deg"):
                if not _finite_number(joint.get(key)) or not -180 <= joint[key] <= 180:
                    errors.append(f"joint_limits[{index}].{key} must be between -180 and 180 degrees")
    if isinstance(masses, dict):
        for key in ("base_kg", "shoulder_kg", "upper_arm_kg", "forearm_kg", "wrist_kg", "tool_kg"):
            if not _finite_number(masses.get(key)) or masses[key] <= 0:
                errors.append(f"masses.{key} must be a finite positive number")
    gripper = spec.get("gripper")
    if not isinstance(gripper, dict):
        errors.append("gripper must be an object")
    else:
        for key in ("jaw_opening_m", "grip_force_n"):
            if not _finite_number(gripper.get(key)) or gripper[key] <= 0:
                errors.append(f"gripper.{key} must be a finite positive number")
    bom = spec.get("bom")
    if not isinstance(bom, list) or len(bom) < 4:
        errors.append("BOM must contain at least four proposed components")
    elif any(not isinstance(item, dict) or item.get("availability") != "unverified" for item in bom):
        errors.append("all generated BOM items must remain marked unverified")
    return errors


ARM_REPAIR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["parameter", "joint_name", "value", "rationale", "review_notes"],
    "properties": {
        "parameter": {"type": "string", "enum": ["joint_effort_nm", "joint_velocity_rad_s", "grip_force_n", "payload_kg", "upper_arm_length_m", "forearm_length_m", "wrist_length_m"]},
        "joint_name": {"type": "string", "enum": [*JOINT_NAMES, "none"]},
        "value": {"type": "number"},
        "rationale": {"type": "string"},
        "review_notes": {"type": "string"},
    },
}


def propose_arm_repair(spec: dict, simulation: dict) -> dict:
    """Ask Meta for one bounded change based on the latest local physics result."""
    if not isinstance(simulation, dict) or simulation.get("schema_version") != "forgebot-arm-simulation/v1":
        raise ValueError("A current MuJoCo simulation result is required before proposing a repair.")
    result = generate_structured_json(
        system_prompt=(
            "You review a conceptual tabletop robot arm after a local physics simulation. Propose exactly one conservative, reviewable change to one existing numeric design parameter. "
            "Use only a parameter allowed by the schema, keep it inside the original design schema bounds, and use a joint_name only for joint limits. "
            "Do not claim the change is correct, useful, safe, or verified. Explain uncertainty and say the user must accept it, rebuild, and rerun MuJoCo."
        ),
        user_prompt=(
            "Reviewed arm specification:\n" + json.dumps(spec, ensure_ascii=False) + "\nLatest MuJoCo task and findings:\n"
            + json.dumps({key: simulation.get(key) for key in ("engine", "engine_version", "task", "controller", "passed", "metrics", "assumptions")}, ensure_ascii=False)
            + "\nChoose one small change that addresses a recorded failure. Prefer changing actuator effort/speed, grip force, payload target rating, or one link length only when the measurements support it."
        ),
        schema_name="forgebot_arm_repair_proposal",
        schema=ARM_REPAIR_SCHEMA,
    )
    if result.get("parameter") not in ARM_REPAIR_SCHEMA["properties"]["parameter"]["enum"]:
        raise ValueError("Model returned an unsupported arm parameter.")
    target = result.get("joint_name")
    parameter = result["parameter"]
    if parameter in ("joint_effort_nm", "joint_velocity_rad_s"):
        if target not in JOINT_NAMES:
            raise ValueError("A joint repair must identify one supported joint.")
        low, high = (0.2, 15.0) if parameter == "joint_effort_nm" else (0.1, 2.0)
    else:
        if target != "none":
            raise ValueError("A non-joint repair must use joint_name=none.")
        low, high = {
            "grip_force_n": (1.0, 20.0),
            "payload_kg": (0.02, 0.25),
            "upper_arm_length_m": (0.08, 0.25),
            "forearm_length_m": (0.08, 0.25),
            "wrist_length_m": (0.04, 0.16),
        }[parameter]
    if not _finite_number(result.get("value")) or not low <= result["value"] <= high:
        raise ValueError(f"Proposed {parameter} must be between {low:g} and {high:g}.")
    for key in ("rationale", "review_notes"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise ValueError("Model returned an incomplete repair explanation.")
        result[key] = result[key].strip()[:1200]
    result["requires_human_review"] = True
    return result


def apply_arm_repair(spec: dict, proposal: dict) -> dict:
    """Apply one schema-bounded repair to a copy and rerun local design checks."""
    parameter = proposal.get("parameter")
    value = proposal.get("value")
    joint_name = proposal.get("joint_name")
    if parameter not in ARM_REPAIR_SCHEMA["properties"]["parameter"]["enum"] or not _finite_number(value):
        raise ValueError("Repair proposal contains an unsupported parameter or value.")
    if parameter in ("joint_effort_nm", "joint_velocity_rad_s"):
        if joint_name not in JOINT_NAMES:
            raise ValueError("Repair proposal references an unknown joint.")
        low, high = (0.2, 15.0) if parameter == "joint_effort_nm" else (0.1, 2.0)
    else:
        if joint_name != "none":
            raise ValueError("A non-joint repair must use joint_name=none.")
        low, high = {
            "grip_force_n": (1.0, 20.0), "payload_kg": (0.02, 0.25),
            "upper_arm_length_m": (0.08, 0.25), "forearm_length_m": (0.08, 0.25),
            "wrist_length_m": (0.04, 0.16),
        }[parameter]
    if not low <= value <= high:
        raise ValueError(f"Proposed {parameter} must be between {low:g} and {high:g}.")
    updated = json.loads(json.dumps(spec))
    if parameter in ("joint_effort_nm", "joint_velocity_rad_s"):
        joint = next((item for item in updated["joint_limits"] if item.get("name") == joint_name), None)
        if joint is None:
            raise ValueError("Repair proposal references an unknown joint.")
        joint["effort_nm" if parameter == "joint_effort_nm" else "velocity_rad_s"] = float(value)
    elif parameter == "grip_force_n":
        updated["gripper"][parameter] = float(value)
    elif parameter == "payload_kg":
        updated[parameter] = float(value)
    else:
        updated["geometry"][parameter] = float(value)
    updated.setdefault("design_history", []).append({"action": "ai_simulation_repair_applied", "parameter": parameter, "joint_name": joint_name, "value": float(value), "requires_resimulation": True})
    errors = validate_arm_design(updated)
    if errors:
        raise ValueError("Repair does not pass local design checks: " + "; ".join(errors))
    return updated


def _inertial(name: str, mass: float, length: float, radius: float, axis: str = "x") -> str:
    if axis == "z":
        ixx = iyy = mass * (3 * radius**2 + length**2) / 12
        izz = mass * radius**2 / 2
    else:
        ixx = mass * radius**2 / 2
        iyy = izz = mass * (3 * radius**2 + length**2) / 12
    return (
        f'<inertial><origin xyz="0 0 0" rpy="0 0 0"/><mass value="{mass:.5f}"/>'
        f'<inertia ixx="{ixx:.7f}" ixy="0" ixz="0" iyy="{iyy:.7f}" iyz="0" izz="{izz:.7f}"/></inertial>'
    )


def _arm_link(name: str, inertial: str, visual: str, collision: str | None = None) -> str:
    collision = collision or visual
    return f'<link name="{name}">{inertial}<visual>{visual}</visual><collision>{collision}</collision></link>'


def generate_arm_urdf(spec: dict) -> str:
    """Create a primitive-geometry URDF from a validated design proposal."""
    errors = validate_arm_design(spec)
    if errors:
        raise ValueError("Cannot generate URDF from invalid design: " + "; ".join(errors))
    g, m, grip = spec["geometry"], spec["masses"], spec["gripper"]
    base_r = g["base_diameter_m"] / 2
    base_h = g["base_height_m"]
    upper_l, fore_l, wrist_l = g["upper_arm_length_m"], g["forearm_length_m"], g["wrist_length_m"]
    arm_r, shoulder_r = 0.018, 0.035

    links = [
        _arm_link("base_link", _inertial("base_link", m["base_kg"], base_h, base_r, "z"),
                  f'<origin xyz="0 0 {base_h/2:.5f}"/><geometry><cylinder radius="{base_r:.5f}" length="{base_h:.5f}"/></geometry>'),
        _arm_link("shoulder_link", _inertial("shoulder_link", m["shoulder_kg"], shoulder_r * 2, shoulder_r, "z"),
                  f'<origin xyz="0 0 {shoulder_r:.5f}"/><geometry><sphere radius="{shoulder_r:.5f}"/></geometry>'),
        _arm_link("upper_arm_link", _inertial("upper_arm_link", m["upper_arm_kg"], upper_l, arm_r),
                  f'<origin xyz="{upper_l/2:.5f} 0 0" rpy="0 1.5707963 0"/><geometry><cylinder radius="{arm_r:.5f}" length="{upper_l:.5f}"/></geometry>'),
        _arm_link("forearm_link", _inertial("forearm_link", m["forearm_kg"], fore_l, arm_r),
                  f'<origin xyz="{fore_l/2:.5f} 0 0" rpy="0 1.5707963 0"/><geometry><cylinder radius="{arm_r:.5f}" length="{fore_l:.5f}"/></geometry>'),
        _arm_link("wrist_link", _inertial("wrist_link", m["wrist_kg"], wrist_l, 0.014),
                  f'<origin xyz="{wrist_l/2:.5f} 0 0" rpy="0 1.5707963 0"/><geometry><cylinder radius="0.014" length="{wrist_l:.5f}"/></geometry>'),
    ]
    jaw = grip["jaw_opening_m"]
    jaw_thickness = 0.008
    jaw_length = max(0.025, jaw * 0.7)
    jaws = (
        f'<visual name="left_finger"><origin xyz="0.025 {jaw/2:.5f} 0"/><geometry><box size="{jaw_length:.5f} {jaw_thickness} 0.018"/></geometry></visual>'
        f'<visual name="right_finger"><origin xyz="0.025 -{jaw/2:.5f} 0"/><geometry><box size="{jaw_length:.5f} {jaw_thickness} 0.018"/></geometry></visual>'
        f'<collision><origin xyz="0.025 {jaw/2:.5f} 0"/><geometry><box size="{jaw_length:.5f} {jaw_thickness} 0.018"/></geometry></collision>'
        f'<collision><origin xyz="0.025 -{jaw/2:.5f} 0"/><geometry><box size="{jaw_length:.5f} {jaw_thickness} 0.018"/></geometry></collision>'
    )
    links.append(
        f'<link name="tool0"><inertial><mass value="{m["tool_kg"]:.5f}"/><inertia ixx="0.0001" iyy="0.0001" izz="0.0001" ixy="0" ixz="0" iyz="0"/></inertial>{jaws}</link>'
    )

    joint_data = {item["name"]: item for item in spec["joint_limits"]}
    joints: list[str] = []
    chain = [
        ("base_yaw", "base_link", "shoulder_link", f"0 0 {base_h:.5f}", "0 0 1"),
        ("shoulder_pitch", "shoulder_link", "upper_arm_link", f"0 0 {shoulder_r*2:.5f}", "0 1 0"),
        ("elbow_pitch", "upper_arm_link", "forearm_link", f"{upper_l:.5f} 0 0", "0 1 0"),
        ("wrist_pitch", "forearm_link", "wrist_link", f"{fore_l:.5f} 0 0", "0 1 0"),
    ]
    for name, parent, child, origin, axis in chain:
        limit = joint_data[name]
        lower = math.radians(limit["lower_deg"])
        upper = math.radians(limit["upper_deg"])
        joints.append(
            f'<joint name="{name}" type="revolute"><parent link="{parent}"/><child link="{child}"/>'
            f'<origin xyz="{origin}" rpy="0 0 0"/><axis xyz="{axis}"/>'
            f'<limit lower="{lower:.6f}" upper="{upper:.6f}" effort="{limit["effort_nm"]:.4f}" velocity="{limit["velocity_rad_s"]:.4f}"/>'
            f'<dynamics damping="0.1" friction="0.0"/></joint>'
        )
    joints.append(
        f'<joint name="tool_mount" type="fixed"><parent link="wrist_link"/><child link="tool0"/>'
        f'<origin xyz="{wrist_l:.5f} 0 0" rpy="0 0 0"/></joint>'
    )
    return "<?xml version=\"1.0\"?>\n<robot name=\"forgebot_tabletop_arm\">\n" + "\n".join(links + joints) + "\n</robot>\n"


def generate_arm_starter_code(spec: dict) -> str:
    limits = {joint["name"]: joint for joint in spec["joint_limits"]}
    rows = ",\n".join(
        f'    "{name}": ({math.radians(limits[name]["lower_deg"]):.5f}, '
        f'{math.radians(limits[name]["upper_deg"]):.5f}, {limits[name]["velocity_rad_s"]:.4f})'
        for name in JOINT_NAMES
    )
    return f'''"""Generated tabletop-arm controller scaffold.

REQUIRES hardware-specific drivers, calibration, safety review, and testing.
This file does not command motors by itself.
"""

JOINT_LIMITS_RAD = {{
{rows}
}}
MAX_PAYLOAD_KG = {spec["payload_kg"]:.4f}
GRIPPER_MAX_OPENING_M = {spec["gripper"]["jaw_opening_m"]:.4f}


def validate_joint_target(targets):
    """Reject incomplete or out-of-range position targets."""
    if set(targets) != set(JOINT_LIMITS_RAD):
        raise ValueError("Targets must include exactly the configured arm joints")
    for name, value in targets.items():
        low, high, _velocity = JOINT_LIMITS_RAD[name]
        if not low <= value <= high:
            raise ValueError(f"{{name}} target is outside its configured limits")
    return True


def plan_pick_and_place(pick_pose, place_pose):
    """Placeholder for a reviewed inverse-kinematics and trajectory planner."""
    raise NotImplementedError(
        "Add a validated kinematics/trajectory backend before hardware execution"
    )
'''


def arm_bom(spec: dict) -> dict:
    return {
        "schema_version": ARM_BOM_SCHEMA_VERSION,
        "items": spec["bom"],
        "notice": "Conceptual components only; availability and compatibility are unverified.",
    }
