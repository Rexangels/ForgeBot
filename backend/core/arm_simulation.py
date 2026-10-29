"""Reproducible MuJoCo simulation and controller tuning for the MVP arm.

The simulator consumes the reviewed arm specification and builds a local MJCF
model.  The initial task is a scripted pick-and-place trajectory; ``train``
uses deterministic coordinate search to tune position-servo gains against
that task.  This is controller tuning, not general reinforcement learning.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from .robot_arm import JOINT_NAMES, validate_arm_design

ENGINE_NAME = "MuJoCo"
ENGINE_VERSION = "3.x"
SIMULATION_SCHEMA = "forgebot-arm-simulation/v1"
DEFAULT_PICK = (0.28, 0.0, 0.055)
DEFAULT_PLACE = (0.20, 0.18, 0.055)


class ArmSimulationError(ValueError):
    """The arm specification or task cannot be simulated."""


def _number(value, label: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ArmSimulationError(f"{label} must be a number between {low} and {high}.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ArmSimulationError(f"{label} must be a number between {low} and {high}.") from exc
    if not math.isfinite(result) or not low <= result <= high:
        raise ArmSimulationError(f"{label} must be a number between {low} and {high}.")
    return result


def _target(value, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ArmSimulationError(f"{label} must contain x, y, z coordinates in metres.")
    return tuple(_number(item, f"{label} {axis}", -0.75, 0.75) for item, axis in zip(value, "xyz"))


def _joint_targets(spec: dict, point: tuple[float, float, float]) -> list[float]:
    """Solve the arm's planar two-link IK with a level tool approximation."""
    geometry = spec["geometry"]
    base_z = geometry["base_height_m"] + 0.07
    yaw = math.atan2(point[1], point[0])
    radius = math.hypot(point[0], point[1]) - geometry["wrist_length_m"] - 0.025
    height = point[2] - base_z
    upper = geometry["upper_arm_length_m"]
    forearm = geometry["forearm_length_m"]
    distance_sq = radius * radius + height * height
    cosine = (distance_sq - upper * upper - forearm * forearm) / (2 * upper * forearm)
    if not -1.0 <= cosine <= 1.0:
        raise ArmSimulationError("Pick/place target is outside the arm's two-link reach.")
    # Use the elbow-up branch. The opposite solution sweeps the upper arm
    # through the tabletop for the default low pick poses.
    elbow_math = -math.acos(cosine)
    shoulder_math = math.atan2(height, radius) - math.atan2(
        forearm * math.sin(elbow_math), upper + forearm * math.cos(elbow_math)
    )
    # URDF/MJCF axes use +Y, which rotates the arm's +X direction toward -Z.
    values = [yaw, -shoulder_math, -elbow_math, shoulder_math + elbow_math]
    for name, value, limit in zip(JOINT_NAMES, values, spec["joint_limits"]):
        if not math.radians(limit["lower_deg"]) <= value <= math.radians(limit["upper_deg"]):
            raise ArmSimulationError(f"Target requires {name} outside its reviewed joint limits.")
    return values


def default_task_targets(spec: dict) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Choose a conservative reachable pick/place pair for this reviewed arm."""
    geometry = spec["geometry"]
    reach = geometry["upper_arm_length_m"] + geometry["forearm_length_m"] + geometry["wrist_length_m"] + 0.025
    yaw = math.radians(30)
    # Lower payload prototypes often have short links and broad joint limits;
    # start closer in so the canned grasp trajectory remains controllable.
    # Heavier payload concepts use the known mid-range pose first to avoid
    # making the default reach unnecessarily close to the shoulder.
    fractions = (0.6, 0.66, 0.72, 0.78, 0.84, 0.9) if spec.get("payload_kg", 0) < 0.1 else (0.72, 0.66, 0.78, 0.84, 0.6, 0.9)
    for fraction in fractions:
        radius = min(0.48, reach * fraction)
        if radius < 0.08:
            continue
        for height in (0.055, 0.075, 0.095, 0.12, 0.045):
            pick = (radius, 0.0, height)
            place = (radius * math.cos(yaw), radius * math.sin(yaw), height)
            try:
                _joint_targets(spec, pick)
                _joint_targets(spec, place)
                return pick, place
            except ArmSimulationError:
                continue
    raise ArmSimulationError("No default pick/place pose fits this arm's current reach and joint limits. Set reachable targets or review its design.")


def _build_mjcf(spec: dict, gains: list[float], pick: tuple[float, ...]) -> str:
    g, masses, joints = spec["geometry"], spec["masses"], spec["joint_limits"]
    ranges = {
        item["name"]: f'{math.radians(item["lower_deg"]):.8f} {math.radians(item["upper_deg"]):.8f}'
        for item in joints
    }
    open_width = spec["gripper"]["jaw_opening_m"]
    grip_force_limit = spec["gripper"]["grip_force_n"] / 2
    xml = f'''<mujoco model="forgebot_tabletop_arm">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="0.002" gravity="0 0 -9.81" integrator="implicitfast"/>
  <default><joint damping="0.4"/><geom condim="3" friction="1.0 0.1 0.01"/><position kp="80"/></default>
  <worldbody>
    <geom name="table" type="box" pos="0 0 -0.025" size="0.6 0.6 0.025" rgba="0.75 0.78 0.76 1"/>
    <body name="base" pos="0 0 0">
      <geom name="base_geom" type="cylinder" size="{g['base_diameter_m']/2:.6f} {g['base_height_m']/2:.6f}" pos="0 0 {g['base_height_m']/2:.6f}" mass="{masses['base_kg']:.5f}"/>
      <body name="yaw_link" pos="0 0 {g['base_height_m']:.6f}">
        <joint name="base_yaw" type="hinge" axis="0 0 1" range="{ranges['base_yaw']}"/>
        <geom name="shoulder_geom" type="sphere" size="0.035" mass="{masses['shoulder_kg']:.5f}"/>
        <body name="upper_link" pos="0 0 0.07">
          <joint name="shoulder_pitch" type="hinge" axis="0 1 0" range="{ranges['shoulder_pitch']}"/>
          <geom name="upper_arm_geom" type="capsule" fromto="0 0 0 {g['upper_arm_length_m']:.6f} 0 0" size="0.018" mass="{masses['upper_arm_kg']:.5f}"/>
          <body name="fore_link" pos="{g['upper_arm_length_m']:.6f} 0 0">
            <joint name="elbow_pitch" type="hinge" axis="0 1 0" range="{ranges['elbow_pitch']}"/>
            <geom name="forearm_geom" type="capsule" fromto="0 0 0 {g['forearm_length_m']:.6f} 0 0" size="0.016" mass="{masses['forearm_kg']:.5f}"/>
            <body name="wrist_link" pos="{g['forearm_length_m']:.6f} 0 0">
              <joint name="wrist_pitch" type="hinge" axis="0 1 0" range="{ranges['wrist_pitch']}"/>
              <geom name="wrist_geom" type="capsule" fromto="0 0 0 {g['wrist_length_m']:.6f} 0 0" size="0.014" mass="{masses['wrist_kg']:.5f}"/>
              <body name="tool0" pos="{g['wrist_length_m']:.6f} 0 0">
                <site name="tcp" pos="0.025 0 0" size="0.004"/>
                <body name="left_finger" pos="0.03 {open_width/2:.6f} -0.025">
                  <joint name="left_gripper" type="slide" axis="0 -1 0" range="0 {open_width/2:.6f}"/>
                  <geom name="left_finger_geom" type="box" size="0.022 0.004 0.02" mass="0.025"/>
                </body>
                <body name="right_finger" pos="0.03 {-open_width/2:.6f} -0.025">
                  <joint name="right_gripper" type="slide" axis="0 1 0" range="0 {open_width/2:.6f}"/>
                  <geom name="right_finger_geom" type="box" size="0.022 0.004 0.02" mass="0.025"/>
                </body>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>
    <body name="payload" pos="{pick[0]:.6f} {pick[1]:.6f} 0.018">
      <freejoint name="payload_free"/>
      <geom name="payload_geom" type="box" size="0.015 0.015 0.015" mass="{spec['payload_kg']:.6f}" rgba="0.85 0.48 0.16 1"/>
    </body>
  </worldbody>
  <actuator>
    <position name="base_yaw_motor" joint="base_yaw" kp="{gains[0]:.4f}" kv="{2*math.sqrt(gains[0]):.4f}" forcerange="-{joints[0]['effort_nm']:.4f} {joints[0]['effort_nm']:.4f}"/>
    <position name="shoulder_pitch_motor" joint="shoulder_pitch" kp="{gains[1]:.4f}" kv="{2*math.sqrt(gains[1]):.4f}" forcerange="-{joints[1]['effort_nm']:.4f} {joints[1]['effort_nm']:.4f}"/>
    <position name="elbow_pitch_motor" joint="elbow_pitch" kp="{gains[2]:.4f}" kv="{2*math.sqrt(gains[2]):.4f}" forcerange="-{joints[2]['effort_nm']:.4f} {joints[2]['effort_nm']:.4f}"/>
    <position name="wrist_pitch_motor" joint="wrist_pitch" kp="{gains[3]:.4f}" kv="{2*math.sqrt(gains[3]):.4f}" forcerange="-{joints[3]['effort_nm']:.4f} {joints[3]['effort_nm']:.4f}"/>
    <position name="left_gripper_motor" joint="left_gripper" kp="800" kv="20" ctrlrange="0 {open_width/2:.6f}" forcelimited="true" forcerange="-{grip_force_limit:.4f} {grip_force_limit:.4f}"/>
    <position name="right_gripper_motor" joint="right_gripper" kp="800" kv="20" ctrlrange="0 {open_width/2:.6f}" forcelimited="true" forcerange="-{grip_force_limit:.4f} {grip_force_limit:.4f}"/>
  </actuator>
</mujoco>'''
    return xml


def _run_trial(spec: dict, pick: tuple[float, ...], place: tuple[float, ...], gains: list[float], capture: bool = False) -> dict:
    try:
        import mujoco
        import numpy as np
    except ImportError as exc:
        raise ArmSimulationError("MuJoCo is not installed. Install backend/requirements.txt first.") from exc

    model = mujoco.MjModel.from_xml_string(_build_mjcf(spec, gains, pick))
    data = mujoco.MjData(model)
    joint_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) for name in JOINT_NAMES]
    actuator_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{name}_motor") for name in JOINT_NAMES]
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "tcp")
    payload_body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "payload")
    gripper_joint_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) for name in ("left_gripper", "right_gripper")]
    gripper_actuator_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in ("left_gripper_motor", "right_gripper_motor")]
    dof_addresses = [model.jnt_dofadr[joint_id] for joint_id in joint_ids]
    pick_approach = (pick[0], pick[1], min(0.65, pick[2] + 0.08))
    place_approach = (place[0], place[1], min(0.65, place[2] + 0.08))
    targets = [_joint_targets(spec, point) for point in (pick_approach, pick, place_approach, place)]
    # Start above the object with jaws open, descend, close under actuator
    # force limits, then lift/transfer/release. Retention comes from simulated
    # contact and friction only; no ideal grasp constraint is used.
    open_jaw = 0.0
    object_width = 0.03
    closed_jaw = min(spec["gripper"]["jaw_opening_m"] / 2, max(0.0, (spec["gripper"]["jaw_opening_m"] - object_width) / 2))
    phases = [
        (targets[0], 0.25, open_jaw, pick_approach),
        (targets[1], 0.45, open_jaw, pick),
        (targets[1], 0.45, closed_jaw, pick),
        (targets[0], 0.5, closed_jaw, pick_approach),
        (targets[2], 0.8, closed_jaw, place_approach),
        (targets[3], 0.5, closed_jaw, place),
        (targets[3], 0.35, open_jaw, place),
    ]
    previous_target = targets[0]
    for joint_id, value in zip(joint_ids, previous_target):
        data.qpos[model.jnt_qposadr[joint_id]] = value
    mujoco.mj_forward(model, data)
    for actuator, value in zip(actuator_ids, previous_target):
        data.ctrl[actuator] = value
    velocity_limits = [item["velocity_rad_s"] for item in spec["joint_limits"]]
    frames = []
    tracking_error = 0.0
    contacts = 0
    peak_joint_velocity = [0.0] * len(JOINT_NAMES)
    peak_gripper_contact_force = 0.0
    unexpected_contacts: set[str] = set()
    observed_contacts: set[str] = set()
    grasp_contact_seen = False
    payload_lifted = False
    initial_payload_z = float(data.xpos[payload_body][2])
    for phase_index, (q_targets, duration, grip_target, phase_target) in enumerate(phases):
        required_duration = max(
            abs(q_targets[index] - previous_target[index]) / max(velocity_limits[index], 0.05)
            for index in range(len(JOINT_NAMES))
        ) * 1.5
        phase_duration = max(duration, required_duration)
        steps = max(1, round(phase_duration / model.opt.timestep))
        start = data.ctrl[actuator_ids].copy()
        for step in range(steps):
            t = min(1.0, (step + 1) / steps)
            smooth = t * t * (3 - 2 * t)
            for index, actuator in enumerate(actuator_ids):
                data.ctrl[actuator] = start[index] + (q_targets[index] - start[index]) * smooth
            grip_value = grip_target
            data.ctrl[gripper_actuator_ids] = grip_value
            mujoco.mj_step(model, data)
            contacts = max(contacts, int(data.ncon))
            for index, dof in enumerate(dof_addresses):
                peak_joint_velocity[index] = max(peak_joint_velocity[index], abs(float(data.qvel[dof])))
            contacts_this_step = set()
            for contact_index in range(data.ncon):
                contact = data.contact[contact_index]
                first = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, contact.geom1) or "geom"
                second = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, contact.geom2) or "geom"
                pair = {first, second}
                contacts_this_step.add(frozenset(pair))
                observed_contacts.add(" and ".join(sorted(pair)))
                if "payload_geom" in pair and any("finger_geom" in item for item in pair):
                    contact_force = np.zeros(6)
                    mujoco.mj_contactForce(model, data, contact_index, contact_force)
                    peak_gripper_contact_force = max(peak_gripper_contact_force, float(contact_force[0]))
                allowed = pair == {"table", "payload_geom"} or ("payload_geom" in pair and any("finger_geom" in item for item in pair)) or pair == {"shoulder_geom", "base_geom"} or pair == {"left_finger_geom", "right_finger_geom"}
                if not allowed:
                    unexpected_contacts.add(" and ".join(sorted(pair)))
            bilateral_contact = frozenset(("left_finger_geom", "payload_geom")) in contacts_this_step and frozenset(("right_finger_geom", "payload_geom")) in contacts_this_step
            if phase_index == 2 and bilateral_contact:
                grasp_contact_seen = True
            if phase_index in (3, 4, 5) and float(data.xpos[payload_body][2]) >= initial_payload_z + 0.03:
                payload_lifted = True
            if step == steps - 1:
                error = np.linalg.norm(data.site_xpos[site_id] - np.asarray(phase_target))
                tracking_error = max(tracking_error, float(error))
            if capture and step % 100 == 0:
                if step % 200 == 0:
                    frames.append({"time_s": round(float(data.time), 3), "joints_rad": [round(float(data.qpos[model.jnt_qposadr[jid]]), 5) for jid in joint_ids], "gripper_m": [round(float(data.qpos[model.jnt_qposadr[jid]]), 5) for jid in gripper_joint_ids], "tcp_m": [round(float(v), 5) for v in data.site_xpos[site_id]], "payload_m": [round(float(v), 5) for v in data.xpos[payload_body]]})
        previous_target = list(q_targets)
    if not grasp_contact_seen:
        unexpected_contacts.add("gripper did not make bilateral contact with the payload")
    if not payload_lifted:
        unexpected_contacts.add("payload did not rise 30 mm during the lift")
    for _ in range(round(0.5 / model.opt.timestep)):
        mujoco.mj_step(model, data)
    payload_pos = [float(v) for v in data.xpos[payload_body]]
    payload_finger_contact = False
    for contact_index in range(data.ncon):
        contact = data.contact[contact_index]
        first = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, contact.geom1) or "geom"
        second = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, contact.geom2) or "geom"
        pair = {first, second}
        if "payload_geom" in pair and any("finger_geom" in item for item in pair):
            payload_finger_contact = True
    payload_released = not payload_finger_contact
    payload_resting_on_table = abs(payload_pos[2] - 0.015) <= 0.01
    goal_error = math.dist(payload_pos[:2], place[:2])
    return {"goal_error_m": goal_error, "peak_tracking_error_m": tracking_error, "contacts_seen": contacts, "observed_contacts": sorted(observed_contacts), "grasp_contact_seen": grasp_contact_seen, "payload_lifted": payload_lifted, "payload_released": payload_released, "payload_resting_on_table": payload_resting_on_table, "peak_gripper_contact_force_n": peak_gripper_contact_force, "gripper_positions_m": [float(data.qpos[model.jnt_qposadr[jid]]) for jid in gripper_joint_ids], "payload_final_m": payload_pos, "unexpected_contacts": sorted(unexpected_contacts), "peak_joint_velocity_rad_s": peak_joint_velocity, "frames": frames}


def _perceived_seed(perceived_target) -> tuple[tuple[float, float, float], dict]:
    """Validate a vision-seeded pick target and its uncertainty gate."""
    from .perception import UNCERTAINTY_GATE_M

    if not isinstance(perceived_target, dict):
        raise ArmSimulationError("Perceived target must be an object with x, y, and uncertainty_m.")
    try:
        x = float(perceived_target["x"])
        y = float(perceived_target["y"])
        z = float(perceived_target.get("z", 0.055))
        sigma = float(perceived_target["uncertainty_m"])
    except (KeyError, TypeError, ValueError):
        raise ArmSimulationError("Perceived target needs numeric x, y, and uncertainty_m.") from None
    if sigma != sigma or sigma < 0:
        raise ArmSimulationError("Perceived target uncertainty must be a non-negative number.")
    if sigma > UNCERTAINTY_GATE_M:
        raise ArmSimulationError(
            f"Perceived target uncertainty {sigma * 1000:.1f} mm exceeds the {UNCERTAINTY_GATE_M * 1000:.0f} mm gate; "
            "recalibrate or measure depth before simulating this pick."
        )
    seed = {
        "x": x, "y": y, "z": z,
        "uncertainty_m": sigma,
        "depth_source": str(perceived_target.get("depth_source", "unknown")),
        "gate_m": UNCERTAINTY_GATE_M,
    }
    return (x, y, z), seed


def run_arm_simulation(spec: dict, pick_target=None, place_target=None, *, train: bool = False, perceived_target=None) -> dict:
    """Run a fixed timestep physics trial and optionally tune servo gains.

    `perceived_target` seeds the pick from a calibrated vision target
    ({x, y, z?, uncertainty_m, depth_source?}); uncertainty beyond the
    perception gate refuses the run before any physics starts.
    """
    errors = validate_arm_design(spec)
    if errors:
        raise ArmSimulationError("Invalid arm design: " + "; ".join(errors))
    vision_seed = None
    if perceived_target is not None:
        seeded_pick, vision_seed = _perceived_seed(perceived_target)
        if pick_target is None:
            pick_target = seeded_pick
    if pick_target is None or place_target is None:
        default_pick, default_place = default_task_targets(spec)
    else:
        default_pick, default_place = DEFAULT_PICK, DEFAULT_PLACE
    pick = _target(default_pick if pick_target is None else pick_target, "pick target")
    place = _target(default_place if place_target is None else place_target, "place target")
    # Reach/limit errors are surfaced before starting the physics run.
    _joint_targets(spec, pick)
    _joint_targets(spec, place)
    # Advisory collision prediction from the same capsule model the kinematic
    # preview gate uses. It never changes acceptance; MuJoCo contacts decide.
    from .arm_collision import check_arm_path, workcell_obstacles
    lifted_pick = (pick[0], pick[1], pick[2] + 0.1)
    lifted_place = (place[0], place[1], place[2] + 0.1)
    prediction_waypoints = [
        _joint_targets(spec, pick),
        _joint_targets(spec, lifted_pick),
        _joint_targets(spec, lifted_place),
        _joint_targets(spec, place),
    ]
    collision_prediction = check_arm_path(spec, prediction_waypoints, workcell_obstacles(pick, place))
    gains = [80.0, 80.0, 60.0, 35.0]
    training = {"status": "not_requested", "method": "none"}
    if train:
        # Small deterministic coordinate search. Each candidate is evaluated
        # in the same MuJoCo task and timestep; no random seeds are involved.
        history = []
        baseline = _run_trial(spec, pick, place, gains)
        best_score = baseline["goal_error_m"] + 0.15 * baseline["peak_tracking_error_m"] + len(baseline["unexpected_contacts"])
        history.append({"iteration": 1, "gains": list(gains), "score": best_score})
        iteration = 1
        for joint_index in range(len(gains)):
            best_gain = gains[joint_index]
            for multiplier in (0.7, 1.3):
                candidate = list(gains)
                candidate[joint_index] = max(5.0, min(500.0, candidate[joint_index] * multiplier))
                trial = _run_trial(spec, pick, place, candidate)
                score = trial["goal_error_m"] + 0.15 * trial["peak_tracking_error_m"] + len(trial["unexpected_contacts"])
                iteration += 1
                history.append({"iteration": iteration, "gains": candidate, "score": score})
                if score < best_score:
                    best_score, best_gain = score, candidate[joint_index]
            gains[joint_index] = best_gain
        training = {"status": "completed", "method": "deterministic coordinate search over position-servo gains", "objective": "payload placement error + 0.15 × peak tool tracking error + unexpected-contact penalty", "iterations": len(history), "history": history, "selected_gains": gains, "notice": "This tunes a scripted position controller in simulation. It is not reinforcement learning or a learned perception policy."}
    result = _run_trial(spec, pick, place, gains, capture=True)
    velocity_violations = [name for name, measured, limit in zip(JOINT_NAMES, result["peak_joint_velocity_rad_s"], [item["velocity_rad_s"] for item in spec["joint_limits"]]) if measured > limit * 1.05]
    checks = {
        "bilateral_grasp_contact": result["grasp_contact_seen"],
        "payload_lifted_30mm": result["payload_lifted"],
        "payload_released": result["payload_released"],
        "payload_resting_on_table": result["payload_resting_on_table"],
        "placed_within_50mm": result["goal_error_m"] <= 0.05,
        "no_unexpected_contacts": not result["unexpected_contacts"],
        "joint_speed_within_limit": not velocity_violations,
    }
    failure_reasons = list(result["unexpected_contacts"])
    if not checks["placed_within_50mm"]:
        failure_reasons.append("payload placement error exceeds 50 mm")
    if not checks["payload_released"]:
        failure_reasons.append("payload remained in contact with a gripper finger after release")
    if not checks["payload_resting_on_table"]:
        failure_reasons.append("payload did not settle near the tabletop after release")
    if velocity_violations:
        failure_reasons.append("joint speed exceeded 105% of configured limit: " + ", ".join(velocity_violations))
    passed = all(checks.values())
    return {
        "schema_version": SIMULATION_SCHEMA,
        "engine": ENGINE_NAME,
        "engine_version": __import__("mujoco").__version__,
        "model": "generated MJCF from reviewed tabletop-arm specification",
        "timestep_s": 0.002,
        "deterministic": True,
        "task": {"name": "tabletop_pick_and_place", "pick_target_m": list(pick), "place_target_m": list(place), "vision_seed": vision_seed},
        "controller": {"type": "position servos", "gains": gains, "training": training},
        "passed": passed,
        "acceptance_checks": checks,
        "failure_reasons": failure_reasons,
        "metrics": {key: result[key] for key in ("goal_error_m", "peak_tracking_error_m", "contacts_seen", "observed_contacts", "grasp_contact_seen", "payload_lifted", "payload_released", "payload_resting_on_table", "peak_gripper_contact_force_n", "gripper_positions_m", "payload_final_m", "unexpected_contacts", "peak_joint_velocity_rad_s")},
        "collision_prediction": {"predicted_clear": not collision_prediction, "findings": collision_prediction, "method": "capsule link model checked at pick, lifted transfer, and place waypoints; advisory only"},
        "trajectory": result["frames"],
        "assumptions": ["The physics model is generated from simplified primitive geometry and estimated inertias.", f"The task uses a 30 mm cube with the reviewed payload mass ({spec['payload_kg']:.3f} kg).", "Grasp retention is determined by simulated finger contact, friction, and grip actuator force limits; no weld or ideal grasp constraint is used.", "No real camera, actuator, manufacturing tolerance, or safety controller is included."],
        "label": "MuJoCo physics simulation; engineering review required",
    }
