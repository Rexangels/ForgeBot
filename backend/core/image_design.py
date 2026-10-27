"""Image-guided conceptual robot design with deterministic local CAD artifacts."""

from __future__ import annotations

import base64
import json
import math
import re

from .meta_model import MetaModelError, generate_image_structured_json, generate_structured_json
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
            "Decompose visible mechanical assemblies into meaningful components. Never represent a visibly open cage, "
            "portal frame, wheel, or arm as one solid rectangular block when distinct members are visible. Use separate "
            "rails/posts where the component limit allows it; state any procedural visual simplification in assumptions. "
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
    if any(word in " ".join(f"{part['name']} {part['role']}" for part in components).lower() for word in ("cage", "frame", "chassis")):
        design["assumptions"] = [
            "Open cage/frame appearance is expanded locally into rails, corner posts, and translucent panel visuals; internal wiring, rated fasteners, plate specification, and load capacity are unverified."
            if "represented as one solid box" in note.lower() else
            "Wheel cylinders are oriented as side wheels and lidar pucks as vertical-axis visuals; exact axle, bearing, and mounting geometry remain unverified."
            if "cylinder primitives are modeled as vertical-axis" in note.lower() else note
            for note in design.get("assumptions", [])
        ]
        design["manufacturing_notes"] = [
            note.replace("represented as one volume for concept", "shown as separate simplified rails and translucent panels in the concept model")
            for note in design.get("manufacturing_notes", [])
        ]
        design["design_rationale"] = design.get("design_rationale", "").replace(
            "Single-box cage stands in for acrylic plates plus corner extrusions to keep prototype buildable from primitives.",
            "Cage appearance is expanded into simplified rails and translucent panels; extrusion profiles, fasteners, and internal trays remain unmodeled.",
        )
    if abs(estimated["height"] - target_height_m) > max(0.1, target_height_m * 0.5):
        design.setdefault("assumptions", []).append("Estimated overall height was adjusted by review; it differs materially from the user scale anchor.")
    design["robot_class"] = "image_robot_design"
    return design


def propose_component_sketch_edit(design: dict, component_id: str, instruction: str, assembly_findings: list[dict] | None = None) -> dict:
    """Ask Meta for a reviewable dimensional edit to one generated component."""
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 1200:
        raise ValueError("Describe the requested component change in up to 1,200 characters.")
    component = next((item for item in design.get("components", []) if item.get("id") == component_id), None)
    if component is None:
        raise ValueError("The selected component is not part of this robot design.")
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["dimensions_m", "rationale", "review_notes"],
        "properties": {
            "dimensions_m": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
            "rationale": {"type": "string"}, "review_notes": {"type": "string"},
        },
    }
    proposal = generate_structured_json(
        system_prompt=(
            "You propose edits to a single conceptual robot component's sketch envelope. Return width, depth, and height in metres. "
            "Preserve the component's stated purpose, neighboring parts, and any named interface intent where possible. Do not change the assembly, "
            "invent geometry, or claim that the result is mechanically useful, strong, safe, manufacturable, or validated. If the request conflicts "
            "with available information, make a conservative proposal and explain the uncertainty in review_notes."
        ),
        user_prompt=(
            f"Requested change: {instruction.strip()}\nSelected component: {json.dumps({key: component.get(key) for key in ('id', 'name', 'role', 'geometry', 'dimensions_m', 'position_m', 'parent_id')}, ensure_ascii=False)}\n"
            f"Robot context: {json.dumps({'title': design.get('title'), 'robot_type': design.get('robot_type'), 'components': [{'id': item.get('id'), 'name': item.get('name'), 'role': item.get('role'), 'parent_id': item.get('parent_id'), 'dimensions_m': item.get('dimensions_m')} for item in design.get('components', [])]}, ensure_ascii=False)}\n"
            f"Latest verified FreeCAD kernel findings for this component (may be empty): {json.dumps(assembly_findings or [], ensure_ascii=False)}\n"
            "Dimensions are [width/diameter, depth/diameter, height/length]. Keep each between 0.005 m and 2.0 m. For a cylinder use the same value for width and depth; for a sphere use the same value for all three dimensions. Propose values only for the selected component."
        ),
        schema_name="forgebot_sketch_edit_proposal",
        schema=schema,
    )
    dimensions = proposal.get("dimensions_m")
    if not isinstance(dimensions, list) or len(dimensions) != 3:
        raise MetaModelError("Meta returned invalid sketch dimensions.")
    proposal["dimensions_m"] = [_num(value, f"{component_id} sketch dimension", 0.005, 2.0) for value in dimensions]
    if component["geometry"] == "cylinder":
        diameter = (proposal["dimensions_m"][0] + proposal["dimensions_m"][1]) / 2
        proposal["dimensions_m"] = [diameter, diameter, proposal["dimensions_m"][2]]
    elif component["geometry"] == "sphere":
        diameter = proposal["dimensions_m"][0]
        proposal["dimensions_m"] = [diameter, diameter, diameter]
    for key in ("rationale", "review_notes"):
        if not isinstance(proposal.get(key), str) or not proposal[key].strip():
            raise MetaModelError("Meta returned an incomplete sketch-edit explanation.")
        proposal[key] = proposal[key].strip()[:1200]
    proposal.update({"component_id": component_id, "component_name": component["name"], "requires_human_review": True})
    return proposal


def explain_design_component(design: dict, component_id: str, question: str = "") -> dict:
    """Explain a selected component using the saved robot's structured design tree."""
    if not isinstance(question, str) or len(question) > 1000:
        raise ValueError("Keep component questions under 1,000 characters.")
    components = design.get("components", [])
    selected = next((item for item in components if item.get("id") == component_id), None)
    if selected is None:
        raise ValueError("The selected component is not part of this robot design.")
    by_id = {item["id"]: item for item in components}
    children = [{"id": item["id"], "name": item["name"], "role": item["role"]} for item in components if item.get("parent_id") == component_id]
    parent = by_id.get(selected.get("parent_id"))
    context = {
        "robot": {"title": design.get("title"), "type": design.get("robot_type"), "design_rationale": design.get("design_rationale")},
        "selected_component": {key: selected.get(key) for key in ("id", "name", "role", "geometry", "dimensions_m", "position_m", "parent_id")},
        "parent": {"id": parent["id"], "name": parent["name"], "role": parent["role"]} if parent else None,
        "children": children,
        "assumptions": design.get("assumptions", []),
        "manufacturing_notes": design.get("manufacturing_notes", []),
    }
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["what_it_is", "intended_role", "known_from_design", "unknown_or_unverified"],
        "properties": {
            "what_it_is": {"type": "string"}, "intended_role": {"type": "string"},
            "known_from_design": {"type": "array", "items": {"type": "string"}},
            "unknown_or_unverified": {"type": "array", "items": {"type": "string"}},
        },
    }
    answer = generate_structured_json(
        system_prompt=(
            "Explain a component in a conceptual robot from its structured design data. Separate facts present in the model from inferences. "
            "Do not claim hidden construction, mechanical usefulness, strength, fit, safety, or manufacturing readiness. Mention when the role or geometry is only an estimate."
        ),
        user_prompt=f"Question: {question.strip() or 'What is this component and what does it do?'}\nRobot wireframe data: {json.dumps(context, ensure_ascii=False)}",
        schema_name="forgebot_component_explanation",
        schema=schema,
    )
    for key in ("what_it_is", "intended_role"):
        if not isinstance(answer.get(key), str) or not answer[key].strip():
            raise MetaModelError("Meta returned an incomplete component explanation.")
    for key in ("known_from_design", "unknown_or_unverified"):
        if not isinstance(answer.get(key), list) or any(not isinstance(item, str) for item in answer[key]):
            raise MetaModelError("Meta returned an invalid component explanation.")
    return answer


def _fmt(value: float) -> str:
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def _visual(kind: str, dimensions: list[float] | tuple[float, float, float], origin=(0, 0, 0), rpy=(0, 0, 0), color="#287c72", alpha=1.0) -> dict:
    return {"geometry": kind, "dimensions_m": list(dimensions), "origin_m": list(origin), "rpy": list(rpy), "color": color, "alpha": alpha}


def _component_visuals(component: dict) -> list[dict]:
    """Expand conceptual parts into detailed, still explicitly illustrative visuals."""
    x, y, z = component["dimensions_m"]
    color = component["color"]
    label = f"{component['name']} {component['role']}".lower()
    if any(word in label for word in ("wheel", "roller", "tire")) and component["geometry"] == "cylinder":
        radius = min(x, y) / 2
        width = z
        visuals = [_visual("cylinder", (2 * radius, 2 * radius, width), rpy=(0, math.pi / 2, 0), color=color)]
        hub_d = radius * 1.05
        hub_w = max(width * 0.28, 0.006)
        for side in (-1, 1):
            visuals.append(_visual("cylinder", (hub_d, hub_d, hub_w), origin=(side * (width / 2 + hub_w * 0.25), 0, 0), rpy=(0, math.pi / 2, 0), color="#AEB9C2"))
        # Tread blocks and paired face grooves give the wheel a readable manufactured silhouette.
        tread_depth = max(radius * 0.12, 0.004)
        tread_width = (2 * math.pi * radius / 18) * 0.72
        for index in range(18):
            angle = 2 * math.pi * index / 18
            visuals.append(_visual("box", (width * 1.02, tread_width, tread_depth), origin=(0, math.sin(angle) * (radius - tread_depth * 0.25), math.cos(angle) * (radius - tread_depth * 0.25)), rpy=(angle, 0, 0), color="#30383E"))
        return visuals

    if any(word in label for word in ("cage", "frame", "chassis")) and component["geometry"] == "box":
        beam = max(0.009, min(x, y, z) * 0.075)
        plate = max(0.004, beam * 0.34)
        hx, hy, hz = x / 2, y / 2, z / 2
        visuals = []
        for px in (-hx + beam / 2, hx - beam / 2):
            for py in (-hy + beam / 2, hy - beam / 2):
                visuals.append(_visual("box", (beam, beam, z), origin=(px, py, 0), color=color))
        for pz in (-hz + beam / 2, hz - beam / 2):
            for py in (-hy + beam / 2, hy - beam / 2):
                visuals.append(_visual("box", (x - 2 * beam, beam, beam), origin=(0, py, pz), color="#788C98"))
            for px in (-hx + beam / 2, hx - beam / 2):
                visuals.append(_visual("box", (beam, y - 2 * beam, beam), origin=(px, 0, pz), color="#788C98"))
        # Transparent panels make an enclosure read as a framed cage, not a solid block.
        for px in (-hx + plate / 2, hx - plate / 2):
            visuals.append(_visual("box", (plate, y - 2 * beam, z - 2 * beam), origin=(px, 0, 0), color="#A9D8E1", alpha=0.22))
        for py in (-hy + plate / 2, hy - plate / 2):
            visuals.append(_visual("box", (x - 2 * beam, plate, z - 2 * beam), origin=(0, py, 0), color="#A9D8E1", alpha=0.18))
        # Top and lower acrylic decks are thin visual plates; they do not imply a rated structure.
        visuals.append(_visual("box", (x - 2 * beam, y - 2 * beam, plate), origin=(0, 0, hz - plate / 2), color="#D4EFF1", alpha=0.34))
        visuals.append(_visual("box", (x - 2 * beam, y - 2 * beam, plate), origin=(0, 0, -hz + plate / 2), color="#B7D9DE", alpha=0.25))
        return visuals

    visuals = [_visual(component["geometry"], (x, y, z), color=color)]
    if "lidar" in label or "laser scanner" in label:
        rim_h = max(0.004, z * 0.12)
        visuals.append(_visual("cylinder", (x * 1.08, y * 1.08, rim_h), origin=(0, 0, z * 0.38), color="#8799A4"))
        visuals.append(_visual("cylinder", (x * 0.88, y * 0.88, rim_h * 0.7), origin=(0, 0, z * 0.48), color="#26343B"))
    elif "camera" in label:
        lens = max(0.008, min(x, y, z) * 0.3)
        visuals.append(_visual("cylinder", (lens, lens, max(0.006, x * 0.13)), origin=(x * 0.54, 0, 0), rpy=(0, math.pi / 2, 0), color="#26343B"))
        visuals.append(_visual("sphere", (lens * 0.55,) * 3, origin=(x * 0.62, 0, 0), color="#4F91B5"))
    elif "gripper" in label or "claw" in label:
        finger = max(0.008, min(x, y, z) * 0.18)
        visuals.append(_visual("box", (finger, y * 0.72, z * 0.55), origin=(-x * 0.48, 0, -z * 0.5), color="#BBC4C9"))
        visuals.append(_visual("box", (finger, y * 0.72, z * 0.55), origin=(x * 0.48, 0, -z * 0.5), color="#BBC4C9"))
    return visuals


def _urdf_geometry(visual: dict) -> str:
    x, y, z = map(_fmt, visual["dimensions_m"])
    if visual["geometry"] == "box":
        return f'<box size="{x} {y} {z}"/>'
    if visual["geometry"] == "cylinder":
        return f'<cylinder radius="{_fmt(min(float(x), float(y)) / 2)}" length="{z}"/>'
    return f'<sphere radius="{_fmt(min(float(x), float(y), float(z)) / 2)}"/>'


def generate_image_robot_urdf(design: dict) -> str:
    rows = ['<?xml version="1.0"?>', f'<robot name="image_concept">', '  <link name="base_link"/>']
    by_id = {item["id"]: item for item in design["components"]}
    for item in design["components"]:
        rows.append(f'  <link name="{item["id"]}">')
        for index, visual in enumerate(_component_visuals(item)):
            px, py, pz = map(_fmt, visual["origin_m"])
            rr, rp, ry = map(_fmt, visual["rpy"])
            rgb = [int(visual["color"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
            rgba = " ".join(f"{channel:.3f}" for channel in rgb) + f" {float(visual['alpha']):.3f}"
            rows.append(f'    <visual name="visual_{item["id"]}_{index}"><origin xyz="{px} {py} {pz}" rpy="{rr} {rp} {ry}"/><geometry>{_urdf_geometry(visual)}</geometry><material name="mat_{item["id"]}_{index}"><color rgba="{rgba}"/></material></visual>')
        rows.append('  </link>')
        parent = item["parent_id"] or "base_link"
        parent_pose = by_id[item["parent_id"]]["position_m"] if item["parent_id"] else [0, 0, 0]
        px, py, pz = (_fmt(float(item["position_m"][i]) - float(parent_pose[i])) for i in range(3))
        rows.append(f'  <joint name="mount_{item["id"]}" type="fixed"><parent link="{parent}"/><child link="{item["id"]}"/><origin xyz="{px} {py} {pz}" rpy="0 0 0"/></joint>')
    rows.append('</robot>')
    return "\n".join(rows)


def generate_openscad(design: dict) -> str:
    rows = ["// ForgeBot conceptual robot model. Estimated from one image; verify before manufacture.", "$fn = 48;", ""]
    for item in design["components"]:
        px, py, pz = map(float, item["position_m"])
        safe_label = " ".join(f"{item['name']}: {item['role']}".split())[:180]
        rows.append(f"// {safe_label}")
        for visual in _component_visuals(item):
            x, y, z = map(_fmt, visual["dimensions_m"])
            ox, oy, oz = map(float, visual["origin_m"])
            rx, ry, rz = [math.degrees(value) for value in visual["rpy"]]
            rgb = [int(visual["color"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
            rgba = ", ".join([*(f"{channel:.3f}" for channel in rgb), f"{float(visual['alpha']):.3f}"])
            shape = f"cube([{x}, {y}, {z}], center=true);" if visual["geometry"] == "box" else f"cylinder(h={z}, d={x}, center=true);" if visual["geometry"] == "cylinder" else f"sphere(d={x});"
            rows += [f"color([{rgba}]) translate([{_fmt(px + ox)}, {_fmt(py + oy)}, {_fmt(pz + oz)}]) rotate([{_fmt(rx)}, {_fmt(ry)}, {_fmt(rz)}]) {shape}"]
        rows.append("")
    return "\n".join(rows)


def _unit_vector(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(float(value) ** 2 for value in vector))
    if length < 1e-9:
        return [0.0, 0.0, 1.0]
    return [float(value) / length for value in vector]


def _component_is_wheel(component: dict) -> bool:
    label = f"{component['name']} {component['role']}".lower()
    return component["geometry"] == "cylinder" and any(word in label for word in ("wheel", "roller", "tire"))


def _component_interfaces(component: dict) -> list[dict]:
    """Describe inferred datum/face interfaces; proposed hole patterns are never cut geometry."""
    x, y, z = component["dimensions_m"]
    interfaces = [{"name": "origin", "type": "datum", "origin_m": [0, 0, 0], "normal": [0, 0, 1], "status": "inferred"}]
    if component["geometry"] == "box":
        faces = [
            ("face_x_positive", [x / 2, 0, 0], [1, 0, 0], y, z),
            ("face_x_negative", [-x / 2, 0, 0], [-1, 0, 0], y, z),
            ("face_y_positive", [0, y / 2, 0], [0, 1, 0], x, z),
            ("face_y_negative", [0, -y / 2, 0], [0, -1, 0], x, z),
            ("face_z_positive", [0, 0, z / 2], [0, 0, 1], x, y),
            ("face_z_negative", [0, 0, -z / 2], [0, 0, -1], x, y),
        ]
        for name, origin, normal, width, height in faces:
            margin = min(width, height) * 0.12
            hole_diameter = min(0.004, margin * 0.5)
            spacing_x, spacing_y = max(width - 2 * margin, 0), max(height - 2 * margin, 0)
            up = [0, 0, 1] if abs(normal[2]) < 0.9 else [0, 1, 0]
            right = [
                up[1] * normal[2] - up[2] * normal[1],
                up[2] * normal[0] - up[0] * normal[2],
                up[0] * normal[1] - up[1] * normal[0],
            ]
            pattern = {
                "status": "proposed_not_cut", "layout": "four_corner", "hole_diameter_m": hole_diameter,
                "hole_centers_local_m": [[sx * spacing_x / 2, sy * spacing_y / 2] for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))],
            } if min(width, height) >= 0.02 else {"status": "not_proposed", "reason": "Face is too small for the default four-hole concept pattern."}
            interfaces.append({
                "name": name, "type": "mounting_face", "origin_m": origin, "normal": normal,
                "right": right, "up": up,
                "size_m": [width, height], "hole_pattern": pattern, "status": "inferred_face",
            })
    elif component["geometry"] == "cylinder":
        wheel = _component_is_wheel(component)
        axis = [1, 0, 0] if wheel else [0, 0, 1]
        half_length = z / 2 if wheel else z / 2
        radius = min(x, y) / 2
        for sign, suffix in ((1, "positive"), (-1, "negative")):
            origin = [axis[i] * half_length * sign for i in range(3)]
            normal = [axis[i] * sign for i in range(3)]
            interfaces.append({
                "name": f"axle_face_{suffix}" if wheel else f"end_face_{suffix}",
                "type": "axle_face" if wheel else "circular_mounting_face",
                "origin_m": origin, "normal": normal, "diameter_m": radius * 2,
                "status": "inferred_face",
            })
        if wheel:
            interfaces.append({
                "name": "axle_center", "type": "axle_center", "origin_m": [0, 0, 0],
                "axis": axis, "diameter_m": radius * 2, "status": "inferred_from_wheel_geometry",
            })
    return interfaces


def _support_distance(component: dict, direction: list[float]) -> float:
    x, y, z = component["dimensions_m"]
    dx, dy, dz = direction
    if component["geometry"] == "box":
        return abs(dx) * x / 2 + abs(dy) * y / 2 + abs(dz) * z / 2
    if component["geometry"] == "sphere":
        return min(x, y, z) / 2
    radius = min(x, y) / 2
    if _component_is_wheel(component):
        return radius * math.sqrt(dy * dy + dz * dz) + abs(dx) * z / 2
    return radius * math.sqrt(dx * dx + dy * dy) + abs(dz) * z / 2


def _assembly_bounds_dimensions(component: dict) -> list[float]:
    x, y, z = component["dimensions_m"]
    return [z, x, y] if _component_is_wheel(component) else [x, y, z]


def validate_cad_assembly(design: dict) -> dict:
    """Run conservative primitive-level placement checks; these are not CAD-kernel checks."""
    components = design.get("components", [])
    by_id = {part["id"]: part for part in components}
    findings = []
    connections = []
    roots = [part for part in components if part.get("parent_id") is None]
    if not 3 <= len(components) <= 12:
        findings.append({"severity": "blocker", "code": "component_count", "message": "Assembly must contain between 3 and 12 components."})
    if len(by_id) != len(components):
        findings.append({"severity": "blocker", "code": "duplicate_component_id", "message": "Component identifiers must be unique."})
    if len(roots) != 1:
        findings.append({"severity": "blocker", "code": "root_count", "message": f"Expected one assembly root; found {len(roots)}."})
    for part in components:
        if any(not math.isfinite(float(value)) or float(value) <= 0 for value in part.get("dimensions_m", [])):
            findings.append({"severity": "blocker", "code": "invalid_dimensions", "component_id": part.get("id"), "message": "Component dimensions must be finite and positive."})
        if any(not math.isfinite(float(value)) for value in part.get("position_m", [])):
            findings.append({"severity": "blocker", "code": "invalid_pose", "component_id": part.get("id"), "message": "Component position must contain finite coordinates."})
        parent_id = part.get("parent_id")
        if parent_id is not None and parent_id not in by_id:
            findings.append({"severity": "blocker", "code": "missing_parent", "component_id": part["id"], "message": f"Parent component {parent_id} does not exist."})
            continue
        cursor = part
        ancestors = set()
        while cursor.get("parent_id") is not None and cursor.get("parent_id") in by_id:
            if cursor["id"] in ancestors:
                findings.append({"severity": "blocker", "code": "parent_cycle", "component_id": part["id"], "message": "Assembly parent relationships contain a cycle."})
                break
            ancestors.add(cursor["id"])
            cursor = by_id[cursor["parent_id"]]
        if cursor.get("parent_id") is not None:
            continue
        if roots and cursor["id"] != roots[0]["id"]:
            findings.append({"severity": "blocker", "code": "disconnected_component", "component_id": part["id"], "message": "Component does not connect to the assembly root."})
        if parent_id is None:
            continue
        parent = by_id[parent_id]
        delta = [float(part["position_m"][i]) - float(parent["position_m"][i]) for i in range(3)]
        direction = _unit_vector(delta)
        distance = math.sqrt(sum(value * value for value in delta))
        is_wheel = _component_is_wheel(part)
        if is_wheel:
            parent_frame = delta
            child_frame = [0, 0, 0]
            gap = 0.0
            parent_interface = f"axle_mount_for_{part['id']}"
            child_interface = "axle_center"
            connection_type = "axle_center"
            # The inferred axle point must land on/within the proposed parent component bounds.
            parent_dims = _assembly_bounds_dimensions(parent)
            outside = [max(abs(parent_frame[i]) - parent_dims[i] / 2, 0.0) for i in range(3)]
            offset = math.sqrt(sum(value * value for value in outside))
            if offset > 0.03:
                findings.append({"severity": "warning", "code": "axle_mount_outside_parent", "component_id": part["id"], "message": f"Inferred wheel axle center sits {offset * 1000:.0f} mm outside the parent part bounds; a bracket or separate axle support may be needed."})
        else:
            parent_radius = _support_distance(parent, direction)
            child_radius = _support_distance(part, direction)
            parent_frame = [direction[i] * parent_radius for i in range(3)]
            child_frame = [-direction[i] * child_radius for i in range(3)]
            gap = distance - parent_radius - child_radius
            parent_interface = f"mount_face_for_{part['id']}"
            child_interface = f"mount_face_to_{parent_id}"
            connection_type = "fixed_surface"
            if gap > 0.02:
                findings.append({"severity": "warning", "code": "mount_gap", "component_id": part["id"], "message": f"Parent and child inferred mounting faces have a {gap * 1000:.0f} mm gap; review the mount or add a bracket."})
            elif gap < -0.01:
                findings.append({"severity": "warning", "code": "mount_interference", "component_id": part["id"], "message": f"Parent and child inferred mounting faces overlap by {-gap * 1000:.0f} mm; confirm the intersection is intentional."})
        connections.append({
            "parent_id": parent_id, "child_id": part["id"], "type": connection_type,
            "parent_interface": parent_interface, "child_interface": child_interface,
            "parent_interface_origin_m": parent_frame, "child_interface_origin_m": child_frame,
            "normal_parent": direction, "normal_child": [-value for value in direction],
            "axis_parent": [1, 0, 0] if is_wheel else None,
            "axis_child": [1, 0, 0] if is_wheel else None,
            "surface_gap_m": gap, "status": "inferred_needs_review",
        })

    for index, first in enumerate(components):
        first_dims = _assembly_bounds_dimensions(first)
        for second in components[index + 1:]:
            second_dims = _assembly_bounds_dimensions(second)
            overlaps = [
                max(0.0, min(first["position_m"][axis] + first_dims[axis] / 2, second["position_m"][axis] + second_dims[axis] / 2)
                    - max(first["position_m"][axis] - first_dims[axis] / 2, second["position_m"][axis] - second_dims[axis] / 2))
                for axis in range(3)
            ]
            if all(value > 0 for value in overlaps):
                overlap_volume = overlaps[0] * overlaps[1] * overlaps[2]
                min_volume = min(math.prod(first_dims), math.prod(second_dims))
                ratio = overlap_volume / min_volume if min_volume else 0
                if ratio > 0.02:
                    findings.append({
                        "severity": "warning", "code": "bounds_overlap", "component_ids": [first["id"], second["id"]],
                        "message": f"Approximate part bounds overlap by {ratio * 100:.0f}% of the smaller part's bounding volume; check clearance or intentional nesting.",
                    })

    blockers = [item for item in findings if item["severity"] == "blocker"]
    warnings = [item for item in findings if item["severity"] == "warning"]
    return {
        "schema_version": "forgebot-assembly-validation/v1",
        "status": "blocked" if blockers else "review_required" if warnings else "passed",
        "method": "primitive AABB and inferred interface checks; not a solid-kernel clearance test",
        "component_count": len(components), "connection_count": len(connections),
        "findings": findings, "connections": connections,
    }


def _generate_freecad_macro(design: dict, manifest: dict, validation: dict) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", design.get("title", "forgebot_robot")).strip("_")[:48] or "forgebot_robot"
    source = r'''# ForgeBot FreeCAD exporter. Run this macro inside FreeCAD to create FCStd and STEP files.
import json
import math
import os
import re
import FreeCAD as App
import FreeCADGui as Gui
import Part
import Mesh
import ImportGui
import Sketcher

PAYLOAD = json.loads(__PAYLOAD_LITERAL__)
OUTPUT_PREFIX = __OUTPUT_PREFIX_LITERAL__

def safe_name(value):
    result = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not result or result[0].isdigit():
        result = "Part_" + result
    return result[:60]

def make_visual(doc, component_id, visual, component_position, visual_index):
    # Box and cylinder profiles are real, dimension-constrained sketches consumed by a Pad.
    dims = [float(value) * 1000.0 for value in visual["dimensions_m"]]
    body = doc.addObject("PartDesign::Body", "Body_" + safe_name(component_id) + "_" + str(visual_index + 1))
    body.Label = component_id + " / editable solid " + str(visual_index + 1)
    sketch = doc.addObject("Sketcher::SketchObject", "Sketch_" + safe_name(component_id) + "_" + str(visual_index + 1))
    sketch.Label = component_id + " / constrained profile " + str(visual_index + 1)
    body.addObject(sketch)
    if visual["geometry"] == "box":
        width, depth, height = dims
        points = [(-width / 2, -depth / 2), (width / 2, -depth / 2), (width / 2, depth / 2), (-width / 2, depth / 2)]
        for index in range(4):
            a, b = points[index], points[(index + 1) % 4]
            sketch.addGeometry(Part.LineSegment(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0)), False)
        constraints = [Sketcher.Constraint("Coincident", 0, 2, 1, 1), Sketcher.Constraint("Coincident", 1, 2, 2, 1), Sketcher.Constraint("Coincident", 2, 2, 3, 1), Sketcher.Constraint("Coincident", 3, 2, 0, 1), Sketcher.Constraint("Horizontal", 0), Sketcher.Constraint("Vertical", 1), Sketcher.Constraint("Horizontal", 2), Sketcher.Constraint("Vertical", 3), Sketcher.Constraint("Distance", 0, width), Sketcher.Constraint("Distance", 1, depth), Sketcher.Constraint("DistanceX", 0, 1, -width / 2), Sketcher.Constraint("DistanceY", 0, 1, -depth / 2)]
        sketch.addConstraint(constraints)
        sketch.Placement = App.Placement(App.Vector(0, 0, -height / 2), App.Rotation())
        pad_length = height
    elif visual["geometry"] == "cylinder":
        radius = min(dims[0], dims[1]) / 2
        sketch.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), radius), False)
        sketch.addConstraint([Sketcher.Constraint("DistanceX", 0, 3, 0.0), Sketcher.Constraint("DistanceY", 0, 3, 0.0), Sketcher.Constraint("Radius", 0, radius)])
        sketch.Placement = App.Placement(App.Vector(0, 0, -dims[2] / 2), App.Rotation())
        pad_length = dims[2]
    elif visual["geometry"] == "sphere":
        # A sphere is not a sketch-driven feature in this first editor milestone.
        body.removeObject(sketch)
        doc.removeObject(sketch.Name)
        sphere = body.newObject("PartDesign::Feature", "Sphere_" + safe_name(component_id) + "_" + str(visual_index + 1))
        sphere.Label = component_id + " / sphere primitive (not sketch-driven)"
        sphere.Shape = Part.makeSphere(min(dims) / 2)
        pad = sphere
    else:
        raise ValueError("Unsupported primitive: " + str(visual["geometry"]))
    if visual["geometry"] != "sphere":
        pad = body.newObject("PartDesign::Pad", "Pad_" + safe_name(component_id) + "_" + str(visual_index + 1))
        pad.Label = component_id + " / Pad " + str(visual_index + 1)
        pad.Profile = sketch
        pad.Length = pad_length
        sketch.Visibility = False
    body.Tip = pad
    rpy = [math.degrees(float(value)) for value in visual.get("rpy", [0, 0, 0])]
    rotation = App.Rotation(App.Vector(1, 0, 0), rpy[0]).multiply(App.Rotation(App.Vector(0, 1, 0), rpy[1])).multiply(App.Rotation(App.Vector(0, 0, 1), rpy[2]))
    local = visual.get("origin_m", [0, 0, 0])
    offset = App.Vector(*[float(local[index]) * 1000.0 + float(component_position[index]) * 1000.0 for index in range(3)])
    body.Placement = App.Placement(offset, rotation)
    doc.recompute()
    shape = body.Shape.copy()
    local_shape = pad.Shape.copy()
    local_origin = App.Vector(float(local[0]) * 1000.0, float(local[1]) * 1000.0, float(local[2]) * 1000.0)
    zero = App.Vector(0, 0, 0)
    for axis, angle in ((App.Vector(1, 0, 0), rpy[0]), (App.Vector(0, 1, 0), rpy[1]), (App.Vector(0, 0, 1), rpy[2])):
        if abs(angle) > 1e-9:
            local_shape.rotate(zero, axis, angle)
    local_shape.translate(local_origin)
    if shape.isNull() or not shape.isValid() or shape.Volume <= 0:
        raise ValueError("A generated sketch feature is null, invalid, or has zero volume.")
    return body, pad, shape, local_shape

def run_export():
    design = PAYLOAD["design"]
    manifest = PAYLOAD["manifest"]
    validation = PAYLOAD["validation"]
    macro_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    output_dir = os.path.join(macro_dir, "forgebot_step_export")
    os.makedirs(output_dir, exist_ok=True)
    parts_dir = os.path.join(output_dir, "parts")
    os.makedirs(parts_dir, exist_ok=True)
    doc = App.newDocument("ForgeBotConcept")
    doc.Label = str(design.get("title", "ForgeBot robot concept"))[:80]
    step_features = []
    solids_report = []
    world_component_shapes = {}
    preview_manifest = {"schema_version": "forgebot-freecad-preview/v1", "units": "mm", "mesh_units": "mm", "components": []}
    component_by_id = {part["id"]: part for part in design["components"]}
    connection_by_child = {item["child_id"]: item for item in validation.get("connections", [])}

    for component in design["components"]:
        component_id = component["id"]
        group = doc.addObject("App::DocumentObjectGroup", "Component_" + safe_name(component_id))
        group.Label = component["name"]
        group.addProperty("App::PropertyString", "ComponentId", "ForgeBot")
        group.addProperty("App::PropertyString", "Role", "ForgeBot")
        group.addProperty("App::PropertyString", "ParentId", "ForgeBot")
        group.addProperty("App::PropertyVector", "ApproximateCenterMm", "ForgeBot")
        group.addProperty("App::PropertyString", "MountInterfaces", "ForgeBot")
        group.ComponentId = component_id
        group.Role = component["role"]
        group.ParentId = component.get("parent_id") or ""
        p = component["position_m"]
        group.ApproximateCenterMm = App.Vector(float(p[0]) * 1000.0, float(p[1]) * 1000.0, float(p[2]) * 1000.0)
        interfaces = next(item["interfaces"] for item in manifest["components"] if item["id"] == component_id)
        group.MountInterfaces = json.dumps(interfaces, ensure_ascii=False)
        visual_count = 0
        local_mesh_shapes = []
        world_shapes = []
        for visual_index, visual in enumerate(PAYLOAD["visuals"][component_id]):
            body, feature, shape, local_shape = make_visual(doc, component_id, visual, component["position_m"], visual_index)
            body.Label = component["name"] + " / solid " + str(visual_index + 1)
            local_mesh_shapes.append(local_shape)
            world_shapes.append(shape)
            rgb = visual["color"]
            feature.ViewObject.ShapeColor = tuple(int(rgb[index:index + 2], 16) / 255.0 for index in (1, 3, 5))
            feature.ViewObject.LineColor = (0.12, 0.16, 0.19)
            feature.ViewObject.Transparency = int(round((1.0 - float(visual.get("alpha", 1.0))) * 100))
            feature.addProperty("App::PropertyString", "ComponentId", "ForgeBot")
            feature.addProperty("App::PropertyInteger", "VisualIndex", "ForgeBot")
            feature.addProperty("App::PropertyString", "Primitive", "ForgeBot")
            feature.ComponentId = component_id
            feature.VisualIndex = visual_index
            feature.Primitive = visual["geometry"]
            group.addObject(body)
            step_features.append(body)
            visual_count += 1
        component_shape = Part.makeCompound(world_shapes)
        world_component_shapes[component_id] = component_shape
        solids_report.append({"component_id": component_id, "solid_count": visual_count, "valid": bool(component_shape.isValid()), "volume_mm3": float(component_shape.Volume)})
        mesh_name = safe_name(component_id) + ".stl"
        mesh = Mesh.Mesh(Part.makeCompound(local_mesh_shapes).tessellate(0.1))
        mesh.write(os.path.join(parts_dir, mesh_name))
        parent_id = component.get("parent_id")
        parent_position = component_by_id[parent_id]["position_m"] if parent_id else [0, 0, 0]
        relative_mm = [(float(component["position_m"][axis]) - float(parent_position[axis])) * 1000.0 for axis in range(3)]
        connection = connection_by_child.get(component_id, {})
        preview_manifest["components"].append({
            "id": component_id,
            "name": component["name"],
            "source_file": "parts/" + mesh_name,
            "visual_format": "stl",
            "units": "mm",
            "parent_component": parent_id,
            "parent_mount": connection.get("parent_interface", "origin"),
            "child_mount": connection.get("child_interface", "origin"),
            "relative_position_mm": relative_mm,
            "relative_rpy": [0, 0, 0],
            "mount_interfaces": interfaces,
        })

    doc.recompute()
    kernel_intersections = []
    component_validity = []
    for component_id, shape in world_component_shapes.items():
        component_validity.append({"component_id": component_id, "valid": bool(shape.isValid()), "volume_mm3": float(shape.Volume)})
    for index, first_id in enumerate(world_component_shapes):
        first_shape = world_component_shapes[first_id]
        for second_id in list(world_component_shapes)[index + 1:]:
            second_shape = world_component_shapes[second_id]
            try:
                intersection = first_shape.common(second_shape)
                volume = float(intersection.Volume)
                if volume > 0.01:
                    kernel_intersections.append({"component_ids": [first_id, second_id], "intersection_volume_mm3": volume, "status": "intersects_review_required"})
            except Exception as error:
                kernel_intersections.append({"component_ids": [first_id, second_id], "status": "check_failed", "message": str(error)[:500]})
    solid_kernel_validation = {
        "schema_version": "forgebot-freecad-kernel-validation/v1",
        "method": "OpenCASCADE shape validity and exact pairwise common-volume checks in millimetres",
        "status": "review_required" if kernel_intersections or any(not item["valid"] for item in component_validity) else "passed",
        "component_validity": component_validity,
        "intersections": kernel_intersections,
        "tolerance_mm3": 0.01,
        "limitations": ["Positive overlap can be intentional at a mount; review the components and mount intent.", "This does not check tolerances, fasteners, motion-envelope collisions, strength, or manufacturability."],
    }
    fcstd_path = os.path.join(output_dir, OUTPUT_PREFIX + ".FCStd")
    step_path = os.path.join(output_dir, OUTPUT_PREFIX + ".step")
    report_path = os.path.join(output_dir, "export_report.json")
    preview_manifest_path = os.path.join(output_dir, "assembly_manifest.json")
    doc.saveAs(fcstd_path)
    if not step_features:
        raise RuntimeError("No solid geometry was generated; refusing to export an empty STEP file.")
    ImportGui.export(step_features, step_path)
    with open(preview_manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(preview_manifest, manifest_file, indent=2, ensure_ascii=False)
    report = {
        "schema_version": "forgebot-freecad-export/v1",
        "freecad_version": ".".join(str(value) for value in App.Version()[:3]),
        "step_file": step_path,
        "native_document": fcstd_path,
        "document_units": "mm",
        "source_manifest_units": "m",
        "component_count": len(design["components"]),
        "solid_feature_count": len(step_features),
        "preview_part_count": len(preview_manifest["components"]),
        "preview_manifest": preview_manifest_path,
        "preview_parts_directory": parts_dir,
        "primitive_shapes": solids_report,
        "assembly_validation": validation,
        "solid_kernel_validation": solid_kernel_validation,
        "limitations": [
            "STEP contains separate primitive solids grouped by conceptual component; inferred dimensions and interfaces need engineering review.",
            "Suggested hole patterns are metadata only and are not cut into the solids.",
            "This exporter does not fuse touching solids or calculate strength, tolerances, or manufacturing suitability."
        ]
    }
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2, ensure_ascii=False)
    App.Console.PrintMessage("ForgeBot FreeCAD export complete: " + step_path + "\n")
    Gui.activeDocument().activeView().viewAxonometric()
    Gui.activeDocument().activeView().fitAll()
    return step_path

run_export()
'''
    visuals = {part["id"]: _component_visuals(part) for part in design["components"]}
    payload = {"design": design, "manifest": manifest, "validation": validation, "visuals": visuals}
    return source.replace("__PAYLOAD_LITERAL__", repr(json.dumps(payload, ensure_ascii=False))).replace("__OUTPUT_PREFIX_LITERAL__", repr(slug))


def generate_cad_package(design: dict) -> dict:
    """Build a multi-file, editable OpenSCAD part library and positioned assembly."""
    files: dict[str, str] = {}
    components = design["components"]
    assembly = [
        "// ForgeBot conceptual assembly. Edit mount coordinates to reposition parts.",
        "// Dimensions and placements are estimates; review interfaces before fabrication.",
        "$fn = 64;", "",
    ]
    manifest_components = []
    by_id = {part["id"]: part for part in components}
    assembly_validation = validate_cad_assembly(design)
    inferred_interfaces = {part["id"]: _component_interfaces(part) for part in components}
    for connection in assembly_validation["connections"]:
        for endpoint in ("parent", "child"):
            component_id = connection[f"{endpoint}_id"]
            name = connection[f"{endpoint}_interface"]
            if any(item["name"] == name for item in inferred_interfaces[component_id]):
                continue
            normal = connection[f"normal_{endpoint}"]
            up = [0, 0, 1] if abs(normal[2]) < 0.9 else [0, 1, 0]
            right = [
                up[1] * normal[2] - up[2] * normal[1],
                up[2] * normal[0] - up[0] * normal[2],
                up[0] * normal[1] - up[1] * normal[0],
            ]
            interface = {
                "name": name,
                "type": "axle_center" if connection["type"] == "axle_center" else "inferred_mating_surface",
                "origin_m": connection[f"{endpoint}_interface_origin_m"],
                "normal": normal, "right": right, "up": up,
                "status": "inferred_needs_review",
            }
            if connection["type"] == "axle_center":
                interface["axis"] = connection[f"axis_{endpoint}"]
            inferred_interfaces[component_id].append(interface)
    for component in components:
        part_id = component["id"]
        dims = component["dimensions_m"]
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", part_id)
        part_path = f"parts/{safe_name}.scad"
        rows = [
            f"// {component['name']} — {component['role']}",
            "// Open this file alone for a preview, or import its module from assembly.scad.",
            "$fn = 64;", "",
            f"module part_{safe_name}(width={_fmt(dims[0])}, depth={_fmt(dims[1])}, height={_fmt(dims[2])}) {{",
            f"  scale([width/{_fmt(dims[0])}, depth/{_fmt(dims[1])}, height/{_fmt(dims[2])}]) {{",
        ]
        for visual in _component_visuals(component):
            x, y, z = map(_fmt, visual["dimensions_m"])
            ox, oy, oz = map(_fmt, visual["origin_m"])
            rx, ry, rz = [_fmt(math.degrees(value)) for value in visual["rpy"]]
            rgb = [int(visual["color"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
            rgba = ", ".join([*(f"{channel:.3f}" for channel in rgb), _fmt(visual["alpha"])])
            shape = f"cube([{x}, {y}, {z}], center=true);" if visual["geometry"] == "box" else f"cylinder(h={z}, d={x}, center=true);" if visual["geometry"] == "cylinder" else f"sphere(d={x});"
            rows.append(f"    color([{rgba}]) translate([{ox}, {oy}, {oz}]) rotate([{rx}, {ry}, {rz}]) {shape}")
        rows += ["  }", "}", "", f"part_{safe_name}();", ""]
        files[part_path] = "\n".join(rows)
        assembly.append(f"use <{part_path}>;")
        px, py, pz = map(_fmt, component["position_m"])
        assembly += [
            f"part_{safe_name}_width = {_fmt(dims[0])};", f"part_{safe_name}_depth = {_fmt(dims[1])};", f"part_{safe_name}_height = {_fmt(dims[2])};",
            f"mount_{safe_name}_x = {px};", f"mount_{safe_name}_y = {py};", f"mount_{safe_name}_z = {pz};",
        ]
        manifest_components.append({
            "id": part_id, "name": component["name"], "role": component["role"],
            "part_file": part_path, "geometry": component["geometry"],
            "dimensions_m": dims, "parent_id": component["parent_id"],
            "joint_type": "fixed", "mount_frame": f"{part_id}.origin",
            "position_m": component["position_m"],
            "relative_position_m": [
                float(component["position_m"][i]) - float(by_id[component["parent_id"]]["position_m"][i])
                if component["parent_id"] else float(component["position_m"][i])
                for i in range(3)
            ],
            "interfaces": inferred_interfaces[component["id"]],
        })
    assembly += ["", "module forgebot_assembly() {"]
    for component in components:
        safe_name = component["id"]
        assembly.append(f"  translate([mount_{safe_name}_x, mount_{safe_name}_y, mount_{safe_name}_z]) part_{safe_name}(part_{safe_name}_width, part_{safe_name}_depth, part_{safe_name}_height);")
    assembly += ["}", "", "forgebot_assembly();", ""]
    files["assembly.scad"] = "\n".join(assembly)
    files["robot.urdf"] = generate_image_robot_urdf(design)
    files["manifest.json"] = json.dumps({
        "schema_version": "forgebot-cad-assembly/v2",
        "title": design.get("title", "ForgeBot concept"), "units": "metres",
        "coordinate_frame": "robot base frame; Z up",
        "assembly_file": "assembly.scad", "components": manifest_components,
        "connections": assembly_validation["connections"],
        "assembly_validation": assembly_validation,
        "limitations": [
            "Image-guided concept dimensions are estimates, not measurements.",
            "Mount frames are component origins; clearances, fasteners, tolerances, materials and strength are not validated.",
            "The assembly is a parametric OpenSCAD source package, not a STEP or certified manufacturing model.",
        ],
    }, indent=2)
    files["assembly_validation.json"] = json.dumps(assembly_validation, indent=2)
    manifest = json.loads(files["manifest.json"])
    files["freecad/build_step.FCMacro"] = _generate_freecad_macro(design, manifest, assembly_validation)
    files["freecad/README.md"] = (
        "# Export native FreeCAD and STEP files\n\n"
        "1. Extract the complete ForgeBot ZIP.\n"
        "2. Open FreeCAD, choose **Macro → Macros…**, select `build_step.FCMacro`, then run it.\n"
        "3. The macro creates `forgebot_step_export/` beside itself with a native `.FCStd` document, a `.step` assembly, and an export report.\n\n"
        "The macro converts source metres to FreeCAD millimetres, builds box and cylinder solids from editable constrained sketches and PartDesign Pads under component groups, exports component STL previews and a browser-import manifest, and records named interface metadata on each group. Spheres remain primitive features in this first sketch-editing milestone. "
        "The STEP file is generated by FreeCAD's OpenCASCADE-based solid kernel. Hole-pattern suggestions remain metadata and are not cut. "
        "Review all dimensions, interferences, materials, and fastening details before manufacturing.\n"
    )
    files["README.md"] = (
        f"# {design.get('title', 'ForgeBot concept')} CAD concept\n\n"
        "Open `assembly.scad` in OpenSCAD to render the complete assembly. Keep the `parts/` directory beside it. "
        "Each part file can be opened alone and exposes width, depth, and height module parameters. "
        "Edit the `part_<component>_width/depth/height` and `mount_<component>_x/y/z` values in `assembly.scad` "
        "to change the assembled dimensions and placement. `manifest.json` records the component "
        "tree, named mounting faces, axle centers, proposed hole patterns, dimensions, poses, and mating frames; "
        "`assembly_validation.json` records primitive-level checks and `robot.urdf` matches the visual concept.\n\n"
        "Hole patterns are recommendations only and are not cut into the OpenSCAD parts. Mating interfaces are inferred "
        "from approximate component geometry and pose; review them in CAD before using them for fabrication.\n\n"
        "This is image-guided conceptual geometry. It does not include verified joints, fastening details, material "
        "properties, tolerances, interference checks, structural analysis, or manufacturing approval. To generate a native "
        "FreeCAD document and STEP assembly, run `freecad/build_step.FCMacro` in FreeCAD; see its README.\n"
    )
    return {"schema_version": "forgebot-cad-package/v4", "files": files}
