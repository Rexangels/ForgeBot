"""Deterministic perception helpers: calibration math, tracking, depth provenance.

The model only reports image-relative regions. Everything metric here is
derived locally from the user's four clicked corners plus an explicit depth:
solving the planar homography, measuring its own leave-one-out error, mapping
detections to workcell targets, and matching objects across frames by box
overlap. No pixels leave the machine in these functions.
"""

from __future__ import annotations

import math

WORK_AREA_CORNERS = [[-0.1, -0.3], [0.7, -0.3], [0.7, 0.3], [-0.1, 0.3]]
DEFAULT_OBJECT_HEIGHT_M = 0.04


def _solve_linear(matrix: list[list[float]], values: list[float]) -> list[float]:
    size = len(values)
    augmented = [list(row) + [values[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-10:
            raise ValueError("Calibration points are too close together or incorrectly ordered.")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        for index in range(column, size + 1):
            augmented[column][index] /= scale
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            for index in range(column, size + 1):
                augmented[row][index] -= factor * augmented[column][index]
    return [row[size] for row in augmented]


def _clean_point(point: object, label: str) -> tuple[float, float]:
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError(f"{label} must be an [x, y] pair.")
    try:
        values = (float(point[0]), float(point[1]))
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be an [x, y] pair.") from None
    if any(not math.isfinite(value) for value in values):
        raise ValueError(f"{label} must hold finite numbers.")
    return values


def solve_homography(image_points: list, world_points: list) -> list[float]:
    """Solve the 8-parameter planar homography from exactly four correspondences."""
    if len(image_points) != 4 or len(world_points) != 4:
        raise ValueError("Calibration needs exactly four image and four world points.")
    rows: list[list[float]] = []
    values: list[float] = []
    for index, (image, world) in enumerate(zip(image_points, world_points)):
        u, v = _clean_point(image, f"image point {index + 1}")
        x, y = _clean_point(world, f"world point {index + 1}")
        rows.append([u, v, 1, 0, 0, 0, -x * u, -x * v])
        values.append(x)
        rows.append([0, 0, 0, u, v, 1, -y * u, -y * v])
        values.append(y)
    return _solve_linear(rows, values)


def apply_homography(point: list | tuple, homography: list[float]) -> tuple[float, float]:
    denominator = homography[6] * point[0] + homography[7] * point[1] + 1
    if abs(denominator) < 1e-10:
        raise ValueError("The image point is outside the calibrated work surface.")
    return (
        (homography[0] * point[0] + homography[1] * point[1] + homography[2]) / denominator,
        (homography[3] * point[0] + homography[4] * point[1] + homography[5]) / denominator,
    )


def leave_one_out_error(image_points: list, world_points: list) -> dict:
    """Measure calibration accuracy: solve from each trio, predict the held-out corner.

    Returns per-corner and summary reprojection errors in metres. Large values
    mean misordered or sloppy clicks, not a bad camera.
    """
    if len(image_points) != 4 or len(world_points) != 4:
        raise ValueError("Calibration needs exactly four image and four world points.")
    per_corner = []
    for held in range(4):
        subset_image = [point for index, point in enumerate(image_points) if index != held]
        subset_world = [point for index, point in enumerate(world_points) if index != held]
        if len(subset_image) != 3:
            raise ValueError("Leave-one-out needs three correspondences.")
        partial = _solve_three_point(subset_image, subset_world)
        predicted = apply_homography(_clean_point(image_points[held], "held-out point"), partial)
        truth = _clean_point(world_points[held], "held-out truth")
        per_corner.append(round(math.dist(predicted, truth), 5))
    return {
        "per_corner_m": per_corner,
        "mean_m": round(sum(per_corner) / len(per_corner), 5),
        "max_m": round(max(per_corner), 5),
    }


def _solve_three_point(image_points: list, world_points: list) -> list[float]:
    """Affine fit from three correspondences (planar homography with zero perspective)."""
    rows: list[list[float]] = []
    values: list[float] = []
    for image, world in zip(image_points, world_points):
        u, v = _clean_point(image, "image point")
        x, y = _clean_point(world, "world point")
        rows.append([u, v, 1, 0, 0, 0])
        values.append(x)
        rows.append([0, 0, 0, u, v, 1])
        values.append(y)
    affine = _solve_linear(rows, values)
    return [affine[0], affine[1], affine[2], affine[3], affine[4], affine[5], 0.0, 0.0]


UNCERTAINTY_GATE_M = 0.01

# 1-sigma depth uncertainty by provenance: measured is tight, single-view
# geometric height is coarse, and the assumed 40 mm plane is a guess.
_DEPTH_SIGMA_M = {"measured": 0.005, "geometric_vanishing_height": 0.03, "geometric_affine_height": 0.03, "assumed_plane_40mm": 0.03, "multi_view_triangulation": 0.01}


def target_uncertainty(
    region: dict,
    homography: list[float],
    depth_source: str,
    calibration_error_m: float | None = None,
) -> dict:
    """1-sigma target uncertainty from calibration error, box size, and depth provenance.

    sigma_xy combines the saved calibration's worst leave-one-out error with
    half the detection's ground footprint (box quantization); sigma_z comes
    from the depth source table. `within_gate` compares the 3D sigma against
    UNCERTAINTY_GATE_M; callers must refuse or flag picks that miss the gate.
    """
    calibration = float(calibration_error_m) if calibration_error_m is not None else 0.02
    if not math.isfinite(calibration) or calibration < 0:
        raise ValueError("Calibration error must be a non-negative number of metres.")
    base_v = float(region["y_max"])
    left = apply_homography([region["x_min"], base_v], homography)
    right = apply_homography([region["x_max"], base_v], homography)
    footprint = math.dist(left, right) / 2.0
    sigma_xy = math.sqrt(calibration**2 + footprint**2)
    sigma_z = _DEPTH_SIGMA_M.get(depth_source, 0.03)
    sigma = math.sqrt(sigma_xy**2 + sigma_z**2)
    return {
        "sigma_xy_m": round(sigma_xy, 5),
        "sigma_z_m": round(sigma_z, 5),
        "sigma_m": round(sigma, 5),
        "gate_m": UNCERTAINTY_GATE_M,
        "within_gate": sigma <= UNCERTAINTY_GATE_M,
        "basis": "calibration leave-one-out max + half footprint + depth-source sigma",
    }


def map_detection(
    region: dict,
    homography: list[float],
    depth_m: float | None = None,
    estimate_height: bool = False,
    camera_height_m: float = 0.75,
    calibration_error_m: float | None = None,
) -> dict:
    """Map a detection's lower box-center to a workcell target with depth provenance."""
    for key in ("x_min", "y_min", "x_max", "y_max"):
        value = region.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise ValueError("Detection regions must hold normalized 0..1 bounds.")
    if not region["x_min"] < region["x_max"] or not region["y_min"] < region["y_max"]:
        raise ValueError("Detection regions must have positive area.")
    if estimate_height:
        mapped = estimate_detection_3d(region, homography, depth_m=depth_m, camera_height_m=camera_height_m)
        mapped["uncertainty"] = target_uncertainty(region, homography, mapped["depth_source"], calibration_error_m)
        return mapped
    x, y = apply_homography([(region["x_min"] + region["x_max"]) / 2, region["y_max"]], homography)
    if depth_m is None:
        target = {"x": round(x, 4), "y": round(y, 4), "z": DEFAULT_OBJECT_HEIGHT_M, "depth_source": "assumed_plane_40mm"}
    else:
        depth = float(depth_m)
        if not math.isfinite(depth) or not 0 < depth <= 1.0:
            raise ValueError("Measured depth must be between 0 and 1 m.")
        target = {"x": round(x, 4), "y": round(y, 4), "z": round(depth, 4), "depth_source": "measured"}
    target["uncertainty"] = target_uncertainty(region, homography, target["depth_source"], calibration_error_m)
    return target


def estimate_detection_3d(
    region: dict,
    homography: list[float],
    depth_m: float | None = None,
    camera_height_m: float = 0.75,
) -> dict:
    """Estimate a 3D bounding envelope, object height, and grasp target from 2D detection and homography.

    Uses perspective vanishing line geometry or local Jacobian vertical scaling to solve
    for object height, bounding volume, and pick approach clearance.
    """
    for key in ("x_min", "y_min", "x_max", "y_max"):
        value = region.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise ValueError("Detection regions must hold normalized 0..1 bounds.")
    if not region["x_min"] < region["x_max"] or not region["y_min"] < region["y_max"]:
        raise ValueError("Detection regions must have positive area.")

    u_center = (region["x_min"] + region["x_max"]) / 2.0
    v_base = float(region["y_max"])
    v_top = float(region["y_min"])
    delta_v = v_base - v_top

    # Ground base point
    x_base, y_base = apply_homography([u_center, v_base], homography)

    # Footprint width in metres on the work surface
    left_x, left_y = apply_homography([region["x_min"], v_base], homography)
    right_x, right_y = apply_homography([region["x_max"], v_base], homography)
    width_m = math.dist((left_x, left_y), (right_x, right_y))

    if depth_m is not None:
        height_m = float(depth_m)
        if not math.isfinite(height_m) or not 0 < height_m <= 1.0:
            raise ValueError("Measured depth must be between 0 and 1 m.")
        depth_source = "measured"
    else:
        # Horizon from line at infinity: h6 * u + h7 * v + 1 = 0 => v = -(1 + h6 * u) / h7
        h6, h7 = homography[6], homography[7]
        if abs(h7) > 1e-4:
            v_horizon = -(1.0 + h6 * u_center) / h7
            if v_base > v_horizon and (v_base - v_horizon) > 0.05:
                # Cross-ratio single-view height estimation relative to camera height
                ratio = delta_v / (v_base - v_horizon)
                height_m = camera_height_m * ratio
                depth_source = "geometric_vanishing_height"
            else:
                p_shifted = apply_homography([u_center, max(0.0, v_base - 0.05)], homography)
                scale_v = math.dist((x_base, y_base), p_shifted) / 0.05
                height_m = delta_v * scale_v
                depth_source = "geometric_affine_height"
        else:
            p_shifted = apply_homography([u_center, max(0.0, v_base - 0.05)], homography)
            scale_v = math.dist((x_base, y_base), p_shifted) / 0.05
            height_m = delta_v * scale_v
            depth_source = "geometric_affine_height"

        # Tabletop object height bounds [15mm, 350mm]
        height_m = max(0.015, min(0.35, height_m))

    z_center = height_m / 2.0
    return {
        "x": round(x_base, 4),
        "y": round(y_base, 4),
        "z": round(height_m, 4),
        "height_m": round(height_m, 4),
        "width_m": round(width_m, 4),
        "depth_source": depth_source,
        "bounding_box_3d": {
            "center": [round(x_base, 4), round(y_base, 4), round(z_center, 4)],
            "extent": [round(width_m, 4), round(width_m, 4), round(height_m, 4)],
        },
        "grasp_target": {
            "x": round(x_base, 4),
            "y": round(y_base, 4),
            "z": round(height_m, 4),
            "approach_z": round(height_m + 0.05, 4),
        },
    }


def triangulate_multi_view(
    point_a: list[float] | tuple[float, float],
    homography_a: list[float],
    camera_a_pos: list[float] | tuple[float, float, float],
    point_b: list[float] | tuple[float, float],
    homography_b: list[float],
    camera_b_pos: list[float] | tuple[float, float, float],
) -> dict:
    """Triangulate a 3D workcell point from two calibrated camera observations.

    Casts 3D sight rays from each camera through the homography-projected ground points
    and finds their closest point of approach (CPA).
    """
    ga_x, ga_y = apply_homography(point_a, homography_a)
    gb_x, gb_y = apply_homography(point_b, homography_b)

    ca = [float(val) for val in camera_a_pos]
    cb = [float(val) for val in camera_b_pos]
    ground_a = [ga_x, ga_y, 0.0]
    ground_b = [gb_x, gb_y, 0.0]

    da = [ground_a[i] - ca[i] for i in range(3)]
    db = [ground_b[i] - cb[i] for i in range(3)]
    len_a = math.sqrt(sum(v * v for v in da))
    len_b = math.sqrt(sum(v * v for v in db))
    if len_a < 1e-6 or len_b < 1e-6:
        raise ValueError("Camera positions cannot coincide with target ground projections.")

    ua = [v / len_a for v in da]
    ub = [v / len_b for v in db]
    w0 = [ca[i] - cb[i] for i in range(3)]

    b = sum(ua[i] * ub[i] for i in range(3))
    denom = 1.0 - b * b
    if denom < 1e-6:
        # Nearly parallel rays: fallback to ground midpoint
        mid = [(ga_x + gb_x) / 2.0, (ga_y + gb_y) / 2.0, DEFAULT_OBJECT_HEIGHT_M]
        residual = math.dist((ga_x, ga_y), (gb_x, gb_y))
    else:
        d = sum(ua[i] * w0[i] for i in range(3))
        e = sum(ub[i] * w0[i] for i in range(3))
        s = (b * e - d) / denom
        t = (e - b * d) / denom
        p1 = [ca[i] + s * ua[i] for i in range(3)]
        p2 = [cb[i] + t * ub[i] for i in range(3)]
        mid = [(p1[i] + p2[i]) / 2.0 for i in range(3)]
        residual = math.sqrt(sum((p1[i] - p2[i]) ** 2 for i in range(3)))

    return {
        "x": round(mid[0], 4),
        "y": round(mid[1], 4),
        "z": round(max(0.0, mid[2]), 4),
        "residual_m": round(residual, 5),
        "depth_source": "multi_view_triangulation",
    }


def _iou(first: dict, second: dict) -> float:
    try:
        (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = (
            (float(item["x_min"]), float(item["y_min"]), float(item["x_max"]), float(item["y_max"]))
            for item in (first, second)
        )
    except (KeyError, TypeError, ValueError):
        return 0.0
    if not (ax0 < ax1 and ay0 < ay1 and bx0 < bx1 and by0 < by1):
        return 0.0
    intersection = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
    if intersection <= 0:
        return 0.0
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - intersection
    return intersection / union if union > 0 else 0.0


def track_objects(previous: list[dict], current: list[dict], iou_threshold: float = 0.3) -> list[dict]:
    """Carry stable track ids across frames by greedy box-overlap matching.

    Objects keep the id of the previous detection they overlap most (same
    label required); unmatched detections open new tracks. Pure function of
    its inputs; the caller persists whatever it needs.
    """
    if not 0 <= iou_threshold <= 1:
        raise ValueError("The overlap threshold must sit between 0 and 1.")
    used: set[int] = set()
    next_id = 1
    for item in previous:
        try:
            candidate = int(item.get("track_id", 0))
        except (TypeError, ValueError):
            candidate = 0
        next_id = max(next_id, candidate + 1)
    tracks = []
    for item in current:
        region = item.get("image_region", {})
        best, best_score = None, iou_threshold
        for index, prior in enumerate(previous):
            if index in used or prior.get("label") != item.get("label"):
                continue
            score = _iou(prior.get("image_region", {}), region)
            if score > best_score:
                best, best_score = index, score
        if best is None:
            tracks.append({**item, "track_id": next_id, "frames_seen": 1})
            next_id += 1
        else:
            used.add(best)
            tracks.append({**item, "track_id": previous[best].get("track_id"), "frames_seen": int(previous[best].get("frames_seen", 1)) + 1})
    return tracks
