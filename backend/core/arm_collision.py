"""Deterministic arm collision prediction for the tabletop-arm preview gate.

The preview cannot run physics, so it models each link as a capsule derived
from the reviewed spec geometry (same radii as the MJCF model) and checks
non-adjacent link pairs plus caller-supplied box obstacles (payload cubes,
bin walls). Findings are advisory: a predicted collision blocks the kinematic
animation, while MuJoCo contacts remain the authoritative task gate.
"""

from __future__ import annotations

import math

# Link radii in metres, mirroring the MJCF capsule sizes and shoulder sphere.
LINK_RADII = {
    "base_column": 0.035,
    "upper_arm": 0.018,
    "forearm": 0.016,
    "wrist": 0.014,
}

# Non-adjacent link pairs whose contact is never expected. Adjacent links
# share a joint by construction (as in the MuJoCo shoulder/base allowlist).
SELF_PAIRS = (
    ("base_column", "forearm"),
    ("base_column", "wrist"),
    ("upper_arm", "wrist"),
)


def link_segments(spec: dict, joints: dict | list) -> dict[str, tuple]:
    """Return capsule segments ((x1,y1,z1),(x2,y2,z2),radius) per link name."""
    geometry = spec["geometry"]
    if isinstance(joints, dict):
        yaw = float(joints.get("base_yaw", 0.0))
        planar = [-float(joints.get("shoulder_pitch", 0.0)), -float(joints.get("elbow_pitch", 0.0)), -float(joints.get("wrist_pitch", 0.0))]
    else:
        yaw, planar = float(joints[0]), [-float(joints[1]), -float(joints[2]), -float(joints[3])]
    upper = float(geometry["upper_arm_length_m"])
    forearm = float(geometry["forearm_length_m"])
    wrist = float(geometry["wrist_length_m"])
    shoulder_z = float(geometry["base_height_m"]) + 0.07
    direction = (math.cos(yaw), math.sin(yaw))

    def extend(origin: tuple, length: float, angle: float) -> tuple:
        radial = length * math.cos(angle)
        return (origin[0] + radial * direction[0], origin[1] + radial * direction[1], origin[2] + length * math.sin(angle))

    shoulder = (0.0, 0.0, shoulder_z)
    elbow = extend(shoulder, upper, planar[0])
    wrist_base = extend(elbow, forearm, planar[0] + planar[1])
    tool = extend(wrist_base, wrist, planar[0] + planar[1] + planar[2])
    return {
        "base_column": ((0.0, 0.0, 0.0), shoulder, LINK_RADII["base_column"]),
        "upper_arm": (shoulder, elbow, LINK_RADII["upper_arm"]),
        "forearm": (elbow, wrist_base, LINK_RADII["forearm"]),
        "wrist": (wrist_base, tool, LINK_RADII["wrist"]),
    }


def _sub(a: tuple, b: tuple) -> tuple:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: tuple, b: tuple) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def segment_segment_distance(p1: tuple, q1: tuple, p2: tuple, q2: tuple) -> float:
    """Shortest distance between segments p1q1 and p2q2 (Real-Time Collision Detection)."""
    d1, d2, r = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    if a < 1e-12 and e < 1e-12:
        return math.sqrt(_dot(r, r))
    if a < 1e-12:
        s = 0.0
        t = max(0.0, min(1.0, f / e))
    elif e < 1e-12:
        t = 0.0
        s = max(0.0, min(1.0, -_dot(d1, r) / a))
    else:
        c = _dot(d1, r)
        b = _dot(d1, d2)
        denom = a * e - b * b
        s = 0.0 if denom < 1e-12 else max(0.0, min(1.0, (b * f - c * e) / denom))
        t = (b * s + f) / e
        if t < 0.0:
            t, s = 0.0, max(0.0, min(1.0, -c / a))
        elif t > 1.0:
            t, s = 1.0, max(0.0, min(1.0, (b - c) / a))
    closest_a = (p1[0] + d1[0] * s, p1[1] + d1[1] * s, p1[2] + d1[2] * s)
    closest_b = (p2[0] + d2[0] * t, p2[1] + d2[1] * t, p2[2] + d2[2] * t)
    return math.sqrt(_dot(_sub(closest_a, closest_b), _sub(closest_a, closest_b)))


def point_box_distance(point: tuple, center: tuple, half: tuple) -> float:
    """Signed distance from a point to an axis-aligned box (negative inside)."""
    dx = abs(point[0] - center[0]) - half[0]
    dy = abs(point[1] - center[1]) - half[1]
    dz = abs(point[2] - center[2]) - half[2]
    outside = math.sqrt(max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2 + max(dz, 0.0) ** 2)
    if outside > 0:
        return outside
    return max(dx, dy, dz)


def segment_box_clearance(start: tuple, end: tuple, radius: float, center: tuple, half: tuple, samples: int = 16) -> float:
    """Minimum surface clearance between a capsule segment and a box."""
    return min(
        point_box_distance(
            (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t, start[2] + (end[2] - start[2]) * t),
            center,
            half,
        )
        for t in [index / samples for index in range(samples + 1)]
    ) - radius


def check_arm_collisions(spec: dict, joints: dict | list, obstacles: list[dict] | None = None) -> list[dict]:
    """Return collision findings for one arm pose; empty means clear."""
    segments = link_segments(spec, joints)
    findings = []
    for first, second in SELF_PAIRS:
        (p1, q1, r1), (p2, q2, r2) = segments[first], segments[second]
        clearance = segment_segment_distance(p1, q1, p2, q2) - r1 - r2
        if clearance < 0:
            findings.append({
                "type": "self_collision",
                "links": [first, second],
                "clearance_m": round(clearance, 5),
                "message": f"{first} and {second} overlap by {-clearance * 1000:.1f} mm; this pose would self-collide.",
            })
    for obstacle in obstacles or []:
        name = obstacle.get("name", "obstacle")
        center = tuple(obstacle["center_m"])
        half = tuple(obstacle["half_m"])
        ignored = {"base_column", *(obstacle.get("ignore") or [])}
        for link, (start, end, radius) in segments.items():
            if link in ignored:
                continue
            clearance = segment_box_clearance(start, end, radius, center, half)
            if clearance < 0:
                findings.append({
                    "type": "obstacle_collision",
                    "links": [link],
                    "obstacle": name,
                    "clearance_m": round(clearance, 5),
                    "message": f"{link} intersects {name} by {-clearance * 1000:.1f} mm.",
                })
    return findings


def check_arm_path(spec: dict, waypoints: list, obstacles: list[dict] | None = None) -> list[dict]:
    """Return the first blocking finding along joint waypoints, annotated by index."""
    for index, joints in enumerate(waypoints):
        findings = check_arm_collisions(spec, joints, obstacles)
        if findings:
            return [{**findings[0], "waypoint_index": index}]
    return []


def workcell_obstacles(pick: tuple, place: tuple, bin_half: tuple | None = None) -> list[dict]:
    """Build payload-cube obstacles, plus open place-bin walls when configured.

    Bin walls are opt-in because the workcell has no bin model by default;
    inventing one would false-block placements that physics accepts.
    """
    # The tool tip intentionally enters payload space during grasp, mirroring
    # the simulation's finger/payload contact allowlist; bin walls check all links.
    obstacles = [
        {"name": "pick_payload", "center_m": (pick[0], pick[1], 0.018), "half_m": (0.015, 0.015, 0.015), "ignore": ["wrist"]},
        {"name": "place_payload", "center_m": (place[0], place[1], 0.018), "half_m": (0.015, 0.015, 0.015), "ignore": ["wrist"]},
    ]
    if bin_half is None:
        return obstacles
    wall = 0.005
    bx, by = place[0], place[1]
    hx, hy, hz = bin_half
    for name, center, half in (
        ("bin_wall_x+", (bx + hx, by, hz / 2), (wall, hy, hz / 2)),
        ("bin_wall_x-", (bx - hx, by, hz / 2), (wall, hy, hz / 2)),
        ("bin_wall_y+", (bx, by + hy, hz / 2), (hx, wall, hz / 2)),
        ("bin_wall_y-", (bx, by - hy, hz / 2), (hx, wall, hz / 2)),
    ):
        obstacles.append({"name": name, "center_m": center, "half_m": half})
    return obstacles
