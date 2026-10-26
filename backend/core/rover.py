"""Differential-drive rover generation for Phase 1.

The rover avoids grasping and complex manipulation; these helpers produce a
reviewable proposal, never a manufacturing-ready claim.
"""

from __future__ import annotations

from .schemas import BOM_SCHEMA_VERSION, SPEC_SCHEMA_VERSION

MAX_SPEED_M_S = 1.0
MAX_MOTOR_EFFORT = 10.0


def generate_rover_spec() -> dict:
    return {
        "schema_version": SPEC_SCHEMA_VERSION,
        "robot_class": "differential-drive-rover",
        "links": ["base_link", "left_wheel", "right_wheel", "caster"],
        "joints": ["left_wheel_joint", "right_wheel_joint", "caster_joint"],
        "assumptions": [
            "Flat indoor surface; no slopes or stairs.",
            f"Speed capped at {MAX_SPEED_M_S} m/s for the demo route.",
        ],
    }


def generate_rover_bom() -> dict:
    return {
        "schema_version": BOM_SCHEMA_VERSION,
        "items": [
            {"name": "chassis-plate", "availability": "verified"},
            {"name": "dc-gear-motor-x2", "availability": "verified"},
            {"name": "caster-wheel", "availability": "verified"},
            {"name": "motor-driver", "availability": "unverified"},
        ],
    }


def generate_rover_urdf() -> str:
    return """<robot name="forgebot-rover">
  <link name="base_link">
    <inertial>
      <mass value="5.0"/>
      <inertia ixx="0.1" iyy="0.1" izz="0.1"/>
    </inertial>
    <visual><geometry><box size="0.5 0.35 0.15"/></geometry></visual>
  </link>
  <link name="left_wheel">
    <inertial>
      <mass value="0.5"/>
      <inertia ixx="0.01" iyy="0.01" izz="0.01"/>
    </inertial>
    <visual><geometry><cylinder radius="0.09" length="0.05"/></geometry></visual>
  </link>
  <link name="right_wheel">
    <inertial>
      <mass value="0.5"/>
      <inertia ixx="0.01" iyy="0.01" izz="0.01"/>
    </inertial>
    <visual><geometry><cylinder radius="0.09" length="0.05"/></geometry></visual>
  </link>
  <link name="caster">
    <inertial>
      <mass value="0.2"/>
      <inertia ixx="0.005" iyy="0.005" izz="0.005"/>
    </inertial>
    <visual><geometry><sphere radius="0.04"/></geometry></visual>
  </link>
  <joint name="left_wheel_joint" type="continuous">
    <parent link="base_link"/><child link="left_wheel"/>
    <limit lower="-3.14" upper="3.14" effort="10.0" velocity="5.0"/>
  </joint>
  <joint name="right_wheel_joint" type="continuous">
    <parent link="base_link"/><child link="right_wheel"/>
    <limit lower="-3.14" upper="3.14" effort="10.0" velocity="5.0"/>
  </joint>
  <joint name="caster_joint" type="fixed">
    <parent link="base_link"/><child link="caster"/>
  </joint>
</robot>
"""


def generate_starter_code() -> str:
    return '''"""Rover waypoint starter (REQUIRES engineering review before hardware use)."""
WAYPOINTS = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]
MAX_SPEED_M_S = 1.0


def plan(waypoints):
    """Return straight-line segments capped at MAX_SPEED_M_S."""
    segments = []
    for a, b in zip(waypoints, waypoints[1:]):
        segments.append({"from": a, "to": b, "speed": MAX_SPEED_M_S})
    return segments
'''
