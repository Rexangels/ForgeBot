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
        color: (() => { const rgba = visual.querySelector("material color")?.getAttribute("rgba"); if (!rgba) return null; const channels = rgba.trim().split(/\s+/).slice(0, 3).map(Number); if (channels.length !== 3 || channels.some((part) => !Number.isFinite(part))) return null; return `#${channels.map((part) => Math.max(0, Math.min(255, Math.round(part * 255))).toString(16).padStart(2, "0")).join("")}`; })(),
        opacity: (() => { const channels = visual.querySelector("material color")?.getAttribute("rgba")?.trim().split(/\s+/).map(Number); return channels?.length === 4 && Number.isFinite(channels[3]) ? Math.max(0, Math.min(1, channels[3])) : 1; })(),
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

// Capsule radii in metres, mirroring the MJCF model and backend predictor.
// Adjacent link pairs share a joint by construction, so only non-adjacent
// pairs are checked, matching the simulation's contact allowlist.
const LINK_RADII = { base: 0.035, upper: 0.018, forearm: 0.016, wrist: 0.014 };
const SELF_PAIRS = [["base", "forearm"], ["base", "wrist"], ["upper", "wrist"]];

function linkSegments(arm, angles) {
  const [shoulder, elbow, wristBase, tool] = forwardArmLinkPoints(arm, angles);
  const origin = { x: 0, y: 0, z: 0 };
  return {
    base: [origin, shoulder, LINK_RADII.base],
    upper: [shoulder, elbow, LINK_RADII.upper],
    forearm: [elbow, wristBase, LINK_RADII.forearm],
    wrist: [wristBase, tool, LINK_RADII.wrist],
  };
}

function sub(a, b) {
  return [a.x - b.x, a.y - b.y, a.z - b.z];
}

function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

export function segmentSegmentDistance(p1, q1, p2, q2) {
  const d1 = sub(q1, p1);
  const d2 = sub(q2, p2);
  const r = sub(p1, p2);
  const a = dot(d1, d1);
  const e = dot(d2, d2);
  const f = dot(d2, r);
  let s;
  let t;
  if (a < 1e-12 && e < 1e-12) return Math.hypot(r[0], r[1], r[2]);
  if (a < 1e-12) {
    s = 0;
    t = Math.max(0, Math.min(1, f / e));
  } else if (e < 1e-12) {
    t = 0;
    s = Math.max(0, Math.min(1, -dot(d1, r) / a));
  } else {
    const c = dot(d1, r);
    const b = dot(d1, d2);
    const denom = a * e - b * b;
    s = denom < 1e-12 ? 0 : Math.max(0, Math.min(1, (b * f - c * e) / denom));
    t = (b * s + f) / e;
    if (t < 0) {
      t = 0;
      s = Math.max(0, Math.min(1, -c / a));
    } else if (t > 1) {
      t = 1;
      s = Math.max(0, Math.min(1, (b - c) / a));
    }
  }
  const ax = p1.x + d1[0] * s;
  const ay = p1.y + d1[1] * s;
  const az = p1.z + d1[2] * s;
  const bx = p2.x + d2[0] * t;
  const by = p2.y + d2[1] * t;
  const bz = p2.z + d2[2] * t;
  return Math.hypot(ax - bx, ay - by, az - bz);
}

function pointBoxDistance(point, center, half) {
  const dx = Math.abs(point.x - center.x) - half.x;
  const dy = Math.abs(point.y - center.y) - half.y;
  const dz = Math.abs(point.z - center.z) - half.z;
  const outside = Math.hypot(Math.max(dx, 0), Math.max(dy, 0), Math.max(dz, 0));
  return outside > 0 ? outside : Math.max(dx, dy, dz);
}

function segmentBoxClearance(start, end, radius, center, half, samples = 16) {
  let clearance = Infinity;
  for (let index = 0; index <= samples; index += 1) {
    const t = index / samples;
    const point = {
      x: start.x + (end.x - start.x) * t,
      y: start.y + (end.y - start.y) * t,
      z: start.z + (end.z - start.z) * t,
    };
    clearance = Math.min(clearance, pointBoxDistance(point, center, half));
  }
  return clearance - radius;
}

export function checkArmSelfCollision(arm, angles) {
  const segments = linkSegments(arm, angles);
  let worst = { clear: true, clearance: Infinity };
  for (const [first, second] of SELF_PAIRS) {
    const [p1, q1, r1] = segments[first];
    const [p2, q2, r2] = segments[second];
    const clearance = segmentSegmentDistance(p1, q1, p2, q2) - r1 - r2;
    if (clearance < Math.min(worst.clearance, 0)) {
      worst = {
        clear: false,
        clearance,
        reason: `${first} and ${second} overlap by ${(-clearance * 1000).toFixed(1)} mm; this pose would self-collide.`,
      };
    }
  }
  return worst.clear ? { clear: true, clearance: worst.clearance } : worst;
}

export function checkArmObstacles(arm, angles, obstacles = []) {
  const segments = linkSegments(arm, angles);
  for (const obstacle of obstacles) {
    const ignored = new Set(["base", ...(obstacle.ignore || [])]);
    for (const [name, [start, end, radius]] of Object.entries(segments)) {
      if (ignored.has(name)) continue;
      const clearance = segmentBoxClearance(start, end, radius, obstacle.center, obstacle.half);
      if (clearance < 0) {
        return {
          clear: false,
          clearance,
          reason: `${name} intersects ${obstacle.name} by ${(-clearance * 1000).toFixed(1)} mm.`,
        };
      }
    }
  }
  return { clear: true };
}

export function workcellObstacles(pick, place, binHalf = null) {
  const wall = 0.005;
  // The tool tip intentionally enters payload space during grasp, mirroring
  // the simulation's finger/payload contact allowlist; bin walls check all links.
  const toolFree = { ignore: ["wrist"] };
  const obstacles = [
    { name: "pick payload", center: { x: pick.x, y: pick.y, z: 0.018 }, half: { x: 0.015, y: 0.015, z: 0.015 }, ...toolFree },
    { name: "place payload", center: { x: place.x, y: place.y, z: 0.018 }, half: { x: 0.015, y: 0.015, z: 0.015 }, ...toolFree },
  ];
  // Bin walls are opt-in: the workcell has no bin model by default, and an
  // invented bin would false-block placements that physics accepts.
  if (!binHalf) return obstacles;
  return [...obstacles,
    { name: "bin wall x+", center: { x: place.x + binHalf.x, y: place.y, z: binHalf.z / 2 }, half: { x: wall, y: binHalf.y, z: binHalf.z / 2 } },
    { name: "bin wall x-", center: { x: place.x - binHalf.x, y: place.y, z: binHalf.z / 2 }, half: { x: wall, y: binHalf.y, z: binHalf.z / 2 } },
    { name: "bin wall y+", center: { x: place.x, y: place.y + binHalf.y, z: binHalf.z / 2 }, half: { x: binHalf.x, y: wall, z: binHalf.z / 2 } },
    { name: "bin wall y-", center: { x: place.x, y: place.y - binHalf.y, z: binHalf.z / 2 }, half: { x: binHalf.x, y: wall, z: binHalf.z / 2 } },
  ];
}

export function solveArmTarget(arm, target) {
  const radial = Math.hypot(target.x, target.y);
  const yaw = Math.atan2(target.y, target.x);
  const vertical = target.z - arm.shoulderZ;
  const [upper, forearm, wrist] = arm.lengths;
  // Solve the first two links to the wrist center, then counter-rotate the
  // wrist so the tool mount stays horizontal in the workcell frame.
  const wristX = radial - wrist;
  const distanceSquared = wristX * wristX + vertical * vertical;
  const cosine = (distanceSquared - upper * upper - forearm * forearm) / (2 * upper * forearm);
  if (cosine < -1 || cosine > 1) return { reachable: false, reason: "Target is outside the arm's geometric reach." };
  const elbowMagnitude = Math.acos(Math.max(-1, Math.min(1, cosine)));
  const candidates = [elbowMagnitude, -elbowMagnitude].map((elbowPlanar) => {
    const shoulderPlanar = Math.atan2(vertical, wristX)
      - Math.atan2(forearm * Math.sin(elbowPlanar), upper + forearm * Math.cos(elbowPlanar));
    return {
      base_yaw: yaw,
      shoulder_pitch: -shoulderPlanar,
      elbow_pitch: -elbowPlanar,
      wrist_pitch: shoulderPlanar + elbowPlanar,
    };
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
