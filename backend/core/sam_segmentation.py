"""SAM 3.1 pixel masks for the assemblies identified by Muse Spark."""

from __future__ import annotations

import base64
import io

from meta_sam_parser import (
    CompletedOutcome,
    FramePrompt,
    PromptBox,
    PromptObject,
    SegmentationMaskRecord,
    build_object_prompt,
    decode_mask_to_raster,
    image_segmentation_format,
)
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .meta_model import MetaModelError, segment_image_with_sam


def segment_observed_assemblies(image: bytes, content_type: str, observation: dict) -> list[dict]:
    """Use Muse's regions as prompts so SAM masks match the reviewed component rows."""
    with Image.open(io.BytesIO(image)) as opened:
        source = ImageOps.exif_transpose(opened)
        width, height = source.size

    assemblies = observation.get("observed_assemblies", [])
    prompt_objects = []
    for index, assembly in enumerate(assemblies):
        region = assembly["image_region"]
        left = max(0, min(width - 1, int(region["x_min"] * width)))
        top = max(0, min(height - 1, int(region["y_min"] * height)))
        right = max(left + 1, min(width, round(region["x_max"] * width)))
        bottom = max(top + 1, min(height, round(region["y_max"] * height)))
        prompt_objects.append(PromptObject(
            id=index + 1,
            prompts=[FramePrompt(frame=0, box=PromptBox(left, top, right, bottom))],
        ))

    prompt = build_object_prompt(size=(width, height), objects=prompt_objects)
    image_data_url = f"data:{content_type};base64,{base64.b64encode(image).decode('ascii')}"
    output_text = segment_image_with_sam(image_data_url, prompt)
    parser = image_segmentation_format().create_parser()
    parser.push(output_text)
    result = parser.finish(CompletedOutcome()).result

    rows = {}
    palette = [(10, 145, 124), (219, 126, 47), (69, 122, 196), (167, 97, 174), (199, 82, 84), (78, 151, 99), (202, 104, 151), (107, 115, 190)]
    for record in result.records:
        if not isinstance(record, SegmentationMaskRecord):
            continue
        mask = record.mask
        raster = decode_mask_to_raster(mask)
        color = palette[(int(record.object_id) - 1) % len(palette)]
        rgba = Image.new("RGBA", (mask.width, mask.height), (*color, 0))
        alpha = Image.frombytes("L", (mask.width, mask.height), bytes(110 if bit else 0 for bit in raster))
        rgba.putalpha(alpha)
        buffer = io.BytesIO()
        rgba.save(buffer, format="PNG", optimize=True)
        rows[int(record.object_id) - 1] = {
            "id": int(record.object_id),
            "color": "#%02x%02x%02x" % color,
            "mask_png": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
            "image_region": {
                "x_min": record.bounds.left / width,
                "y_min": record.bounds.top / height,
                "x_max": record.bounds.right / width,
                "y_max": record.bounds.bottom / height,
            },
            "confidence": record.confidence,
        }
    return [rows.get(index) for index in range(len(assemblies))]


def build_numbered_overlay_data_url(image: bytes, content_type: str, segmentation: list[dict | None]) -> str:
    """Create a full-frame image with SAM masks tinted and numbered for visual review."""
    with Image.open(io.BytesIO(image)) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGBA")
    overlay = Image.new("RGBA", source.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = source.size
    for row in segmentation:
        if not row or not row.get("mask_png"):
            continue
        region = row["image_region"]
        left, top = round(region["x_min"] * width), round(region["y_min"] * height)
        right, bottom = round(region["x_max"] * width), round(region["y_max"] * height)
        encoded = row["mask_png"].split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as mask_image:
            mask = mask_image.convert("RGBA").resize((max(1, right - left), max(1, bottom - top)))
            overlay.alpha_composite(mask, (left, top))
        color = row.get("color", "#0a917c")
        label = str(row.get("id", "?"))
        x, y = max(0, left + 3), max(0, top + 3)
        try:
            font = ImageFont.truetype("arial.ttf", max(14, min(width, height) // 36))
        except OSError:
            font = ImageFont.load_default()
        bounds = draw.textbbox((x, y), label, font=font, stroke_width=0)
        draw.rounded_rectangle((bounds[0] - 4, bounds[1] - 2, bounds[2] + 4, bounds[3] + 2), radius=4, fill=color)
        draw.text((x, y), label, font=font, fill="white")
    combined = Image.alpha_composite(source, overlay).convert("RGB")
    buffer = io.BytesIO()
    combined.save(buffer, format="JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def review_sam_masks_with_muse(original_data_url: str, numbered_overlay_data_url: str, observation: dict, segmentation: list[dict | None]) -> dict:
    """Ask Muse to verify numbered SAM masks against their proposed assembly labels."""
    from .meta_model import generate_multi_image_structured_json

    assemblies = observation.get("observed_assemblies", [])
    expected = [
        {"id": row.get("id", index + 1), "label": part["label"], "appearance": part["appearance"]}
        for index, (part, row) in enumerate(zip(assemblies, segmentation)) if row and row.get("mask_png")
    ]
    if not expected:
        return {"summary": "No SAM masks were available to verify.", "components": []}
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "components": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "id": {"type": "integer", "enum": [item["id"] for item in expected]},
                    "status": {"type": "string", "enum": ["matches", "uncertain", "mismatch"]},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                }, "required": ["id", "status", "reason", "confidence"],
            }},
        }, "required": ["summary", "components"],
    }
    result = generate_multi_image_structured_json(
        system_prompt="You are checking image segmentation labels. Be conservative: only call a mask a match if its colored pixels visibly cover the named assembly in the original image. A numbered overlay's number corresponds to the part with that same number. A 2D mask cannot establish hidden geometry, dimensions, or function.",
        user_prompt="Compare the original robot reference and the numbered SAM overlay. Check each listed mask id against its expected component. Report matches, uncertainty (including overlap/partial visibility), or a mismatch. Explain briefly using visible evidence. Return every listed ID exactly once. Expected mappings: " + str(expected),
        images=[("original robot reference", original_data_url), ("numbered SAM 3.1 masks", numbered_overlay_data_url)],
        schema_name="sam_mask_review", schema=schema,
    )
    expected_ids = {item["id"] for item in expected}
    components = result.get("components")
    if not isinstance(components, list):
        raise MetaModelError("Meta returned an invalid SAM mask review.")
    cleaned = {}
    for item in components:
        if not isinstance(item, dict) or item.get("id") not in expected_ids or item["id"] in cleaned:
            continue
        if item.get("status") not in {"matches", "uncertain", "mismatch"}:
            continue
        cleaned[item["id"]] = {
            "id": item["id"], "status": item["status"],
            "reason": str(item.get("reason", ""))[:400],
            "confidence": max(0, min(1, float(item.get("confidence", 0)))),
        }
    if set(cleaned) != expected_ids:
        raise MetaModelError("Meta did not verify every SAM mask.")
    return {"summary": str(result.get("summary", ""))[:600], "components": [cleaned[item["id"]] for item in expected]}
