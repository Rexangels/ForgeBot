"""Standard mechanical hardware registry, fasteners, and mounting hole generators.

Provides physical dimensions for standard metric fasteners (ISO 262/DIN 912),
common robotics actuator/sensor patterns (NEMA 17/14, standard/micro servos, bearings),
and helper routines to generate cut geometry in OpenSCAD and FreeCAD.
"""

from __future__ import annotations

import math
from typing import Any

METRIC_FASTENERS: dict[str, dict[str, float]] = {
    "M2": {
        "nominal_dia_mm": 2.0,
        "clearance_close_mm": 2.2,
        "clearance_standard_mm": 2.4,
        "tap_drill_mm": 1.6,
        "head_dia_mm": 3.8,
        "head_height_mm": 2.0,
        "nut_width_flats_mm": 4.0,
        "nut_thickness_mm": 1.6,
    },
    "M2.5": {
        "nominal_dia_mm": 2.5,
        "clearance_close_mm": 2.7,
        "clearance_standard_mm": 2.9,
        "tap_drill_mm": 2.05,
        "head_dia_mm": 4.5,
        "head_height_mm": 2.5,
        "nut_width_flats_mm": 5.0,
        "nut_thickness_mm": 2.0,
    },
    "M3": {
        "nominal_dia_mm": 3.0,
        "clearance_close_mm": 3.2,
        "clearance_standard_mm": 3.4,
        "tap_drill_mm": 2.5,
        "head_dia_mm": 5.5,
        "head_height_mm": 3.0,
        "nut_width_flats_mm": 5.5,
        "nut_thickness_mm": 2.4,
    },
    "M4": {
        "nominal_dia_mm": 4.0,
        "clearance_close_mm": 4.3,
        "clearance_standard_mm": 4.5,
        "tap_drill_mm": 3.3,
        "head_dia_mm": 7.0,
        "head_height_mm": 4.0,
        "nut_width_flats_mm": 7.0,
        "nut_thickness_mm": 3.2,
    },
    "M5": {
        "nominal_dia_mm": 5.0,
        "clearance_close_mm": 5.3,
        "clearance_standard_mm": 5.5,
        "tap_drill_mm": 4.2,
        "head_dia_mm": 8.5,
        "head_height_mm": 5.0,
        "nut_width_flats_mm": 8.0,
        "nut_thickness_mm": 4.0,
    },
}

ACTUATOR_MOUNT_PATTERNS: dict[str, dict[str, Any]] = {
    "nema17": {
        "name": "NEMA 17 Stepper Mount",
        "fastener": "M3",
        "hole_pitch_mm": [31.0, 31.0],
        "hole_count": 4,
        "pilot_dia_mm": 22.0,
        "pilot_depth_mm": 2.0,
        "shaft_dia_mm": 5.0,
    },
    "nema14": {
        "name": "NEMA 14 Stepper Mount",
        "fastener": "M3",
        "hole_pitch_mm": [26.0, 26.0],
        "hole_count": 4,
        "pilot_dia_mm": 22.0,
        "pilot_depth_mm": 2.0,
        "shaft_dia_mm": 5.0,
    },
    "standard_servo": {
        "name": "Standard RC Servo (MG996R/Futaba)",
        "fastener": "M3",
        "hole_pitch_mm": [10.0, 49.5],
        "hole_count": 4,
        "spline_dia_mm": 6.0,
        "body_cutout_mm": [41.0, 20.0],
    },
    "micro_servo": {
        "name": "Micro RC Servo (SG90/MG90S)",
        "fastener": "M2",
        "hole_pitch_mm": [0.0, 28.0],
        "hole_count": 2,
        "body_cutout_mm": [23.0, 12.5],
    },
    "bearing_608": {
        "name": "608 Ball Bearing Pocket",
        "outer_dia_mm": 22.0,
        "inner_dia_mm": 8.0,
        "width_mm": 7.0,
    },
    "bearing_mr105": {
        "name": "MR105 Ball Bearing Pocket",
        "outer_dia_mm": 10.0,
        "inner_dia_mm": 5.0,
        "width_mm": 4.0,
    },
}


def get_fastener(size: str) -> dict[str, float]:
    """Retrieve standard metric fastener dimensions."""
    key = size.upper().strip()
    if key not in METRIC_FASTENERS:
        raise ValueError(f"Unknown fastener '{size}'. Supported: {', '.join(METRIC_FASTENERS.keys())}")
    return METRIC_FASTENERS[key]


def get_mount_pattern(name: str) -> dict[str, Any]:
    """Retrieve standard actuator or bearing mounting pattern."""
    key = name.lower().strip()
    if key not in ACTUATOR_MOUNT_PATTERNS:
        raise ValueError(f"Unknown mount pattern '{name}'. Supported: {', '.join(ACTUATOR_MOUNT_PATTERNS.keys())}")
    return ACTUATOR_MOUNT_PATTERNS[key]


def calculate_four_hole_pattern(
    width_mm: float,
    height_mm: float,
    fastener: str = "M3",
    margin_ratio: float = 0.15,
) -> dict[str, Any]:
    """Calculate 4-hole mounting grid coordinates on a planar face."""
    spec = get_fastener(fastener)
    hole_dia = spec["clearance_standard_mm"]
    margin = max(hole_dia * 1.5, min(width_mm, height_mm) * margin_ratio)

    pitch_x = max(0.0, width_mm - 2 * margin)
    pitch_y = max(0.0, height_mm - 2 * margin)

    if pitch_x < hole_dia or pitch_y < hole_dia:
        return {
            "status": "too_small",
            "reason": f"Face {width_mm:.1f}x{height_mm:.1f}mm is too small for {fastener} 4-hole pattern.",
            "holes": [],
        }

    half_x = pitch_x / 2.0
    half_y = pitch_y / 2.0
    holes = [
        {"x_mm": round(-half_x, 3), "y_mm": round(-half_y, 3)},
        {"x_mm": round(half_x, 3), "y_mm": round(-half_y, 3)},
        {"x_mm": round(half_x, 3), "y_mm": round(half_y, 3)},
        {"x_mm": round(-half_x, 3), "y_mm": round(half_y, 3)},
    ]

    return {
        "status": "ready",
        "fastener": fastener.upper(),
        "hole_diameter_mm": hole_dia,
        "pitch_x_mm": round(pitch_x, 3),
        "pitch_y_mm": round(pitch_y, 3),
        "counterbore_dia_mm": spec["head_dia_mm"],
        "counterbore_depth_mm": spec["head_height_mm"],
        "holes": holes,
    }


def generate_scad_hole_subtraction(
    pattern: dict[str, Any],
    through_depth_mm: float,
    add_counterbore: bool = False,
) -> str:
    """Generate OpenSCAD difference() children for through-holes and counterbores."""
    if pattern.get("status") != "ready" or not pattern.get("holes"):
        return ""

    radius = pattern["hole_diameter_mm"] / 2.0
    lines = [f"    // Fastener pattern: {pattern.get('fastener', 'M3')} through-holes"]
    for hole in pattern["holes"]:
        x, y = hole["x_mm"], hole["y_mm"]
        lines.append(
            f"    translate([{x:.3f}, {y:.3f}, -1.0]) cylinder(r={radius:.3f}, h={through_depth_mm + 2.0:.3f}, $fn=32);"
        )
        if add_counterbore:
            cb_r = pattern["counterbore_dia_mm"] / 2.0
            cb_h = pattern["counterbore_depth_mm"]
            lines.append(
                f"    translate([{x:.3f}, {y:.3f}, {through_depth_mm - cb_h:.3f}]) cylinder(r={cb_r:.3f}, h={cb_h + 1.1:.3f}, $fn=32);"
            )
    return "\n".join(lines)


def check_fastener_alignment(
    parent_pattern: dict[str, Any],
    child_pattern: dict[str, Any],
    tolerance_mm: float = 0.5,
) -> dict[str, Any]:
    """Verify alignment between parent and child mounting hole patterns."""
    if parent_pattern.get("status") != "ready" or child_pattern.get("status") != "ready":
        return {
            "status": "incompatible",
            "aligned": False,
            "error_mm": 999.0,
            "reason": "One or both patterns are unconfigured or too small.",
        }

    dx = abs(parent_pattern.get("pitch_x_mm", 0.0) - child_pattern.get("pitch_x_mm", 0.0))
    dy = abs(parent_pattern.get("pitch_y_mm", 0.0) - child_pattern.get("pitch_y_mm", 0.0))
    max_error = max(dx, dy)

    p_fastener = parent_pattern.get("fastener", "").upper()
    c_fastener = child_pattern.get("fastener", "").upper()
    fastener_match = (p_fastener == c_fastener)

    aligned = (max_error <= tolerance_mm) and fastener_match
    return {
        "status": "aligned" if aligned else "misaligned",
        "aligned": aligned,
        "max_pitch_error_mm": round(max_error, 3),
        "fastener_match": fastener_match,
        "parent_fastener": p_fastener,
        "child_fastener": c_fastener,
        "reason": (
            "Holes align within tolerance."
            if aligned
            else f"Pitch mismatch (dx={dx:.2f}mm, dy={dy:.2f}mm) or fastener mismatch ({p_fastener} vs {c_fastener})."
        ),
    }


COTS_FOOTPRINTS: dict[str, dict[str, Any]] = {
    "hc_sr04": {
        "cots_id": "hc_sr04",
        "name": "HC-SR04 Ultrasonic Distance Sensor",
        "keywords": ("hc-sr04", "hcsr04", "ultrasonic", "sonar", "rangefinder"),
        "datasheet_dimensions_mm": [45.0, 20.0, 15.0],
        "fastener": "M2",
        "hole_diameter_mm": 2.2,
        "hole_pitch_mm": [42.0, 17.0],
        "bore_diameter_mm": 0.0,
    },
    "tt_gearmotor": {
        "cots_id": "tt_gearmotor",
        "name": "TT DC Dual-Shaft Gearmotor (1:48)",
        "keywords": ("tt motor", "gearmotor", "gear motor", "drive motor", "dc motor"),
        "datasheet_dimensions_mm": [37.0, 70.0, 22.5],
        "fastener": "M3",
        "hole_diameter_mm": 3.2,
        "hole_pitch_mm": [0.0, 17.5],
        "bore_diameter_mm": 5.4,
    },
    "nema17_stepper": {
        "cots_id": "nema17_stepper",
        "name": "NEMA 17 Bipolar Stepper Motor",
        "keywords": ("nema17", "nema 17", "stepper"),
        "datasheet_dimensions_mm": [42.3, 42.3, 40.0],
        "fastener": "M3",
        "hole_diameter_mm": 3.2,
        "hole_pitch_mm": [31.0, 31.0],
        "bore_diameter_mm": 5.0,
    },
    "raspberry_pi_4b": {
        "cots_id": "raspberry_pi_4b",
        "name": "Raspberry Pi 4B / 5 Single-Board Computer",
        "keywords": ("raspberry", "rpi", "pi 4", "pi 5"),
        "datasheet_dimensions_mm": [85.0, 56.0, 17.0],
        "fastener": "M2.5",
        "hole_diameter_mm": 2.7,
        "hole_pitch_mm": [58.0, 49.0],
        "bore_diameter_mm": 0.0,
    },
    "arduino_uno_r3": {
        "cots_id": "arduino_uno_r3",
        "name": "Arduino Uno R3 / Microcontroller Board",
        "keywords": ("arduino", "uno", "microcontroller", "controller", "control board", "flight controller", "pcb"),
        "datasheet_dimensions_mm": [68.6, 53.4, 15.0],
        "fastener": "M3",
        "hole_diameter_mm": 3.2,
        "hole_pitch_mm": [50.8, 48.3],
        "bore_diameter_mm": 0.0,
    },
    "l298n_driver": {
        "cots_id": "l298n_driver",
        "name": "L298N Dual H-Bridge Motor Driver Module",
        "keywords": ("l298n", "motor driver", "h-bridge", "esc"),
        "datasheet_dimensions_mm": [43.0, 43.0, 27.0],
        "fastener": "M3",
        "hole_diameter_mm": 3.2,
        "hole_pitch_mm": [37.0, 37.0],
        "bore_diameter_mm": 0.0,
    },
    "battery_18650_pack": {
        "cots_id": "battery_18650_pack",
        "name": "2S/3S 18650 Li-Ion / LiPo Battery Pack",
        "keywords": ("battery", "lipo", "18650", "power pack"),
        "datasheet_dimensions_mm": [76.0, 41.0, 21.5],
        "fastener": "M3",
        "hole_diameter_mm": 3.2,
        "hole_pitch_mm": [40.0, 0.0],
        "bore_diameter_mm": 0.0,
    },
    "ball_caster_m3": {
        "cots_id": "ball_caster_m3",
        "name": "Swivel Ball Transfer Caster (M3 Flange)",
        "keywords": ("caster", "ball transfer", "omni"),
        "datasheet_dimensions_mm": [22.0, 22.0, 20.0],
        "fastener": "M3",
        "hole_diameter_mm": 3.2,
        "hole_pitch_mm": [14.0, 0.0],
        "bore_diameter_mm": 0.0,
    },
    "wheel_65mm": {
        "cots_id": "wheel_65mm",
        "name": "65mm Robot Traction Wheel & Hub",
        "keywords": ("wheel", "tire", "roller"),
        "datasheet_dimensions_mm": [65.0, 65.0, 26.0],
        "fastener": "M3",
        "hole_diameter_mm": 3.2,
        "hole_pitch_mm": [0.0, 0.0],
        "bore_diameter_mm": 5.4,
    },
    "m3_hex_standoff": {
        "cots_id": "m3_hex_standoff",
        "name": "M3 Threaded Standoff / Spacer Pillar",
        "keywords": ("standoff", "spacer", "pillar"),
        "datasheet_dimensions_mm": [6.0, 6.0, 35.0],
        "fastener": "M3",
        "hole_diameter_mm": 3.2,
        "hole_pitch_mm": [0.0, 0.0],
        "bore_diameter_mm": 3.2,
    },
}


def _keyword_matches(label: str, tokens: set[str], kw: str) -> bool:
    if " " in kw or "-" in kw:
        return kw in label
    return kw in tokens


def match_cots_footprint(component: dict[str, Any]) -> dict[str, Any] | None:
    """Match a conceptual component to a standard COTS hardware footprint."""
    import re as _re

    label = f"{component.get('id', '')} {component.get('name', '')} {component.get('role', '')}".lower()
    tokens = set(_re.split(r"[^a-z0-9.]+", label))
    for cots in COTS_FOOTPRINTS.values():
        if any(_keyword_matches(label, tokens, kw) for kw in cots["keywords"]):
            dims_mm = [round(float(v) * 1000.0, 2) for v in component.get("dimensions_m", [0.05, 0.05, 0.02])]
            width_mm, depth_mm = dims_mm[0], dims_mm[1]
            px, py = cots["hole_pitch_mm"]
            hole_dia = cots["hole_diameter_mm"]
            margin = hole_dia * 1.4
            holes: list[dict[str, float]] = []
            if px > 0 and py > 0:
                eff_px = min(px, max(0.0, width_mm - 2 * margin))
                eff_py = min(py, max(0.0, depth_mm - 2 * margin))
                if eff_px >= hole_dia and eff_py >= hole_dia:
                    for sx in (-0.5, 0.5):
                        for sy in (-0.5, 0.5):
                            holes.append({"x_mm": round(sx * eff_px, 3), "y_mm": round(sy * eff_py, 3)})
            elif px > 0 or py > 0:
                span = max(px, py)
                along_x = width_mm >= depth_mm
                limit = (width_mm if along_x else depth_mm) - 2 * margin
                eff_span = min(span, max(0.0, limit))
                if eff_span >= hole_dia:
                    for s in (-0.5, 0.5):
                        holes.append(
                            {"x_mm": round(s * eff_span, 3), "y_mm": 0.0}
                            if along_x
                            else {"x_mm": 0.0, "y_mm": round(s * eff_span, 3)}
                        )
            return {
                "cots_id": cots["cots_id"],
                "name": cots["name"],
                "fastener": cots["fastener"],
                "hole_diameter_mm": hole_dia,
                "bore_diameter_mm": cots["bore_diameter_mm"],
                "datasheet_dimensions_mm": list(cots["datasheet_dimensions_mm"]),
                "mounting_holes_local_mm": holes,
            }
    return None


def project_assembly_mating_holes(components: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Compute per-component local XY holes combining COTS, cross-part child projections, and corner holes."""
    import re as _re

    result: dict[str, list[dict[str, Any]]] = {part["id"]: [] for part in components}
    cots_by_id = {part["id"]: match_cots_footprint(part) for part in components}

    def _add_unique_hole(
        hole_list: list[dict[str, Any]],
        x_mm: float,
        y_mm: float,
        dia_mm: float,
        fastener: str,
        source: str,
        max_x: float,
        max_y: float,
    ) -> None:
        r = dia_mm / 2.0
        edge_margin = max(r * 1.6, 2.0)
        if abs(x_mm) > (max_x - edge_margin) or abs(y_mm) > (max_y - edge_margin):
            return
        for existing in hole_list:
            min_sep = (dia_mm + existing["diameter_mm"]) * 0.75 + 1.5
            if math.hypot(existing["x_mm"] - x_mm, existing["y_mm"] - y_mm) < min_sep:
                return
        hole_list.append({
            "x_mm": round(x_mm, 3),
            "y_mm": round(y_mm, 3),
            "diameter_mm": round(dia_mm, 3),
            "fastener": fastener,
            "source": source,
        })

    # Pass 1: intrinsic COTS datasheet hole patterns for recognized components
    for part in components:
        part_id = part["id"]
        dims_mm = [float(v) * 1000.0 for v in part.get("dimensions_m", [0.05, 0.05, 0.02])]
        hx_max, hy_max = dims_mm[0] / 2.0, dims_mm[1] / 2.0
        cots = cots_by_id.get(part_id)
        if cots and cots["mounting_holes_local_mm"]:
            for h in cots["mounting_holes_local_mm"]:
                _add_unique_hole(
                    result[part_id],
                    h["x_mm"],
                    h["y_mm"],
                    cots["hole_diameter_mm"],
                    cots["fastener"],
                    f"cots:{cots['cots_id']}",
                    hx_max,
                    hy_max,
                )

    # Pass 2: cross-part projection from vertical standoffs and child components onto structural plates/decks
    for plate in components:
        if plate.get("geometry") != "box":
            continue
        plate_id = plate["id"]
        p_dims_mm = [float(v) * 1000.0 for v in plate["dimensions_m"]]
        pw_mm, pd_mm, ph_mm = p_dims_mm
        p_label = f"{plate_id} {plate.get('name', '')} {plate.get('role', '')}".lower()
        is_plate = ph_mm < min(pw_mm, pd_mm) or any(
            w in p_label for w in ("plate", "deck", "chassis", "base", "tray", "frame", "mount")
        )
        if not is_plate:
            continue
        p_pos = [float(v) for v in plate["position_m"]]
        p_z_min = p_pos[2] - float(plate["dimensions_m"][2]) / 2.0
        p_z_max = p_pos[2] + float(plate["dimensions_m"][2]) / 2.0
        hx_max, hy_max = pw_mm / 2.0, pd_mm / 2.0
        plate_holes = result[plate_id]

        # Project standoffs first so deck corner holes are coaxial with standoff bores
        ordered_others = sorted(
            [o for o in components if o["id"] != plate_id],
            key=lambda o: 0 if any(w in f"{o['id']} {o.get('name', '')} {o.get('role', '')}".lower() for w in ("standoff", "spacer", "pillar")) else 1,
        )
        for other in ordered_others:
            o_label = f"{other['id']} {other.get('name', '')} {other.get('role', '')}".lower()
            o_tokens = set(_re.split(r"[^a-z0-9]+", o_label))
            if o_tokens & {"wheel", "tire", "roller", "plate", "deck", "chassis"}:
                continue
            is_standoff = bool(o_tokens & {"standoff", "spacer", "pillar"})
            o_pos = [float(v) for v in other["position_m"]]
            o_dz = float(other["dimensions_m"][2]) / 2.0
            o_z_min, o_z_max = o_pos[2] - o_dz, o_pos[2] + o_dz
            z_touches = (
                (abs(o_z_min - p_z_max) <= 0.025)
                or (abs(o_z_max - p_z_min) <= 0.025)
                or (o_z_min <= p_z_max and o_z_max >= p_z_min)
            )
            is_direct_child = other.get("parent_id") == plate_id
            if not (is_direct_child or (is_standoff and z_touches)):
                continue

            dx_mm = (o_pos[0] - p_pos[0]) * 1000.0
            dy_mm = (o_pos[1] - p_pos[1]) * 1000.0
            if is_standoff or other.get("geometry") == "cylinder":
                _add_unique_hole(plate_holes, dx_mm, dy_mm, 3.4, "M3", f"mate:{other['id']}", hx_max, hy_max)
            else:
                child_holes = result.get(other["id"], [])
                if child_holes:
                    for ch in child_holes:
                        _add_unique_hole(
                            plate_holes,
                            dx_mm + ch["x_mm"],
                            dy_mm + ch["y_mm"],
                            ch["diameter_mm"],
                            ch["fastener"],
                            f"mate:{other['id']}",
                            hx_max,
                            hy_max,
                        )
                else:
                    _add_unique_hole(plate_holes, dx_mm, dy_mm, 3.4, "M3", f"mate:{other['id']}", hx_max, hy_max)

    # Pass 3: fallback 4-corner M3 pattern for any box >= 20x20mm that still has fewer than 2 mounting holes
    for part in components:
        part_id = part["id"]
        if part.get("geometry") != "box" or len(result[part_id]) >= 2:
            continue
        dims_mm = [float(v) * 1000.0 for v in part.get("dimensions_m", [0.05, 0.05, 0.02])]
        w_mm, d_mm, _ = dims_mm
        if w_mm >= 20.0 and d_mm >= 20.0:
            hx_max, hy_max = w_mm / 2.0, d_mm / 2.0
            four = calculate_four_hole_pattern(w_mm, d_mm, fastener="M3")
            if four["status"] == "ready":
                for h in four["holes"]:
                    _add_unique_hole(result[part_id], h["x_mm"], h["y_mm"], four["hole_diameter_mm"], "M3", "self_corner_m3", hx_max, hy_max)

    return result

