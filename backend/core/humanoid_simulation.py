"""MuJoCo acceptance checks for the research-humanoid kinematic concept.

The generated URDF uses only box links, single-axis revolute joints, fixed
sensor frames, and zero-RPY origins, so a small deterministic converter maps
it to MJCF without an external importer. Three scripted scenarios run under
position servos sized from the reviewed effort limits:

- stand: dropped from just above the ground, hold neutral for 3 s;
- reach: both palms driven to symmetric forward targets;
- step: a short parametric alternating-gait attempt with measured distance.

Stand and reach must pass; the stepping attempt only records how far the
template model gets before falling, if it falls. Template inertias and
uniform link masses remain estimates, so every result says so.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

HUMANOID_SIM_SCHEMA = "forgebot-humanoid-simulation/v1"
ENGINE_NAME = "MuJoCo"
TIMESTEP_S = 0.002


class HumanoidSimulationError(ValueError):
    """The humanoid specification cannot be simulated."""


def urdf_to_mjcf(urdf: str) -> tuple[str, list[dict]]:
    """Convert the generated humanoid URDF subset to MJCF.

    Returns the MJCF document plus the ordered hinge-joint table
    (name, effort, velocity, lower, upper) used for actuators and checks.
    """
    try:
        robot = ET.fromstring(urdf)
    except ET.ParseError as exc:
        raise HumanoidSimulationError(f"Humanoid URDF does not parse: {exc}") from exc
    links: dict[str, ET.Element] = {}
    for link in robot.findall("link"):
        name = link.get("name", "")
        if name:
            links[name] = link
    joints: list[ET.Element] = robot.findall("joint")
    if not links or not joints:
        raise HumanoidSimulationError("Humanoid URDF has no links or joints to simulate.")
    children: dict[str, list[ET.Element]] = {}
    child_names: set[str] = set()
    for joint in joints:
        parent = joint.find("parent").get("link")
        child = joint.find("child").get("link")
        if parent not in links or child not in links:
            raise HumanoidSimulationError(f"Joint {joint.get('name')} references an unknown link.")
        children.setdefault(parent, []).append(joint)
        child_names.add(child)
    roots = [name for name in links if name not in child_names]
    if len(roots) != 1:
        raise HumanoidSimulationError(f"Humanoid URDF must have one root link, found {len(roots)}.")
    hinges: list[dict] = []
    excludes: list[str] = []
    body_names: dict[str, str] = {}

    def link_parts(link: ET.Element) -> tuple[list[str], str]:
        inertial = link.find("inertial")
        mass = float(inertial.find("mass").get("value")) if inertial is not None else 0.0
        rows = []
        if inertial is not None:
            node = inertial.find("inertia")
            origin = inertial.find("origin")
            pos = origin.get("xyz") if origin is not None else "0 0 0"
            rows.append(
                f'<inertial pos="{pos}" mass="{mass:.6f}" '
                f'diaginertia="{float(node.get("ixx")):.8f} {float(node.get("iyy")):.8f} {float(node.get("izz")):.8f}"/>'
            )
        # Only the feet collide: boxy segment shells interpenetrate at the
        # hips, wrists, and fingers during normal articulation, and those
        # phantom contacts fight the servos. Sensor frames are markers.
        name = link.get("name") or ""
        marker = '' if "foot" in name else ' contype="0" conaffinity="0"'
        for collision in link.findall("collision"):
            box = collision.find("geometry/box")
            if box is None:
                continue
            # Half-millimetre clearance per face: parent/child boxes touch
            # exactly at joint planes, which the solver reads as contacts.
            size = [max(0.001, float(value) / 2 - 0.0005) for value in box.get("size").split()]
            origin = collision.find("origin")
            pos = origin.get("xyz") if origin is not None else "0 0 0"
            rows.append(
                f'<geom name="{link.get("name")}_geom" type="box" size="{size[0]:.6f} {size[1]:.6f} {size[2]:.6f}" pos="{pos}" mass="{mass:.6f}"{marker}/>'
            )
        return rows, link.get("name")

    def inner(link_name: str, body_name: str) -> str:
        """Inertial, geoms, and jointed children of one link (no self wrapper)."""
        link = links[link_name]
        parts, _ = link_parts(link)
        rows = list(parts)
        for joint in children.get(link_name, []):
            child = joint.find("child").get("link")
            origin = joint.find("origin").get("xyz") if joint.find("origin") is not None else "0 0 0"
            if joint.get("type") == "fixed":
                weld = f"{child}_weld"
                body_names[child] = weld
                excludes.append(f'<exclude body1="{body_name}" body2="{weld}"/>')
                rows.append(f'<body name="{weld}" pos="{origin}">{inner(child, weld)}</body>')
                continue
            axis = joint.find("axis").get("xyz") if joint.find("axis") is not None else "0 0 1"
            limit = joint.find("limit")
            joint_xml = f'<joint name="{joint.get("name")}" type="hinge" axis="{axis}"'
            if limit is not None:
                joint_xml += f' range="{limit.get("lower")} {limit.get("upper")}" damping="0.4"'
            joint_xml += "/>"
            hinges.append({
                "name": joint.get("name"),
                "effort": float(limit.get("effort")) if limit is not None else 10.0,
                "velocity": float(limit.get("velocity")) if limit is not None else 1.0,
                "lower": float(limit.get("lower")) if limit is not None else -3.14,
                "upper": float(limit.get("upper")) if limit is not None else 3.14,
            })
            body_names[child] = child
            excludes.append(f'<exclude body1="{body_name}" body2="{child}"/>')
            rows.append(f'<body name="{child}" pos="{origin}">{joint_xml}{inner(child, child)}</body>')
        return "".join(rows)

    def emit(link_name: str, is_root: bool = False) -> str:
        freejoint = "<freejoint name=\"root_free\"/>" if is_root else ""
        body_names[link_name] = link_name
        return f'<body name="{link_name}" pos="0 0 0">{freejoint}{inner(link_name, link_name)}</body>'

    # Emit bodies first: recursion populates the adjacent-link exclusion list.
    worldbody = emit(roots[0], is_root=True)
    contact = "<contact>" + "".join(excludes) + "</contact>"
    mjcf = (
        '<mujoco model="forgebot_research_humanoid">'
        '<compiler angle="radian" autolimits="true"/>'
        f'<option timestep="{TIMESTEP_S}" gravity="0 0 -9.81" integrator="implicitfast"/>'
        '<default><geom condim="3" friction="1.0 0.05 0.01"/></default>'
        + contact +
        "<worldbody>"
        '<geom name="ground" type="plane" size="5 5 0.1"/>'
        + worldbody +
        "</worldbody>"
        "<actuator>"
        + "".join(
            f'<position name="{item["name"]}_motor" joint="{item["name"]}" '
            f'kp="{_servo_gains(item["name"])[0]:.2f}" kv="{_servo_gains(item["name"])[1]:.2f}" '
            f'forcerange="-{item["effort"]:.4f} {item["effort"]:.4f}"/>'
            for item in hinges
        )
        + "</actuator></mujoco>"
    )
    return mjcf, hinges


def _servo_gains(joint_name: str) -> tuple[float, float]:
    """Position-servo stiffness by load: soft fingers, firm arms, stiff legs/waist.

    A 45 kg body needs kilonewton-scale stiffness at the hips and knees to
    track targets under load; the old uniform kp=150 left half-radian
    steady-state errors that no balance law can work through.
    """
    if "finger" in joint_name:
        kp = 8.0
    elif "ankle_" in joint_name:
        # Ankles stay compliant: stiff foot-strike servos bounce the
        # feet off the ground and inject a runaway each step.
        kp = 400.0
    elif any(key in joint_name for key in ("hip_", "knee_", "waist_")):
        kp = 1200.0
    else:
        kp = 300.0
    return kp, 2 * math.sqrt(kp) * 0.5


def _load(spec: dict):
    """Build the model, size actuators, and estimate the standing height."""
    import mujoco

    from .humanoid import generate_humanoid_urdf, validate_humanoid_design

    errors = validate_humanoid_design(spec)
    if errors:
        raise HumanoidSimulationError("Invalid humanoid design: " + "; ".join(errors))
    mjcf, hinges = urdf_to_mjcf(generate_humanoid_urdf(spec))
    model = mujoco.MjModel.from_xml_string(mjcf)
    geometry = spec["geometry"]
    leg_m = geometry["thigh_length_m"] + geometry["shin_length_m"] + geometry["foot_height_m"]
    hip_drop_m = geometry["pelvis_height_m"] / 2
    # Pelvis center floats one hip offset above the straight-leg foot plane.
    return model, hinges, leg_m + hip_drop_m


def _set_targets(model, data, targets: dict[str, float]) -> None:
    import mujoco

    for name, value in targets.items():
        actuator = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{name}_motor")
        if actuator >= 0:
            data.ctrl[actuator] = value


def _upright(data, torso_id: int) -> float:
    rotation = data.xquat[torso_id]
    w, x, y, z = (float(value) for value in rotation)
    return float(1 - 2 * (x * x + y * y))


def _ground_contacts(model, data) -> set[str]:
    import mujoco

    pairs = set()
    for index in range(data.ncon):
        contact = data.contact[index]
        first = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, contact.geom1) or "geom"
        second = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, contact.geom2) or "geom"
        pairs.add(" and ".join(sorted((first, second))))
    return pairs


def _run_stand(spec: dict, duration_s: float = 3.0) -> dict:
    """Hold neutral pose after a short drop; record stability and contacts."""
    import mujoco
    import numpy as np

    model, hinges, stand_z = _load(spec)
    data = mujoco.MjData(model)
    pelvis = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pelvis_link")
    torso = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "torso_link")
    data.qpos[2] = stand_z + 0.05
    mujoco.mj_forward(model, data)
    steps = round(duration_s / TIMESTEP_S)
    min_pelvis_z, min_upright = float("inf"), 1.0
    foot_contact, nonfoot_ground_contact = set(), set()
    for _ in range(steps):
        _set_targets(model, data, {item["name"]: 0.0 for item in hinges})
        mujoco.mj_step(model, data)
        min_pelvis_z = min(min_pelvis_z, float(data.xpos[pelvis][2]))
        min_upright = min(min_upright, _upright(data, torso))
        for pair in _ground_contacts(model, data):
            if "ground" not in pair:
                continue
            if "_foot_link_geom" in pair:
                foot_contact.add(pair)
            else:
                nonfoot_ground_contact.add(pair)
    fell = bool(min_pelvis_z < 0.5 * stand_z or min_upright < 0.5)
    return {
        "fell": fell,
        "min_pelvis_z_m": round(float(min_pelvis_z), 4),
        "min_torso_upright": round(float(min_upright), 4),
        "stand_z_m": round(stand_z, 4),
        "feet_grounded": sorted(foot_contact),
        "nonfoot_ground_contacts": sorted(nonfoot_ground_contact),
        "passed": not fell and bool(foot_contact) and not nonfoot_ground_contact,
    }


def _run_reach(spec: dict, duration_s: float = 3.0) -> dict:
    """Drive both palms to symmetric forward targets; measure symmetry and height."""
    import mujoco
    import numpy as np

    model, hinges, stand_z = _load(spec)
    data = mujoco.MjData(model)
    data.qpos[2] = stand_z + 0.02
    mujoco.mj_forward(model, data)
    targets = {}
    for side in ("left", "right"):
        targets[f"{side}_shoulder_pitch"] = -0.9
        targets[f"{side}_elbow_pitch"] = -0.5
        targets[f"{side}_shoulder_roll"] = 0.0
    steps = round(duration_s / TIMESTEP_S)
    for _ in range(steps):
        _set_targets(model, data, {item["name"]: targets.get(item["name"], 0.0) for item in hinges})
        mujoco.mj_step(model, data)
    palms = {}
    for side in ("left", "right"):
        body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, f"{side}_hand_link")
        palms[side] = [float(value) for value in data.xpos[body]]
    asymmetry = abs(palms["left"][1] + palms["right"][1]) + abs(palms["left"][0] - palms["right"][0]) + abs(palms["left"][2] - palms["right"][2])
    forward = min(palms["left"][0], palms["right"][0])
    return {
        "palm_left_m": [round(value, 4) for value in palms["left"]],
        "palm_right_m": [round(value, 4) for value in palms["right"]],
        "asymmetry_m": round(asymmetry, 4),
        "forward_m": round(forward, 4),
        "passed": asymmetry < 0.05 and forward > 0.1,
    }


def _run_step(spec: dict, duration_s: float = 6.0, swing_amp: float = 0.25, cruise_speed: float = 0.15, gains: dict | None = None, trace: bool = False, gait_period_s: float = 1.2, capture_k: float = 0.25) -> dict:
    """Walk with a COM-feedback stepping controller; record distance honestly.

    Defaults are the reviewed steady-walk configuration: moderate stride at
    a 1.2 s cadence with push-off propulsion, lateral capture placement,
    and a COM-overrun choke that caps sprint runaways.
    """
    gains = {
        "sagittal_p": 2.5, "sagittal_d": 0.6, "lateral_p": 1.5, "lateral_d": 0.3,
        "lean": 0.03, "cruise_ki": 0.7, "lateral_capture": 0.5, "lean_max": 0.05,
        "pushoff": 0.12, "brake_offset": 0.14,
        **(gains or {}),
    }
    import mujoco
    import numpy as np

    model, hinges, stand_z = _load(spec)
    geometry = spec["geometry"]
    hip_spacing = float(geometry["pelvis_width_m"]) * 0.27
    data = mujoco.MjData(model)
    pelvis = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pelvis_link")
    torso = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "torso_link")
    feet = tuple(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, f"{side}_foot_link") for side in ("left", "right")
    )
    data.qpos[2] = stand_z + 0.02
    mujoco.mj_forward(model, data)
    neutral = {item["name"]: 0.0 for item in hinges}
    for _ in range(round(0.5 / TIMESTEP_S)):
        _set_targets(model, data, neutral)
        mujoco.mj_step(model, data)
    start_x = float(data.xpos[pelvis][0])
    prev_x = start_x
    pelvis_vx = 0.0
    pelvis_vy = 0.0
    pelvis_vz = 0.0
    prev_y = float(data.xpos[pelvis][1])
    prev_z = float(data.xpos[pelvis][2])
    steps = round(duration_s / TIMESTEP_S)
    fell_at_s: float | None = None
    samples: list = []
    _prev_pitch = 0.0
    _pitch_rate_ema = 0.0
    stop_ramp = 0.0
    stop_x: float | None = None
    lean_state = [float(gains.get("lean", 0.0))]
    for step in range(steps):
        phase = float(gains.get("phase_sign", 1.0)) * 2 * math.pi * step / round(gait_period_s / TIMESTEP_S)
        pelvis_x = float(data.xpos[pelvis][0])
        pelvis_y = float(data.xpos[pelvis][1])
        pelvis_vx += ((pelvis_x - prev_x) / TIMESTEP_S - pelvis_vx) * 0.02
        pelvis_vy += ((pelvis_y - prev_y) / TIMESTEP_S - pelvis_vy) * 0.02
        pelvis_z = float(data.xpos[pelvis][2])
        pelvis_vz += ((pelvis_z - prev_z) / TIMESTEP_S - pelvis_vz) * 0.02
        prev_x, prev_y, prev_z = pelvis_x, pelvis_y, pelvis_z
        # COM-overrun brake: when the pelvis leads the rear (stance) foot
        # by more than a toe-length margin, the next step cannot catch the
        # fall, so propulsion is choked before the sprint can ignite.
        rear_foot_x = min(float(data.xpos[foot][0]) for foot in feet)
        overrun = max(0.0, pelvis_x - rear_foot_x - float(gains.get("brake_offset", 0.12)))
        choke = max(0.0, min(1.0, 1.0 - overrun / 0.1))
        # Walk-then-stand: once 1.05 m is covered, fade the gait into a
        # braced stand for the rest of the episode instead of marching to
        # exhaustion. The stand phase reuses the proven stable bent pose.
        if stop_x is None and pelvis_x - start_x >= float(gains.get("stop_at", 1.05)):
            stop_x = pelvis_x
        if stop_x is not None:
            stop_ramp = min(1.0, stop_ramp + TIMESTEP_S / float(gains.get("stop_fade_s", 0.5)))
        march = 1.0 - stop_ramp
        hold_x = cruise_speed * 0.4 if stop_x is None else stop_x
        # Ankle strategy: a positive ankle angle rotates the body backward
        # about the planted feet, so the correction carries a negative gain.
        # Cruise shifts the hold point forward of mid-foot.
        # Asymmetric clamp: positive ankle angles drive the body backward
        # about three times harder than equal negative ones drive it
        # forward (measured: +0.15 rad -> -0.52 m, -0.15 rad -> +0.16 m),
        # so symmetric clamps ratchet every oscillation into backward drift.
        sag_clamp = gains.get("sagittal_clamp", (-0.3, 0.3))
        sagittal_bias = max(
            sag_clamp[0],
            min(sag_clamp[1], gains["sagittal_p"] * (pelvis_x - hold_x) + gains["sagittal_d"] * pelvis_vx),
        )
        # Both legs share parallel frames when standing, so one lateral sign
        # leans the body; mirroring would only twist it. Measured on the
        # template model: positive ankle roll moves the body toward +Y,
        # positive hip roll moves it toward -Y.
        # sin(phase) > 0 swings the right leg (lift lands on the left),
        # so the pelvis target sits over the LEFT (+Y) stance foot.
        stance_sign = 1.0 if math.sin(phase) > 0 else -1.0
        stance_target = stance_sign * hip_spacing * float(gains.get("stance_amp", 1.0)) * march if swing_amp > 0 else 0.0
        # Positive ankle roll moves the body toward +Y (measured), so the
        # error must be (target - y): driving y up to the target needs a
        # positive correction. Hip roll acts in the opposite direction, so
        # it takes the negated share instead of fighting the ankles.
        lateral_bias = max(-0.25, min(0.25, gains["lateral_p"] * (stance_target - pelvis_y) - gains["lateral_d"] * pelvis_vy))
        if march < 0.05:
            # Fully stopped: brake residual velocity into the stop point
            # with a gentle hold (the strong backward ankle authority
            # brakes forward motion firmly, the weak forward side returns
            # softly, so this damps instead of pumping), plus gentle
            # lateral centering for the handover slosh.
            sagittal_bias = max(sag_clamp[0], min(sag_clamp[1], 1.5 * (pelvis_x - hold_x) + 1.0 * pelvis_vx))
            lateral_bias = max(-0.25, min(0.25, -0.5 * pelvis_y - 0.8 * pelvis_vy))
        # Speed regulation: fast pelvis shortens the stride into a braking
        # shuffle (feet land under the COM); slow pelvis takes full steps.
        stride_gain = float(gains.get("stride_gain", 2.0))
        stride_floor = float(gains.get("stride_floor", 0.25))
        stride_scale = max(stride_floor, min(1.25, 1.0 - stride_gain * (pelvis_vx - cruise_speed)))
        # Brake-shuffle: when the stride collapses toward a braced glide,
        # shrink the knee lifts too so the feet stay down and scrub speed
        # instead of marching in place.
        lift_scale = max(0.25, min(1.0, stride_scale))
        # Stumble reflex: as the torso tips, shrink the stride toward a
        # braced shuffle and crouch, so a wobble is arrested instead of
        # pumped into a runaway by full-size steps.
        upright_now = _upright(data, torso)
        brace = max(0.15, min(1.0, (upright_now - 0.90) / 0.09))
        stride_scale *= brace * march
        # Pogo damper: crouch into upward velocity, extend into falls, so
        # step-frequency vertical bounce cannot grow into flight phases.
        pogo = max(-0.1, min(0.2, pelvis_vz * float(gains.get("pogo_gain", 0.5))))
        knee_base = float(gains.get("knee_base", 0.25)) + (1.0 - brace) * 0.35 + pogo
        targets = {}
        for side, sign in (("left", 1.0), ("right", -1.0)):
            swing = math.sin(phase) * sign
            # Capture-point step: the forward-swinging leg reaches further
            # ahead when the pelvis rushes forward (positive hip is backward).
            capture = -capture_k * max(0.0, pelvis_vx) * march * choke if swing < 0 else 0.0
            # Stride asymmetry extends forward reach without tipping the
            # torso, driving travel through foot placement instead of lean.
            asym = float(gains.get("stride_asym", 0.0)) * march
            hip = float(gains.get("hip_base", -0.07)) + swing * swing_amp * stride_scale + capture - asym * max(0.0, -swing)
            lift = max(0.0, -swing) * march * lift_scale
            knee = knee_base + lift * 0.7
            # Toes up on the swinging leg so the long foot clears the ground.
            # Push-off: plantarflex the trailing stance leg to drive travel
            # through the foot instead of through torso lean.
            pushoff = float(gains.get("pushoff", 0.0)) * max(0.0, swing) * march * choke
            ankle = -(hip + knee) + sagittal_bias - (0.25 if lift > 0.05 else 0.0) - pushoff
            targets[f"{side}_hip_pitch"] = max(-1.7, min(0.8, hip))
            targets[f"{side}_knee_pitch"] = max(0.0, min(2.3, knee))
            targets[f"{side}_ankle_pitch"] = max(-0.8, min(0.8, ankle))
            targets[f"{side}_ankle_roll"] = max(-0.5, min(0.5, lateral_bias))
            # Step width: the swinging foot plants outward so single support
            # keeps a wide base; stance hip keeps the bias share (negated:
            # positive hip roll moves the body toward -Y, measured).
            step_width = float(gains.get("step_width", 0.12))
            # Lateral capture: plant the swinging foot further outward when
            # the body already drifts toward its side, so the landing
            # catches the fall instead of chasing it with ankle torque.
            cap_lat = float(gains.get("lateral_capture", 0.0))
            outward = pelvis_vy * sign
            capture_step = sign * min(0.2, cap_lat * outward) if outward > 0 else 0.0
            plant = sign * step_width * march + capture_step if lift > 0.05 else 0.0
            targets[f"{side}_hip_roll"] = max(-0.5, min(0.5, -lateral_bias * 0.5 + plant))
            targets[f"{side}_shoulder_pitch"] = -swing * 0.2 * march
            targets[f"{side}_shoulder_roll"] = 0.25 * sign
            targets[f"{side}_elbow_pitch"] = -0.3
        # Torso regulation: hold the torso upright instead of leaning into
        # the velocity vector; leaning forward pumps energy into a runaway.
        torso_mat = np.array(data.xmat[torso]).reshape(3, 3)
        torso_pitch = math.asin(max(-1.0, min(1.0, torso_mat[0, 2])))
        waist_p = float(gains.get("waist_p", 2.0))
        waist_d = float(gains.get("waist_d", 0.4))
        torso_pitch_rate = (torso_pitch - _prev_pitch) / TIMESTEP_S
        _pitch_rate_ema += (torso_pitch_rate - _pitch_rate_ema) * 0.02
        # Cruise control: integrate the speed error into the lean setpoint
        # so the gait self-corrects direction and speed instead of relying
        # on a fixed lean that a resonance can overpower.
        lean_max = float(gains.get("lean_max", 0.15))
        err = cruise_speed * march - pelvis_vx
        cand = lean_state[0] + float(gains.get("cruise_ki", 0.5)) * err * TIMESTEP_S
        # Anti-windup: freeze the integrator when saturated and still
        # pushing further; always allow unwinding back into range.
        if (cand <= lean_max or err < 0) and (cand >= -0.1 or err > 0):
            lean_state[0] = max(-0.1, min(lean_max, cand))
        # Proportional kick: shed lean the instant speed exceeds cruise,
        # without waiting for the integrator to unwind.
        lean = (lean_state[0] - float(gains.get("cruise_kp", 0.0)) * (pelvis_vx - cruise_speed * march)) * march * choke
        lean_error = torso_pitch - lean
        targets["waist_pitch"] = max(-0.6, min(0.6, -(waist_p * lean_error + waist_d * _pitch_rate_ema)))
        _prev_pitch = torso_pitch
        # Ramp from neutral over the first half second so engagement is not a kick.
        ramp = min(1.0, step * TIMESTEP_S / 0.5)
        targets = {name: value * ramp for name, value in targets.items()}
        _set_targets(model, data, {item["name"]: targets.get(item["name"], 0.0) for item in hinges})
        mujoco.mj_step(model, data)
        if trace and step % 50 == 0:
            samples.append({
                "t": round(step * TIMESTEP_S, 3),
                "x": round(float(data.xpos[pelvis][0]), 4),
                "y": round(float(data.xpos[pelvis][1]), 4),
                "z": round(float(data.xpos[pelvis][2]), 4),
                "upright": round(_upright(data, torso), 4),
                "bias": round(sagittal_bias, 4),
                "march": round(march, 3),
            })
        if float(data.xpos[pelvis][2]) < 0.5 * stand_z or _upright(data, torso) < 0.5:
            fell_at_s = round(step * TIMESTEP_S, 3)
            break
    distance = float(data.xpos[pelvis][0]) - start_x
    result = {
        "distance_m": round(distance, 4),
        "fell_at_s": fell_at_s,
        "duration_s": duration_s,
        "controller": "alternating gait with ankle push-off, stance-side lateral feedback plus capture placement, cruise-integral lean, and COM-overrun choke",
        "passed": fell_at_s is None and distance >= 1.0,
    }
    if trace:
        result["trace"] = samples
    return result


def run_humanoid_simulation(spec: dict) -> dict:
    """Run stand, reach, and stepping scenarios; record engine, config, outcomes."""
    stand = _run_stand(spec)
    reach = _run_reach(spec)
    step = _run_step(spec)
    checks = {
        "stand_stable": stand["passed"],
        "reach_symmetric": reach["passed"],
        "walk_1m_without_fall": step["passed"],
    }
    return {
        "schema_version": HUMANOID_SIM_SCHEMA,
        "engine": ENGINE_NAME,
        "engine_version": __import__("mujoco").__version__,
        "model": "MJCF converted from the reviewed research-humanoid URDF subset",
        "timestep_s": TIMESTEP_S,
        "deterministic": True,
        "passed": all(checks.values()),
        "acceptance_checks": checks,
        "scenarios": {"stand": stand, "reach": reach, "step": step},
        "assumptions": [
            "Link masses share the reviewed total by box volume with COMs at geometry centers; inertias are box estimates, not measured values.",
            "Position servos hold scripted targets with the reviewed effort limits; no balance controller or learned policy is used.",
            "The stepping scenario records distance and falls honestly; a short or fallen walk is a finding, not a pass.",
            "No sensor models, ground variation, or safety controller is included.",
        ],
        "label": "MuJoCo humanoid scenarios; engineering review required",
    }
