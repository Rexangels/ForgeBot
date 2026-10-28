import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { checkArmObstacles, checkArmSelfCollision, checkArmTableClearance, forwardArmKinematics, parseRobotUrdf, solveArmTarget, workcellObstacles } from "./armKinematics.js";

const JOINTS = ["base_yaw", "shoulder_pitch", "elbow_pitch", "wrist_pitch"];
const DEFAULT_PICK = { x: 0.3, y: 0, z: 0.04 };
const DEFAULT_PLACE = { x: 0.2, y: 0.2, z: 0.04 };

function materialForLink(name) {
  const color = name.includes("base") ? "#344d58"
    : name.includes("tool") || name.includes("finger") ? "#d9864c"
      : name.includes("shoulder") ? "#263740" : "#267c72";
  return new THREE.MeshStandardMaterial({ color, roughness: 0.58, metalness: 0.08 });
}

function urdfRpyQuaternion(rpy) {
  return new THREE.Quaternion().setFromEuler(new THREE.Euler(rpy[0], rpy[1], rpy[2], "ZYX"));
}

function createVisual(visual, linkName, onGripperVisual) {
  const frame = new THREE.Group();
  frame.position.set(...visual.origin);
  frame.quaternion.copy(urdfRpyQuaternion(visual.rpy));
  const material = materialForLink(linkName);
  const a = visual.attributes;
  let geometry;
  if (visual.type === "cylinder") {
    geometry = new THREE.CylinderGeometry(a.radius, a.radius, a.length, 28);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.rotation.x = Math.PI / 2; // URDF cylinders use local Z; Three cylinders use local Y.
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    frame.add(mesh);
  } else if (visual.type === "sphere") {
    geometry = new THREE.SphereGeometry(a.radius, 20, 16);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    frame.add(mesh);
  } else if (visual.type === "box") {
    geometry = new THREE.BoxGeometry(a.size_x, a.size_y, a.size_z);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    frame.add(mesh);
  }
  if (visual.name === "left_finger" || visual.name === "right_finger") {
    onGripperVisual({ frame, side: visual.name === "left_finger" ? 1 : -1, initialY: visual.origin[1] });
  }
  return frame;
}

function buildRobot(model, scene) {
  const linkFrames = new Map();
  const jointFrames = new Map();
  const gripperVisuals = [];
  const childJoints = new Map();
  for (const joint of model.joints) {
    if (!childJoints.has(joint.parent)) childJoints.set(joint.parent, []);
    childJoints.get(joint.parent).push(joint);
  }
  const buildLink = (name) => {
    const linkFrame = new THREE.Group();
    linkFrame.name = `link:${name}`;
    linkFrames.set(name, linkFrame);
    const link = model.links.get(name);
    for (const visual of link.visuals) {
      linkFrame.add(createVisual(visual, name, (jaw) => gripperVisuals.push(jaw)));
    }
    for (const joint of childJoints.get(name) || []) {
      const originFrame = new THREE.Group();
      originFrame.name = `origin:${joint.name}`;
      originFrame.position.set(...joint.origin);
      originFrame.quaternion.copy(urdfRpyQuaternion(joint.rpy));
      const motionFrame = new THREE.Group();
      motionFrame.name = `joint:${joint.name}`;
      originFrame.add(motionFrame);
      linkFrame.add(originFrame);
      jointFrames.set(joint.name, { frame: motionFrame, axis: new THREE.Vector3(...joint.axis).normalize(), type: joint.type });
      motionFrame.add(buildLink(joint.child));
    }
    return linkFrame;
  };
  const root = buildLink(model.root);
  scene.add(root);
  return { linkFrames, jointFrames, gripperVisuals };
}

function applyJointPose(jointFrames, pose) {
  for (const [name, value] of Object.entries(pose)) {
    const joint = jointFrames.get(name);
    if (!joint) continue;
    if (joint.type === "prismatic") joint.frame.position.copy(joint.axis).multiplyScalar(value);
    else joint.frame.quaternion.setFromAxisAngle(joint.axis, value);
  }
}

function addTable(scene) {
  const table = new THREE.Mesh(
    new THREE.BoxGeometry(0.8, 0.6, 0.025),
    new THREE.MeshStandardMaterial({ color: "#e5e9e5", roughness: 0.95 }),
  );
  table.position.set(0.3, 0, -0.0125);
  table.receiveShadow = true;
  scene.add(table);
  const grid = new THREE.GridHelper(0.8, 16, "#bfcac4", "#dce3df");
  grid.rotation.x = Math.PI / 2;
  grid.position.set(0.3, 0, 0.001);
  scene.add(grid);
  scene.add(new THREE.AxesHelper(0.12));
}

function addPickPlaceObjects(scene) {
  const pickObject = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.04, 0.04), new THREE.MeshStandardMaterial({ color: "#e8a842", roughness: 0.7 }));
  pickObject.name = "pick-object";
  pickObject.position.set(DEFAULT_PICK.x, DEFAULT_PICK.y, 0.02);
  pickObject.castShadow = true;
  scene.add(pickObject);

  const destination = new THREE.Group();
  destination.name = "place-bin";
  const binMaterial = new THREE.MeshStandardMaterial({ color: "#598d7c", roughness: 0.8, transparent: true, opacity: 0.72 });
  const base = new THREE.Mesh(new THREE.BoxGeometry(0.13, 0.13, 0.012), binMaterial);
  base.position.z = 0.006;
  destination.add(base);
  const walls = [
    [0, 0.059, 0.032, 0.13, 0.012, 0.052], [0, -0.059, 0.032, 0.13, 0.012, 0.052],
    [0.059, 0, 0.032, 0.012, 0.106, 0.052], [-0.059, 0, 0.032, 0.012, 0.106, 0.052],
  ];
  for (const [x, y, z, w, d, h] of walls) {
    const wall = new THREE.Mesh(new THREE.BoxGeometry(w, d, h), binMaterial);
    wall.position.set(x, y, z);
    destination.add(wall);
  }
  destination.position.set(DEFAULT_PLACE.x, DEFAULT_PLACE.y, 0);
  scene.add(destination);
  const deliveredObject = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.04, 0.04), new THREE.MeshStandardMaterial({ color: "#e8a842", roughness: 0.7 }));
  deliveredObject.position.set(0, 0, 0.044);
  deliveredObject.visible = false;
  destination.add(deliveredObject);
  const payload = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.03, 0.03), new THREE.MeshStandardMaterial({ color: "#e8a842", roughness: 0.7 }));
  payload.name = "animated-payload";
  payload.position.set(0.045, 0, -0.02);
  payload.castShadow = true;
  payload.visible = false;
  return { pickObject, destination, payload, deliveredObject };
}

function numberPoint(point, key, value) {
  return { ...point, [key]: Number(value) };
}

function reachableHeight(arm, target, offsets) {
  for (const offset of offsets) {
    const point = { ...target, z: target.z + offset };
    if (solveArmTarget(arm, point).reachable) return point;
  }
  return { ...target };
}

export default function ArmModelViewer({ urdf, initialPickTarget = null, simulationTrajectory = null }) {
  const mountRef = useRef(null);
  const sceneNodes = useRef(null);
  const playbackRef = useRef(null);
  const angleRef = useRef({ base_yaw: 0, shoulder_pitch: 0, elbow_pitch: 0, wrist_pitch: 0 });
  const [angles, setAngles] = useState(angleRef.current);
  const [pick, setPick] = useState(() => initialPickTarget ? { x: initialPickTarget.x, y: initialPickTarget.y, z: initialPickTarget.z } : DEFAULT_PICK);
  const [place, setPlace] = useState(DEFAULT_PLACE);
  const [error, setError] = useState("");
  const [renderError, setRenderError] = useState("");
  const [playing, setPlaying] = useState(false);
  const [playbackNote, setPlaybackNote] = useState("Kinematic preview · no physics or collision guarantees");
  const parsed = useMemo(() => {
    if (!urdf) return null;
    try { return parseRobotUrdf(urdf); } catch (err) { return { parseError: err.message }; }
  }, [urdf]);
  const arm = parsed?.arm;
  const pickSolution = arm ? solveArmTarget(arm, pick) : null;
  const placeSolution = arm ? solveArmTarget(arm, place) : null;

  useEffect(() => {
    if (!arm || !Array.isArray(simulationTrajectory) || simulationTrajectory.length < 2) return;
    const frames = simulationTrajectory
      .filter((frame) => Number.isFinite(frame.time_s) && Array.isArray(frame.joints_rad) && frame.joints_rad.length === JOINTS.length)
      .map((frame) => ({ time: frame.time_s, angles: Object.fromEntries(JOINTS.map((name, index) => [name, frame.joints_rad[index]])), gripper: frame.gripper_m, payload: frame.payload_m }));
    if (frames.length < 2) return;
    const path = simulationTrajectory.filter((frame) => Array.isArray(frame.tcp_m) && frame.tcp_m.length === 3).map((frame) => ({ x: frame.tcp_m[0], y: frame.tcp_m[1], z: frame.tcp_m[2] }));
    angleRef.current = { ...frames[0].angles };
    setAngles(angleRef.current);
    const physicsPlayback = frames.every((frame) => Array.isArray(frame.payload) && frame.payload.length === 3 && Array.isArray(frame.gripper));
    playbackRef.current = { startedAt: performance.now(), startAngles: { ...frames[0].angles }, frames, path, physicsPlayback };
    setPlaybackNote("Replaying sampled MuJoCo joint, jaw, and payload states; rendered geometry is reconstructed from URDF");
    setPlaying(true);
  }, [simulationTrajectory, arm]);

  useEffect(() => {
    if (initialPickTarget) setPick({ x: initialPickTarget.x, y: initialPickTarget.y, z: initialPickTarget.z });
  }, [initialPickTarget?.x, initialPickTarget?.y, initialPickTarget?.z]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !parsed || parsed.parseError) return undefined;
    setRenderError("");
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#f5f8f6");
    const camera = new THREE.PerspectiveCamera(36, 1, 0.01, 10);
    camera.up.set(0, 0, 1);
    camera.position.set(0.72, -0.82, 0.62);
    let renderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false }); }
    catch { setRenderError("WebGL is unavailable in this browser, so the URDF assembly cannot be shown."); return undefined; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    mount.appendChild(renderer.domElement);
    scene.add(new THREE.HemisphereLight("#ffffff", "#aebbb4", 2.1));
    const keyLight = new THREE.DirectionalLight("#ffffff", 2.3);
    keyLight.position.set(1.2, -0.8, 1.5);
    keyLight.castShadow = true;
    scene.add(keyLight);
    addTable(scene);
    const built = buildRobot(parsed, scene);
    const objects = addPickPlaceObjects(scene);
    const toolJoint = parsed.joints.find((joint) => joint.name === "tool_mount");
    const physicsPlayback = Array.isArray(simulationTrajectory) && simulationTrajectory.some((item) => Array.isArray(item.payload_m));
    if (physicsPlayback) {
      scene.add(objects.payload);
      const firstPayloadFrame = simulationTrajectory.find((item) => Array.isArray(item.payload_m) && item.payload_m.length === 3);
      if (firstPayloadFrame) objects.payload.position.set(...firstPayloadFrame.payload_m);
      objects.payload.visible = true;
      objects.pickObject.visible = false;
    } else if (toolJoint) built.linkFrames.get(toolJoint.child)?.add(objects.payload);
    const targetMarker = new THREE.Mesh(new THREE.SphereGeometry(0.012, 16, 12), new THREE.MeshStandardMaterial({ color: "#d9864c", emissive: "#7d3a16" }));
    scene.add(targetMarker);
    const pathGeometry = new THREE.BufferGeometry();
    const pathMaterial = new THREE.LineDashedMaterial({ color: "#d9864c", dashSize: 0.012, gapSize: 0.008 });
    const pathLine = new THREE.Line(pathGeometry, pathMaterial);
    scene.add(pathLine);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0.27, 0.04, 0.12);
    controls.enableDamping = true;
    controls.minDistance = 0.25;
    controls.maxDistance = 1.6;
    camera.lookAt(controls.target);
    const resize = () => {
      const width = Math.max(mount.clientWidth, 1);
      const height = Math.max(mount.clientHeight, 1);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);
    resize();
    sceneNodes.current = { scene, built, objects, targetMarker, pathLine };
    applyJointPose(built.jointFrames, angleRef.current);
    let frame = 0;
    const render = (now) => {
      const playback = playbackRef.current;
      if (playback) {
        const elapsed = (now - playback.startedAt) / 1000;
        let segmentIndex = playback.frames.findIndex((item) => item.time >= elapsed);
        if (segmentIndex < 0) {
          const last = playback.frames[playback.frames.length - 1];
          angleRef.current = { ...last.angles };
          setAngles(angleRef.current);
          objects.pickObject.visible = false;
          if (playback.physicsPlayback) {
            if (last.payload) objects.payload.position.set(...last.payload);
            objects.payload.visible = true;
            objects.deliveredObject.visible = false;
          } else {
            objects.payload.visible = false;
            objects.deliveredObject.visible = true;
          }
          playbackRef.current = null;
          setPlaying(false);
        } else {
          const from = segmentIndex === 0 ? { time: 0, angles: playback.startAngles, closed: false } : playback.frames[segmentIndex - 1];
          const to = playback.frames[segmentIndex];
          const fraction = Math.max(0, Math.min(1, (elapsed - from.time) / Math.max(to.time - from.time, 0.001)));
          const smooth = fraction * fraction * (3 - 2 * fraction);
          const next = Object.fromEntries(JOINTS.map((name) => [name, from.angles[name] + (to.angles[name] - from.angles[name]) * smooth]));
          angleRef.current = next;
          setAngles(next);
          applyJointPose(built.jointFrames, next);
          const gripFrom = from.gripper ? Math.min(1, Math.max(...from.gripper) / 0.01) : (from.closed ? 1 : 0);
          const gripTo = to.gripper ? Math.min(1, Math.max(...to.gripper) / 0.01) : (to.closed ? 1 : 0);
          const gripAmount = gripFrom + (gripTo - gripFrom) * smooth;
          built.gripperVisuals.forEach(({ frame: jawFrame, side, initialY }) => { jawFrame.position.y = initialY + side * gripAmount * (0.006 - Math.abs(initialY)); });
          if (playback.physicsPlayback && from.payload && to.payload) {
            const payloadPosition = from.payload.map((value, index) => value + (to.payload[index] - value) * smooth);
            objects.payload.position.set(...payloadPosition);
            objects.payload.visible = true;
            objects.pickObject.visible = false;
            objects.deliveredObject.visible = false;
          } else {
            objects.payload.visible = to.closed;
            objects.pickObject.visible = segmentIndex < 2 || (segmentIndex === 2 && !to.closed);
            if (segmentIndex >= 6 && fraction >= 0.95) objects.deliveredObject.visible = true;
          }
        }
      }
      const pose = forwardArmKinematics(parsed.arm, angleRef.current);
      targetMarker.position.set(pose.x, pose.y, pose.z);
      pathLine.geometry.setFromPoints((playbackRef.current?.path || []).map((point) => new THREE.Vector3(point.x, point.y, point.z)));
      pathLine.computeLineDistances();
      controls.update();
      renderer.render(scene, camera);
      frame = window.requestAnimationFrame(render);
    };
    render(0);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      scene.traverse((object) => {
        object.geometry?.dispose();
        if (Array.isArray(object.material)) object.material.forEach((material) => material.dispose());
        else object.material?.dispose();
      });
      renderer.dispose();
      renderer.domElement.remove();
      sceneNodes.current = null;
    };
  }, [parsed]);

  useEffect(() => {
    if (!sceneNodes.current || !parsed?.arm) return;
    applyJointPose(sceneNodes.current.built.jointFrames, angles);
    const pickYaw = pickSolution?.angles?.base_yaw || 0;
    const placeYaw = placeSolution?.angles?.base_yaw || 0;
    sceneNodes.current.objects.pickObject.position.set(pick.x + 0.045 * Math.cos(pickYaw), pick.y + 0.045 * Math.sin(pickYaw), 0.02);
    sceneNodes.current.objects.destination.position.set(place.x + 0.045 * Math.cos(placeYaw), place.y + 0.045 * Math.sin(placeYaw), 0);
    sceneNodes.current.targetMarker.position.set(...Object.values(forwardArmKinematics(parsed.arm, angles)));
  }, [angles, pick, place, parsed, pickSolution?.reachable, placeSolution?.reachable]);

  const setJoint = (name, degrees) => {
    const next = { ...angleRef.current, [name]: Number(degrees) * Math.PI / 180 };
    angleRef.current = next;
    setAngles(next);
  };

  const planAnimation = () => {
    setError("");
    if (!arm || !pickSolution?.reachable || !placeSolution?.reachable) {
      setError("Choose reachable pick and place targets within the generated URDF joint limits.");
      return;
    }
    const approachPick = reachableHeight(arm, pick, [0.08, 0.06, 0.04, 0.02, 0]);
    const liftPick = reachableHeight(arm, pick, [0.12, 0.10, 0.08, 0.06, 0.04, 0.02, 0]);
    const approachPlace = reachableHeight(arm, place, [0.08, 0.06, 0.04, 0.02, 0]);
    const retreatPlace = reachableHeight(arm, place, [0.12, 0.10, 0.08, 0.06, 0.04, 0.02, 0]);
    const specifications = [
      { point: approachPick, closed: false, label: "Approach pick" },
      { point: pick, closed: false, label: "Align with object" },
      { point: pick, closed: true, label: "Close gripper" },
      { point: liftPick, closed: true, label: "Lift" },
      { point: approachPlace, closed: true, label: "Transfer" },
      { point: place, closed: true, label: "Lower" },
      { point: place, closed: false, label: "Release" },
      { point: retreatPlace, closed: false, label: "Retreat" },
    ];
    const frames = [];
    let prior = angleRef.current;
    let time = 0;
    const path = [];
    const obstacles = workcellObstacles(pick, place);
    for (const step of specifications) {
      const solution = solveArmTarget(arm, step.point);
      if (!solution.reachable) {
        setError(`${step.label}: ${solution.reason}`);
        return;
      }
      const previous = prior;
      const seconds = Math.max(0.5, ...JOINTS.map((name) => 1.6 * Math.abs(solution.angles[name] - previous[name]) / Math.max(arm.limits[name].velocity, 0.1)));
      time += seconds;
      frames.push({ time, angles: solution.angles, closed: step.closed, label: step.label, point: solution.actual });
      for (let sample = 1; sample <= 16; sample += 1) {
        const t = sample / 16;
        const pose = Object.fromEntries(JOINTS.map((name) => [name, previous[name] + (solution.angles[name] - previous[name]) * t]));
        const clearance = checkArmTableClearance(arm, pose);
        if (!clearance.clear) {
          setError(`${step.label}: ${clearance.reason} The animation was not started.`);
          return;
        }
        const selfCollision = checkArmSelfCollision(arm, pose);
        if (!selfCollision.clear) {
          setError(`${step.label}: ${selfCollision.reason} The animation was not started.`);
          return;
        }
        const obstacleHit = checkArmObstacles(arm, pose, obstacles);
        if (!obstacleHit.clear) {
          setError(`${step.label}: ${obstacleHit.reason} The animation was not started.`);
          return;
        }
        path.push(forwardArmKinematics(arm, pose));
      }
      prior = solution.angles;
    }
    sceneNodes.current?.objects && (sceneNodes.current.objects.deliveredObject.visible = false);
    playbackRef.current = { startedAt: performance.now(), startAngles: angleRef.current, frames, path };
    setPlaybackNote("Playing a velocity-limited kinematic preview · table, self, and payload collisions checked; grasp is illustrative");
    setPlaying(true);
  };

  const resetPose = () => {
    playbackRef.current = null;
    setPlaying(false);
    const home = Object.fromEntries(JOINTS.map((name) => [name, 0]));
    angleRef.current = home;
    setAngles(home);
    if (sceneNodes.current?.objects) {
      sceneNodes.current.objects.pickObject.visible = true;
      sceneNodes.current.objects.payload.visible = false;
      sceneNodes.current.objects.deliveredObject.visible = false;
    }
    setPlaybackNote("Kinematic preview · no physics or collision guarantees");
  };

  if (!urdf) return <div className="arm-placeholder"><div className="arm-viewport placeholder-viewport"><span className="viewport-tag">URDF-DRIVEN ASSEMBLY</span><div className="vision-empty"><strong>Robot model appears after approval</strong><small>The generated URDF will provide its links, geometry, joint origins, axes, and limits.</small></div></div><p className="arm-preview-note">The project preview is schematic until the design proposal is approved and robot files are generated.</p></div>;
  if (parsed?.parseError) return <div className="arm-placeholder"><p className="error">{parsed.parseError}</p></div>;
  if (!arm) return <div className="arm-placeholder"><p className="error">URDF assembly loaded, but it does not contain the supported four-axis arm joint chain required for target planning.</p></div>;

  return (
    <div className="arm-editor">
      <div className="arm-viewport" ref={mountRef}><span className="viewport-tag">URDF ASSEMBLY · DRAG TO ORBIT · SCROLL TO ZOOM</span>{renderError && <p className="error viewport-error">{renderError}</p>}</div>
      <div className="joint-control-header"><span>JOINTS FROM URDF · RADIANS CONVERTED TO DEGREES</span><button onClick={resetPose}>Reset pose</button></div>
      <div className="joint-controls">{JOINTS.map((name) => {
        const limits = arm.limits[name];
        const value = angles[name] * 180 / Math.PI;
        return <label key={name} className="joint-control"><span>{name.replace(/_/g, " ")}</span><strong>{value.toFixed(0)}°</strong><input type="range" min={Math.ceil(limits.lower * 180 / Math.PI)} max={Math.floor(limits.upper * 180 / Math.PI)} step="1" value={value} disabled={playing} onChange={(event) => setJoint(name, event.target.value)} /></label>;
      })}</div>
      <section className="target-planner"><div className="joint-control-header"><span>TARGET-BASED PICK & PLACE</span><small>Metres · workcell frame</small></div>{initialPickTarget?.label && <p className="vision-target-note">Pick target loaded from calibrated vision: <strong>{initialPickTarget.label}</strong>. Review the estimate before animation.</p>}
        {[{ title: "Pick target", value: pick, set: setPick, solution: pickSolution }, { title: "Place target", value: place, set: setPlace, solution: placeSolution }].map((target) => <fieldset className="target-fields" key={target.title}><legend>{target.title}</legend>{["x", "y", "z"].map((axis) => <label key={axis}>{axis.toUpperCase()}<input type="number" step="0.01" value={target.value[axis]} disabled={playing} onChange={(event) => target.set((old) => numberPoint(old, axis, event.target.value))} /></label>)}<span className={`target-status ${target.solution?.reachable ? "reachable" : "unreachable"}`}>{target.solution?.reachable ? `Reachable · ${(target.solution.error * 1000).toFixed(1)} mm solve error` : target.solution?.reason || "Waiting for URDF"}</span></fieldset>)}
        {error && <p className="error">{error}</p>}
        <div className="planner-actions"><button className="primary-action" disabled={playing || !pickSolution?.reachable || !placeSolution?.reachable} onClick={planAnimation}>{playing ? "Animation running…" : "Animate pick and place →"}</button><span>{playbackNote}</span></div>
      </section>
    </div>
  );
}
