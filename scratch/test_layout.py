import sys
import os
import math
import copy
import json

sys.path.insert(0, 'backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

import sqlite3
import core.world_training as wt

conn = sqlite3.connect('backend/db.sqlite3')
cur = conn.cursor()
cur.execute("SELECT body FROM core_artifact WHERE id=257")
spec = json.loads(cur.fetchone()[0])
conn.close()

components = spec.get("components", [])
max_reach = 0.0
for p in components:
    px, py = float(p.get("position_m", [0, 0, 0])[0]), float(p.get("position_m", [0, 0, 0])[1])
    dims = [float(v) for v in p.get("dimensions_m", [0.04, 0.04, 0.02])]
    dx, dy = dims[0], dims[1]
    corner_reach = math.hypot(abs(px) + dx / 2.0, abs(py) + dy / 2.0)
    if corner_reach > max_reach:
        max_reach = corner_reach

robot_radius_m = max(0.18, max_reach + 0.04) # ~0.497m

obstacles = [
    {
        "id": "obs_pillar_1",
        "name": "Steel Bollard A",
        "shape": "cylinder",
        "x_m": 1.25,
        "y_m": -0.10,
        "radius_m": 0.14,
        "height_m": 0.32,
        "color": "#ef4444",
    },
    {
        "id": "obs_hazard_drum",
        "name": "Hazard Drum",
        "shape": "cylinder",
        "x_m": 0.70,
        "y_m": 1.15,
        "radius_m": 0.13,
        "height_m": 0.26,
        "color": "#f97316",
    },
    {
        "id": "obs_crate_1",
        "name": "Supply Crate South",
        "shape": "box",
        "x_m": 2.10,
        "y_m": 0.10,
        "width_m": 0.28,
        "depth_m": 0.24,
        "radius_m": 0.18,
        "height_m": 0.22,
        "color": "#d97706",
    },
    {
        "id": "obs_pillar_2",
        "name": "Telemetry Mast",
        "shape": "cylinder",
        "x_m": 1.45,
        "y_m": 1.65,
        "radius_m": 0.14,
        "height_m": 0.38,
        "color": "#ef4444",
    },
    {
        "id": "obs_gate_left",
        "name": "Corridor Gate West",
        "shape": "box",
        "x_m": 1.95,
        "y_m": 2.25,
        "width_m": 0.24,
        "depth_m": 0.24,
        "radius_m": 0.16,
        "height_m": 0.30,
        "color": "#64748b",
    },
    {
        "id": "obs_gate_right",
        "name": "Corridor Gate East",
        "shape": "box",
        "x_m": 2.85,
        "y_m": 1.05,
        "width_m": 0.24,
        "depth_m": 0.24,
        "radius_m": 0.16,
        "height_m": 0.30,
        "color": "#64748b",
    },
    {
        "id": "obs_crate_2",
        "name": "Barrier Block North",
        "shape": "box",
        "x_m": 3.35,
        "y_m": 2.05,
        "width_m": 0.26,
        "depth_m": 0.24,
        "radius_m": 0.18,
        "height_m": 0.25,
        "color": "#eab308",
    },
]

waypoints = [
    (0.0, 0.0),
    (1.10, 0.55),
    (2.15, 1.40),
    (3.20, 2.35),
]

params = {
    "repulsive_gain": 0.042,
    "sensor_lookahead_m": 0.95,
    "cruise_speed_m_s": 0.52,
    "yaw_speed_damping": 0.28,
    "max_yaw_rate_rad_s": 2.4,
}

# Temporarily test with max_steps
old_sim = wt._simulate_policy_episode

# Let's inspect line 236 in world_training.py: max_steps = 260
# Let's run wt._simulate_policy_episode with cruise_speed = 0.52
ep = wt._simulate_policy_episode(
    episode_idx=1,
    params=params,
    waypoints=waypoints,
    obstacles=obstacles,
    robot_radius_m=robot_radius_m,
    com_height_m=0.12,
    stability_margin_m=0.15,
    wheel_radius_m=0.04,
    track_width_m=0.47,
)

print("Episode with cruise 0.52:")
print("  Reached goal:", ep["reached_goal"])
print("  Goal error:", ep["goal_error_m"])
print("  Collisions:", ep["collisions"])
print("  Min clearance:", ep["min_clearance_m"])
print("  Duration:", ep["duration_s"])
