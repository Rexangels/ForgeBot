import sqlite3
import json

def test_collision_and_mounting_fixes():
    conn = sqlite3.connect('backend/db.sqlite3')
    cur = conn.cursor()
    cur.execute('select body from core_artifact where id=240')
    spec = json.loads(cur.fetchone()[0])
    components = spec["components"]
    by_id = {p["id"]: p for p in components}

    chassis = by_id["chassis_lower"]
    hull = by_id["hull_main"]
    cam = by_id["head_camera"]
    lidar = by_id["lidar_puck"]
    disp = by_id["display_panel"]
    wheels = [p for p in components if "wheel" in p["id"]]
    susp = [p for p in components if "susp" in p["id"]]

    print("--- Current positions ---")
    print(f"chassis_lower: pos={chassis['position_m']}, dims={chassis['dimensions_m']}")
    print(f"hull_main: pos={hull['position_m']}, dims={hull['dimensions_m']}")
    print(f"head_camera: pos={cam['position_m']}, dims={cam['dimensions_m']}")
    print(f"lidar_puck: pos={lidar['position_m']}, dims={lidar['dimensions_m']}")
    print(f"display_panel: pos={disp['position_m']}, dims={disp['dimensions_m']}")

    # 1. Wheels vs chassis
    w_inners = [abs(float(w["position_m"][1])) - float(w["dimensions_m"][2])/2 for w in wheels]
    min_w_inner = min(w_inners)
    max_chassis_w = round(2.0 * (min_w_inner - 0.015), 4)
    print(f"\nWheel min inner clearance: {min_w_inner:.3f}m -> max chassis width: {max_chassis_w:.3f}m (current: {chassis['dimensions_m'][1]}m)")

    # 2. Camera & Lidar
    hull_x = float(hull["position_m"][0])
    hull_dx = float(hull["dimensions_m"][0])
    hull_z = float(hull["position_m"][2])
    hull_dz = float(hull["dimensions_m"][2])
    hull_top_z = round(hull_z + hull_dz / 2.0, 4)
    hull_front_x = round(hull_x + hull_dx / 2.0, 4)

    cam_dx = float(cam["dimensions_m"][0])
    cam_dz = float(cam["dimensions_m"][2])
    # Camera front aligns with hull front
    new_cam_x = round(hull_front_x - cam_dx / 2.0, 4)
    new_cam_z = round(hull_top_z + cam_dz / 2.0, 4)
    print(f"New camera pose: x={new_cam_x}, y=0.0, z={new_cam_z} (hull front={hull_front_x}, hull top={hull_top_z})")

    lidar_dx = float(lidar["dimensions_m"][0])
    lidar_dz = float(lidar["dimensions_m"][2])
    new_lidar_x = new_cam_x
    new_lidar_y = 0.0
    new_lidar_z = round(new_cam_z + cam_dz / 2.0 + lidar_dz / 2.0, 4)
    print(f"New lidar pose: x={new_lidar_x}, y={new_lidar_y}, z={new_lidar_z}")

    # 3. Suspension vs chassis bottom
    chassis_z = float(chassis["position_m"][2])
    chassis_dz = float(chassis["dimensions_m"][2])
    chassis_bot_z = round(chassis_z - chassis_dz / 2.0, 4)
    print(f"Chassis bottom z: {chassis_bot_z:.3f}m")
    for s in susp:
        s_dz = float(s["dimensions_m"][2])
        # Mount flush under chassis
        new_s_z = round(chassis_bot_z - s_dz / 2.0, 4)
        print(f"  {s['id']}: old_z={s['position_m'][2]}, new_z={new_s_z} (top={new_s_z + s_dz/2:.3f}m == chassis_bottom)")

if __name__ == "__main__":
    test_collision_and_mounting_fixes()
