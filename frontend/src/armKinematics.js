const EXPECTED_JOINTS = ["base_yaw", "shoulder_pitch", "elbow_pitch", "wrist_pitch"];

function vector(value, fallback = [0, 0, 0]) {
  if (!value) return fallback;
  const parts = value.trim().split(/\s+/).map(Number);
  return parts.length === 3 && parts.every(Number.isFinite) ? parts : fallback;
}

function parseLimits(node) {
  const limit = node.querySelector("limit");
  if (!limit) return { lower: -Math.PI, upper: Math.PI, velocity: 1 };
  return {
    lower: Number(limit.getAttribute("lower")),
    upper: Number(limit.getAttribute("upper")),
    velocity: Number(limit.getAttribute("velocity") || 1),
  };
}

export function parseRobotUrdf(urdfText) {
  if (!urdfText || typeof urdfText !== "string") throw new Error("The robot URDF is not available yet.");
  const xml = new DOMParser().parseFromString(urdfText, "application/xml");
  if (xml.querySelector("parsererror")) throw new Error("The generated URDF could not be parsed.");
  const links = new Map();
  for (const node of xml.querySelectorAll("robot > link")) {
    const name = node.getAttribute("name");
    if (!name) continue;
    const visuals = [...node.querySelectorAll(":scope > visual")].map((visual) => {
      const origin = visual.querySelector("origin");
      const geometry = visual.querySelector("geometry");
      const shape = geometry?.firstElementChild;
      if (!shape) return null;
      const attributes = {};
      if (shape.tagName === "box") {
        const [size_x, size_y, size_z] = vector(shape.getAttribute("size"));
        Object.assign(attributes, { size_x, size_y, size_z });
      } else {
        for (const attribute of shape.attributes) attributes[attribute.name] = Number(attribute.value);
      }
      return {
        name: visual.getAttribute("name") || "",
        origin: vector(origin?.getAttribute("xyz")),
        rpy: vector(origin?.getAttribute("rpy")),
        type: shape.tagName,
        attributes,
      };
    }).filter(Boolean);
    links.set(name, { name, visuals });
  }
  const joints = [];
  for (const node of xml.querySelectorAll("robot > joint")) {
    const origin = node.querySelector("origin");
    const axisNode = node.querySelector("axis");
    const parent = node.querySelector("parent")?.getAttribute("link");
    const child = node.querySelector("child")?.getAttribute("link");
    const type = node.getAttribute("type") || "fixed";
    if (!parent || !child || !links.has(parent) || !links.has(child)) continue;
    joints.push({
      name: node.getAttribute("name") || `${parent}-${child}`,
      type,
      parent,
      child,
      origin: vector(origin?.getAttribute("xyz")),
      rpy: vector(origin?.getAttribute("rpy")),
      axis: vector(axisNode?.getAttribute("xyz"), [1, 0, 0]),
      limits: parseLimits(node),
    });
  }
  const children = new Set(joints.map((joint) => joint.child));
  const root = [...links.keys()].find((name) => !children.has(name));
  if (!root || !joints.length) throw new Error("The URDF does not contain a connected robot assembly.");
  const model = { links, joints, root };
  model.arm = describeArm(model);
  return model;
}

function describeArm(model) {
  const byName = Object.fromEntries(model.joints.map((joint) => [joint.name, joint]));
  if (!EXPECTED_JOINTS.every((name) => byName[name])) return null;
  const shoulderJoint = byName.shoulder_pitch;
  const elbowJoint = byName.elbow_pitch;
  const wristJoint = byName.wrist_pitch;
  const toolMount = byName.tool_mount;
  const near = (actual, expected) => Math.abs(actual - expected) < 1e-6;
  const zeroVector = (value) => value.every((component) => near(component, 0));
  const axisIs = (joint, expected) => joint.axis.every((component, index) => near(component, expected[index]));
  if (!axisIs(byName.base_yaw, [0, 0, 1])
    || ![shoulderJoint, elbowJoint, wristJoint].every((joint) => axisIs(joint, [0, 1, 0]))
    || byName.base_yaw.type !== "revolute"
    || ![shoulderJoint, elbowJoint, wristJoint].every((joint) => joint.type === "revolute")
    || (toolMount && toolMount.type !== "fixed")
    || !zeroVector(byName.base_yaw.rpy)
    || !zeroVector(shoulderJoint.rpy)
    || !zeroVector(elbowJoint.rpy)
    || !zeroVector(wristJoint.rpy)
    || (toolMount && !zeroVector(toolMount.rpy))
    || !near(byName.base_yaw.origin[0], 0)
    || !near(byName.base_yaw.origin[1], 0)
    || !near(shoulderJoint.origin[0], 0)
    || !near(shoulderJoint.origin[1], 0)
    || !near(elbowJoint.origin[1], 0)
    || !near(elbowJoint.origin[2], 0)
    || !near(wristJoint.origin[1], 0)
    || !near(wristJoint.origin[2], 0)
    || (toolMount && (!near(toolMount.origin[1], 0) || !near(toolMount.origin[2], 0)))) return null;
  const lengths = [elbowJoint.origin[0], wristJoint.origin[0], toolMount?.origin[0] || 0];
  if (lengths.some((length) => !Number.isFinite(length) || length <= 0)) return null;
  return {
    joints: EXPECTED_JOINTS.map((name) => byName[name]),
    lengths,
    shoulderZ: byName.base_yaw.origin[2] + shoulderJoint.origin[2],
    limits: Object.fromEntries(EXPECTED_JOINTS.map((name) => [name, byName[name].limits])),
  };
}

export function forwardArmKinematics(arm, angles) {
  const [upper, forearm, wrist] = arm.lengths;
  const yaw = angles.base_yaw || 0;
  const shoulder = -(angles.shoulder_pitch || 0);
  const elbow = -(angles.elbow_pitch || 0);
  const wristPitch = -(angles.wrist_pitch || 0);
  const radial = upper * Math.cos(shoulder)
    + forearm * Math.cos(shoulder + elbow)
    + wrist * Math.cos(shoulder + elbow + wristPitch);
  const z = arm.shoulderZ
    + upper * Math.sin(shoulder)
    + forearm * Math.sin(shoulder + elbow)
    + wrist * Math.sin(shoulder + elbow + wristPitch);
  return { x: radial * Math.cos(yaw), y: radial * Math.sin(yaw), z };
}

export function forwardArmLinkPoints(arm, angles) {
  const [upper, forearm, wrist] = arm.lengths;
  const yaw = angles.base_yaw || 0;
  const shoulder = -(angles.shoulder_pitch || 0);
  const elbow = -(angles.elbow_pitch || 0);
  const wristPitch = -(angles.wrist_pitch || 0);
  const pointAt = (distance, angle) => ({
    x: distance * Math.cos(angle) * Math.cos(yaw),
    y: distance * Math.cos(angle) * Math.sin(yaw),
    z: arm.shoulderZ + distance * Math.sin(angle),
  });
  const shoulderPoint = { x: 0, y: 0, z: arm.shoulderZ };
  const elbowPoint = pointAt(upper, shoulder);
  const toolPoint = forwardArmKinematics(arm, angles);
  const wristBase = pointAt(
    upper * Math.cos(shoulder) + forearm * Math.cos(shoulder + elbow),
    Math.atan2(upper * Math.sin(shoulder) + forearm * Math.sin(shoulder + elbow), upper * Math.cos(shoulder) + forearm * Math.cos(shoulder + elbow)),
  );
  return [shoulderPoint, elbowPoint, wristBase, toolPoint];
}

function tableClearance(arm, angles) {
  const points = forwardArmLinkPoints(arm, angles);
  const radii = [0.018, 0.018, 0.014];
  for (let segment = 0; segment < points.length - 1; segment += 1) {
    const start = points[segment];
    const end = points[segment + 1];
    for (let sample = 0; sample <= 12; sample += 1) {
      const t = sample / 12;
      const x = start.x + (end.x - start.x) * t;
      const y = start.y + (end.y - start.y) * t;
      const z = start.z + (end.z - start.z) * t;
      if (x < -0.1 || x > 0.7 || y < -0.3 || y > 0.3) return { clear: false, reason: "A link extends beyond the tabletop work area." };
      if (z < radii[segment]) return { clear: false, reason: "A link would intersect the tabletop in the simplified clearance check." };
    }
  }
  return { clear: true };
}

export function checkArmTableClearance(arm, angles) {
  return tableClearance(arm, angles);
}

export function solveArmTarget(arm, target) {
  const radial = Math.hypot(target.x, target.y);
  const yaw = Math.atan2(target.y, target.x);
  const vertical = target.z - arm.shoulderZ;
  const [upper, forearm, wrist] = arm.lengths;
  // Keep the tool mount horizontal for this first 4-axis position solver.
  const wristX = radial - wrist;
  const distanceSquared = wristX * wristX + vertical * vertical;
  const cosine = (distanceSquared - upper * upper - forearm * forearm) / (2 * upper * forearm);
  if (cosine < -1 || cosine > 1) return { reachable: false, reason: "Target is outside the arm's geometric reach." };
  const elbowMagnitude = Math.acos(Math.max(-1, Math.min(1, cosine)));
  const candidates = [elbowMagnitude, -elbowMagnitude].map((elbowPlanar) => {
    const shoulderPlanar = Math.atan2(vertical, wristX)
      - Math.atan2(forearm * Math.sin(elbowPlanar), upper + forearm * Math.cos(elbowPlanar));
    return { base_yaw: yaw, shoulder_pitch: -shoulderPlanar, elbow_pitch: -elbowPlanar, wrist_pitch: 0 };
  });
  const reasons = [];
  for (const angles of candidates) {
    let withinLimits = true;
    for (const [name, value] of Object.entries(angles)) {
      const limits = arm.limits[name];
      if (value < limits.lower || value > limits.upper) {
        reasons.push(`${name.replace(/_/g, " ")} would exceed its URDF joint limits.`);
        withinLimits = false;
        break;
      }
    }
    if (!withinLimits) continue;
    const clearance = tableClearance(arm, angles);
    if (!clearance.clear) {
      reasons.push(clearance.reason);
      continue;
    }
    const actual = forwardArmKinematics(arm, angles);
    const error = Math.hypot(actual.x - target.x, actual.y - target.y, actual.z - target.z);
    return { reachable: error <= 0.01, angles, actual, error, reason: error <= 0.01 ? "" : "Target solve exceeded the 10 mm position tolerance." };
  }
  return { reachable: false, reason: reasons[0] || "No collision-clear joint solution was found." };
}
