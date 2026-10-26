"""Model-backed scene analysis for user-captured or uploaded workcell images."""

from __future__ import annotations

import base64

from .meta_model import generate_image_structured_json, MetaModelError

VISION_SCHEMA_VERSION = "scene-observation/v1"

SCENE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "scene_summary", "work_surface", "objects", "relationships", "suggested_task", "safety_notes", "limitations"],
    "properties": {
        "schema_version": {"type": "string", "enum": [VISION_SCHEMA_VERSION]},
        "scene_summary": {"type": "string"},
        "work_surface": {"type": ["object", "null"], "additionalProperties": False, "required": ["description", "visible_area"], "properties": {"description": {"type": "string"}, "visible_area": {"type": "string"}}},
        "objects": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["label", "category", "appearance", "state", "image_region", "confidence", "graspability", "uncertainty"], "properties": {"label": {"type": "string"}, "category": {"type": "string"}, "appearance": {"type": "string"}, "state": {"type": "string"}, "image_region": {"type": "object", "additionalProperties": False, "required": ["x_min", "y_min", "x_max", "y_max"], "properties": {"x_min": {"type": "number", "minimum": 0, "maximum": 1}, "y_min": {"type": "number", "minimum": 0, "maximum": 1}, "x_max": {"type": "number", "minimum": 0, "maximum": 1}, "y_max": {"type": "number", "minimum": 0, "maximum": 1}}}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "graspability": {"type": "string", "enum": ["appears_graspable", "appears_not_graspable", "uncertain"]}, "uncertainty": {"type": "string"}}}},
        "relationships": {"type": "array", "items": {"type": "string"}},
        "suggested_task": {"type": "string"},
        "safety_notes": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
}


def _image_format_allowed(content_type: str, image: bytes) -> bool:
    signatures = {
        "image/jpeg": image.startswith(b"\xff\xd8\xff"),
        "image/png": image.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": len(image) >= 12 and image[:4] == b"RIFF" and image[8:12] == b"WEBP",
    }
    return signatures.get(content_type, False)


def analyze_scene_image(image: bytes, content_type: str) -> dict:
    """Send one still image to Meta and return grounded structured observations."""
    if not _image_format_allowed(content_type, image):
        raise ValueError("Upload a valid JPEG, PNG, or WebP image.")
    encoded = base64.b64encode(image).decode("ascii")
    data_url = f"data:{content_type};base64,{encoded}"
    result = generate_image_structured_json(
        system_prompt=(
            "You are the vision observation stage of a tabletop robot workcell. "
            "Describe only visible evidence. Identify distinct objects and their visual appearance, "
            "condition, occlusion, and spatial relationships. Estimate image-region boxes as normalized "
            "0..1 fractions with origin at the top-left. Do not invent physical dimensions, metric/world "
            "coordinates, depth, object mass, or confirmed grasp safety from a monocular image. "
            "Mark uncertainty explicitly. Graspability is only a visual hypothesis. Do not command a robot. "
            "Return the requested schema and include limitations and any visible hazards."
        ),
        user_prompt=(
            "Analyze this workcell image for a human-reviewed tabletop pick-and-place task. Describe the "
            "scene naturally and identify objects, appearance, condition, relationships, and whether each "
            "only appears visually graspable."
        ),
        image_data_url=data_url,
        schema_name="forgebot_scene_observation",
        schema=SCENE_SCHEMA,
    )
    if not isinstance(result, dict) or result.get("schema_version") != VISION_SCHEMA_VERSION:
        raise MetaModelError("Meta returned a scene analysis with an unexpected structure.")
    for index, item in enumerate(result.get("objects", [])):
        region = item.get("image_region", {})
        if (
            not isinstance(region, dict)
            or any(region.get(key) is None for key in ("x_min", "y_min", "x_max", "y_max"))
            or region["x_min"] >= region["x_max"]
            or region["y_min"] >= region["y_max"]
        ):
            raise MetaModelError(f"Meta returned an invalid image region for object {index + 1}.")
    return result
