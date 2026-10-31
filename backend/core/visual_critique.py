"""Closed-loop vision-in-the-loop verification, multi-view 3D CAD rendering, and critique."""

from __future__ import annotations

import base64
import io
import json
import math
import re
from typing import Any

from PIL import Image, ImageDraw

from .meta_model import MetaModelError, generate_multi_image_structured_json

CRITIQUE_SCHEMA_VERSION = "image-robot-visual-critique/v1"

CRITIQUE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "visual_match_score",
        "passed_verification",
        "critique_summary",
        "symmetry_assessment",
        "stance_and_mounting_assessment",
        "component_adjustments",
        "missing_parts_identified",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": [CRITIQUE_SCHEMA_VERSION]},
        "visual_match_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "passed_verification": {"type": "boolean"},
        "critique_summary": {"type": "string"},
        "symmetry_assessment": {"type": "string"},
        "stance_and_mounting_assessment": {"type": "string"},
        "component_adjustments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["component_id", "issue", "suggested_action"],
                "properties": {
                    "component_id": {"type": "string"},
                    "issue": {"type": "string"},
                    "suggested_action": {"type": "string"},
                    "delta_position_m": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "number"},
                    },
                    "delta_dimensions_m": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "number"},
                    },
                },
            },
        },
        "missing_parts_identified": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "role",
                    "functional_category",
                    "likely_position_m",
                    "likely_dimensions_m",
                    "parent_id",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "functional_category": {"type": "string"},
                    "likely_position_m": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "number"},
                    },
                    "likely_dimensions_m": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "number"},
                    },
                    "parent_id": {"type": "string"},
                    "geometry": {"type": "string", "enum": ["box", "cylinder", "sphere"]},
                    "color": {"type": "string"},
                },
            },
        },
    },
}


def _hex_to_rgb(hex_str: str | None) -> tuple[int, int, int]:
    val = (hex_str or "#4a5568").lstrip("#")
    if len(val) == 3:
        val = "".join(c * 2 for c in val)
    try:
        return (int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16))
    except Exception:
        return (80, 90, 100)


def _rotate_point(p: list[float], rpy: list[float]) -> list[float]:
    cr, sr = math.cos(rpy[0]), math.sin(rpy[0])
    cp, sp = math.cos(rpy[1]), math.sin(rpy[1])
    cy, sy = math.cos(rpy[2]), math.sin(rpy[2])
    x, y, z = p
    x1 = x
    y1 = y * cr - z * sr
    z1 = y * sr + z * cr
    x2 = x1 * cp + z1 * sp
    y2 = y1
    z2 = -x1 * sp + z1 * cp
    x3 = x2 * cy - y2 * sy
    y3 = x2 * sy + y2 * cy
    z3 = z2
    return [x3, y3, z3]


def _get_box_faces(pos: list[float], dims: list[float], rpy: list[float], color: str) -> list[dict]:
    dx, dy, dz = [d / 2.0 for d in dims]
    base_verts = [
        [-dx, -dy, -dz], [dx, -dy, -dz], [dx, dy, -dz], [-dx, dy, -dz],
        [-dx, -dy, dz], [dx, -dy, dz], [dx, dy, dz], [-dx, dy, dz],
    ]
    world_verts = []
    for bv in base_verts:
        rv = _rotate_point(bv, rpy)
        world_verts.append([rv[0] + pos[0], rv[1] + pos[1], rv[2] + pos[2]])

    quads = [
        ([0, 1, 2, 3], [0, 0, -1]),
        ([4, 7, 6, 5], [0, 0, 1]),
        ([0, 4, 5, 1], [0, -1, 0]),
        ([2, 6, 7, 3], [0, 1, 0]),
        ([0, 3, 7, 4], [-1, 0, 0]),
        ([1, 5, 6, 2], [1, 0, 0]),
    ]
    faces = []
    for indices, n in quads:
        rot_n = _rotate_point(n, rpy)
        pts = [world_verts[i] for i in indices]
        faces.append({"pts": pts, "normal": rot_n, "color": color})
    return faces


def _get_cylinder_faces(pos: list[float], dims: list[float], rpy: list[float], color: str, segments: int = 16) -> list[dict]:
    r = min(dims[0], dims[1]) / 2.0
    half_h = dims[2] / 2.0
    top_ring = []
    bot_ring = []
    for i in range(segments):
        th = 2.0 * math.pi * i / segments
        top_ring.append([r * math.cos(th), r * math.sin(th), half_h])
        bot_ring.append([r * math.cos(th), r * math.sin(th), -half_h])

    w_top = [_rotate_point(p, rpy) for p in top_ring]
    w_top = [[p[0] + pos[0], p[1] + pos[1], p[2] + pos[2]] for p in w_top]
    w_bot = [_rotate_point(p, rpy) for p in bot_ring]
    w_bot = [[p[0] + pos[0], p[1] + pos[1], p[2] + pos[2]] for p in w_bot]

    faces = []
    faces.append({"pts": w_top, "normal": _rotate_point([0, 0, 1], rpy), "color": color})
    faces.append({"pts": list(reversed(w_bot)), "normal": _rotate_point([0, 0, -1], rpy), "color": color})
    for i in range(segments):
        nxt = (i + 1) % segments
        pts = [w_bot[i], w_bot[nxt], w_top[nxt], w_top[i]]
        th_mid = 2.0 * math.pi * (i + 0.5) / segments
        n = _rotate_point([math.cos(th_mid), math.sin(th_mid), 0], rpy)
        faces.append({"pts": pts, "normal": n, "color": color})
    return faces


def _render_viewport(faces: list[dict], width: int, height: int, view: str, center: list[float], zoom_scale: float) -> Image.Image:
    # High-contrast studio CAD light theme: ensures dark gray robot armor & black wheels pop with 100% clarity
    bg_col = (248, 250, 252, 255)  # slate-50
    grid_col = (226, 232, 240, 255)  # slate-200

    img = Image.new("RGBA", (width, height), bg_col)
    draw = ImageDraw.Draw(img)

    if view == "isometric":
        elev = math.radians(26)
        azim = math.radians(-50)
        badge_col = (2, 132, 199, 255)  # sky-600
        badge_text = "ISOMETRIC 3D"
    elif view == "front":
        elev = math.radians(4)
        azim = math.radians(0)
        badge_col = (16, 185, 129, 255)  # emerald-500
        badge_text = "FRONT VIEW [+X]"
    elif view == "side":
        elev = math.radians(4)
        azim = math.radians(90)
        badge_col = (245, 158, 11, 255)  # amber-500
        badge_text = "SIDE PROFILE [+Y]"
    elif view == "top":
        elev = math.radians(88)
        azim = math.radians(0)
        badge_col = (139, 92, 246, 255)  # purple-500
        badge_text = "TOP PLAN [+Z]"
    else:
        elev, azim = math.radians(25), math.radians(-45)
        badge_col = (100, 116, 139, 255)
        badge_text = view.upper()

    ce, se = math.cos(elev), math.sin(elev)
    ca, sa = math.cos(azim), math.sin(azim)

    light_dir = [0.35, 0.55, 0.75]
    l_mag = math.sqrt(sum(v * v for v in light_dir))
    light_dir = [v / l_mag for v in light_dir]

    def world_to_camera(pt: list[float]) -> list[float]:
        x = pt[0] - center[0]
        y = pt[1] - center[1]
        z = pt[2] - center[2]
        x1 = x * ca - y * sa
        y1 = x * sa + y * ca
        z1 = z
        xc = y1
        yc = z1 * ce - x1 * se
        zc = x1 * ce + z1 * se
        return [xc, yc, zc]

    transformed = []
    for f in faces:
        cam_pts = [world_to_camera(p) for p in f["pts"]]
        if len(cam_pts) >= 3:
            v0, v1, v2 = cam_pts[0], cam_pts[1], cam_pts[2]
            d1 = [v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]]
            d2 = [v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]]
            nx = d1[1] * d2[2] - d1[2] * d2[1]
            ny = d1[2] * d2[0] - d1[0] * d2[2]
            nz = d1[0] * d2[1] - d1[1] * d2[0]
            mag = math.sqrt(nx * nx + ny * ny + nz * nz)
            cam_norm = [nx / mag, ny / mag, nz / mag] if mag > 1e-6 else [0, 0, 1]
        else:
            cam_norm = [0, 0, 1]

        if cam_norm[2] <= 0.04 and view != "top":
            continue

        avg_z = sum(p[2] for p in cam_pts) / len(cam_pts)
        dot = max(0.0, cam_norm[0] * light_dir[0] + cam_norm[1] * light_dir[1] + cam_norm[2] * light_dir[2])
        base_rgb = _hex_to_rgb(f["color"])
        intensity = 0.45 + 0.55 * dot
        shaded_rgb = tuple(min(255, max(0, int(c * intensity))) for c in base_rgb)
        outline_rgb = (15, 23, 42, 255)  # Crisp dark outline for sharp CAD drafting silhouette

        transformed.append({
            "cam_pts": cam_pts,
            "avg_z": avg_z,
            "fill": shaded_rgb,
            "outline": outline_rgb,
        })

    transformed.sort(key=lambda item: item["avg_z"])
    cx = width / 2.0
    cy = height / 2.0

    # Grid floor
    for g in range(-6, 7):
        pos_val = g * 0.08
        p1 = world_to_camera([pos_val, -0.48, 0.0])
        p2 = world_to_camera([pos_val, 0.48, 0.0])
        draw.line([(cx + p1[0] * zoom_scale, cy - p1[1] * zoom_scale), (cx + p2[0] * zoom_scale, cy - p2[1] * zoom_scale)], fill=grid_col, width=1)
        p3 = world_to_camera([-0.48, pos_val, 0.0])
        p4 = world_to_camera([0.48, pos_val, 0.0])
        draw.line([(cx + p3[0] * zoom_scale, cy - p3[1] * zoom_scale), (cx + p4[0] * zoom_scale, cy - p4[1] * zoom_scale)], fill=grid_col, width=1)

    for item in transformed:
        poly_pts = [(cx + p[0] * zoom_scale, cy - p[1] * zoom_scale) for p in item["cam_pts"]]
        draw.polygon(poly_pts, fill=item["fill"], outline=item["outline"])

    draw.rounded_rectangle([(14, 14), (160, 38)], radius=4, fill=badge_col)
    draw.text((22, 18), badge_text, fill=(255, 255, 255, 255))
    return img


def render_cad_multiview(components: list[dict], width: int = 1024, height: int = 1024) -> bytes:
    """Render a 4-view (Isometric, Front, Side, Top) orthographic projection of the CAD assembly to PNG bytes."""
    faces = []
    for p in components:
        geom = p.get("geometry", "box")
        pos = [float(v) for v in p.get("position_m", [0, 0, 0])]
        dims = [float(v) for v in p.get("dimensions_m", [0.1, 0.1, 0.1])]
        rpy = [float(v) for v in p.get("orientation_rpy", [0, 0, 0])]
        col = p.get("color", "#3b82f6")
        if geom in ("cylinder", "wheel"):
            faces.extend(_get_cylinder_faces(pos, dims, rpy, col))
        else:
            faces.extend(_get_box_faces(pos, dims, rpy, col))

    xs = [float(p.get("position_m", [0, 0, 0])[0]) for p in components] or [0.0]
    ys = [float(p.get("position_m", [0, 0, 0])[1]) for p in components] or [0.0]
    zs = [float(p.get("position_m", [0, 0, 0])[2]) for p in components] or [0.0]
    center = [(max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0, (max(zs) + min(zs)) / 2.0]
    span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 0.3)
    zoom = (width / 4.4) / span

    half_w = width // 2
    half_h = height // 2
    v_iso = _render_viewport(faces, half_w, half_h, "isometric", center, zoom)
    v_front = _render_viewport(faces, half_w, half_h, "front", center, zoom)
    v_side = _render_viewport(faces, half_w, half_h, "side", center, zoom)
    v_top = _render_viewport(faces, half_w, half_h, "top", center, zoom)

    combined = Image.new("RGBA", (width, height), (203, 213, 225, 255))
    combined.paste(v_iso, (0, 0))
    combined.paste(v_front, (half_w, 0))
    combined.paste(v_side, (0, half_h))
    combined.paste(v_top, (half_w, half_h))

    draw = ImageDraw.Draw(combined)
    draw.line([(half_w, 0), (half_w, height)], fill=(148, 163, 184, 255), width=2)
    draw.line([(0, half_h), (width, half_h)], fill=(148, 163, 184, 255), width=2)

    buf = io.BytesIO()
    combined.save(buf, format="PNG")
    return buf.getvalue()


def run_vision_in_the_loop_critique(
    reference_views: list[dict],
    design: dict,
    brief: str,
    target_height_m: float,
) -> dict:
    """Execute a closed-loop multi-modal critique pass comparing reference pixels vs 3D CAD render."""
    components = design.get("components", [])
    if not components:
        return {
            "schema_version": CRITIQUE_SCHEMA_VERSION,
            "visual_match_score": 0.0,
            "passed_verification": False,
            "critique_summary": "No components in design.",
            "symmetry_assessment": "N/A",
            "stance_and_mounting_assessment": "N/A",
            "component_adjustments": [],
            "missing_parts_identified": [],
        }

    render_png_bytes = render_cad_multiview(components)
    cad_render_b64 = base64.b64encode(render_png_bytes).decode("ascii")
    cad_data_url = f"data:image/png;base64,{cad_render_b64}"

    multimodal_images: list[tuple[str, str]] = []
    for idx, view_item in enumerate(reference_views):
        if isinstance(view_item, (tuple, list)):
            img_bytes = view_item[0]
            c_type = view_item[1] if len(view_item) > 1 else "image/png"
            v_name = view_item[2] if len(view_item) > 2 else f"reference_view_{idx+1}"
        elif isinstance(view_item, dict):
            img_bytes = view_item.get("image")
            c_type = view_item.get("content_type", "image/png")
            v_name = view_item.get("view", f"reference_view_{idx+1}")
        else:
            continue
        if img_bytes:
            b64 = base64.b64encode(img_bytes).decode("ascii")
            multimodal_images.append((f"user_photo_{v_name}", f"data:{c_type};base64,{b64}"))

    multimodal_images.append(("cad_3d_render_multiview", cad_data_url))

    system_prompt = (
        "You are a Senior Lead Robotics Mechanical & Industrial CAD Reviewer. "
        "You are performing a closed-loop visual-spatial verification comparing the user's reference robot photograph "
        "against the synthesized 3D CAD multi-view render (showing Isometric, Front, Side, and Top orthographic projections). "
        "Carefully compare the 3D CAD render against the original photograph across:\n"
        "1. Visual alignment score (0.0 to 1.0) and whether it passes verification (>= 0.85).\n"
        "2. Mechanical symmetry & completeness: Did the CAD synthesis omit rear suspension coils when front coils exist? "
        "Are mirrored side-pods, tracks, or dual antennas missing?\n"
        "3. Stance & mounting: Is the body properly centered over the wheelbase? Does it sit perched unnaturally, shifted backwards, "
        "or floating in the air?\n"
        "4. Component adjustments: Specify small coordinate or dimension deltas for any parts that are mispositioned relative to the photo.\n"
        "5. Missing parts: If a prominent component visible in the photo was omitted, specify its name, role, category, likely position and parent.\n"
        "Be objective, thorough, and precise."
    )

    user_prompt = (
        f"Design brief: {brief.strip()}\n"
        f"Target height: {target_height_m:.3f} m\n"
        f"Current 3D CAD components: {json.dumps([{k: p.get(k) for k in ('id', 'name', 'role', 'geometry', 'dimensions_m', 'position_m', 'parent_id')} for p in components], ensure_ascii=False)}\n"
        "Compare the user photograph against the 4-view CAD render (Isometric, Front, Side, Top). Output a structured visual-spatial critique."
    )

    critique = generate_multi_image_structured_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        images=multimodal_images,
        schema_name="forgebot_image_robot_visual_critique",
        schema=CRITIQUE_SCHEMA,
    )
    critique["multiview_render_base64"] = cad_render_b64
    return critique


def apply_visual_critique_refinements(
    design: dict,
    critique: dict,
    target_height_m: float,
) -> tuple[dict, list[str]]:
    """Apply visual critique adjustments and missing components, then re-ground."""
    from .image_design import ground_image_design

    components = design.get("components", [])
    by_id = {p["id"]: p for p in components}
    notes: list[str] = []

    # 1. Apply component adjustments
    adjusted_count = 0
    for adj in critique.get("component_adjustments", []):
        cid = adj.get("component_id")
        if cid in by_id:
            part = by_id[cid]
            d_pos = adj.get("delta_position_m")
            if d_pos and len(d_pos) == 3:
                part["position_m"] = [
                    round(max(-2.0, min(2.0, float(part["position_m"][i]) + float(d_pos[i]))), 4)
                    for i in range(3)
                ]
                adjusted_count += 1
            d_dims = adj.get("delta_dimensions_m")
            if d_dims and len(d_dims) == 3:
                part["dimensions_m"] = [
                    round(max(0.005, min(2.0, float(part["dimensions_m"][i]) + float(d_dims[i]))), 4)
                    for i in range(3)
                ]
                adjusted_count += 1

    if adjusted_count:
        notes.append(f"Visual critique applied {adjusted_count} component adjustment(s).")

    # 2. Synthesize missing parts identified by visual critique (up to 4 parts)
    added_count = 0
    for missing in critique.get("missing_parts_identified", [])[:4]:
        raw_name = missing.get("name", "Extra component")
        slug = re.sub(r"[^a-z0-9_]+", "", raw_name.lower().replace(" ", "_")) or "part_extra"
        part_id = slug
        idx = 2
        while part_id in by_id:
            part_id = f"{slug}_{idx}"
            idx += 1

        parent_id = missing.get("parent_id")
        if parent_id not in by_id:
            # Fallback to root component
            root_part = next((p for p in components if not p.get("parent_id")), components[0])
            parent_id = root_part["id"]

        new_part = {
            "id": part_id,
            "name": raw_name,
            "role": missing.get("role", "structural support"),
            "functional_category": missing.get("functional_category", "structure"),
            "mount_intent": "rigid_mount",
            "parent_id": parent_id,
            "geometry": missing.get("geometry", "box"),
            "dimensions_m": [round(float(v), 4) for v in missing.get("likely_dimensions_m", [0.05, 0.05, 0.05])],
            "position_m": [round(float(v), 4) for v in missing.get("likely_position_m", [0, 0, 0.1])],
            "orientation_rpy": [0.0, 0.0, 0.0],
            "color": missing.get("color", "#4a5568"),
            "confidence": 0.82,
            "visible_vs_inferred": "inferred_from_visual_critique",
        }
        components.append(new_part)
        by_id[part_id] = new_part
        added_count += 1

    if added_count:
        notes.append(f"Visual critique synthesized {added_count} missing component(s).")

    # 3. Re-ground design to reconcile symmetry, height, and parent contact
    grounded, ground_report = ground_image_design(design, target_height_m)
    grounded["visual_critique"] = critique
    return grounded, notes
