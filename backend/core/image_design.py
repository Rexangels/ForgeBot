"""Image-guided conceptual robot design with deterministic local CAD artifacts."""

from __future__ import annotations

import base64
import math
import re

from .meta_model import MetaModelError, generate_image_structured_json
from .vision import _image_format_allowed

IMAGE_DESIGN_VERSION = "image-robot-concept/v1"

DESIGN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "title", "robot_type", "design_rationale", "estimated_dimensions_m", "components", "assumptions", "manufacturing_notes"],
    "properties": {
        "schema_version": {"type": "string", "enum": [IMAGE_DESIGN_VERSION]},
        "title": {"type": "string"},
        "robot_type": {"type": "string"},
        "design_rationale": {"type": "string"},
        "estimated_dimensions_m": {"type": "object", "additionalProperties": False, "required": ["width", "depth", "height"], "properties": {"width": {"type": "number"}, "depth": {"type": "number"}, "height": {"type": "number"}}},
        "components": {"type": "array", "minItems": 3, "maxItems": 12, "items": {"type": "object", "additionalProperties": False, "required": ["id", "name", "role", "parent_id", "geometry", "dimensions_m", "position_m", "color"], "properties": {
            "id": {"type": "string"}, "name": {"type": "string"}, "role": {"type": "string"}, "parent_id": {"type": ["string", "null"]},
            "geometry": {"type": "string", "enum": ["box", "cylinder", "sphere"]},
            "dimensions_m": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
            "position_m": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
            "color": {"type": "string"},
        }}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "manufacturing_notes": {"type": "array", "items": {"type": "string"}},
    },
}


def _num(value, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g} metres.")
    return float(value)


def propose_image_robot(image: bytes, content_type: str, brief: str, target_height_m: float, constraints: str = "") -> dict:
    if not _image_format_allowed(content_type, image):
        raise ValueError("Upload a valid JPEG, PNG, or WebP reference image.")
    if len(image) > 12 * 1024 * 1024:
        raise ValueError("Reference image must be no larger than 12 MB.")
    height = _num(target_height_m, "Target height", 0.05, 3.0)
    if not brief.strip() or len(brief) > 2000 or len(constraints) > 2000:
        raise ValueError("Add a design brief (up to 2,000 characters) and keep constraints under 2,000 characters.")
    data_url = f"data:{content_type};base64,{base64.b64encode(image).decode('ascii')}"
    design = generate_image_structured_json(
        system_prompt=(
            "You are a conceptual robot industrial designer. Use the reference image only for visible silhouette, "
            "proportions, colors, and apparent component arrangement. Do not claim to recover hidden internals or "
            "exact dimensions from pixels. The user-provided target height is the only scale anchor. Propose an "
            "original, buildable-looking concept as 3 to 12 simple primitive components, each with a unique id, "
            "a tree parent (exactly one null root), positive dimensions in metres, absolute center position in metres, "
            "and a hex color. Keep all dimensions plausible relative to the target height and coordinates within +/-2 m. "
            "Use simple geometry supported by URDF/OpenSCAD. Distinguish estimates and unknowns in assumptions and "
            "manufacturing_notes. Do not invent electronics, actuators, material strengths, or certified performance."
        ),
        user_prompt=(
            f"Design a robot concept from this reference image. User brief: {brief.strip()}\n"
            f"Approximate target height: {height:.3f} m. Additional constraints: {constraints.strip() or 'none supplied'}. "
            "Return a coherent primitive component tree with base/body, locomotion or support elements where visible, "
            "and task-specific end components. Geometry dimensions are [x,y,z]; for cylinders use x/y as diameter "
            "and z as length; for spheres use equal dimensions. position_m is each component center in the robot base frame."
        ),
        image_data_url=data_url,
        schema_name="forgebot_image_robot_design",
        schema=DESIGN_SCHEMA,
    )
    return validate_image_design(design, height)


def validate_image_design(design: dict, target_height_m: float) -> dict:
    if design.get("schema_version") != IMAGE_DESIGN_VERSION:
        raise MetaModelError("Meta returned an unexpected image-design schema.")
    components = design.get("components")
    if not isinstance(components, list) or not 3 <= len(components) <= 12:
        raise MetaModelError("Image design must contain 3 to 12 components.")
    ids = [item.get("id") for item in components]
    if len(set(ids)) != len(ids) or any(not isinstance(item, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,39}", item) for item in ids):
        raise MetaModelError("Image design contains invalid or duplicate component identifiers.")
    by_id = {item["id"]: item for item in components}
    roots = [item for item in components if item.get("parent_id") is None]
    if len(roots) != 1:
        raise MetaModelError("Image design must have exactly one root component.")
    for item in components:
        parent = item.get("parent_id")
        if parent is not None and parent not in by_id:
            raise MetaModelError(f"Component {item['id']} references a missing parent.")
        dims = item.get("dimensions_m")
        pose = item.get("position_m")
        if not isinstance(dims, list) or len(dims) != 3 or not isinstance(pose, list) or len(pose) != 3:
            raise MetaModelError(f"Component {item['id']} has invalid geometry or position arrays.")
        item["dimensions_m"] = [_num(v, f"{item['id']} dimension", 0.005, 2.0) for v in dims]
        item["position_m"] = [_num(v, f"{item['id']} position", -2.0, 2.0) for v in pose]
        if item.get("geometry") not in ("box", "cylinder", "sphere"):
            raise MetaModelError(f"Component {item['id']} uses unsupported geometry.")
        if item["geometry"] == "sphere":
            diameter = min(item["dimensions_m"])
            item["dimensions_m"] = [diameter, diameter, diameter]
        elif item["geometry"] == "cylinder":
            diameter = (item["dimensions_m"][0] + item["dimensions_m"][1]) / 2
            item["dimensions_m"] = [diameter, diameter, item["dimensions_m"][2]]
        color = item.get("color", "")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            item["color"] = "#287c72"
        if not isinstance(item.get("name"), str) or not isinstance(item.get("role"), str) or not item["name"].strip() or not item["role"].strip():
            raise MetaModelError(f"Component {item['id']} is missing its name or role.")
        item["name"] = " ".join(item["name"].split())[:100]
        item["role"] = " ".join(item["role"].split())[:160]
    for item in components:
        seen = set()
        cursor = item
        while cursor.get("parent_id") is not None:
            if cursor["id"] in seen:
                raise MetaModelError("Image design component relationships contain a cycle.")
            seen.add(cursor["id"])
            cursor = by_id[cursor["parent_id"]]
    estimated = design.get("estimated_dimensions_m", {})
    for axis in ("width", "depth", "height"):
        estimated[axis] = _num(estimated.get(axis), f"Estimated {axis}", 0.01, 3.0)
    if abs(estimated["height"] - target_height_m) > max(0.1, target_height_m * 0.5):
        design.setdefault("assumptions", []).append("Estimated overall height was adjusted by review; it differs materially from the user scale anchor.")
    design["robot_class"] = "image_robot_design"
    return design


def _fmt(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def generate_image_robot_urdf(design: dict) -> str:
    rows = ['<?xml version="1.0"?>', f'<robot name="image_concept">', '  <link name="base_link"/>']
    by_id = {item["id"]: item for item in design["components"]}
    for item in design["components"]:
        x, y, z = map(_fmt, item["dimensions_m"])
        geom = f'<box size="{x} {y} {z}"/>' if item["geometry"] == "box" else f'<cylinder radius="{_fmt(float(x) / 2)}" length="{z}"/>' if item["geometry"] == "cylinder" else f'<sphere radius="{_fmt(float(x) / 2)}"/>'
        rows += [f'  <link name="{item["id"]}">', f'    <visual><geometry>{geom}</geometry><material name="mat_{item["id"]}"><color rgba="{int(item["color"][1:3],16)/255:.3f} {int(item["color"][3:5],16)/255:.3f} {int(item["color"][5:7],16)/255:.3f} 1"/></material></visual>', '  </link>']
        parent = item["parent_id"] or "base_link"
        parent_pose = by_id[item["parent_id"]]["position_m"] if item["parent_id"] else [0, 0, 0]
        px, py, pz = (_fmt(float(item["position_m"][i]) - float(parent_pose[i])) for i in range(3))
        rows.append(f'  <joint name="mount_{item["id"]}" type="fixed"><parent link="{parent}"/><child link="{item["id"]}"/><origin xyz="{px} {py} {pz}" rpy="0 0 0"/></joint>')
    rows.append('</robot>')
    return "\n".join(rows)


def generate_openscad(design: dict) -> str:
    rows = ["// ForgeBot conceptual robot model. Estimated from one image; verify before manufacture.", "$fn = 48;", ""]
    for item in design["components"]:
        x, y, z = map(_fmt, item["dimensions_m"])
        px, py, pz = map(_fmt, item["position_m"])
        safe_label = " ".join(f"{item['name']}: {item['role']}".split())[:180]
        rows += [f"// {safe_label}", f"color(\"{item['color']}\") translate([{px}, {py}, {pz}])", "  " + (f"cube([{x}, {y}, {z}], center=true);" if item["geometry"] == "box" else f"cylinder(h={z}, d={x}, center=true);" if item["geometry"] == "cylinder" else f"sphere(d={x});"), ""]
    return "\n".join(rows)
