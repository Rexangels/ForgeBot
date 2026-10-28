"""Constrained design and URDF generation for a research humanoid concept.

The output is a reviewable kinematic concept, not manufacturing CAD or a
validated dynamic model. Body dimensions come from Meta; topology and joint
interfaces are generated locally from a fixed, documented template.
"""

from __future__ import annotations

import json
import math

from .meta_model import generate_structured_json

HUMANOID_SPEC_VERSION = "research-humanoid/v1"
HUMANOID_BOM_VERSION = "research-humanoid-bom/v1"
ROBOT_CLASS = "research_humanoid"

_GEOMETRY_FIELDS = {
    "pelvis_width_m": (0.16, 0.36),
    "pelvis_height_m": (0.10, 0.24),
    "shoulder_width_m": (0.25, 0.56),
    "torso_length_m": (0.28, 0.62),
    "neck_height_m": (0.04, 0.15),
    "head_height_m": (0.14, 0.30),
    "upper_arm_length_m": (0.20, 0.44),
    "forearm_length_m": (0.18, 0.40),
    "hand_length_m": (0.08, 0.18),
    "thigh_length_m": (0.25, 0.48),
    "shin_length_m": (0.25, 0.48),
    "foot_length_m": (0.16, 0.32),
    "foot_height_m": (0.06, 0.16),
}

_GEOMETRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_GEOMETRY_FIELDS),
    "properties": {
        name: {"type": "number", "minimum": bounds[0], "maximum": bounds[1]}
        for name, bounds in _GEOMETRY_FIELDS.items()
    },
}

_JOINT_LIMITS_DEG = {
    "hip_yaw": (-35, 35, 70, 1.0),
    "hip_roll": (-30, 30, 90, 1.0),
    "hip_pitch": (-100, 45, 140, 1.2),
    "knee_pitch": (0, 135, 120, 1.2),
    "ankle_pitch": (-45, 45, 65, 1.0),
    "ankle_roll": (-30, 30, 45, 1.0),
    "shoulder_yaw": (-90, 90, 35, 1.5),
    "shoulder_roll": (-90, 90, 35, 1.5),
    "shoulder_pitch": (-120, 120, 45, 1.5),
    "elbow_pitch": (0, 135, 30, 1.5),
    "wrist_yaw": (-90, 90, 8, 2.0),
    "wrist_pitch": (-90, 90, 8, 2.0),
    "wrist_roll": (-90, 90, 8, 2.0),
    "waist_yaw": (-45, 45, 80, 1.0),
    "waist_roll": (-25, 25, 80, 1.0),
    "waist_pitch": (-35, 35, 80, 1.0),
    "neck_yaw": (-75, 75, 8, 1.0),
    "neck_pitch": (-40, 40, 8, 1.0),
    "finger_flex": (0, 90, 2, 2.0),
}

HUMANOID_DESIGN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "robot_class", "name", "summary", "height_m",
        "mass_kg", "payload_per_arm_kg", "battery_energy_wh", "geometry",
        "sensor_suite", "compute_platform", "bom", "assumptions", "open_questions",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": [HUMANOID_SPEC_VERSION]},
        "robot_class": {"type": "string", "enum": [ROBOT_CLASS]},
        "name": {"type": "string", "minLength": 1, "maxLength": 100},
        "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
        "height_m": {"type": "number", "minimum": 1.0, "maximum": 1.7},
        "mass_kg": {"type": "number", "minimum": 20, "maximum": 65},
        "payload_per_arm_kg": {"type": "number", "minimum": 0.5, "maximum": 3.0},
        "battery_energy_wh": {"type": "number", "minimum": 150, "maximum": 900},
        "geometry": _GEOMETRY_SCHEMA,
        "sensor_suite": {
            "type": "array", "minItems": 3, "maxItems": 5,
            "items": {"type": "string", "enum": [
                "head_rgbd_camera", "wrist_rgb_cameras", "lidar", "imu", "tactile_hands"
            ]},
        },
        "compute_platform": {"type": "string", "minLength": 3, "maxLength": 160},
        "bom": {
            "type": "array", "minItems": 8, "maxItems": 16,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["component", "purpose", "availability"],
                "properties": {
                    "component": {"type": "string", "minLength": 2, "maxLength": 100},
                    "purpose": {"type": "string", "minLength": 2, "maxLength": 240},
                    "availability": {"type": "string", "enum": ["unverified"]},
                },
            },
        },
        "assumptions": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}


def propose_humanoid(mission: dict) -> dict:
    """Ask Meta for dimensions and integration choices inside strict bounds."""
    prompt = {
        "mission": mission.get("task", ""),
        "environment": mission.get("environment", ""),
        "units": mission.get("units", "si"),
        "reference": "A general-purpose research humanoid such as the public Agile ONE SN concept.",
        "design_boundary": {
            "robot_class": ROBOT_CLASS,
            "body_degrees_of_freedom": 31,
            "hand_degrees_of_freedom_per_hand": 20,
            "height_m_definition": "sum of foot_height, shin, thigh, pelvis, torso, neck, and head segment heights",
            "maximum_payload_per_arm_kg": 3.0,
            "sensor_options": [
                "head_rgbd_camera", "wrist_rgb_cameras", "lidar", "imu", "tactile_hands"
            ],
            "design_intent": "original simplified research concept; do not claim to reproduce a commercial robot",
        },
    }
    spec = generate_structured_json(
        system_prompt=(
            "You are a robotics concept-design assistant. Propose a plausible, conservative "
            "research humanoid using the required schema. Respect all dimensional, mass, "
            "payload, and battery bounds, and set height_m to the sum of the specified "
            "vertical segments within 0.12 m. Keep BOM availability unverified. State that "
            "actuator sizing, inertias, balance, thermal design, battery runtime, and safety "
            "need engineering validation. The public reference is inspiration only; do not "
            "claim access to or reproduce proprietary designs. Return only schema JSON."
        ),
        user_prompt=json.dumps(prompt),
        schema_name="forgebot_research_humanoid_design",
        schema=HUMANOID_DESIGN_SCHEMA,
    )
    errors = validate_humanoid_design(spec)
    if errors:
        raise ValueError("Generated humanoid proposal did not pass validation: " + "; ".join(errors))
    spec["assumptions"].append(
        "The generated URDF uses equal provisional link-mass allocation and template inertias; these are not validated for dynamics or hardware."
    )
    return spec


def validate_humanoid_design(spec: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["design must be an object"]
    if spec.get("schema_version") != HUMANOID_SPEC_VERSION:
        errors.append(f"schema_version must be {HUMANOID_SPEC_VERSION}")
    if spec.get("robot_class") != ROBOT_CLASS:
        errors.append(f"robot_class must be {ROBOT_CLASS}")
    for name, low_high in (("height_m", (1.0, 1.7)), ("mass_kg", (20, 65)),
                           ("payload_per_arm_kg", (0.5, 3.0)), ("battery_energy_wh", (150, 900))):
        value = spec.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            errors.append(f"{name} must be a finite number")
        elif not low_high[0] <= value <= low_high[1]:
            errors.append(f"{name} must be between {low_high[0]} and {low_high[1]}")
    geometry = spec.get("geometry")
    if not isinstance(geometry, dict):
        errors.append("geometry must be an object")
        geometry = {}
    for name, (low, high) in _GEOMETRY_FIELDS.items():
        value = geometry.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            errors.append(f"geometry.{name} must be a finite number")
        elif not low <= value <= high:
            errors.append(f"geometry.{name} must be between {low} and {high} m")
    if all(isinstance(geometry.get(name), (int, float)) for name in _GEOMETRY_FIELDS):
        total_height = sum(geometry[name] for name in (
            "foot_height_m", "shin_length_m", "thigh_length_m", "pelvis_height_m",
            "torso_length_m", "neck_height_m", "head_height_m",
        ))
        height = spec.get("height_m")
        if isinstance(height, (int, float)) and abs(total_height - height) > 0.12:
            errors.append("height_m must agree with the sum of the vertical body segments within 0.12 m")
    sensors = spec.get("sensor_suite")
    if (not isinstance(sensors, list) or not all(isinstance(item, str) for item in sensors)
            or len(set(sensors)) != len(sensors)):
        errors.append("sensor_suite must be a list of unique sensor identifiers")
    bom = spec.get("bom")
    if not isinstance(bom, list) or len(bom) < 8:
        errors.append("bom must contain at least eight conceptual components")
    else:
        for index, item in enumerate(bom):
            if not isinstance(item, dict) or not item.get("component") or not item.get("purpose"):
                errors.append(f"bom[{index}] needs a component and purpose")
            elif item.get("availability") != "unverified":
                errors.append(f"bom[{index}] availability must remain unverified")
    return errors


def validate_humanoid_bom(bom: dict) -> list[str]:
    errors = []
    if not isinstance(bom, dict) or bom.get("schema_version") != HUMANOID_BOM_VERSION:
        return [f"bom.schema_version must be {HUMANOID_BOM_VERSION}"]
    items = bom.get("items")
    if not isinstance(items, list) or len(items) < 8:
        return ["bom.items must contain at least eight components"]
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("component") or not item.get("purpose"):
            errors.append(f"bom.items[{index}] needs a component and purpose")
        elif item.get("availability") != "unverified":
            errors.append(f"bom.items[{index}].availability must be unverified")
    return errors


def humanoid_bom(spec: dict) -> dict:
    return {
        "schema_version": HUMANOID_BOM_VERSION,
        "items": spec["bom"],
        "notice": "Conceptual components only; compatibility, availability, cost, and sizing are unverified.",
    }


def _fmt(values: tuple[float, float, float]) -> str:
    return " ".join(f"{value:.5f}" for value in values)


def _link_xml(name: str, mass: float, box: tuple[float, float, float] | None = None,
              visual_xyz: tuple[float, float, float] = (0, 0, 0)) -> str:
    x, y, z = box or (0.04, 0.04, 0.04)
    visual = ""
    if box:
        visual = (
            f'<visual><origin xyz="{_fmt(visual_xyz)}"/><geometry><box size="{_fmt(box)}"/></geometry>'
            '<material name="humanoid_teal"><color rgba="0.12 0.43 0.39 1"/></material></visual>'
            f'<collision><origin xyz="{_fmt(visual_xyz)}"/><geometry><box size="{_fmt(box)}"/></geometry></collision>'
        )
    inertia = max(0.000001, mass * (y * y + z * z) / 12)
    inertia_y = max(0.000001, mass * (x * x + z * z) / 12)
    inertia_z = max(0.000001, mass * (x * x + y * y) / 12)
    return (
        f'<link name="{name}"><inertial><origin xyz="{_fmt(visual_xyz)}"/><mass value="{mass:.6f}"/>'
        f'<inertia ixx="{inertia:.8f}" ixy="0" ixz="0" iyy="{inertia_y:.8f}" iyz="0" izz="{inertia_z:.8f}"/>'
        f'</inertial>{visual}</link>'
    )


def _joint_xml(name: str, parent: str, child: str, xyz: tuple[float, float, float],
               axis: tuple[int, int, int] = (0, 0, 1), kind: str = "revolute") -> str:
    origin = f'<origin xyz="{_fmt(xyz)}" rpy="0 0 0"/>'
    parent_child = f'<parent link="{parent}"/><child link="{child}"/>'
    if kind == "fixed":
        return f'<joint name="{name}" type="fixed">{parent_child}{origin}</joint>'
    family = "finger_flex" if name.startswith("finger_") else next(
        (key for key in _JOINT_LIMITS_DEG if name.endswith(key)), None
    )
    if family is None:
        raise ValueError(f"No joint-limit template is configured for {name!r}")
    lower, upper, effort, velocity = _JOINT_LIMITS_DEG[family]
    return (
        f'<joint name="{name}" type="revolute">{parent_child}{origin}'
        f'<axis xyz="{axis[0]} {axis[1]} {axis[2]}"/>'
        f'<limit lower="{math.radians(lower):.6f}" upper="{math.radians(upper):.6f}" '
        f'effort="{effort:.4f}" velocity="{velocity:.4f}"/>'
        '<dynamics damping="0.1" friction="0.0"/></joint>'
    )


def generate_humanoid_urdf(spec: dict) -> str:
    """Build a primitive-geometry 31-DOF body and 20-DOF-per-hand topology."""
    g = spec["geometry"]
    links: list[str] = []
    joints: list[str] = []
    pending_links: list[tuple[str, tuple[float, float, float] | None, tuple[float, float, float]]] = []

    def add_link(name, box=None, visual_xyz=(0, 0, 0)):
        pending_links.append((name, box, visual_xyz))

    def add_joint(name, parent, child, xyz, axis=(0, 0, 1), kind="revolute"):
        joints.append(_joint_xml(name, parent, child, xyz, axis, kind))

    # Pelvis and three-axis waist lead to the torso.
    add_link("pelvis_link", (g["pelvis_width_m"], 0.20, g["pelvis_height_m"]))
    add_link("waist_yaw_link")
    add_joint("waist_yaw", "pelvis_link", "waist_yaw_link", (0, 0, g["pelvis_height_m"] / 2), (0, 0, 1))
    add_link("waist_roll_link")
    add_joint("waist_roll", "waist_yaw_link", "waist_roll_link", (0, 0, 0), (1, 0, 0))
    add_link("torso_link", (0.28, 0.22, g["torso_length_m"]), (0, 0, g["torso_length_m"] / 2))
    add_joint("waist_pitch", "waist_roll_link", "torso_link", (0, 0, 0), (0, 1, 0))

    # Two-axis neck and head.
    add_link("neck_yaw_link")
    add_joint("neck_yaw", "torso_link", "neck_yaw_link", (0, 0, g["torso_length_m"]), (0, 0, 1))
    add_link("head_link", (0.18, 0.17, g["head_height_m"]), (0, 0, g["head_height_m"] / 2))
    add_joint("neck_pitch", "neck_yaw_link", "head_link", (0, 0, g["neck_height_m"]), (0, 1, 0))

    # Seven joints per arm: three shoulder, one elbow, and three wrist joints.
    hand_links: dict[str, str] = {}
    for side, sign in (("left", 1), ("right", -1)):
        parent = "torso_link"
        shoulder_origin = (0, sign * g["shoulder_width_m"] / 2, g["torso_length_m"] * 0.72)
        for family, axis in (("shoulder_yaw", (0, 0, 1)), ("shoulder_roll", (1, 0, 0))):
            child = f"{side}_{family}_link"
            add_link(child)
            add_joint(f"{side}_{family}", parent, child, shoulder_origin if parent == "torso_link" else (0, 0, 0), axis)
            parent, shoulder_origin = child, (0, 0, 0)
        upper = f"{side}_upper_arm_link"
        add_link(upper, (0.085, 0.085, g["upper_arm_length_m"]), (0, 0, -g["upper_arm_length_m"] / 2))
        add_joint(f"{side}_shoulder_pitch", parent, upper, (0, 0, 0), (0, 1, 0))
        forearm = f"{side}_forearm_link"
        add_link(forearm, (0.07, 0.07, g["forearm_length_m"]), (0, 0, -g["forearm_length_m"] / 2))
        add_joint(f"{side}_elbow_pitch", upper, forearm, (0, 0, -g["upper_arm_length_m"]), (0, 1, 0))
        parent = forearm
        for family, axis in (("wrist_yaw", (0, 0, 1)), ("wrist_roll", (1, 0, 0))):
            child = f"{side}_{family}_link"
            add_link(child)
            add_joint(f"{side}_{family}", parent, child,
                      (0, 0, -g["forearm_length_m"]) if parent == forearm else (0, 0, 0), axis)
            parent = child
        palm = f"{side}_hand_link"
        add_link(palm, (g["hand_length_m"] * 0.72, 0.10, 0.035), (g["hand_length_m"] * 0.36, 0, 0))
        add_joint(f"{side}_wrist_pitch", parent, palm, (0, 0, 0), (0, 1, 0))
        hand_links[side] = palm

        # Five four-joint digits give each hand a declared 20-DOF topology.
        finger_offsets = ((0.04, -0.036), (0.06, -0.018), (0.065, 0), (0.06, 0.018), (0.04, 0.036))
        finger_length = g["hand_length_m"] / 4
        for digit, (x_offset, y_offset) in zip(("thumb", "index", "middle", "ring", "little"), finger_offsets):
            parent = palm
            for segment in range(1, 5):
                child = f"{side}_{digit}_{segment}_link"
                add_link(child, (finger_length, 0.012, 0.014), (finger_length / 2, 0, 0))
                joint_name = f"finger_{side}_{digit}_{segment}"
                add_joint(joint_name, parent, child,
                          (x_offset, y_offset, 0) if parent == palm else (finger_length, 0, 0),
                          (0, 1, 0))
                parent = child

    # Six joints per leg: three-axis hip, knee, and two-axis ankle.
    for side, sign in (("left", 1), ("right", -1)):
        parent = "pelvis_link"
        hip_origin = (0, sign * g["pelvis_width_m"] * 0.27, -g["pelvis_height_m"] / 2)
        for family, axis in (("hip_yaw", (0, 0, 1)), ("hip_roll", (1, 0, 0))):
            child = f"{side}_{family}_link"
            add_link(child)
            add_joint(f"{side}_{family}", parent, child, hip_origin if parent == "pelvis_link" else (0, 0, 0), axis)
            parent, hip_origin = child, (0, 0, 0)
        thigh = f"{side}_thigh_link"
        add_link(thigh, (0.13, 0.13, g["thigh_length_m"]), (0, 0, -g["thigh_length_m"] / 2))
        add_joint(f"{side}_hip_pitch", parent, thigh, (0, 0, 0), (0, 1, 0))
        shin = f"{side}_shin_link"
        add_link(shin, (0.10, 0.10, g["shin_length_m"]), (0, 0, -g["shin_length_m"] / 2))
        add_joint(f"{side}_knee_pitch", thigh, shin, (0, 0, -g["thigh_length_m"]), (0, 1, 0))
        ankle_pitch = f"{side}_ankle_pitch_link"
        add_link(ankle_pitch)
        add_joint(f"{side}_ankle_pitch", shin, ankle_pitch, (0, 0, -g["shin_length_m"]), (0, 1, 0))
        foot = f"{side}_foot_link"
        add_link(foot, (g["foot_length_m"], 0.12, g["foot_height_m"]),
                 (g["foot_length_m"] * 0.28, 0, -g["foot_height_m"] / 2))
        add_joint(f"{side}_ankle_roll", ankle_pitch, foot, (0, 0, 0), (1, 0, 0))

    # Sensor frames are explicit fixed links, matching the approved sensor suite.
    sensors = set(spec["sensor_suite"])
    sensor_frames = []
    if "head_rgbd_camera" in sensors:
        sensor_frames.append(("head_rgbd_frame", "head_link", (0.08, 0, g["head_height_m"] * 0.65)))
    if "imu" in sensors:
        sensor_frames.append(("torso_imu_frame", "torso_link", (0, 0, g["torso_length_m"] * 0.5)))
    if "lidar" in sensors:
        sensor_frames.append(("lidar_frame", "torso_link", (0.10, 0, g["torso_length_m"] * 0.55)))
    if "wrist_rgb_cameras" in sensors:
        sensor_frames.extend((
            ("left_wrist_camera_frame", hand_links["left"], (0.03, 0, 0)),
            ("right_wrist_camera_frame", hand_links["right"], (0.03, 0, 0)),
        ))
    if "tactile_hands" in sensors:
        sensor_frames.extend((
            ("left_hand_tactile_frame", hand_links["left"], (g["hand_length_m"] * 0.65, 0, 0)),
            ("right_hand_tactile_frame", hand_links["right"], (g["hand_length_m"] * 0.65, 0, 0)),
        ))
    for name, parent, xyz in sensor_frames:
        add_link(name, (0.025, 0.025, 0.025))
        add_joint(name.replace("_frame", "_mount"), parent, name, xyz, kind="fixed")

    # Mass follows volume: box links share the reviewed total by their box
    # volume, joint-only links and sensor frames by a 4 cm reference cube.
    # The total still sums to the reviewed mass; each link's share is now
    # proportional to its size instead of uniform across 77 links.
    def _volume(box):
        dims = box or (0.04, 0.04, 0.04)
        return dims[0] * dims[1] * dims[2]

    total_volume = sum(_volume(box) for _, box, _ in pending_links)
    for name, box, visual_xyz in pending_links:
        links.append(_link_xml(name, spec["mass_kg"] * _volume(box) / total_volume, box, visual_xyz))

    return (
        '<?xml version="1.0"?>\n<robot name="forgebot_research_humanoid">\n'
        + "\n".join(links + joints)
        + "\n</robot>\n"
    )


def generate_humanoid_starter_code(spec: dict) -> str:
    """Generate a non-executable joint interface and safety reminders."""
    return '''"""ForgeBot research humanoid command interface scaffold.

Conceptual joint map only. No hardware drivers are included or invoked.
Validate actuator ratings, balance, limits, stop behavior, and safety before use.
"""

BODY_DOF = 31
HAND_DOF_PER_HAND = 20
TOTAL_ACTUATED_JOINTS = BODY_DOF + 2 * HAND_DOF_PER_HAND
JOINT_GROUPS = {
    "waist": ("waist_yaw", "waist_roll", "waist_pitch"),
    "head": ("neck_yaw", "neck_pitch"),
    "arms_each": ("shoulder_yaw", "shoulder_roll", "shoulder_pitch", "elbow_pitch", "wrist_yaw", "wrist_roll", "wrist_pitch"),
    "legs_each": ("hip_yaw", "hip_roll", "hip_pitch", "knee_pitch", "ankle_pitch", "ankle_roll"),
    "hands_each": ("thumb", "index", "middle", "ring", "little"),
}

def validate_joint_map(joint_names):
    """Require the generated 71 actuated joint names before adapter mapping."""
    if len(joint_names) != TOTAL_ACTUATED_JOINTS or len(set(joint_names)) != TOTAL_ACTUATED_JOINTS:
        raise ValueError("Expected 71 unique actuated joints in the approved robot description")
    return True

def command_positions(_targets):
    """Hardware transport is intentionally not configured in this scaffold."""
    raise NotImplementedError("Select and validate hardware-specific actuator drivers first")
'''
