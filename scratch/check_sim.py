import sqlite3
import json

conn = sqlite3.connect("backend/db.sqlite3")
cur = conn.cursor()
cur.execute("SELECT body FROM core_artifact WHERE id=262")
data = json.loads(cur.fetchone()[0])
ot = data.get("obstacle_training", {})
best = ot.get("best_episode", {})
traj = best.get("trajectory", [])
print("Traj points:", len(traj))
obstacles = ot.get("obstacles", [])
for obs in obstacles:
    print(f"Obstacle {obs['id']}: ({obs.get('x_m')}, {obs.get('y_m')}) r={obs.get('radius_m')} w={obs.get('width_m')}")

print("\nTrajectory sample points:")
for p in traj[::10]:
    print(f"t={p.get('t', 0):.2f} x={p.get('x', 0):.2f} y={p.get('y', 0):.2f} clear={p.get('clearance_m', 0):.3f}")

# Check minimum distance between trajectory points and each obstacle
print("\nDistance checks:")
min_dists = {obs['id']: 999.0 for obs in obstacles}
for p in traj:
    px, py = p['x'], p['y']
    for obs in obstacles:
        ox, oy = obs['x_m'], obs['y_m']
        rad = obs.get('radius_m', 0.14)
        dist = ((px - ox)**2 + (py - oy)**2)**0.5 - rad
        if dist < min_dists[obs['id']]:
            min_dists[obs['id']] = dist

for obs_id, d in min_dists.items():
    print(f"  {obs_id}: min margin to robot center - obstacle radius = {d:.3f} m")
