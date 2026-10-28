"""Image-guided conceptual robot design with deterministic local CAD artifacts."""

from __future__ import annotations

import base64
import json
import math
import re

from .hardware import (
    calculate_four_hole_pattern,
    check_fastener_alignment,
    match_cots_footprint,
    project_assembly_mating_holes,
)
from .meta_model import MetaModelError, generate_image_structured_json, generate_multi_image_structured_json, generate_structured_json
from .vision import _image_format_allowed

IMAGE_DESIGN_VERSION = "image-robot-concept/v1"
OBSERVATION_SCHEMA_VERSION = "image-robot-observation/v1"
MIN_COMPONENTS = 3
MAX_COMPONENTS = 20
MAX_OBSERVED_ASSEMBLIES = 20

# Optional per-component reasoning fields. They are accepted by validation and
# sent on the wire so the model can ground each part in what it actually saw.
REASONED_COMPONENT_PROPERTIES = {
    "functional_category": {"type": "string"},
    "orientation_rpy": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
    "symmetry_group": {"type": ["string", "null"]},
    "mount_intent": {"type": "string"},
    "visible_vs_inferred": {"type": "string", "enum": ["visible", "partially_visible", "inferred"]},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "evidence_bbox": {
        "type": ["object", "null"],
        "additionalProperties": False,
        "required": ["x_min", "y_min", "x_max", "y_max"],
        "properties": {
            "x_min": {"type": "number", "minimum": 0, "maximum": 1},
            "y_min": {"type": "number", "minimum": 0, "maximum": 1},
            "x_max": {"type": "number", "minimum": 0, "maximum": 1},
            "y_max": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
    "parent_reason": {"type": "string"},
    "sub_assembly": {"type": ["string", "null"]},
}

OBSERVATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "scene_summary", "observed_assemblies"],
    "properties": {
        "schema_version": {"type": "string", "enum": [OBSERVATION_SCHEMA_VERSION]},
        "scene_summary": {"type": "string"},
        "observed_assemblies": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_OBSERVED_ASSEMBLIES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "appearance", "image_region", "visibility", "confidence", "functional_hint"],
                "properties": {
                    "label": {"type": "string"},
                    "appearance": {"type": "string"},
                    "image_region": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["x_min", "y_min", "x_max", "y_max"],
                        "properties": {
                            "x_min": {"type": "number", "minimum": 0, "maximum": 1},
                            "y_min": {"type": "number", "minimum": 0, "maximum": 1},
                            "x_max": {"type": "number", "minimum": 0, "maximum": 1},
                            "y_max": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                    },
                    "visibility": {"type": "string", "enum": ["visible", "partially_visible", "inferred"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "functional_hint": {"type": "string"},
                },
            },
        },
    },
}

REVISION_SCHEMA_VERSION = "image-robot-revision/v1"

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
        "components": {"type": "array", "minItems": MIN_COMPONENTS, "maxItems": MAX_COMPONENTS, "items": {"type": "object", "additionalProperties": False, "required": ["id", "name", "role", "parent_id", "geometry", "dimensions_m", "position_m", "color"], "properties": {
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

# Expose the optional reasoning fields on the wire so the model can return them.
DESIGN_SCHEMA["properties"]["components"]["items"]["properties"].update(REASONED_COMPONENT_PROPERTIES)


def _num(value, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g} metres.")
    return float(value)


def _apply_reasoning_defaults(item: dict) -> None:
    """Fill optional component-reasoning fields so older specs keep validating."""
    if not isinstance(item.get("functional_category"), str) or not item["functional_category"].strip():
        item["functional_category"] = "structure"
    else:
        item["functional_category"] = " ".join(item["functional_category"].split())[:80]
    orientation = item.get("orientation_rpy")
    if not isinstance(orientation, list) or len(orientation) != 3 or any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in orientation):
        item["orientation_rpy"] = [0.0, 0.0, 0.0]
    else:
        item["orientation_rpy"] = [float(v) for v in orientation]
    group = item.get("symmetry_group")
    item["symmetry_group"] = group if (group is None or (isinstance(group, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,39}", group))) else None
    if not isinstance(item.get("mount_intent"), str):
        item["mount_intent"] = ""
    else:
        item["mount_intent"] = item["mount_intent"].strip()[:300]
    if item.get("visible_vs_inferred") not in ("visible", "partially_visible", "inferred"):
        item["visible_vs_inferred"] = "visible"
    confidence = item.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        item["confidence"] = 0.5
    else:
        item["confidence"] = min(1.0, max(0.0, float(confidence)))
    bbox = item.get("evidence_bbox")
    if bbox is not None and not (
        isinstance(bbox, dict)
        and all(isinstance(bbox.get(key), (int, float)) and not isinstance(bbox.get(key), bool) and 0 <= bbox[key] <= 1 for key in ("x_min", "y_min", "x_max", "y_max"))
        and bbox["x_min"] < bbox["x_max"]
        and bbox["y_min"] < bbox["y_max"]
    ):
        item["evidence_bbox"] = None
    if not isinstance(item.get("parent_reason"), str):
        item["parent_reason"] = ""
    else:
        item["parent_reason"] = item["parent_reason"].strip()[:300]
    group = item.get("sub_assembly")
    if group is None or (isinstance(group, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,39}", group.strip())):
        item["sub_assembly"] = group.strip() if isinstance(group, str) else None
    else:
        item["sub_assembly"] = None


VIEW_LABELS = ("front", "side", "top", "other")
MAX_REFERENCE_VIEWS = 3


def _clean_view_label(value: object) -> str:
    label = str(value or "").strip().lower()
    return label if label in VIEW_LABELS else "other"


def observe_reference_components(image_data_url: str, brief: str, view_label: str = "other") -> dict:
    """Stage 1: dense visual observation of one reference view.

    Returns per-assembly labels, appearance, image regions, visibility, and
    functional hints, stamped with the viewpoint. Raises MetaModelError on a
    bad model response.
    """
    view = _clean_view_label(view_label)
    observation = generate_image_structured_json(
        system_prompt=(
            "You are the visual-observation stage of an image-to-robot pipeline. "
            "Describe only what is visible in the reference image: distinct mechanical "
            "assemblies, their appearance, image-region boxes as normalized 0..1 fractions "
            "with origin at the top-left, occlusion state, and a short functional hint. "
            "Do not invent metric dimensions, masses, hidden internals, or materials. "
            "Mark uncertainty with low confidence or partially_visible/inferred."
        ),
        user_prompt=(
            f"Observe this reference image (reported viewpoint: {view}) for robot concept design. "
            f"User brief: {brief.strip()}. "
            "List each distinct visible mechanical assembly (body, supports, wheels, masts, "
            "sensor pods, frames) with appearance and region. Keep labels short."
        ),
        image_data_url=image_data_url,
        schema_name="forgebot_image_robot_observation",
        schema=OBSERVATION_SCHEMA,
    )
    if not isinstance(observation, dict) or observation.get("schema_version") != OBSERVATION_SCHEMA_VERSION:
        raise MetaModelError("Meta returned an observation with an unexpected structure.")
    assemblies = observation.get("observed_assemblies")
    if not isinstance(assemblies, list) or not 1 <= len(assemblies) <= MAX_OBSERVED_ASSEMBLIES:
        raise MetaModelError("Meta returned an observation with an invalid assembly list.")
    for entry in assemblies:
        region = entry.get("image_region", {})
        if region["x_min"] >= region["x_max"] or region["y_min"] >= region["y_max"]:
            raise MetaModelError("Meta returned an invalid observation region.")
    observation["view"] = view
    return observation


def _observation_context(observations: list[dict]) -> str:
    if not observations:
        return "No separate observation pass succeeded; design directly from the images."
    try:
        return json.dumps(
            [
                {
                    "view": observation.get("view", "other"),
                    "scene_summary": observation.get("scene_summary", ""),
                    "observed_assemblies": [
                        {key: entry.get(key) for key in ("label", "appearance", "image_region", "visibility", "confidence", "functional_hint")}
                        for entry in observation.get("observed_assemblies", [])
                    ],
                }
                for observation in observations
            ],
            ensure_ascii=False,
        )[:9000]
    except (TypeError, ValueError):
        return "Observation context could not be serialized; design directly from the images."


def _validate_reference_view(item: dict, index: int) -> tuple[bytes, str, str]:
    image = item.get("image")
    content_type = item.get("content_type", "")
    if not isinstance(image, (bytes, bytearray)) or not _image_format_allowed(content_type, bytes(image)):
        raise ValueError(f"Reference view {index + 1}: upload a valid JPEG, PNG, or WebP reference image.")
    if len(image) > 12 * 1024 * 1024:
        raise ValueError(f"Reference view {index + 1}: each image must be no larger than 12 MB.")
    return bytes(image), content_type, _clean_view_label(item.get("view"))


def _propose_text_only(cleaned: list[tuple[bytes, str, str]], brief: str, height: float, constraints: str) -> dict:
    """Local-only proposal: no reference pixel leaves this machine.

    Each view is probed locally (format, pixel size, byte size) and the
    model designs from the text brief plus those container facts. Stage 1
    observation is skipped because there are no pixels to observe remotely;
    every component is therefore inferred, which validation records and the
    human review must weigh.
    """
    from .local_vision import LocalVisionError, describe_view, probe_image

    probes = []
    for index, (image, _content_type, view) in enumerate(cleaned):
        try:
            probe = probe_image(image)
        except LocalVisionError as exc:
            raise ValueError(f"Reference view {index + 1}: {exc}") from exc
        probe["view"] = view
        probes.append(probe)
    view_lines = "\n".join(describe_view(probe["view"], probe, index) for index, probe in enumerate(probes))
    design = generate_structured_json(
        system_prompt=(
            "You are a conceptual robot industrial designer working WITHOUT the reference images: "
            "they stay on the user's machine by policy, so you receive only a text brief, a target height, "
            "and per-view container facts (viewpoint label, format, pixel dimensions). "
            "Propose an original, buildable-looking concept as 3 to 20 simple primitive components with the same "
            "tree, dimension, position, and color rules as the image-guided path. Mark every component inferred "
            "with modest confidence and put the no-pixel limitation in assumptions. Do not invent electronics, "
            "actuators, material strengths, or certified performance."
        ),
        user_prompt=(
            f"Design a robot concept from this text brief (no reference pixels were uploaded). User brief: {brief.strip()}\n"
            f"Approximate target height: {height:.3f} m. Additional constraints: {constraints.strip() or 'none supplied'}.\n"
            f"Local reference-view facts:\n{view_lines}\n"
            "Return a coherent primitive component tree with base/body, locomotion or support elements, "
            "and task-specific end components. Geometry dimensions are [x,y,z]; position_m is each component "
            "center in the robot base frame."
        ),
        schema_name="forgebot_image_robot_design",
        schema=DESIGN_SCHEMA,
    )
    validated = validate_image_design(design, height)
    grounded, report = ground_image_design(validated, height)
    grounded["pixels_local_only"] = True
    grounded["local_probes"] = probes
    grounded.setdefault("assumptions", []).append(
        "Reference pixels never left this machine: the concept was designed from the text brief plus local "
        "container facts only, so every component is inferred and proportions need careful human review."
    )
    grounded["grounding"] = report
    return grounded


def propose_image_robot_from_views(views: list[dict], brief: str, target_height_m: float, constraints: str = "", local_only: bool = False) -> dict:
    """Two-stage multi-view proposal with cross-view checks and grounding.

    `views` holds up to three {image, content_type, view} mappings. Stage 1
    observes each view; stage 2 reasons one component tree from all views at
    once, keeping cross-view dimensions consistent. Validated, grounded, and
    returned for human review. With `local_only=True`, reference pixels stay
    on this machine and the model designs from a text brief plus local
    container facts instead.
    """
    if not isinstance(views, list) or not 1 <= len(views) <= MAX_REFERENCE_VIEWS:
        raise ValueError(f"Provide 1 to {MAX_REFERENCE_VIEWS} reference views.")
    cleaned = [_validate_reference_view(item, index) for index, item in enumerate(views)]
    height = _num(target_height_m, "Target height", 0.05, 3.0)
    if not brief.strip() or len(brief) > 2000 or len(constraints) > 2000:
        raise ValueError("Add a design brief (up to 2,000 characters) and keep constraints under 2,000 characters.")
    if local_only:
        return _propose_text_only(cleaned, brief, height, constraints)
    data_urls = [(view, f"data:{content_type};base64,{base64.b64encode(image).decode('ascii')}") for image, content_type, view in cleaned]
    observations: list[dict] = []
    for view, data_url in data_urls:
        try:
            observations.append(observe_reference_components(data_url, brief, view))
        except (MetaModelError, ValueError):
            continue
    design = generate_multi_image_structured_json(
        system_prompt=(
            "You are a conceptual robot industrial designer. Use the reference views only for visible silhouette, "
            "proportions, colors, and apparent component arrangement. Do not claim to recover hidden internals or "
            "exact dimensions from pixels. The user-provided target height is the only scale anchor. Propose an "
            "original, buildable-looking concept as 3 to 20 simple primitive components, each with a unique id, "
            "a tree parent (exactly one null root), positive dimensions in metres (each between 0.005 m and 2.0 m), absolute center position in metres, "
            "and a hex color. Keep all dimensions plausible relative to the target height and coordinates within +/-2 m. "
            "Decompose articulated limbs into small sub-parts (hip mount, femur, knee servo, tibia, foot) grouped under "
            "a shared sub_assembly name such as leg_fl; sub-assembly members must still connect through the parent tree. "
            "The views show the same robot: a component's width in the front view must agree with its depth in the "
            "side view and its footprint in the top view. When views disagree, prefer the clearest view, lower the "
            "component confidence, and record the conflict in assumptions. "
            "Decompose visible mechanical assemblies into meaningful components. Never represent a visibly open cage, "
            "portal frame, wheel, or arm as one solid rectangular block when distinct members are visible. Use separate "
            "rails/posts where the component limit allows it; state any procedural visual simplification in assumptions. "
            "For every component, explain your reasoning: functional_category, mount_intent, parent_reason, whether it "
            "was visible/partially_visible/inferred, a 0..1 confidence, and the observed image region it came from. "
            "Group mirrored parts (wheel pairs, rail pairs) under a shared symmetry_group. "
            "Use simple geometry supported by URDF/OpenSCAD. Distinguish estimates and unknowns in assumptions and "
            "manufacturing_notes. Do not invent electronics, actuators, material strengths, or certified performance."
        ),
        user_prompt=(
            f"Design a robot concept from these {len(data_urls)} reference views of the same robot. User brief: {brief.strip()}\n"
            f"Approximate target height: {height:.3f} m. Additional constraints: {constraints.strip() or 'none supplied'}. "
            f"Visual observations from stage 1: {_observation_context(observations)}\n"
            "Return a coherent primitive component tree with base/body, locomotion or support elements where visible, "
            "and task-specific end components. Geometry dimensions are [x,y,z]; for cylinders use x/y as diameter "
            "and z as length; for spheres use equal dimensions. position_m is each component center in the robot base frame."
        ),
        images=data_urls,
        schema_name="forgebot_image_robot_design",
        schema=DESIGN_SCHEMA,
    )
    validated = validate_image_design(design, height)
    grounded, report = ground_image_design(validated, height)
    if observations:
        grounded["visual_observation"] = [
            {
                "schema_version": OBSERVATION_SCHEMA_VERSION,
                "view": observation.get("view", "other"),
                "scene_summary": observation.get("scene_summary", "")[:1000],
                "observed_assemblies": observation.get("observed_assemblies", []),
            }
            for observation in observations
        ]
    grounded["grounding"] = report
    return grounded


def propose_image_robot(image: bytes, content_type: str, brief: str, target_height_m: float, constraints: str = "") -> dict:
    """Single-view proposal, kept for existing callers and tests."""
    return propose_image_robot_from_views(
        [{"image": image, "content_type": content_type, "view": "other"}], brief, target_height_m, constraints
    )


def ground_image_design(design: dict, target_height_m: float) -> tuple[dict, dict]:
    """Deterministically ground a validated design to the scale anchor.

    Uniformly rescales positions/dimensions when the assembled height differs
    materially from the user-supplied anchor, averages mirrored symmetry-group
    dimensions, clamps coordinates, and rounds to millimetres. Returns the
    adjusted design plus a machine-readable grounding report. Pure function of
    its inputs; never calls the model.
    """
    components = design.get("components", [])
    notes: list[str] = []
    if not components:
        return design, {"status": "skipped", "notes": ["No components to ground."]}
    overall_min = min(part["position_m"][2] - part["dimensions_m"][2] / 2 for part in components)
    overall_max = max(part["position_m"][2] + part["dimensions_m"][2] / 2 for part in components)
    span = max(overall_max - overall_min, 1e-6)
    scale = 1.0
    if abs(span - target_height_m) > max(0.1, target_height_m * 0.25):
        scale = target_height_m / span
        # Keep rescaling conservative: never shrink below 1/4 or grow beyond 4x.
        scale = min(4.0, max(0.25, scale))
        for part in components:
            part["dimensions_m"] = [round(max(0.005, min(2.0, v * scale)), 4) for v in part["dimensions_m"]]
            part["position_m"] = [round(max(-2.0, min(2.0, v * scale)), 4) for v in part["position_m"]]
        notes.append(f"Uniformly rescaled by {scale:.3f} to match the {target_height_m:.3f} m height anchor.")
    else:
        notes.append("Assembly span already agreed with the height anchor; no rescaling applied.")
    # Average mirrored symmetry-group envelopes so pairs match exactly.
    groups: dict[str, list[dict]] = {}
    for part in components:
        group = part.get("symmetry_group")
        if group:
            groups.setdefault(group, []).append(part)
    symmetrized = 0
    for group, members in groups.items():
        if len(members) < 2:
            continue
        for axis in range(3):
            average = round(sum(float(member["dimensions_m"][axis]) for member in members) / len(members), 4)
            for member in members:
                member["dimensions_m"][axis] = average
        symmetrized += 1
    if symmetrized:
        notes.append(f"Averaged dimensions across {symmetrized} symmetry group(s).")

    # Snap floating child components (and their descendant subtrees) to contact
    # their parent's mounting surface whenever an unintended air gap exists.
    by_id = {part["id"]: part for part in components}
    children_map: dict[str, list[dict]] = {}
    for part in components:
        pid = part.get("parent_id")
        if pid and pid in by_id:
            children_map.setdefault(pid, []).append(part)

    def _shift_subtree(node: dict, delta_xyz: list[float]) -> None:
        node["position_m"] = [
            round(max(-2.0, min(2.0, float(node["position_m"][axis]) + delta_xyz[axis])), 4)
            for axis in range(3)
        ]
        for child in children_map.get(node["id"], []):
            _shift_subtree(child, delta_xyz)

    snapped = 0
    visited: set[str] = set()
    queue = [part for part in components if not part.get("parent_id") or part.get("parent_id") not in by_id]
    while queue:
        current = queue.pop(0)
        if current["id"] in visited:
            continue
        visited.add(current["id"])
        parent_id = current.get("parent_id")
        if parent_id and parent_id in by_id and not _component_is_wheel(current):
            parent = by_id[parent_id]
            p_dims = _assembly_bounds_dimensions(parent)
            c_dims = _assembly_bounds_dimensions(current)
            delta = [float(current["position_m"][i]) - float(parent["position_m"][i]) for i in range(3)]
            axis_gaps = [abs(delta[i]) - (p_dims[i] + c_dims[i]) / 2.0 for i in range(3)]
            if max(axis_gaps) > 0.0015:
                shift = [0.0, 0.0, 0.0]
                for axis in range(3):
                    if axis_gaps[axis] > 0.0015:
                        sign = 1.0 if delta[axis] >= 0 else -1.0
                        shift[axis] = -sign * axis_gaps[axis]
                if any(abs(v) > 1e-6 for v in shift):
                    _shift_subtree(current, shift)
                    snapped += 1
        queue.extend(children_map.get(current["id"], []))
    if snapped:
        notes.append(f"Snapped {snapped} floating component(s) flush against their parent mounting surfaces.")

    # Re-derive the estimated envelope from grounded geometry.
    overall_min = min(part["position_m"][2] - part["dimensions_m"][2] / 2 for part in components)
    overall_max = max(part["position_m"][2] + part["dimensions_m"][2] / 2 for part in components)
    estimated = design.get("estimated_dimensions_m", {})
    estimated["height"] = round(max(0.01, min(3.0, overall_max - overall_min)), 4)
    report = {
        "schema_version": "image-robot-grounding/v1",
        "status": "grounded",
        "scale_factor": round(scale, 4),
        "target_height_m": target_height_m,
        "assembly_span_m": round(overall_max - overall_min, 4),
        "notes": notes,
    }
    return design, report


REVISION_EDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "rationale", "edits"],
    "properties": {
        "schema_version": {"type": "string", "enum": [REVISION_SCHEMA_VERSION]},
        "rationale": {"type": "string"},
        "edits": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["component_id"],
                "properties": {
                    "component_id": {"type": "string"},
                    "dimensions_m": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
                    "position_m": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}


def propose_single_revision(design: dict, findings: list[dict], instruction: str = "") -> dict:
    """Ask Meta for ONE bounded revision pass over kernel/assembly findings.

    Returns at most four component edits for human review. Never mutates the
    design; the caller applies the returned edits only after user approval.
    """
    if not isinstance(findings, list) or not findings:
        raise ValueError("Supply at least one assembly or kernel finding to revise against.")
    if not isinstance(instruction, str) or len(instruction) > 1200:
        raise ValueError("Keep revision instructions under 1,200 characters.")
    components = design.get("components", [])
    if not components:
        raise ValueError("The design has no components to revise.")
    proposal = generate_structured_json(
        system_prompt=(
            "You revise a conceptual robot spec against deterministic FreeCAD/assembly "
            "findings. Propose at most four component edits: corrected dimensions_m "
            "and/or position_m for listed components only. Keep every dimension between "
            "0.005 m and 2.0 m and every coordinate within +/-2 m. Prefer the smallest "
            "change that separates intersecting solids or closes unintended gaps. Do not "
            "restructure the tree, add components, or claim the result is validated, "
            "manufacturable, or safe."
        ),
        user_prompt=(
            f"Revision goal: {(instruction.strip() or 'Resolve the reported intersections and gaps with minimal changes.')}\n"
            f"Components: {json.dumps([{key: part.get(key) for key in ('id', 'dimensions_m', 'position_m', 'parent_id', 'symmetry_group')} for part in components], ensure_ascii=False)}\n"
            f"Deterministic findings: {json.dumps(findings, ensure_ascii=False)[:4000]}"
        ),
        schema_name="forgebot_image_robot_revision",
        schema=REVISION_EDIT_SCHEMA,
    )
    by_id = {part["id"]: part for part in components}
    edits = proposal.get("edits", [])
    if not isinstance(edits, list) or not 1 <= len(edits) <= 4:
        raise MetaModelError("Meta returned an invalid revision edit list.")
    cleaned = []
    for edit in edits:
        component_id = edit.get("component_id")
        if component_id not in by_id:
            raise MetaModelError(f"Revision references unknown component {component_id!r}.")
        entry: dict = {"component_id": component_id}
        if "dimensions_m" in edit and edit["dimensions_m"] is not None:
            dims = edit["dimensions_m"]
            if not isinstance(dims, list) or len(dims) != 3:
                raise MetaModelError(f"Revision has invalid dimensions for {component_id}.")
            entry["dimensions_m"] = [_num(v, f"{component_id} revision dimension", 0.005, 2.0) for v in dims]
        if "position_m" in edit and edit["position_m"] is not None:
            pose = edit["position_m"]
            if not isinstance(pose, list) or len(pose) != 3:
                raise MetaModelError(f"Revision has invalid position for {component_id}.")
            entry["position_m"] = [_num(v, f"{component_id} revision position", -2.0, 2.0) for v in pose]
        if "dimensions_m" not in entry and "position_m" not in entry:
            raise MetaModelError(f"Revision edit for {component_id} changes nothing.")
        if isinstance(edit.get("reason"), str) and edit["reason"].strip():
            entry["reason"] = edit["reason"].strip()[:300]
        cleaned.append(entry)
    rationale = proposal.get("rationale", "")
    if not isinstance(rationale, str) or not rationale.strip():
        raise MetaModelError("Meta returned a revision without rationale.")
    return {
        "schema_version": REVISION_SCHEMA_VERSION,
        "rationale": rationale.strip()[:1200],
        "edits": cleaned,
        "requires_human_review": True,
    }


def apply_revision_edits(design: dict, proposal: dict, target_height_m: float) -> dict:
    """Apply an approved revision proposal, then re-validate and re-ground.

    Call only after the user has reviewed and accepted `proposal`.
    """
    import copy

    if proposal.get("schema_version") != REVISION_SCHEMA_VERSION:
        raise ValueError("This revision proposal has an unexpected schema.")
    updated = copy.deepcopy(design)
    by_id = {part["id"]: part for part in updated.get("components", [])}
    for edit in proposal.get("edits", []):
        part = by_id.get(edit.get("component_id"))
        if part is None:
            raise ValueError(f"Unknown component {edit.get('component_id')!r} in revision.")
        if "dimensions_m" in edit:
            part["dimensions_m"] = list(edit["dimensions_m"])
        if "position_m" in edit:
            part["position_m"] = list(edit["position_m"])
    validated = validate_image_design(updated, _num(target_height_m, "Target height", 0.05, 3.0))
    grounded, report = ground_image_design(validated, float(target_height_m))
    grounded["grounding"] = report
    return grounded


def validate_image_design(design: dict, target_height_m: float) -> dict:
    if design.get("schema_version") != IMAGE_DESIGN_VERSION:
        raise MetaModelError("Meta returned an unexpected image-design schema.")
    components = design.get("components")
    if not isinstance(components, list) or not MIN_COMPONENTS <= len(components) <= MAX_COMPONENTS:
        raise MetaModelError(f"Image design must contain {MIN_COMPONENTS} to {MAX_COMPONENTS} components.")
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
        item["dimensions_m"] = [max(0.005, _num(v, f"{item['id']} dimension", 0.0005, 2.0)) for v in dims]
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
        _apply_reasoning_defaults(item)
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


def _visual(kind: str, dimensions: list[float] | tuple[float, float, float], origin=(0, 0, 0), rpy=(0, 0, 0), color="#287c72", alpha=1.0, is_detail: bool = False, bore_m: float = 0.0) -> dict:
    item = {"geometry": kind, "dimensions_m": list(dimensions), "origin_m": list(origin), "rpy": list(rpy), "color": color, "alpha": alpha, "is_detail": is_detail}
    if bore_m > 0.0:
        item["bore_m"] = round(float(bore_m), 5)
    return item


def _is_leg_component(component: dict) -> bool:
    """Detect articulated-leg concepts by name/role/id tokens or walking category."""
    text = f"{component.get('id', '')} {component.get('name', '')} {component.get('role', '')}".lower()
    tokens = set(re.split(r"[^a-z]+", text))
    if tokens & {
        "wheel", "wheels", "tire", "tires", "roller", "rollers", "caster",
        "track", "tracks", "tread", "motor", "motors", "gearmotor", "gearbox",
        "axle", "hub", "propeller", "rotor", "thruster",
    }:
        return False
    if tokens & {"leg", "legs", "femur", "tibia", "coxa", "limb", "walker", "hexapod", "quadruped", "biped", "shin", "thigh"}:
        return True
    return component.get("functional_category") in ("leg", "walking")


def _leg_visuals(component: dict, parent_position: list[float] | None = None) -> list[dict]:
    """Render a leg envelope as femur, tibia, and foot-pad segments.

    The component dimensions stay the bounding envelope of the whole leg; these
    visuals only change how the leg reads in the viewer and CAD exports. The
    femur splays outward from the hip and the tibia drops to a foot pad, which
    matches articulated walker legs instead of straight posts. Splay side comes
    from the leg's lateral offset to its parent; +Y when unknown.
    """
    x, y, z = (float(value) for value in component["dimensions_m"])
    color = component["color"]
    side = 1.0
    if parent_position is not None:
        lateral = float(component["position_m"][1]) - float(parent_position[1])
        if abs(lateral) > 1e-6:
            side = 1.0 if lateral > 0 else -1.0
    thickness = max(0.006, 0.35 * min(x, y))
    segment_geometry = component["geometry"] if component["geometry"] == "cylinder" else "box"

    def _segment_dims(length: float) -> list[float]:
        if segment_geometry == "cylinder":
            return [thickness, thickness, length]
        return [thickness, thickness, length]

    # Femur: from the hip (top of the envelope) outward and down.
    femur_angle = math.radians(35)
    direction = (0.0, side * math.cos(femur_angle), -math.sin(femur_angle))
    femur_length = 0.55 * z
    # Roll maps local +Z onto the femur direction: Rx(roll) sends
    # (0, 0, 1) to (0, -sin(roll), cos(roll)).
    roll = math.atan2(-direction[1], direction[2])
    hip = (0.0, 0.0, z / 2)
    femur_center = tuple(hip[index] + direction[index] * femur_length / 2 for index in range(3))
    knee = tuple(hip[index] + direction[index] * femur_length for index in range(3))
    # Tibia: vertical drop from the knee to a foot pad at the envelope bottom.
    foot_height = max(0.006, 0.08 * z)
    foot_top = -z / 2 + foot_height
    tibia_length = max(knee[2] - foot_top, 0.01)
    tibia_center = (knee[0], knee[1], (knee[2] + foot_top) / 2)
    foot_center = (knee[0], knee[1], -z / 2 + foot_height / 2)
    foot_size = max(2.5 * thickness, 0.012)
    return [
        _visual(segment_geometry, _segment_dims(femur_length), origin=femur_center, rpy=(roll, 0, 0), color=color, is_detail=True),
        _visual(segment_geometry, _segment_dims(tibia_length), origin=tibia_center, color=color, is_detail=True),
        _visual("box", (foot_size, foot_size, foot_height), origin=foot_center, color="#3A4248", is_detail=True),
    ]


def _component_visuals(component: dict, parent_position: list[float] | None = None) -> list[dict]:
    """Expand conceptual parts into detailed, still explicitly illustrative visuals."""
    x, y, z = component["dimensions_m"]
    color = component["color"]
    label = f"{component['name']} {component['role']}".lower()
    id_and_name = f"{component.get('id', '')} {component.get('name', '')}".lower()
    is_split_limb_segment = any(
        w in id_and_name for w in ("femur", "tibia", "shin", "thigh", "hip", "knee", "ankle", "claw", "talon")
    )
    if _is_leg_component(component) and not is_split_limb_segment and component["geometry"] in ("box", "cylinder"):
        return _leg_visuals(component, parent_position)

    # Bionic / Avian / Articulated Limb Segment (already-decomposed femur or tibia)
    if any(w in id_and_name for w in ("femur", "tibia", "thigh", "shin")):
        is_femur = any(w in id_and_name for w in ("femur", "thigh"))
        pitch = -0.26 if is_femur else 0.22
        tube_d = max(0.010, min(x, y) * 0.56)
        clevis_d = max(0.014, min(x, y) * 0.78)
        clevis_w = max(0.010, y * 0.65)
        rod_d = max(0.005, tube_d * 0.42)
        dx_end = math.sin(pitch) * (z * 0.36)
        dz_end = math.cos(pitch) * (z * 0.36)
        return [
            _visual("cylinder", (tube_d, tube_d, z * 0.92), rpy=(0, pitch, 0), color="#1E293B"),
            _visual("cylinder", (clevis_d, clevis_d, clevis_w), origin=(dx_end, 0, dz_end), rpy=(math.pi / 2, 0, 0), color="#D97706", is_detail=True),
            _visual("cylinder", (clevis_d * 0.9, clevis_d * 0.9, clevis_w * 0.9), origin=(-dx_end, 0, -dz_end), rpy=(math.pi / 2, 0, 0), color="#D97706", is_detail=True),
            _visual("cylinder", (rod_d, rod_d, z * 0.78), origin=(-tube_d * 0.75, 0, 0), rpy=(0, pitch, 0), color="#EAB308", is_detail=True),
            _visual("box", (tube_d * 1.15, tube_d * 0.85, z * 0.48), origin=(tube_d * 0.35, 0, 0), rpy=(0, pitch, 0), color=color, is_detail=True),
        ]

    # Hip mount or Knee rotary servo joint in an articulated biped/walker
    if any(w in id_and_name for w in ("hip_mount", "knee_servo", "knee_joint", "hip_joint")):
        drum_d = max(0.022, min(x, z) * 0.88)
        drum_w = max(0.018, y * 0.85)
        return [
            _visual("cylinder", (drum_d, drum_d, drum_w), rpy=(math.pi / 2, 0, 0), color="#1E293B"),
            _visual("cylinder", (drum_d * 0.82, drum_d * 0.82, drum_w * 1.08), rpy=(math.pi / 2, 0, 0), color="#D97706", is_detail=True),
            _visual("cylinder", (drum_d * 0.38, drum_d * 0.38, drum_w * 1.18), rpy=(math.pi / 2, 0, 0), color="#CBD5E1", is_detail=True),
        ]

    # Prehensile Avian / Biped Claw Foot with Actuators (3 forward talons + 1 rear hallux talon)
    if ("claw" in label or "talon" in label) and ("foot" in label or "toe" in label or "claw" in id_and_name):
        visuals = [
            # Tarsal actuator manifold & gold knuckle bridge
            _visual("box", (x * 0.42, y * 0.56, z * 0.82), origin=(-x * 0.08, 0, z * 0.12), color="#1E293B"),
            _visual("box", (x * 0.30, y * 0.82, z * 0.68), origin=(x * 0.08, 0, 0), color="#D97706", is_detail=True),
        ]
        for idx, ty in enumerate((-y * 0.32, 0.0, y * 0.32)):
            yaw = (idx - 1) * 0.16
            visuals.append(_visual("box", (x * 0.44, y * 0.15, z * 0.46), origin=(x * 0.28, ty, -z * 0.10), rpy=(0, 0.10, yaw), color="#EAB308", is_detail=True))
            visuals.append(_visual("box", (x * 0.24, y * 0.10, z * 0.50), origin=(x * 0.47, ty * 1.12, -z * 0.24), rpy=(0, 0.48, yaw), color="#334155", is_detail=True))
        # Rear hallux talon
        visuals.append(_visual("box", (x * 0.32, y * 0.16, z * 0.44), origin=(-x * 0.28, 0, -z * 0.12), rpy=(0, -0.12, 0), color="#D97706", is_detail=True))
        visuals.append(_visual("box", (x * 0.20, y * 0.11, z * 0.48), origin=(-x * 0.44, 0, -z * 0.24), rpy=(0, -0.46, 0), color="#334155", is_detail=True))
        return visuals

    # Pneumatic Beak Actuator Assembly (tapered upper maxilla + lower mandible + actuator rings)
    if "beak" in label or "rostrum" in label:
        return [
            # Base pneumatic beak housing
            _visual("cylinder", (min(y, z) * 0.82, min(y, z) * 0.82, x * 0.36), origin=(-x * 0.32, 0, 0), rpy=(0, math.pi / 2, 0), color="#1E293B"),
            # Upper maxilla (sleek angled upper beak)
            _visual("box", (x * 0.68, y * 0.62, z * 0.46), origin=(x * 0.04, 0, z * 0.14), rpy=(0, 0.16, 0), color="#D97706", is_detail=True),
            # Upper rostrum hooked tip
            _visual("box", (x * 0.38, y * 0.34, z * 0.28), origin=(x * 0.36, 0, z * 0.02), rpy=(0, 0.36, 0), color="#F59E0B", is_detail=True),
            # Lower articulated mandible jaw
            _visual("box", (x * 0.54, y * 0.44, z * 0.26), origin=(-x * 0.02, 0, -z * 0.18), rpy=(0, -0.12, 0), color="#B45309", is_detail=True),
            # Lateral pneumatic actuator pivot cylinders
            _visual("cylinder", (z * 0.32, z * 0.32, y * 0.76), origin=(-x * 0.22, 0, -z * 0.06), rpy=(math.pi / 2, 0, 0), color="#EAB308", is_detail=True),
        ]

    # Optical Sensor Head Assembly (cranial dome + left/right optical camera eyes + dorsal crest)
    if "head" in label or "skull" in label or "cranium" in label:
        eye_d = max(0.016, min(x, y, z) * 0.34)
        visuals = [
            _visual("sphere", (x, y, z), color=color),
            # Dorsal carbon-fiber cranial sensor crest
            _visual("box", (x * 0.58, y * 0.22, z * 0.18), origin=(-x * 0.08, 0, z * 0.42), rpy=(0, 0.18, 0), color="#1E293B", is_detail=True),
        ]
        for side in (-1, 1):
            ey = side * (y * 0.43)
            visuals.append(_visual("cylinder", (eye_d, eye_d, 0.008), origin=(x * 0.14, ey, z * 0.06), rpy=(math.pi / 2, 0, 0), color="#EAB308", is_detail=True))
            visuals.append(_visual("cylinder", (eye_d * 0.72, eye_d * 0.72, 0.011), origin=(x * 0.14, ey, z * 0.06), rpy=(math.pi / 2, 0, 0), color="#0F172A", is_detail=True))
            visuals.append(_visual("cylinder", (eye_d * 0.38, eye_d * 0.38, 0.013), origin=(x * 0.14, ey, z * 0.06), rpy=(math.pi / 2, 0, 0), color="#38BDF8", is_detail=True))
        return visuals

    # Multi-Jointed Bionic Wing Frame & Solar-Collecting Primary Flight Feathers
    if "wing" in label:
        pos_y = float(component.get("position_m", [0, 0.08, 0])[1])
        side = 1.0 if pos_y >= 0 else -1.0
        sh_d = max(0.024, min(x, z) * 0.38)
        # Structural shoulder pivot axle bridging the clearance gap between the wing root and torso
        gap_span = abs(pos_y - float(parent_position[1])) if parent_position is not None else y * 1.5
        axle_len = max(0.024, min(0.045, gap_span * 0.38 + 0.014))
        axle_mid_y = -side * (axle_len * 0.45)
        flange_y = -side * (axle_len * 0.82)
        visuals = [
            # Shoulder & wrist micro-actuator rotary joints
            _visual("cylinder", (sh_d, sh_d, max(0.016, y * 0.68)), origin=(x * 0.22, -side * y * 0.12, z * 0.34), rpy=(math.pi / 2, 0, 0), color="#D97706"),
            _visual("cylinder", (sh_d * 0.68, sh_d * 0.68, max(0.018, y * 0.76)), origin=(x * 0.22, -side * y * 0.12, z * 0.34), rpy=(math.pi / 2, 0, 0), color="#1E293B", is_detail=True),
            # Structural shoulder pivot axle bridging inward through clearance gap into the torso
            _visual("cylinder", (sh_d * 0.48, sh_d * 0.48, axle_len), origin=(x * 0.22, axle_mid_y, z * 0.34), rpy=(math.pi / 2, 0, 0), color="#0F172A", is_detail=True),
            # Inboard mounting flange collar flush against torso wall
            _visual("cylinder", (sh_d * 0.82, sh_d * 0.82, 0.006), origin=(x * 0.22, flange_y, z * 0.34), rpy=(math.pi / 2, 0, 0), color="#D97706", is_detail=True),
            # Leading-edge multi-jointed carbon/gold wing frame spar
            _visual("box", (x * 0.26, max(0.010, y * 0.42), z * 0.76), origin=(x * 0.20, 0, z * 0.06), rpy=(side * 0.08, 0.22, 0), color="#1E293B", is_detail=True),
            _visual("box", (x * 0.18, max(0.012, y * 0.48), z * 0.58), origin=(x * 0.12, side * y * 0.06, z * 0.12), rpy=(side * 0.08, 0.28, 0), color="#D97706", is_detail=True),
        ]
        # 6 fanned primary & secondary solar-collecting flight feathers
        for f_idx in range(6):
            frac = f_idx / 5.0
            fz = z * (0.30 - frac * 0.64)
            fx = -x * (0.04 + frac * 0.22)
            fy = side * y * (0.04 + frac * 0.16)
            f_len = x * (0.95 + (1.0 - abs(frac - 0.35)) * 0.35)
            f_w = max(0.005, y * 0.24)
            f_h = max(0.016, z * 0.13)
            pitch_ang = 0.26 + frac * 0.38
            roll_ang = side * (0.06 + frac * 0.08)
            visuals.append(_visual("box", (f_len, f_w, f_h), origin=(fx, fy, fz), rpy=(roll_ang, pitch_ang, 0), color="#0EA5E9" if f_idx % 2 == 0 else "#22D3EE", is_detail=True))
            # Dark solar-collecting cell inlay on each feather
            visuals.append(_visual("box", (f_len * 0.76, f_w * 1.12, f_h * 0.56), origin=(fx - 0.003, fy, fz), rpy=(roll_ang, pitch_ang, 0), color="#0F172A", is_detail=True))
        return visuals

    # Hydraulic Tail Stabilizer Fan (actuator cylinder + 5 fanned tail feathers)
    if "tail" in label:
        visuals = [
            # Hydraulic tail stabilizer actuator housing & gold piston
            _visual("cylinder", (max(0.016, y * 0.18), max(0.016, y * 0.18), x * 0.48), origin=(x * 0.24, 0, z * 0.6), rpy=(0, math.pi / 2 - 0.34, 0), color="#1E293B"),
            _visual("cylinder", (max(0.010, y * 0.11), max(0.010, y * 0.11), x * 0.62), origin=(x * 0.12, 0, z * 0.3), rpy=(0, math.pi / 2 - 0.34, 0), color="#D97706", is_detail=True),
        ]
        for t_idx in (-2, -1, 0, 1, 2):
            yaw = t_idx * 0.18
            ty = t_idx * (y * 0.16)
            tx = -x * 0.08 - abs(t_idx) * (x * 0.04)
            tz = -abs(t_idx) * 0.003
            blade_l = x * (1.08 - abs(t_idx) * 0.07)
            blade_w = max(0.014, y * 0.20)
            blade_h = max(0.005, z * 0.38)
            visuals.append(_visual("box", (blade_l, blade_w, blade_h), origin=(tx, ty, tz), rpy=(0, -0.34, yaw), color="#06B6D4", is_detail=True))
            visuals.append(_visual("box", (blade_l * 0.74, blade_w * 0.58, blade_h * 1.18), origin=(tx - 0.004, ty, tz), rpy=(0, -0.34, yaw), color="#0F172A", is_detail=True))
        return visuals

    # Avian / Biped Central Processing & Battery Housing Torso + Carbon Fiber Spine
    if any(w in label for w in ("torso", "chest", "trunk")) and component["geometry"] == "box":
        visuals = [
            # Sculpted keeled avian torso core pitched forward
            _visual("box", (x * 0.84, y * 0.84, z * 0.82), rpy=(0, 0.16, 0), color=color),
            # Upper thoracic shoulder arch & lower pelvic tapers
            _visual("cylinder", (x * 0.78, x * 0.78, y * 0.88), origin=(x * 0.05, 0, z * 0.28), rpy=(math.pi / 2, 0, 0), color="#0E7490", is_detail=True),
            _visual("cylinder", (x * 0.66, x * 0.66, y * 0.74), origin=(-x * 0.06, 0, -z * 0.30), rpy=(math.pi / 2, 0, 0), color="#1E293B", is_detail=True),
            # Central Processing & Battery Housing (Chest Armor & Core on +X front)
            _visual("box", (0.016, y * 0.60, z * 0.48), origin=(x * 0.41, 0, z * 0.04), rpy=(0, 0.16, 0), color="#D97706", is_detail=True),
            _visual("box", (0.018, y * 0.50, z * 0.38), origin=(x * 0.43, 0, z * 0.04), rpy=(0, 0.16, 0), color="#0F172A", is_detail=True),
            _visual("box", (0.020, y * 0.30, z * 0.22), origin=(x * 0.44, 0, z * 0.04), rpy=(0, 0.16, 0), color="#22D3EE", is_detail=True),
            # Lightweight Carbon Fiber Spine along dorsal back (-X)
            _visual("box", (0.018, y * 0.22, z * 0.84), origin=(-x * 0.42, 0, 0.02), rpy=(0, 0.16, 0), color="#1E293B", is_detail=True),
        ]
        for v_idx in range(4):
            vz = z * (0.30 - v_idx * 0.20)
            vx = -x * 0.43 + (vz * 0.16)
            visuals.append(_visual("box", (0.014, y * 0.36, 0.010), origin=(vx, 0, vz), rpy=(0, 0.16, 0), color="#EAB308", is_detail=True))
        return visuals

    if any(word in label for word in ("wheel", "roller", "tire")) and component["geometry"] == "cylinder":
        radius = min(x, y) / 2
        width = z
        axle_bore = min(0.0054, radius * 0.32)
        visuals = [_visual("cylinder", (2 * radius, 2 * radius, width), rpy=(0, math.pi / 2, 0), color=color, bore_m=axle_bore)]
        hub_d = radius * 1.05
        hub_w = max(width * 0.28, 0.006)
        for side in (-1, 1):
            visuals.append(_visual("cylinder", (hub_d, hub_d, hub_w), origin=(side * (width / 2 + hub_w * 0.25), 0, 0), rpy=(0, math.pi / 2, 0), color="#AEB9C2", is_detail=True, bore_m=axle_bore))
        # Tread blocks and paired face grooves give the wheel a readable manufactured silhouette.
        tread_depth = max(radius * 0.12, 0.004)
        tread_width = (2 * math.pi * radius / 18) * 0.72
        for index in range(18):
            angle = 2 * math.pi * index / 18
            visuals.append(_visual("box", (width * 1.02, tread_width, tread_depth), origin=(0, math.sin(angle) * (radius - tread_depth * 0.25), math.cos(angle) * (radius - tread_depth * 0.25)), rpy=(angle, 0, 0), color="#30383E", is_detail=True))
        return visuals

    if "caster" in label and component["geometry"] == "sphere":
        ball_d = max(0.01, z * 0.76)
        cup_d = max(0.012, z * 0.88)
        cup_h = max(0.008, z * 0.44)
        flange_h = max(0.004, z * 0.12)
        return [
            _visual("sphere", (ball_d, ball_d, ball_d), origin=(0, 0, -z / 2 + ball_d / 2), color=color),
            _visual("cylinder", (cup_d, cup_d, cup_h), origin=(0, 0, z / 2 - flange_h - cup_h / 2 + 0.002), color="#788C98", is_detail=True),
            _visual("box", (x * 1.05, y * 1.05, flange_h), origin=(0, 0, z / 2 - flange_h / 2), color="#AEB9C2"),
        ]

    if any(word in label for word in ("sonar", "transducer", "ultrasonic", "rangefinder")) and component["geometry"] == "cylinder":
        # Standalone cylindrical transducer can: orient horizontally along outward axis.
        pos = component.get("position_m", [0, 0, 0])
        if parent_position is not None:
            dy = abs(float(pos[1]) - float(parent_position[1]))
            dx = abs(float(pos[0]) - float(parent_position[0]))
            along_y = dy >= dx
        else:
            along_y = abs(float(pos[1])) >= abs(float(pos[0]))
        rpy = (math.pi / 2, 0, 0) if along_y else (0, math.pi / 2, 0)
        sign_y = 1.0 if float(pos[1]) >= 0 else -1.0
        sign_x = 1.0 if float(pos[0]) >= 0 else -1.0
        cap_offset = (0, sign_y * (z / 2 + 0.001), 0) if along_y else (sign_x * (z / 2 + 0.001), 0, 0)
        return [
            _visual("cylinder", (x, y, z), rpy=rpy, color=color, bore_m=min(x, y) * 0.62),
            _visual("cylinder", (x * 0.76, y * 0.76, 0.002), origin=cap_offset, rpy=rpy, color="#26343B", is_detail=True),
        ]

    if (
        any(word in label for word in ("cage", "frame", "chassis"))
        and not any(word in label for word in ("plate", "deck", "sheet", "board", "tray"))
        and z >= 0.035
        and component["geometry"] == "box"
    ):
        beam = max(0.009, min(x, y, z) * 0.075)
        plate = max(0.004, beam * 0.34)
        hx, hy, hz = x / 2, y / 2, z / 2
        visuals = []
        for px in (-hx + beam / 2, hx - beam / 2):
            for py in (-hy + beam / 2, hy - beam / 2):
                visuals.append(_visual("box", (beam, beam, z), origin=(px, py, 0), color=color, is_detail=True))
        for pz in (-hz + beam / 2, hz - beam / 2):
            for py in (-hy + beam / 2, hy - beam / 2):
                visuals.append(_visual("box", (x - 2 * beam, beam, beam), origin=(0, py, pz), color="#788C98", is_detail=True))
            for px in (-hx + beam / 2, hx - beam / 2):
                visuals.append(_visual("box", (beam, y - 2 * beam, beam), origin=(px, 0, pz), color="#788C98", is_detail=True))
        # Transparent panels make an enclosure read as a framed cage, not a solid block.
        for px in (-hx + plate / 2, hx - plate / 2):
            visuals.append(_visual("box", (plate, y - 2 * beam, z - 2 * beam), origin=(px, 0, 0), color="#A9D8E1", alpha=0.22, is_detail=True))
        for py in (-hy + plate / 2, hy - plate / 2):
            visuals.append(_visual("box", (x - 2 * beam, plate, z - 2 * beam), origin=(0, py, 0), color="#A9D8E1", alpha=0.18, is_detail=True))
        # Top and lower acrylic decks are thin visual plates; they do not imply a rated structure.
        visuals.append(_visual("box", (x - 2 * beam, y - 2 * beam, plate), origin=(0, 0, hz - plate / 2), color="#D4EFF1", alpha=0.34))
        visuals.append(_visual("box", (x - 2 * beam, y - 2 * beam, plate), origin=(0, 0, -hz + plate / 2), color="#B7D9DE", alpha=0.25))
        return visuals

    is_standoff_cyl = any(word in label for word in ("standoff", "spacer", "pillar")) and component["geometry"] == "cylinder"
    base_bore = min(0.0032, min(x, y) * 0.42) if is_standoff_cyl else 0.0
    visuals = [_visual(component["geometry"], (x, y, z), color=color, bore_m=base_bore)]
    if "lidar" in label or "laser scanner" in label:
        rim_h = max(0.004, z * 0.12)
        visuals.append(_visual("cylinder", (x * 1.08, y * 1.08, rim_h), origin=(0, 0, z * 0.38), color="#8799A4", is_detail=True))
        visuals.append(_visual("cylinder", (x * 0.88, y * 0.88, rim_h * 0.7), origin=(0, 0, z * 0.48), color="#26343B", is_detail=True))
    elif "camera" in label:
        lens = max(0.008, min(x, y, z) * 0.3)
        visuals.append(_visual("cylinder", (lens, lens, max(0.006, x * 0.13)), origin=(x * 0.54, 0, 0), rpy=(0, math.pi / 2, 0), color="#26343B", is_detail=True))
        visuals.append(_visual("sphere", (lens * 0.55,) * 3, origin=(x * 0.62, 0, 0), color="#4F91B5", is_detail=True))
    elif "gripper" in label or "claw" in label:
        finger = max(0.008, min(x, y, z) * 0.18)
        visuals.append(_visual("box", (finger, y * 0.72, z * 0.55), origin=(-x * 0.48, 0, -z * 0.5), color="#BBC4C9", is_detail=True))
        visuals.append(_visual("box", (finger, y * 0.72, z * 0.55), origin=(x * 0.48, 0, -z * 0.5), color="#BBC4C9", is_detail=True))
    elif any(word in label for word in ("sonar", "ultrasonic", "rangefinder")):
        if component["geometry"] == "cylinder":
            # Individual ultrasonic transducer can facing forward (+Y)
            visuals[0]["rpy"] = [math.pi / 2, 0.0, 0.0]
            visuals[0]["color"] = "#CBD5E1"
            visuals[0]["bore_m"] = min(x, y) * 0.64
            visuals.append(_visual("cylinder", (min(x, y) * 0.60, min(x, y) * 0.60, z * 0.45), origin=(0, 0, 0), rpy=(math.pi / 2, 0, 0), color="#1E293B", is_detail=True))
        elif "board" in label or "pod" in label:
            # Ultrasonic carrier PCB when transducer cans are separate child parts: add 4-pin JST header & crystal
            hdr_h = max(0.004, y * 0.45)
            visuals.append(_visual("box", (min(0.014, x * 0.18), hdr_h, min(0.008, z * 0.22)), origin=(0, -y / 2 - hdr_h / 2, -z * 0.22), color="#F8FAFC", is_detail=True))
            visuals.append(_visual("box", (min(0.012, x * 0.14), max(0.003, y * 0.28), min(0.006, z * 0.16)), origin=(0, y / 2 + 0.0015, z * 0.22), color="#94A3B8", is_detail=True))
        else:
            # Standalone rangefinder box: two transducer cans facing outward.
            can_d = max(0.006, min(x, y, z) * 0.45)
            along_y = x >= y
            for side in (-1, 1):
                if along_y:
                    visuals.append(_visual("cylinder", (can_d, can_d, max(0.006, y * 0.35)), origin=(side * x * 0.24, y / 2 + 0.002, 0), rpy=(math.pi / 2, 0, 0), color="#AEB9C2", is_detail=True, bore_m=can_d * 0.62))
                else:
                    visuals.append(_visual("cylinder", (can_d, can_d, max(0.006, x * 0.15)), origin=(x / 2 + 0.002, side * y * 0.22, 0), rpy=(0, math.pi / 2, 0), color="#AEB9C2", is_detail=True, bore_m=can_d * 0.62))
    elif "breadboard" in label or "protoboard" in label:
        # Solderless breadboard: cream tie-point body with red/blue power distribution rails and center DIP ravine
        visuals[0]["color"] = "#F1F5F9"
        rail_h = 0.0008
        visuals.append(_visual("box", (max(0.0025, x * 0.05), y * 0.88, rail_h), origin=(-x * 0.38, 0, z / 2 + rail_h / 2), color="#EF4444", is_detail=True))
        visuals.append(_visual("box", (max(0.0025, x * 0.05), y * 0.88, rail_h), origin=(x * 0.38, 0, z / 2 + rail_h / 2), color="#3B82F6", is_detail=True))
        visuals.append(_visual("box", (max(0.004, x * 0.08), y * 0.88, rail_h), origin=(0, 0, z / 2 + rail_h / 2), color="#94A3B8", is_detail=True))
        hdr_h = max(0.0045, z * 0.36)
        visuals.append(_visual("box", (min(0.016, x * 0.24), min(0.008, y * 0.10), hdr_h), origin=(x * 0.22, -y * 0.34, z / 2 + hdr_h / 2), color="#1E293B", is_detail=True))
    elif any(word in label for word in ("pcb", "board", "controller", "esc", "computer", "raspberry", "arduino", "pixhawk", "driver")):
        # Detailed 3D PCB Carrier / Motherboard stack matching ForgeBot-PowerSense-Carrier-v1.0:
        # 1) Gold XT60 Battery Header (-X, -Y)
        xt_h = max(0.005, z * 0.48)
        visuals.append(_visual("box", (min(0.016, x * 0.20), min(0.009, y * 0.16), xt_h), origin=(-x * 0.32, -y * 0.28, z / 2 + xt_h / 2), color="#EAB308", is_detail=True))
        # 2) Red 10A Blade Fuse Block
        fuse_h = max(0.0045, z * 0.42)
        visuals.append(_visual("box", (min(0.012, x * 0.15), min(0.006, y * 0.12), fuse_h), origin=(-x * 0.10, -y * 0.28, z / 2 + fuse_h / 2), color="#EF4444", is_detail=True))
        # 3) 5V 3A Synchronous Buck Module + Toroidal Inductor
        buck_h = max(0.0035, z * 0.32)
        visuals.append(_visual("box", (x * 0.24, y * 0.28, buck_h), origin=(-x * 0.22, 0.0, z / 2 + buck_h / 2), color="#0EA5E9", is_detail=True))
        ind_d = max(0.006, min(x, y) * 0.14)
        visuals.append(_visual("cylinder", (ind_d, ind_d, buck_h * 1.4), origin=(-x * 0.22, 0.0, z / 2 + buck_h + buck_h * 0.5), color="#334155", is_detail=True))
        # 4) 40-Pin 2.54mm GPIO Header Block & Compute IC
        gpio_h = max(0.0055, z * 0.52)
        visuals.append(_visual("box", (x * 0.32, min(0.010, y * 0.16), gpio_h), origin=(x * 0.12, y * 0.04, z / 2 + gpio_h / 2), color="#1E293B", is_detail=True))
        chip_h = max(0.003, z * 0.26)
        visuals.append(_visual("box", (x * 0.24, y * 0.24, chip_h), origin=(x * 0.12, -y * 0.14, z / 2 + chip_h / 2), color="#14181D", is_detail=True))
        # 5) White JST-XH Motor Driver & Left/Right Ultrasonic Headers
        jst_h = max(0.0045, z * 0.42)
        visuals.append(_visual("box", (min(0.018, x * 0.22), min(0.007, y * 0.12), jst_h), origin=(x * 0.25, -y * 0.28, z / 2 + jst_h / 2), color="#F8FAFC", is_detail=True))
        for side in (-1, 1):
            visuals.append(_visual("box", (min(0.014, x * 0.18), min(0.007, y * 0.12), jst_h), origin=(side * x * 0.24, y * 0.30, z / 2 + jst_h / 2), color="#F8FAFC", is_detail=True))
        # 6) Raised Copper Power Bus Traces on Top Solder Mask
        trace_h = 0.0006
        visuals.append(_visual("box", (x * 0.55, 0.0022, trace_h), origin=(0.0, -y * 0.28, z / 2 + trace_h / 2), color="#D97706", is_detail=True))
        visuals.append(_visual("box", (0.0020, y * 0.52, trace_h), origin=(-x * 0.22, 0.0, z / 2 + trace_h / 2), color="#D97706", is_detail=True))
    elif any(word in label for word in ("motor", "servo", "actuator", "gearbox")):
        # Drive unit: gearbox body, rear DC motor cap, and steel output shaft.
        shaft_d = max(0.005, min(x, y, z) * 0.22)
        pos = component.get("position_m", [0, 0, 0])
        if parent_position is not None and abs(float(pos[0]) - float(parent_position[0])) > 0.01:
            sign_x = 1.0 if float(pos[0]) >= float(parent_position[0]) else -1.0
            shaft_l = max(0.008, x * 0.26)
            visuals.append(_visual("cylinder", (shaft_d, shaft_d, shaft_l), origin=(sign_x * (x / 2 + shaft_l / 2 - 0.002), 0, 0), rpy=(0, math.pi / 2, 0), color="#AEB9C2", is_detail=True))
            can_d = min(x, z) * 0.88
            can_l = max(0.012, y * 0.34)
            visuals.append(_visual("cylinder", (can_d, can_d, can_l), origin=(0, -y / 2 + can_l / 2 - 0.002, 0), rpy=(math.pi / 2, 0, 0), color="#8A959E", is_detail=True))
        else:
            shaft_l = max(0.006, z * 0.18)
            visuals.append(_visual("cylinder", (shaft_d, shaft_d, shaft_l), origin=(0, 0, z / 2 + shaft_l / 2 - 0.002), color="#AEB9C2", is_detail=True))
            cap_h = max(0.004, z * 0.12)
            visuals.append(_visual("box" if component["geometry"] == "box" else "cylinder", (x * 0.92, y * 0.92, cap_h), origin=(0, 0, -z / 2 + cap_h / 2), color="#3A4248", is_detail=True))
    elif any(word in label for word in ("battery", "lipo")):
        # Power pack: terminals on top and two retaining straps.
        for side in (-1, 1):
            visuals.append(_visual("cylinder", (0.008, 0.008, 0.012), origin=(side * x * 0.25, 0, z / 2 + 0.006), color="#C0392B" if side < 0 else "#287c72", is_detail=True))
        for fraction in (-0.25, 0.25):
            visuals.append(_visual("box", (x * 1.02, y * 1.02, max(0.004, z * 0.06)), origin=(0, 0, fraction * z), color="#3A4248", is_detail=True))
    elif any(word in label for word in ("standoff", "spacer", "pillar")):
        # Spacer post with washer discs on both faces and M3 center bore.
        washer_d = min(x, y) * 1.5
        washer_h = max(0.003, z * 0.08)
        for side in (-1, 1):
            visuals.append(_visual("cylinder", (washer_d, washer_d, washer_h), origin=(0, 0, side * (z / 2 - washer_h / 2)), color="#AEB9C2", is_detail=True, bore_m=base_bore))
    elif any(word in label for word in ("joint", "hinge", "pivot")):
        # Hinge: cheek plates plus a steel pivot pin across Y.
        cheek = max(0.004, y * 0.18)
        for side in (-1, 1):
            visuals.append(_visual("box", (x * 0.9, cheek, z * 0.9), origin=(0, side * (y / 2 - cheek / 2), 0), color=color, is_detail=True))
        pin_d = max(0.006, min(x, z) * 0.3)
        visuals.append(_visual("cylinder", (pin_d, pin_d, y * 1.05), origin=(0, 0, 0), rpy=(math.pi / 2, 0, 0), color="#AEB9C2", is_detail=True))
    elif any(word in label for word in ("propeller", "prop", "rotor", "blade", "fan")):
        # Rotor: hub plus a two-blade span.
        hub_d = max(0.008, min(x, y) * 0.25)
        visuals.append(_visual("cylinder", (hub_d, hub_d, max(0.006, z * 1.2)), origin=(0, 0, 0), color="#3A4248", is_detail=True))
        visuals.append(_visual("box", (x, max(0.004, min(x, y) * 0.16), max(0.002, z * 0.4)), origin=(0, 0, 0), color="#22282E", is_detail=True))
    elif any(word in label for word in ("antenna", "aerial", "gps")):
        # Whip: base, thin pole, and a tip bead (swept backward when paired/whisker).
        is_swept = any(w in id_and_name for w in ("left", "right", "whisker", "crest"))
        pos_y = float(component.get("position_m", [0, 0, 0])[1])
        roll = (0.18 if pos_y >= 0 else -0.18) if is_swept else 0.0
        pitch = -0.68 if is_swept else 0.0
        visuals[0]["rpy"] = [roll, pitch, 0.0]
        visuals[0]["color"] = "#D97706" if is_swept else color
        pole_d = max(0.003, min(x, y) * 0.45 if is_swept else min(x, y) * 0.15)
        pole_h = z * 0.95 if is_swept else z * 0.8
        visuals.append(_visual("cylinder", (pole_d, pole_d, pole_h), origin=(-z * 0.18 if is_swept else 0, 0, z * 0.15 if is_swept else -z / 2 + z * 0.1 + pole_h / 2), rpy=(roll, pitch, 0), color="#EAB308" if is_swept else "#22282E", is_detail=True))
        tip_d = pole_d * (1.6 if is_swept else 2.2)
        visuals.append(_visual("sphere", (tip_d, tip_d, tip_d), origin=(-z * 0.45 if is_swept else 0, 0, z * 0.42 if is_swept else -z / 2 + z * 0.1 + pole_h + tip_d / 2), color="#38BDF8" if is_swept else "#C0392B", is_detail=True))
    elif any(word in label for word in ("foot", "feet", "skid")):
        # Standalone foot: rubber sole under the body block.
        sole_h = max(0.004, z * 0.25)
        visuals.append(_visual("box", (x * 1.1, y * 1.1, sole_h), origin=(0, 0, -z / 2 + sole_h / 2), color="#22282E", is_detail=True))
    return visuals


def _urdf_geometry(visual: dict) -> str:
    x, y, z = map(_fmt, visual["dimensions_m"])
    if visual["geometry"] == "box":
        return f'<box size="{x} {y} {z}"/>'
    if visual["geometry"] == "cylinder":
        return f'<cylinder radius="{_fmt(min(float(x), float(y)) / 2)}" length="{z}"/>'
    return f'<sphere radius="{_fmt(min(float(x), float(y), float(z)) / 2)}"/>'


def _leg_parent_position(by_id: dict, component: dict) -> list[float] | None:
    """Return the parent link center so leg visuals know which side to splay."""
    parent_id = component.get("parent_id")
    if parent_id and parent_id in by_id:
        return list(by_id[parent_id]["position_m"])
    return None


def _component_mass_and_inertia(component: dict) -> tuple[float, tuple[float, float, float], float]:
    """Estimate mass (kg), principal inertia (ixx, iyy, izz in kg*m^2), and density (kg/m^3)."""
    x, y, z = (float(v) for v in component["dimensions_m"])
    geom = component.get("geometry", "box")
    if geom == "cylinder":
        r = min(x, y) / 2.0
        volume = math.pi * r * r * z
    elif geom == "sphere":
        r = min(x, y, z) / 2.0
        volume = (4.0 / 3.0) * math.pi * (r ** 3)
    else:
        volume = x * y * z

    label = f"{component.get('name', '')} {component.get('role', '')} {component.get('functional_category', '')}".lower()
    if any(w in label for w in ("standoff", "spacer", "pillar", "caster", "shaft", "aluminum", "steel")):
        density = 2700.0 * 0.45
    elif any(w in label for w in ("motor", "servo", "actuator", "gear")):
        density = 2100.0 * 0.40
    elif any(w in label for w in ("battery", "lipo", "power")):
        density = 1750.0 * 0.55
    elif any(w in label for w in ("pcb", "board", "controller", "arduino", "raspberry", "breadboard", "sonar", "ultrasonic")):
        density = 1350.0 * 0.30
    elif any(w in label for w in ("plate", "deck", "chassis", "frame")):
        density = 1180.0 * 0.35
    elif any(w in label for w in ("wheel", "tire", "roller")):
        density = 950.0 * 0.35
    else:
        density = 1100.0 * 0.30

    mass = max(0.005, round(volume * density, 4))
    if geom == "cylinder":
        r = min(x, y) / 2.0
        ixx = iyy = max(1e-5, (1.0 / 12.0) * mass * (3.0 * r * r + z * z))
        izz = max(1e-5, 0.5 * mass * r * r)
    elif geom == "sphere":
        r = min(x, y, z) / 2.0
        ixx = iyy = izz = max(1e-5, 0.4 * mass * r * r)
    else:
        ixx = max(1e-5, (1.0 / 12.0) * mass * (y * y + z * z))
        iyy = max(1e-5, (1.0 / 12.0) * mass * (x * x + z * z))
        izz = max(1e-5, (1.0 / 12.0) * mass * (x * x + y * y))
    return mass, (ixx, iyy, izz), round(density, 1)


def generate_image_robot_urdf(design: dict) -> str:
    rows = ['<?xml version="1.0"?>', '<robot name="image_concept">', '  <link name="base_link"/>']
    by_id = {item["id"]: item for item in design["components"]}
    for item in design["components"]:
        rows.append(f'  <link name="{item["id"]}">')
        mass, (ixx, iyy, izz), _ = _component_mass_and_inertia(item)
        rows.append(
            f'    <inertial><origin xyz="0 0 0" rpy="0 0 0"/><mass value="{mass:.4f}"/>'
            f'<inertia ixx="{ixx:.6f}" ixy="0" ixz="0" iyy="{iyy:.6f}" iyz="0" izz="{izz:.6f}"/></inertial>'
        )
        visuals = _component_visuals(item, _leg_parent_position(by_id, item))
        for index, visual in enumerate(visuals):
            px, py, pz = map(_fmt, visual["origin_m"])
            rr, rp, ry = map(_fmt, visual["rpy"])
            rgb = [int(visual["color"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
            rgba = " ".join(f"{channel:.3f}" for channel in rgb) + f" {float(visual['alpha']):.3f}"
            rows.append(f'    <visual name="visual_{item["id"]}_{index}"><origin xyz="{px} {py} {pz}" rpy="{rr} {rp} {ry}"/><geometry>{_urdf_geometry(visual)}</geometry><material name="mat_{item["id"]}_{index}"><color rgba="{rgba}"/></material></visual>')
        if visuals:
            v0 = visuals[0]
            cpx, cpy, cpz = map(_fmt, v0["origin_m"])
            crr, crp, cry = map(_fmt, v0["rpy"])
            rows.append(f'    <collision name="collision_{item["id"]}"><origin xyz="{cpx} {cpy} {cpz}" rpy="{crr} {crp} {cry}"/><geometry>{_urdf_geometry(v0)}</geometry></collision>')
        rows.append('  </link>')
        parent = item["parent_id"] or "base_link"
        parent_pose = by_id[item["parent_id"]]["position_m"] if item["parent_id"] else [0, 0, 0]
        px, py, pz = (_fmt(float(item["position_m"][i]) - float(parent_pose[i])) for i in range(3))
        if _component_is_wheel(item):
            rows.append(
                f'  <joint name="mount_{item["id"]}" type="revolute"><parent link="{parent}"/><child link="{item["id"]}"/>'
                f'<origin xyz="{px} {py} {pz}" rpy="0 0 0"/><axis xyz="1 0 0"/>'
                f'<limit lower="-3.14159" upper="3.14159" effort="2.5" velocity="12.0"/></joint>'
            )
        elif any(w in f"{item['id']} {item['name']}".lower() for w in ("hip", "knee", "foot_claw", "beak", "tail")):
            rows.append(
                f'  <joint name="mount_{item["id"]}" type="revolute"><parent link="{parent}"/><child link="{item["id"]}"/>'
                f'<origin xyz="{px} {py} {pz}" rpy="0 0 0"/><axis xyz="0 1 0"/>'
                f'<limit lower="-0.7854" upper="0.7854" effort="4.0" velocity="6.0"/></joint>'
            )
        elif any(w in f"{item['id']} {item['name']}".lower() for w in ("wing", "neck")):
            axis_str = "1 0 0" if "wing" in f"{item['id']} {item['name']}".lower() else "0 0 1"
            rows.append(
                f'  <joint name="mount_{item["id"]}" type="revolute"><parent link="{parent}"/><child link="{item["id"]}"/>'
                f'<origin xyz="{px} {py} {pz}" rpy="0 0 0"/><axis xyz="{axis_str}"/>'
                f'<limit lower="-0.65" upper="0.65" effort="3.0" velocity="8.0"/></joint>'
            )
        else:
            rows.append(f'  <joint name="mount_{item["id"]}" type="fixed"><parent link="{parent}"/><child link="{item["id"]}"/><origin xyz="{px} {py} {pz}" rpy="0 0 0"/></joint>')
    rows.append('</robot>')
    return "\n".join(rows)


def _infer_locomotion_profile(design: dict, components: list[dict], wheels: list[dict]) -> dict:
    """Infer locomotion mode, CAD forward axis, gait description, and per-part animation role."""
    by_id = {p["id"]: p for p in components}
    all_text = " ".join(
        [str(design.get("title", "")), str(design.get("robot_type", ""))]
        + [f"{p.get('id', '')} {p.get('name', '')} {p.get('role', '')}" for p in components]
    ).lower()

    # Determine bilateral left/right symmetry axis to know whether +X or +Y is forward in CAD space
    if wheels and len(wheels) >= 2:
        span_x = max(float(w["position_m"][0]) for w in wheels) - min(float(w["position_m"][0]) for w in wheels)
        span_y = max(float(w["position_m"][1]) for w in wheels) - min(float(w["position_m"][1]) for w in wheels)
        forward_axis = "y" if span_x >= span_y else "x"
    else:
        pair_dx = 0.0
        pair_dy = 0.0
        for p in components:
            pid = p["id"]
            if "left" in pid:
                partner = by_id.get(pid.replace("left", "right"))
                if partner:
                    pair_dx += abs(float(p["position_m"][0]) - float(partner["position_m"][0]))
                    pair_dy += abs(float(p["position_m"][1]) - float(partner["position_m"][1]))
        forward_axis = "y" if pair_dx > pair_dy else "x"

    has_wings = any("wing" in f"{p.get('id', '')} {p.get('name', '')}".lower() for p in components)
    has_legs = any(
        _is_leg_component(p) or any(w in f"{p.get('id', '')} {p.get('name', '')}".lower() for w in ("hip", "femur", "knee", "tibia", "claw"))
        for p in components
    )

    if wheels:
        mode = "wheeled"
        gait_label = "Differential-Drive Wheel Rotation"
    elif has_wings and has_legs:
        mode = "bipedal_avian"
        gait_label = "Digitigrade Bipedal Stride + Wing Balance Assist"
    elif has_legs:
        mode = "bipedal_walker" if ("biped" in all_text or "humanoid" in all_text) else "legged_walker"
        gait_label = "Articulated Bipedal Walking Gait" if mode == "bipedal_walker" else "Multi-Legged Walking Gait"
    elif has_wings or any(w in all_text for w in ("drone", "quadcopter", "aerial")):
        mode = "aerial"
        gait_label = "Aerial Wing / Rotor Propulsion"
    else:
        mode = "wheeled"
        gait_label = "Kinematic Surface Locomotion"

    component_roles: dict[str, str] = {}
    for p in components:
        pid = p["id"]
        id_name = f"{pid} {p.get('name', '')}".lower()
        parent_id = p.get("parent_id")
        parent_name = f"{parent_id or ''} {(by_id.get(parent_id) or {}).get('name', '')}".lower()

        if "left" in id_name:
            side = "left"
        elif "right" in id_name:
            side = "right"
        else:
            lat = float(p["position_m"][1]) if forward_axis == "x" else -float(p["position_m"][0])
            side = "left" if lat >= 0 else "right"

        if _component_is_wheel(p):
            component_roles[pid] = f"wheel_{side}"
        elif any(w in id_name for w in ("hip_mount", "hip_joint", "hip")):
            component_roles[pid] = f"hip_{side}"
        elif any(w in id_name for w in ("femur", "thigh")):
            component_roles[pid] = f"hip_{side}" if "hip" not in parent_name else f"femur_{side}"
        elif any(w in id_name for w in ("knee_servo", "knee_joint", "knee")):
            component_roles[pid] = f"knee_{side}"
        elif any(w in id_name for w in ("tibia", "shin")):
            component_roles[pid] = f"knee_{side}" if "knee" not in parent_name else f"tibia_{side}"
        elif any(w in id_name for w in ("foot_claw", "claw", "talon", "foot", "ankle")):
            component_roles[pid] = f"ankle_{side}"
        elif "wing" in id_name:
            component_roles[pid] = f"wing_{side}"
        elif "tail" in id_name:
            component_roles[pid] = "tail_stabilizer"
        elif "neck" in id_name:
            component_roles[pid] = "neck_yaw"
        elif "head" in id_name:
            component_roles[pid] = "head_pitch" if "neck" in parent_name else "neck_yaw"
        elif "beak" in id_name or "jaw" in id_name:
            component_roles[pid] = "beak_jaw"
        elif parent_id is None or any(w in id_name for w in ("torso", "chest", "chassis_base")):
            component_roles[pid] = "torso_root"
        elif _is_leg_component(p):
            component_roles[pid] = f"hip_{side}"
        else:
            component_roles[pid] = "static"

    return {
        "mode": mode,
        "forward_axis": forward_axis,
        "gait_label": gait_label,
        "has_wings": has_wings,
        "has_legs": has_legs,
        "component_roles": component_roles,
    }


def simulate_image_robot(design: dict) -> dict:
    """Run mass-property, static stability, and kinematic waypoint route simulation."""
    from .simulation import ENGINE_NAME, ENGINE_VERSION, run_waypoint_route

    components = design.get("components", [])
    total_mass = 0.0
    weighted_pos = [0.0, 0.0, 0.0]
    part_masses = []
    wheels = []
    support_points = []

    for part in components:
        mass, _, density = _component_mass_and_inertia(part)
        pos = [float(v) for v in part["position_m"]]
        dims = _assembly_bounds_dimensions(part)
        total_mass += mass
        for axis in range(3):
            weighted_pos[axis] += mass * pos[axis]
        part_masses.append({
            "id": part["id"],
            "name": part["name"],
            "mass_kg": round(mass, 4),
            "density_kg_m3": int(density),
        })
        if _component_is_wheel(part):
            wheels.append(part)
        label = f"{part.get('name', '')} {part.get('role', '')}".lower()
        if _component_is_wheel(part) or "caster" in label or _is_leg_component(part) or "foot" in label:
            support_points.append((pos[0], pos[1], pos[2] - dims[2] / 2.0, dims[0], dims[1]))

    total_mass = max(total_mass, 1e-6)
    com = [round(weighted_pos[axis] / total_mass, 4) for axis in range(3)]

    if support_points:
        x_min = min(p[0] - p[3] / 2.0 for p in support_points)
        x_max = max(p[0] + p[3] / 2.0 for p in support_points)
        y_min = min(p[1] - p[4] / 2.0 for p in support_points)
        y_max = max(p[1] + p[4] / 2.0 for p in support_points)
        ground_z = min(p[2] for p in support_points)
    else:
        x_min = min(float(p["position_m"][0]) - float(p["dimensions_m"][0]) / 2.0 for p in components) if components else -0.1
        x_max = max(float(p["position_m"][0]) + float(p["dimensions_m"][0]) / 2.0 for p in components) if components else 0.1
        y_min = min(float(p["position_m"][1]) - float(p["dimensions_m"][1]) / 2.0 for p in components) if components else -0.1
        y_max = max(float(p["position_m"][1]) + float(p["dimensions_m"][1]) / 2.0 for p in components) if components else 0.1
        ground_z = min(float(p["position_m"][2]) - float(p["dimensions_m"][2]) / 2.0 for p in components) if components else 0.0

    margin_x = min(com[0] - x_min, x_max - com[0])
    margin_y = min(com[1] - y_min, y_max - com[1])
    stability_margin_m = round(min(margin_x, margin_y), 4)
    com_height_above_ground = max(0.01, com[2] - ground_z)
    tip_over_angle_deg = round(math.degrees(math.atan2(max(0.0, stability_margin_m), com_height_above_ground)), 1)

    if wheels:
        avg_wheel_dia = sum(min(float(w["dimensions_m"][0]), float(w["dimensions_m"][1])) for w in wheels) / len(wheels)
        wheel_xs = [float(w["position_m"][0]) for w in wheels]
        wheel_ys = [float(w["position_m"][1]) for w in wheels]
        track_width_m = round(max(max(wheel_xs) - min(wheel_xs), max(wheel_ys) - min(wheel_ys)), 4)
        # Estimate cruise speed from wheel diameter assuming ~90 RPM geared hobby motor
        speed_m_s = round(min(0.95, max(0.25, (avg_wheel_dia / 2.0) * (90.0 * 2.0 * math.pi / 60.0))), 3)
    else:
        avg_wheel_dia = 0.0
        track_width_m = round(x_max - x_min, 4)
        speed_m_s = 0.45

    motor_effort = round(min(9.5, max(1.2, total_mass * 9.81 * max(avg_wheel_dia / 2.0, 0.04) * 1.5)), 2)
    waypoints = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]
    sim = run_waypoint_route(
        waypoints,
        speed_m_s=speed_m_s,
        max_speed_m_s=1.2,
        motor_effort=motor_effort,
        max_motor_effort=10.0,
    )
    violations = list(sim.violations)
    if stability_margin_m <= 0:
        violations.append(f"Center of mass projection falls outside the support footprint by {-stability_margin_m * 1000:.1f} mm.")
    passed = sim.passed and stability_margin_m > 0

    by_id = {p["id"]: p for p in components}
    locomotion_profile = _infer_locomotion_profile(design, components, wheels)
    robot_preview = {
        "ground_z_m": round(ground_z, 4),
        "locomotion_profile": locomotion_profile,
        "components": [
            {
                "id": p["id"],
                "name": p.get("name", p["id"]),
                "role": p.get("role", ""),
                "functional_category": p.get("functional_category", ""),
                "parent_id": p.get("parent_id"),
                "geometry": p.get("geometry", "box"),
                "dimensions_m": p["dimensions_m"],
                "position_m": p["position_m"],
                "color": p.get("color", "#4c9b8c"),
                "is_wheel": _component_is_wheel(p),
                "anim_role": locomotion_profile["component_roles"].get(p["id"], "static"),
                "visuals": _component_visuals(p, _leg_parent_position(by_id, p)),
            }
            for p in components
        ],
    }

    from .mechatronics import (
        compute_power_and_battery_sizing,
        generate_deployable_firmware_package,
        generate_pcb_and_wiring_architecture,
    )
    from .world_training import run_obstacle_world_training

    obstacle_training = run_obstacle_world_training(
        design=design,
        com_height_m=com_height_above_ground,
        stability_margin_m=stability_margin_m,
        tip_over_angle_deg=tip_over_angle_deg,
        track_width_m=track_width_m,
        wheel_diameter_m=avg_wheel_dia,
        cruise_speed_m_s=speed_m_s,
        motor_effort_nm=motor_effort,
    )

    power_sizing = compute_power_and_battery_sizing(
        design=design,
        total_mass_kg=total_mass,
        wheel_diameter_m=avg_wheel_dia,
        wheel_count=len(wheels),
        track_width_m=track_width_m,
        cruise_speed_m_s=speed_m_s,
        obstacle_training=obstacle_training,
        target_runtime_hours=3.0,
    )
    pcb_wiring = generate_pcb_and_wiring_architecture(design=design, power_report=power_sizing)
    robot_preview["wiring_harness_3d"] = pcb_wiring.get("wiring_harness_3d", [])
    firmware_bundle = generate_deployable_firmware_package(
        design=design,
        power_report=power_sizing,
        pcb_wiring=pcb_wiring,
        obstacle_training=obstacle_training,
    )

    return {
        "status": "completed",
        "engine": f"{ENGINE_NAME} {ENGINE_VERSION} + obstacle world policy trainer & co-design analyzer",
        "waypoints": obstacle_training["course_waypoints"],
        "passed": passed and obstacle_training["best_episode"]["reached_goal"],
        "distance_m": obstacle_training["best_episode"]["distance_m"],
        "max_speed_used": speed_m_s,
        "collisions": obstacle_training["best_episode"]["collisions"],
        "violations": violations,
        "mass_properties": {
            "total_mass_kg": round(total_mass, 3),
            "center_of_mass_m": com,
            "com_height_above_ground_m": round(com_height_above_ground, 4),
            "components": part_masses,
        },
        "stability": {
            "support_footprint_m": {
                "x_min": round(x_min, 4),
                "x_max": round(x_max, 4),
                "y_min": round(y_min, 4),
                "y_max": round(y_max, 4),
            },
            "stability_margin_m": stability_margin_m,
            "tip_over_angle_deg": tip_over_angle_deg,
            "static_stable": stability_margin_m > 0,
        },
        "drivetrain": {
            "wheel_count": len(wheels),
            "wheel_diameter_m": round(avg_wheel_dia, 4),
            "track_width_m": track_width_m,
            "cruise_speed_m_s": speed_m_s,
            "estimated_motor_effort_nm": motor_effort,
        },
        "robot_preview": robot_preview,
        "obstacle_training": obstacle_training,
        "mechatronics": {
            "power_sizing": power_sizing,
            "pcb_wiring": pcb_wiring,
            "firmware_files": firmware_bundle["files"],
        },
        "assumptions": [
            *sim.assumptions,
            "Component masses and moments of inertia are estimated from bounding primitive volumes and typical material densities.",
            "Static stability margin measures horizontal distance from the 3D Center of Mass projection to the support footprint boundary.",
            "Obstacle world policy training iteratively tunes potential-field repulsion, lookahead horizon, and yaw-coupled speed damping across 8 episodes.",
            "Battery capacity and motor current are physics-coupled to tire normal load, rolling resistance, obstacle-avoidance turning scrub, and a 7.5 W avionics + vision load for a 3.0-hour mission.",
        ],
        "label": "simulated; requires engineering review",
    }


def generate_image_robot_firmware_artifact(design: dict, simulation: dict | None = None) -> str:
    """Return the JSON-serialized deployable firmware, custom KiCad PCB, and wiring harness artifact."""
    from .mechatronics import (
        compute_power_and_battery_sizing,
        generate_deployable_firmware_package,
        generate_pcb_and_wiring_architecture,
    )

    sim = simulation if simulation is not None else simulate_image_robot(design)
    mech = sim.get("mechatronics")
    if mech and "power_sizing" in mech and "pcb_wiring" in mech:
        pkg = generate_deployable_firmware_package(
            design=design,
            power_report=mech["power_sizing"],
            pcb_wiring=mech["pcb_wiring"],
            obstacle_training=sim.get("obstacle_training"),
        )
    else:
        mp = sim.get("mass_properties", {})
        dt = sim.get("drivetrain", {})
        power_sizing = compute_power_and_battery_sizing(
            design=design,
            total_mass_kg=float(mp.get("total_mass_kg", 1.2)),
            wheel_diameter_m=float(dt.get("wheel_diameter_m", 0.07)),
            wheel_count=int(dt.get("wheel_count", 2)),
            track_width_m=float(dt.get("track_width_m", 0.22)),
            cruise_speed_m_s=float(dt.get("cruise_speed_m_s", 0.45)),
            obstacle_training=sim.get("obstacle_training"),
            target_runtime_hours=3.0,
        )
        pcb_wiring = generate_pcb_and_wiring_architecture(design=design, power_report=power_sizing)
        pkg = generate_deployable_firmware_package(
            design=design,
            power_report=power_sizing,
            pcb_wiring=pcb_wiring,
            obstacle_training=sim.get("obstacle_training"),
        )
    return json.dumps(pkg, indent=2)



def generate_openscad(design: dict) -> str:
    rows = ["// ForgeBot conceptual robot model. Estimated from one image; verify before manufacture.", "$fn = 48;", ""]
    by_id = {item["id"]: item for item in design["components"]}
    for item in design["components"]:
        px, py, pz = map(float, item["position_m"])
        safe_label = " ".join(f"{item['name']}: {item['role']}".split())[:180]
        rows.append(f"// {safe_label}")
        for visual in _component_visuals(item, _leg_parent_position(by_id, item)):
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


def _component_interfaces(component: dict, projected_holes_mm: list[dict] | None = None) -> list[dict]:
    """Describe inferred datum/face interfaces, COTS footprint matches, and projected mating holes."""
    x, y, z = component["dimensions_m"]
    cots = match_cots_footprint(component)
    origin_datum: dict = {"name": "origin", "type": "datum", "origin_m": [0, 0, 0], "normal": [0, 0, 1], "status": "inferred"}
    if cots:
        origin_datum["cots_standard"] = {
            "cots_id": cots["cots_id"],
            "name": cots["name"],
            "fastener": cots["fastener"],
            "datasheet_dimensions_mm": cots["datasheet_dimensions_mm"],
        }
    interfaces = [origin_datum]
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
            up = [0, 0, 1] if abs(normal[2]) < 0.9 else [0, 1, 0]
            right = [
                up[1] * normal[2] - up[2] * normal[1],
                up[2] * normal[0] - up[0] * normal[2],
                up[0] * normal[1] - up[1] * normal[0],
            ]
            is_z_face = name in ("face_z_positive", "face_z_negative")
            if is_z_face and projected_holes_mm:
                fastener = projected_holes_mm[0].get("fastener", "M3")
                dia_mm = projected_holes_mm[0].get("diameter_mm", 3.4)
                xs = [h["x_mm"] for h in projected_holes_mm]
                ys = [h["y_mm"] for h in projected_holes_mm]
                pitch_x_mm = (max(xs) - min(xs)) if len(xs) >= 2 else 0.0
                pitch_y_mm = (max(ys) - min(ys)) if len(ys) >= 2 else 0.0
                pattern = {
                    "status": "modeled_cut",
                    "fastener": fastener,
                    "layout": "cots_and_mating_projected" if any(h.get("source", "").startswith(("cots:", "mate:")) for h in projected_holes_mm) else "four_corner",
                    "hole_diameter_m": round(dia_mm / 1000.0, 5),
                    "pitch_x_m": round(pitch_x_mm / 1000.0, 5),
                    "pitch_y_m": round(pitch_y_mm / 1000.0, 5),
                    "hole_centers_local_m": [
                        [round(h["x_mm"] / 1000.0, 5), round(h["y_mm"] / 1000.0, 5)] for h in projected_holes_mm
                    ],
                    "mating_sources": sorted({h.get("source", "self") for h in projected_holes_mm}),
                }
            else:
                pattern_calc = calculate_four_hole_pattern(width * 1000.0, height * 1000.0, fastener="M3")
                if pattern_calc["status"] == "ready":
                    pattern = {
                        "status": "modeled_cut",
                        "fastener": "M3",
                        "layout": "four_corner",
                        "hole_diameter_m": pattern_calc["hole_diameter_mm"] / 1000.0,
                        "pitch_x_m": pattern_calc["pitch_x_mm"] / 1000.0,
                        "pitch_y_m": pattern_calc["pitch_y_mm"] / 1000.0,
                        "counterbore_dia_m": pattern_calc["counterbore_dia_mm"] / 1000.0,
                        "counterbore_depth_m": pattern_calc["counterbore_depth_mm"] / 1000.0,
                        "hole_centers_local_m": [
                            [h["x_mm"] / 1000.0, h["y_mm"] / 1000.0] for h in pattern_calc["holes"]
                        ],
                    }
                else:
                    pattern = {"status": "not_proposed", "reason": pattern_calc.get("reason", "Face is too small for the default four-hole concept pattern.")}
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
                "axis": axis, "diameter_m": radius * 2, "bore_diameter_m": 0.0054, "status": "inferred_from_wheel_geometry",
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
    if not MIN_COMPONENTS <= len(components) <= MAX_COMPONENTS:
        findings.append({"severity": "blocker", "code": "component_count", "message": f"Assembly must contain between {MIN_COMPONENTS} and {MAX_COMPONENTS} components."})
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
            p_dims = _assembly_bounds_dimensions(parent)
            c_dims = _assembly_bounds_dimensions(part)
            axis_gaps = [abs(delta[i]) - (p_dims[i] + c_dims[i]) / 2.0 for i in range(3)]
            if max(axis_gaps) > 0.0:
                sep = [(1.0 if delta[i] >= 0 else -1.0) * max(0.0, axis_gaps[i]) for i in range(3)]
                gap = math.sqrt(sum(v * v for v in sep))
                direction = _unit_vector(sep)
            else:
                primary_axis = max(range(3), key=lambda axis: axis_gaps[axis])
                gap = axis_gaps[primary_axis]
                direction = [0.0, 0.0, 0.0]
                direction[primary_axis] = 1.0 if delta[primary_axis] >= 0 else -1.0
            parent_radius = _support_distance(parent, direction)
            child_radius = _support_distance(part, direction)
            parent_frame = [direction[i] * parent_radius for i in range(3)]
            child_frame = [-direction[i] * child_radius for i in range(3)]
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


def _generate_freecad_macro(design: dict, manifest: dict, validation: dict, projected_holes: dict[str, list[dict]] | None = None) -> str:
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
    dims = [float(value) * 1000.0 for value in visual["dimensions_m"]]
    rpy = [math.degrees(float(value)) for value in visual.get("rpy", [0, 0, 0])]
    local = visual.get("origin_m", [0, 0, 0])
    local_origin = App.Vector(float(local[0]) * 1000.0, float(local[1]) * 1000.0, float(local[2]) * 1000.0)
    comp_origin = App.Vector(float(component_position[0]) * 1000.0, float(component_position[1]) * 1000.0, float(component_position[2]) * 1000.0)
    zero = App.Vector(0, 0, 0)

    # Fast direct OpenCASCADE solid construction for secondary detail visuals (feathers, talons, bolts, PCB chips)
    if visual_index > 0 or visual.get("is_detail"):
        if visual["geometry"] == "box":
            width, depth, height = dims
            raw_shape = Part.makeBox(width, depth, height, App.Vector(-width / 2.0, -depth / 2.0, -height / 2.0))
        elif visual["geometry"] == "cylinder":
            radius = min(dims[0], dims[1]) / 2.0
            height = dims[2]
            raw_shape = Part.makeCylinder(radius, height, App.Vector(0, 0, -height / 2.0), App.Vector(0, 0, 1))
            bore_r = float(visual.get("bore_m", 0.0)) * 1000.0 / 2.0
            if 0.4 <= bore_r <= radius * 0.82:
                raw_shape = raw_shape.cut(Part.makeCylinder(bore_r, height + 0.2, App.Vector(0, 0, -height / 2.0 - 0.1), App.Vector(0, 0, 1)))
        elif visual["geometry"] == "sphere":
            raw_shape = Part.makeSphere(min(dims) / 2.0)
        else:
            raise ValueError("Unsupported primitive: " + str(visual["geometry"]))
        local_shape = raw_shape.copy()
        for axis, angle in ((App.Vector(1, 0, 0), rpy[0]), (App.Vector(0, 1, 0), rpy[1]), (App.Vector(0, 0, 1), rpy[2])):
            if abs(angle) > 1e-9:
                local_shape.rotate(zero, axis, angle)
        local_shape.translate(local_origin)
        shape = local_shape.copy()
        shape.translate(comp_origin)
        feat = doc.addObject("Part::Feature", "Detail_" + safe_name(component_id) + "_" + str(visual_index + 1))
        feat.Shape = shape
        return feat, feat, shape, local_shape

    # Primary component solid (visual_index == 0): real dimension-constrained Sketch + Pad inside PartDesign::Body
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
        if not visual.get("is_detail"):
            holes_mm = visual.get("holes_mm")
            if holes_mm:
                for h in holes_mm:
                    hr = float(h.get("diameter_mm", 3.4)) / 2.0
                    hx_pt = float(h["x_mm"])
                    hy_pt = float(h["y_mm"])
                    if abs(hx_pt) <= (width / 2.0 - hr * 1.3) and abs(hy_pt) <= (depth / 2.0 - hr * 1.3):
                        sketch.addGeometry(Part.Circle(App.Vector(hx_pt, hy_pt, 0), App.Vector(0, 0, 1), hr), False)
            elif width >= 20.0 and depth >= 20.0:
                hole_r = 1.7
                mx, my = width * 0.15, depth * 0.15
                hx, hy = (width - 2 * mx) / 2.0, (depth - 2 * my) / 2.0
                for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                    sketch.addGeometry(Part.Circle(App.Vector(sx * hx, sy * hy, 0), App.Vector(0, 0, 1), hole_r), False)
        sketch.Placement = App.Placement(App.Vector(0, 0, -height / 2), App.Rotation())
        pad_length = height
    elif visual["geometry"] == "cylinder":
        radius = min(dims[0], dims[1]) / 2
        sketch.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), radius), False)
        sketch.addConstraint([Sketcher.Constraint("DistanceX", 0, 3, 0.0), Sketcher.Constraint("DistanceY", 0, 3, 0.0), Sketcher.Constraint("Radius", 0, radius)])
        bore_r = float(visual.get("bore_m", 0.0)) * 1000.0 / 2.0
        if 0.4 <= bore_r <= radius * 0.82:
            sketch.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), bore_r), False)
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
    rotation = App.Rotation(App.Vector(1, 0, 0), rpy[0]).multiply(App.Rotation(App.Vector(0, 1, 0), rpy[1])).multiply(App.Rotation(App.Vector(0, 0, 1), rpy[2]))
    offset = App.Vector(*[float(local[index]) * 1000.0 + float(component_position[index]) * 1000.0 for index in range(3)])
    body.Placement = App.Placement(offset, rotation)
    doc.recompute()
    shape = body.Shape.copy()
    local_shape = pad.Shape.copy()
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
        world_component_shapes[component_id] = world_shapes[0]
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
            "color": component.get("color", "#4c9b8c"),
            "role": component.get("role", ""),
            "functional_category": component.get("functional_category", ""),
            "visuals": PAYLOAD["visuals"][component_id],
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

    # Build 3D Routed Electrical Wiring Harness solids in FreeCAD (.FCStd / .STEP / .STL)
    harness_cables = PAYLOAD.get("wiring_harness_3d", [])
    if harness_cables:
        harness_group = doc.addObject("App::DocumentObjectGroup", "Component_wiring_harness")
        harness_group.Label = "3D Electrical Wiring Harness"
        harness_shapes = []
        for cable in harness_cables:
            pts = cable.get("points_m", [])
            rad_mm = max(1.2, float(cable.get("radius_m", 0.0018)) * 1000.0)
            rgb = cable.get("color", "#ef4444")
            color_tuple = tuple(int(rgb[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
            cable_seg_shapes = []
            for seg_idx in range(len(pts) - 1):
                p1 = App.Vector(*[float(v) * 1000.0 for v in pts[seg_idx]])
                p2 = App.Vector(*[float(v) * 1000.0 for v in pts[seg_idx + 1]])
                delta = p2.sub(p1)
                seg_len = delta.Length
                if seg_len > 0.5:
                    cyl = Part.makeCylinder(rad_mm, seg_len, p1, delta)
                    cable_seg_shapes.append(cyl)
                    cable_seg_shapes.append(Part.makeSphere(rad_mm * 1.05, p1))
            if pts:
                p_end = App.Vector(*[float(v) * 1000.0 for v in pts[-1]])
                cable_seg_shapes.append(Part.makeSphere(rad_mm * 1.15, p_end))
            if cable_seg_shapes:
                cable_comp = Part.makeCompound(cable_seg_shapes)
                harness_shapes.append(cable_comp)
                cable_obj = doc.addObject("Part::Feature", "Wire_" + safe_name(cable["id"]))
                cable_obj.Label = cable.get("name", cable["id"])
                cable_obj.Shape = cable_comp
                cable_obj.ViewObject.ShapeColor = color_tuple
                cable_obj.ViewObject.LineColor = (0.10, 0.14, 0.18)
                harness_group.addObject(cable_obj)
                step_features.append(cable_obj)
        if harness_shapes:
            harness_compound = Part.makeCompound(harness_shapes)
            harness_mesh_name = "wiring_harness.stl"
            harness_mesh = Mesh.Mesh(harness_compound.tessellate(0.15))
            harness_mesh.write(os.path.join(parts_dir, harness_mesh_name))
            solids_report.append({"component_id": "wiring_harness", "solid_count": len(harness_shapes), "valid": bool(harness_compound.isValid()), "volume_mm3": float(harness_compound.Volume)})
            preview_manifest["components"].append({
                "id": "wiring_harness",
                "name": "3D Electrical Wiring Harness",
                "color": "#ef4444",
                "role": "16-26 AWG routed power & sensor harness",
                "functional_category": "electronics_harness",
                "visuals": [],
                "cables": harness_cables,
                "source_file": "parts/" + harness_mesh_name,
                "visual_format": "stl",
                "units": "mm",
                "parent_component": None,
                "parent_mount": "origin",
                "child_mount": "origin",
                "relative_position_mm": [0.0, 0.0, 0.0],
                "relative_rpy": [0, 0, 0],
                "mount_interfaces": [],
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
                if not first_shape.BoundBox.intersect(second_shape.BoundBox):
                    continue
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
    from .mechatronics import generate_3d_wiring_harness_segments

    by_id = {part["id"]: part for part in design["components"]}
    holes_map = projected_holes if projected_holes is not None else project_assembly_mating_holes(design["components"])
    visuals = {}
    for part in design["components"]:
        pid = part["id"]
        v_list = _component_visuals(part, _leg_parent_position(by_id, part))
        part_holes = holes_map.get(pid, [])
        if part_holes:
            for v in v_list:
                if v["geometry"] == "box" and not v.get("is_detail"):
                    v["holes_mm"] = part_holes
        visuals[pid] = v_list
    payload = {
        "design": design,
        "manifest": manifest,
        "validation": validation,
        "visuals": visuals,
        "wiring_harness_3d": generate_3d_wiring_harness_segments(design),
    }
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
    projected_holes = project_assembly_mating_holes(components)
    inferred_interfaces = {part["id"]: _component_interfaces(part, projected_holes.get(part["id"])) for part in components}
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
        parent_position = _leg_parent_position(by_id, component)
        part_holes = projected_holes.get(part_id, [])
        for visual in _component_visuals(component, parent_position):
            x, y, z = map(_fmt, visual["dimensions_m"])
            ox, oy, oz = map(_fmt, visual["origin_m"])
            rx, ry, rz = [_fmt(math.degrees(value)) for value in visual["rpy"]]
            rgb = [int(visual["color"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
            rgba = ", ".join([*(f"{channel:.3f}" for channel in rgb), _fmt(visual["alpha"])])
            vx, vy, vz = visual["dimensions_m"]
            bore_m = float(visual.get("bore_m", 0.0))
            if visual["geometry"] == "box" and vx >= 0.02 and vy >= 0.02 and not visual.get("is_detail"):
                rows.append(f"    color([{rgba}]) translate([{ox}, {oy}, {oz}]) rotate([{rx}, {ry}, {rz}]) difference() {{")
                rows.append(f"      cube([{x}, {y}, {z}], center=true);")
                if part_holes:
                    sources = ", ".join(sorted({h.get("source", "M3") for h in part_holes}))
                    rows.append(f"      // {len(part_holes)}x M3 fastener clearance holes · cross-part mating ({sources})")
                    for h in part_holes:
                        hx_m = float(h["x_mm"]) / 1000.0
                        hy_m = float(h["y_mm"]) / 1000.0
                        hd_m = float(h.get("diameter_mm", 3.4)) / 1000.0
                        rows.append(f"      translate([{_fmt(hx_m)}, {_fmt(hy_m)}, 0]) cylinder(h={_fmt(vz * 1.1)}, d={_fmt(hd_m)}, center=true);")
                else:
                    mx, my = vx * 0.15, vy * 0.15
                    hx, hy = (vx - 2 * mx) / 2.0, (vy - 2 * my) / 2.0
                    hd = 0.0034  # M3 clearance diameter in metres
                    rows.append(f"      // 4x M3 fastener clearance holes")
                    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                        rows.append(f"      translate([{_fmt(sx * hx)}, {_fmt(sy * hy)}, 0]) cylinder(h={_fmt(vz * 1.1)}, d={_fmt(hd)}, center=true);")
                rows.append("    }")
            elif visual["geometry"] == "cylinder" and bore_m > 0.0:
                rows.append(f"    color([{rgba}]) translate([{ox}, {oy}, {oz}]) rotate([{rx}, {ry}, {rz}]) difference() {{")
                rows.append(f"      cylinder(h={z}, d={x}, center=true);")
                rows.append(f"      cylinder(h={_fmt(vz * 1.1)}, d={_fmt(bore_m)}, center=true);")
                rows.append("    }")
            else:
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
    files["freecad/build_step.FCMacro"] = _generate_freecad_macro(design, manifest, assembly_validation, projected_holes)
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
