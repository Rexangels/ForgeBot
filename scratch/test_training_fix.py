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
from core.world_training import run_obstacle_world_training

conn = sqlite3.connect('backend/db.sqlite3')
cur = conn.cursor()
cur.execute("SELECT body FROM core_artifact WHERE id=257")
spec = json.loads(cur.fetchone()[0])
conn.close()

res = run_obstacle_world_training(
    design=spec,
    com_height_m=0.12,
    stability_margin_m=0.15,
    tip_over_angle_deg=45.0,
    track_width_m=0.47,
    wheel_diameter_m=0.08,
    cruise_speed_m_s=0.45,
    motor_effort_nm=1.8,
)

best = res["best_episode"]
print("Best episode:", best["episode"])
print("Collisions:", best["collisions"])
print("Reached goal:", best["reached_goal"])
print("Min clearance:", best["min_clearance_m"])
print("Goal error:", best["goal_error_m"])
