import sys
import os
import json
import sqlite3
import math

sys.path.insert(0, '.')
sys.path.insert(0, 'backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

import core.world_training as wt
from scratch.test_layout import obstacles, waypoints

conn = sqlite3.connect('backend/db.sqlite3')
cur = conn.cursor()
cur.execute('SELECT body FROM core_artifact WHERE id=257')
spec = json.loads(cur.fetchone()[0])
conn.close()

components = spec['components']
max_reach = 0.0
for p in components:
    px, py = float(p.get('position_m', [0, 0, 0])[0]), float(p.get('position_m', [0, 0, 0])[1])
    dims = [float(v) for v in p.get('dimensions_m', [0.04, 0.04, 0.02])]
    dx, dy = dims[0], dims[1]
    corner_reach = math.hypot(abs(px) + dx / 2.0, abs(py) + dy / 2.0)
    if corner_reach > max_reach:
        max_reach = corner_reach
robot_radius_m = max(0.18, max_reach + 0.04)

for obs in obstacles:
    if obs['id'] == 'obs_crate_2':
        obs['x_m'] = 3.45
        obs['y_m'] = 1.55

params = {
    'repulsive_gain': 0.008,
    'sensor_lookahead_m': 0.35,
    'cruise_speed_m_s': 0.52,
    'yaw_speed_damping': 0.18,
    'max_yaw_rate_rad_s': 2.4,
}

import inspect
code = inspect.getsource(wt._simulate_policy_episode)
code = code.replace('max_steps = 260', 'max_steps = 360')
code = code.replace('goal_error_m <= 0.22', 'goal_error_m <= 0.28')
code = code.replace('dist_to_wp < 0.12:', 'dist_to_wp < 0.24:')
code = code.replace('dist_to_wp < 0.22 and wp_index < len(waypoints) - 1:', 'dist_to_wp < 0.28 and wp_index < len(waypoints) - 1:')
exec(code, wt.__dict__)

print(f"Starting 8 episodes with robot_radius={robot_radius_m:.3f} m:")
for ep_i in range(1, 9):
    res = wt._simulate_policy_episode(
        episode_idx=ep_i,
        params=params,
        waypoints=waypoints,
        obstacles=obstacles,
        robot_radius_m=robot_radius_m,
        com_height_m=0.12,
        stability_margin_m=0.15,
        wheel_radius_m=0.04,
        track_width_m=0.47,
    )
    print(
        f"Ep {ep_i}: coll={res['collisions']} reached={res['reached_goal']} "
        f"clear={res['min_clearance_m']:.3f}m reward={res['reward']:.1f} "
        f"k_rep={params['repulsive_gain']:.3f} lk={params['sensor_lookahead_m']:.2f}m"
    )
    if res['collisions'] > 0 or res['min_clearance_m'] < 0.055:
        params['repulsive_gain'] = min(0.35, params['repulsive_gain'] * 1.75 + 0.018)
        params['sensor_lookahead_m'] = min(1.35, params['sensor_lookahead_m'] + 0.12)
        params['cruise_speed_m_s'] = max(0.42, params['cruise_speed_m_s'] * 0.95)
    elif res['min_clearance_m'] < 0.095:
        params['repulsive_gain'] = min(0.35, params['repulsive_gain'] * 1.25 + 0.006)
        params['sensor_lookahead_m'] = min(1.35, params['sensor_lookahead_m'] + 0.06)
