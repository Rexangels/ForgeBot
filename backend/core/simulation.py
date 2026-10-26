"""Isolated-simulation acceptance check for the MVP rover.

This is a deterministic kinematic gate, not a physics engine: it verifies a
waypoint route completes without collisions and within configured speed and
motor limits. Results are labeled simulated with their assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

ENGINE_NAME = "forgebot-kinematic-gate"
ENGINE_VERSION = "0.1.0"


@dataclass
class SimResult:
    passed: bool
    distance_m: float
    max_speed_used: float
    collisions: int
    violations: list[str]
    assumptions: list[str]


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _segment_hits_obstacle(
    a: tuple[float, float],
    b: tuple[float, float],
    center: tuple[float, float],
    radius: float,
) -> bool:
    # Distance from segment to circle center; samples keep it deterministic.
    steps = 20
    for i in range(steps + 1):
        t = i / steps
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        if math.hypot(p[0] - center[0], p[1] - center[1]) <= radius:
            return True
    return False


def run_waypoint_route(
    waypoints: list[tuple[float, float]],
    speed_m_s: float,
    max_speed_m_s: float = 1.0,
    motor_effort: float = 5.0,
    max_motor_effort: float = 10.0,
    obstacles: list[tuple[tuple[float, float], float]] | None = None,
) -> SimResult:
    """Check a waypoint route against speed, effort, and collision limits."""
    violations: list[str] = []
    if len(waypoints) < 2:
        return SimResult(False, 0.0, 0.0, 0, ["route needs 2+ waypoints"], [])
    if speed_m_s <= 0 or speed_m_s > max_speed_m_s:
        violations.append(
            f"speed {speed_m_s} exceeds configured max {max_speed_m_s}"
        )
    if motor_effort <= 0 or motor_effort > max_motor_effort:
        violations.append(
            f"motor effort {motor_effort} exceeds configured max {max_motor_effort}"
        )
    distance = sum(_dist(a, b) for a, b in zip(waypoints, waypoints[1:]))
    collisions = 0
    for a, b in zip(waypoints, waypoints[1:]):
        for center, radius in obstacles or []:
            if _segment_hits_obstacle(a, b, center, radius):
                collisions += 1
    if collisions:
        violations.append(f"{collisions} segment(s) intersect obstacles")
    assumptions = [
        f"engine={ENGINE_NAME} {ENGINE_VERSION}",
        "kinematic straight-line motion on a flat plane; no dynamics, "
        "wheel slip, sensor noise, or terrain variation modeled",
    ]
    return SimResult(
        passed=not violations,
        distance_m=distance,
        max_speed_used=speed_m_s,
        collisions=collisions,
        violations=violations,
        assumptions=assumptions,
    )
