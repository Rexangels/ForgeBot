import os, sys, json
sys.path.insert(0, 'backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from core.models import Artifact
from core.image_design import _component_is_wheel

def ground_drivetrain(design):
    components = design.get("components", [])
    by_id = {p["id"]: p for p in components}
    notes = []

    # 1. Identify chassis
    chassis = next(
        (
            p for p in components
            if any(k in p.get("id", "").lower() for k in ("chassis_plate", "chassis_lower", "chassis", "base_plate", "bottom_deck", "deck"))
            or (p.get("functional_category") == "structure" and not p.get("parent_id"))
        ),
        None,
    )
    if not chassis:
        return notes

    # 2. Identify drive wheels and motors
    wheels = [p for p in components if _component_is_wheel(p)]
    drive_wheels = [w for w in wheels if not any(c in w.get("id", "").lower() for c in ("caster", "omni"))]
    if len(drive_wheels) < 2:
        return notes

    # Sort wheels: left (y > 0) and right (y < 0)
    w_left = next((w for w in drive_wheels if float(w["position_m"][1]) > 0), drive_wheels[0])
    w_right = next((w for w in drive_wheels if float(w["position_m"][1]) < 0), drive_wheels[1])

    # Find motors
    motors = [
        p for p in components
        if any(k in p.get("id", "").lower() or k in p.get("role", "").lower() for k in ("motor", "actuator", "drive"))
        and not _component_is_wheel(p)
    ]
    m_left = by_id.get(w_left.get("parent_id")) or next((m for m in motors if "left" in m.get("id", "").lower() or float(m["position_m"][1]) > 0), None)
    m_right = by_id.get(w_right.get("parent_id")) or next((m for m in motors if "right" in m.get("id", "").lower() or float(m["position_m"][1]) < 0), None)

    # 3. Calculate wheel radius
    # For cylinder wheels, diameter is usually dims[0] or dims[1], length/thickness is dims[2] (or vice versa)
    w_r = max(float(w_left["dimensions_m"][0]), float(w_left["dimensions_m"][1])) / 2.0
    w_thick = min(float(w_left["dimensions_m"][i]) for i in range(3))

    ch_dims = [float(v) for v in chassis["dimensions_m"]]
    ch_half_w = ch_dims[1] / 2.0
    ch_thick = ch_dims[2]

    # Motor dimensions
    m_dims = [float(v) for v in m_left["dimensions_m"]] if m_left else [0.06, 0.04, 0.04]
    m_h = m_dims[2]
    m_w = m_dims[1]

    # Ground contact: drive wheel bottom rests at z = 0
    wheel_z = round(w_r, 4)
    w_left["position_m"][2] = wheel_z
    w_right["position_m"][2] = wheel_z

    # Motor axle aligns with wheel center in X and Z
    wheel_x = float(w_left["position_m"][0])
    if m_left:
        m_left["position_m"][0] = wheel_x
        m_left["position_m"][2] = wheel_z
        # Lateral: motor inside chassis edge, wheel outside motor
        m_y = round(ch_half_w - m_w / 2.0, 4)
        m_left["position_m"][1] = m_y
        w_left["position_m"][1] = round(m_y + m_w / 2.0 + w_thick / 2.0 + 0.005, 4)

    if m_right:
        m_right["position_m"][0] = wheel_x
        m_right["position_m"][2] = wheel_z
        m_y = round(-(ch_half_w - m_w / 2.0), 4)
        m_right["position_m"][1] = m_y
        w_right["position_m"][1] = round(m_y - (m_w / 2.0 + w_thick / 2.0 + 0.005), 4)

    # 4. Position chassis on top of motors
    new_ch_bottom = wheel_z + m_h / 2.0
    new_ch_z = round(new_ch_bottom + ch_thick / 2.0, 4)
    old_ch_z = float(chassis["position_m"][2])
    delta_z = new_ch_z - old_ch_z

    # Shift chassis and deck-mounted components by delta_z
    for p in components:
        if p["id"] in (w_left["id"], w_right["id"], m_left["id"] if m_left else "", m_right["id"] if m_right else ""):
            continue
        if "caster" in p.get("id", "").lower():
            continue
        p["position_m"][2] = round(float(p["position_m"][2]) + delta_z, 4)

    # 5. Ground casters at z = 0
    casters = [p for p in components if "caster" in p.get("id", "").lower()]
    for caster in casters:
        c_r = min(float(v) for v in caster["dimensions_m"]) / 2.0
        caster["position_m"][2] = round(c_r, 4)
        # Check gap to chassis bottom
        c_top = 2.0 * c_r
        gap = new_ch_bottom - c_top
        if gap > 0.01:
            # Check if there is a caster bracket / standoff
            bracket = next((p for p in components if "brk" in p["id"].lower() and "caster" in p["id"].lower()), None)
            if not bracket:
                # Add or extend a caster post
                post_id = f"post_{caster['id']}"
                if post_id not in by_id:
                    post = {
                        "id": post_id,
                        "name": f"{caster.get('name', 'Caster')} Standoff Post",
                        "role": "structural standoff coupling caster to chassis",
                        "functional_category": "structure",
                        "mount_intent": "rigid_mount",
                        "parent_id": chassis["id"],
                        "geometry": "cylinder",
                        "dimensions_m": [0.02, 0.02, round(gap, 4)],
                        "position_m": [float(caster["position_m"][0]), float(caster["position_m"][1]), round(c_top + gap / 2.0, 4)],
                        "orientation_rpy": [0.0, 0.0, 0.0],
                        "color": "#718096",
                        "confidence": 0.95,
                        "visible_vs_inferred": "inferred_structural_support",
                    }
                    components.append(post)
                    by_id[post_id] = post
                    caster["parent_id"] = post_id
                    notes.append(f"Added structural standoff '{post_id}' to mount caster flush to chassis deck.")

    notes.append(f"Mechanically grounded drivetrain: coupled wheels to motors and mounted chassis flush at Z={new_ch_z:.3f} m.")
    return notes

spec_art = Artifact.objects.get(id=318)
spec = json.loads(spec_art.body)
notes = ground_drivetrain(spec)
print("\nNotes:", notes)
print("\nUpdated components:")
for p in spec["components"]:
    print(f"  {p['id']:22}: pos={p['position_m']}, dims={p['dimensions_m']}, parent={p.get('parent_id')}")

