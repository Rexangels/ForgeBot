import json
import sqlite3
import math

conn = sqlite3.connect('backend/db.sqlite3')
cur = conn.cursor()
cur.execute('SELECT body FROM core_artifact WHERE id=257')
spec = json.loads(cur.fetchone()[0])
for p in spec['components']:
    px, py = float(p['position_m'][0]), float(p['position_m'][1])
    dx, dy = float(p['dimensions_m'][0]), float(p['dimensions_m'][1])
    r = math.hypot(abs(px) + dx/2.0, abs(py) + dy/2.0)
    print(f"{p['id']:25s}: pos=({px:.3f}, {py:.3f}), dims=({dx:.3f}, {dy:.3f}), reach={r:.3f} m")
