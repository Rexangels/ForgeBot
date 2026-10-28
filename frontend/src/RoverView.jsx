import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

function buildPiecewiseTimeline(waypoints, cruiseSpeed = 0.65, trackWidth = 0.28, wheelRadius = 0.05) {
  const pts = waypoints?.length >= 2 ? waypoints : [[0, 0], [2, 0], [2, 2]];
  const segments = [];
  let totalTime = 0;
  let prevHeading = pts.length >= 2 ? Math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0]) : 0;

  for (let i = 0; i < pts.length - 1; i += 1) {
    const [x0, y0] = pts[i];
    const [x1, y1] = pts[i + 1];
    const dx = x1 - x0;
    const dy = y1 - y0;
    const dist = Math.hypot(dx, dy);
    const heading = Math.atan2(dy, dx);

    let dTheta = heading - prevHeading;
    while (dTheta > Math.PI) dTheta -= Math.PI * 2;
    while (dTheta < -Math.PI) dTheta += Math.PI * 2;

    if (i > 0 && Math.abs(dTheta) > 0.02) {
      const turnDuration = Math.max(0.6, Math.abs(dTheta) / 1.4);
      segments.push({
        type: "turn",
        t0: totalTime,
        t1: totalTime + turnDuration,
        duration: turnDuration,
        x0,
        y0,
        x1: x0,
        y1: y0,
        heading0: prevHeading,
        heading1: heading,
        dTheta,
        dist: 0,
        waypointIndex: i,
      });
      totalTime += turnDuration;
    }

    const driveDuration = Math.max(0.8, dist / Math.max(0.15, cruiseSpeed));
    segments.push({
      type: "drive",
      t0: totalTime,
      t1: totalTime + driveDuration,
      duration: driveDuration,
      x0,
      y0,
      x1,
      y1,
      heading0: heading,
      heading1: heading,
      dTheta: 0,
      dist,
      waypointIndex: i + 1,
    });
    totalTime += driveDuration;
    prevHeading = heading;
  }

  return { mode: "piecewise", segments, totalTime: Math.max(totalTime, 1.0), trackWidth, wheelRadius };
}

function buildSampledTrajectoryTimeline(samples, labelPrefix = "Trained Policy") {
  if (!samples?.length) return null;
  const totalTime = Math.max(0.5, Number(samples[samples.length - 1].t || samples.length * 0.1));
  return {
    mode: "sampled",
    samples,
    totalTime,
    labelPrefix,
  };
}

function sampleTimeline(timeline, t) {
  if (timeline.mode === "sampled") {
    const samples = timeline.samples;
    const clamped = Math.max(0, Math.min(t, timeline.totalTime));
    if (samples.length === 1) {
      const s = samples[0];
      return {
        x: s.x,
        y: s.y,
        heading: s.heading,
        roll_deg: s.roll_deg || 0,
        pitch_deg: s.pitch_deg || 0,
        speed: s.speed || 0,
        omega: s.omega || 0,
        clearance_m: s.clearance_m ?? 1.0,
        leftWheelAngle: s.left_wheel_rad || 0,
        rightWheelAngle: s.right_wheel_rad || 0,
        phase: `${timeline.labelPrefix} · Navigating obstacle course`,
      };
    }
    let idx = 0;
    while (idx < samples.length - 2 && Number(samples[idx + 1].t) < clamped) {
      idx += 1;
    }
    const a = samples[idx];
    const b = samples[idx + 1];
    const dt = Math.max(1e-4, Number(b.t) - Number(a.t));
    const u = Math.max(0, Math.min(1, (clamped - Number(a.t)) / dt));
    let dHeading = Number(b.heading) - Number(a.heading);
    while (dHeading > Math.PI) dHeading -= Math.PI * 2;
    while (dHeading < -Math.PI) dHeading += Math.PI * 2;

    const clearance = (Number(a.clearance_m ?? 1.0) * (1 - u)) + (Number(b.clearance_m ?? 1.0) * u);
    const rollDeg = Number(a.roll_deg || 0) + (Number(b.roll_deg || 0) - Number(a.roll_deg || 0)) * u;
    const pitchDeg = Number(a.pitch_deg || 0) + (Number(b.pitch_deg || 0) - Number(a.pitch_deg || 0)) * u;
    const isCollision = clearance <= 0.005;
    const isNearEnd = clamped >= timeline.totalTime - 0.12;
    return {
      x: Number(a.x) + (Number(b.x) - Number(a.x)) * u,
      y: Number(a.y) + (Number(b.y) - Number(a.y)) * u,
      heading: Number(a.heading) + dHeading * u,
      roll_deg: rollDeg,
      pitch_deg: pitchDeg,
      speed: Number(a.speed || 0) + (Number(b.speed || 0) - Number(a.speed || 0)) * u,
      omega: Number(a.omega || 0) + (Number(b.omega || 0) - Number(a.omega || 0)) * u,
      clearance_m: clearance,
      leftWheelAngle: Number(a.left_wheel_rad || 0) + (Number(b.left_wheel_rad || 0) - Number(a.left_wheel_rad || 0)) * u,
      rightWheelAngle: Number(a.right_wheel_rad || 0) + (Number(b.right_wheel_rad || 0) - Number(a.right_wheel_rad || 0)) * u,
      phase: isCollision
        ? `${timeline.labelPrefix} · OBSTACLE COLLISION!`
        : isNearEnd
        ? `${timeline.labelPrefix} · Goal W3 Reached`
        : `${timeline.labelPrefix} · Slalom Obstacle Avoidance`,
    };
  }

  const clamped = Math.max(0, Math.min(t, timeline.totalTime));
  let leftWheelAngle = 0;
  let rightWheelAngle = 0;
  const r = Math.max(0.02, timeline.wheelRadius);
  const halfTrack = Math.max(0.05, timeline.trackWidth / 2);

  for (const seg of timeline.segments) {
    if (clamped >= seg.t1) {
      if (seg.type === "drive") {
        const dPhi = seg.dist / r;
        leftWheelAngle += dPhi;
        rightWheelAngle += dPhi;
      } else {
        const arc = seg.dTheta * halfTrack;
        leftWheelAngle -= arc / r;
        rightWheelAngle += arc / r;
      }
      continue;
    }

    const uRaw = seg.duration > 0 ? (clamped - seg.t0) / seg.duration : 1;
    const u = uRaw * uRaw * (3 - 2 * uRaw);
    const du = 6 * uRaw * (1 - uRaw);

    if (seg.type === "drive") {
      const distNow = seg.dist * u;
      const dPhi = distNow / r;
      leftWheelAngle += dPhi;
      rightWheelAngle += dPhi;
      const vNow = seg.duration > 0 ? (seg.dist / seg.duration) * du : 0;
      return {
        x: seg.x0 + (seg.x1 - seg.x0) * u,
        y: seg.y0 + (seg.y1 - seg.y0) * u,
        heading: seg.heading0,
        speed: vNow,
        omega: 0,
        clearance_m: 1.0,
        leftWheelAngle,
        rightWheelAngle,
        phase: `Driving to W${seg.waypointIndex}`,
      };
    }

    const thetaNow = seg.heading0 + seg.dTheta * u;
    const arc = seg.dTheta * u * halfTrack;
    leftWheelAngle -= arc / r;
    rightWheelAngle += arc / r;
    const omegaNow = seg.duration > 0 ? (seg.dTheta / seg.duration) * du : 0;
    return {
      x: seg.x0,
      y: seg.y0,
      heading: thetaNow,
      speed: 0,
      omega: omegaNow,
      clearance_m: 1.0,
      leftWheelAngle,
      rightWheelAngle,
      phase: `Pivoting at W${seg.waypointIndex}`,
    };
  }

  const last = timeline.segments[timeline.segments.length - 1];
  return {
    x: last ? last.x1 : 0,
    y: last ? last.y1 : 0,
    heading: last ? last.heading1 : 0,
    speed: 0,
    omega: 0,
    clearance_m: 1.0,
    leftWheelAngle,
    rightWheelAngle,
    phase: "Goal reached",
  };
}

function materialForVisual(visual, partRole = "") {
  const text = `${partRole}`.toLowerCase();
  const alpha = visual.alpha !== undefined ? Number(visual.alpha) : 1.0;
  let metalness = 0.22;
  let roughness = 0.48;
  if (/wheel|tire|rubber/.test(text) && !visual.is_detail) {
    metalness = 0.06;
    roughness = 0.85;
  } else if (/standoff|spacer|pillar|caster|shaft|sonar|ultrasonic/.test(text) || visual.color === "#AEB9C2") {
    metalness = 0.72;
    roughness = 0.26;
  } else if (/motor|servo|gear/.test(text)) {
    metalness = 0.32;
    roughness = 0.42;
  } else if (/plate|deck|chassis/.test(text)) {
    metalness = 0.15;
    roughness = 0.28;
  }
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color(visual.color || "#4c9b8c"),
    metalness,
    roughness,
    transparent: alpha < 0.99,
    opacity: alpha,
  });
}

function buildVisualMesh(visual, role) {
  const [dx, dy, dz] = (visual.dimensions_m || [0.05, 0.05, 0.05]).map(Number);
  let geom;
  if (visual.geometry === "cylinder") {
    const r = Math.min(dx, dy) / 2;
    geom = new THREE.CylinderGeometry(r, r, dz, 28);
    geom.rotateX(Math.PI / 2);
  } else if (visual.geometry === "sphere") {
    const r = Math.min(dx, dy, dz) / 2;
    geom = new THREE.SphereGeometry(r, 24, 24);
  } else {
    geom = new THREE.BoxGeometry(dx, dy, dz);
  }
  const mesh = new THREE.Mesh(geom, materialForVisual(visual, role));
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  const [ox, oy, oz] = (visual.origin_m || [0, 0, 0]).map(Number);
  const [rr, rp, ry] = (visual.rpy || [0, 0, 0]).map(Number);
  mesh.position.set(ox, oy, oz);
  mesh.rotation.set(rr, rp, ry, "XYZ");
  return mesh;
}

export default function RoverView({ sim }) {
  const mountRef = useRef(null);
  const fallbackCanvasRef = useRef(null);
  const stateRef = useRef({
    playing: true,
    time: 0,
    speedMultiplier: 1.0,
    followCam: false,
    showVisionPip: true,
  });

  const [playing, setPlaying] = useState(true);
  const [simTime, setSimTime] = useState(0);
  const [speedMultiplier, setSpeedMultiplier] = useState(1.0);
  const [followCam, setFollowCam] = useState(false);
  const [showWiring, setShowWiring] = useState(true);
  const [showVisionPip, setShowVisionPip] = useState(true);
  const [episodeMode, setEpisodeMode] = useState("converged"); // "converged" | "initial"
  const [webglAvailable, setWebglAvailable] = useState(true);

  const obstacleTraining = sim?.obstacle_training;
  const obstacles = useMemo(() => obstacleTraining?.obstacles || [], [obstacleTraining]);
  const waypoints = useMemo(
    () => obstacleTraining?.course_waypoints || sim?.waypoints || [[0, 0], [2, 0], [2, 2]],
    [obstacleTraining, sim]
  );
  const cruiseSpeed = sim?.drivetrain?.cruise_speed_m_s || sim?.max_speed_used || 0.65;
  const trackWidth = sim?.drivetrain?.track_width_m || 0.3;
  const wheelRadius = (sim?.drivetrain?.wheel_diameter_m || 0.14) / 2;

  const timeline = useMemo(() => {
    if (obstacleTraining) {
      if (episodeMode === "initial" && obstacleTraining.initial_episode?.trajectory?.length) {
        return buildSampledTrajectoryTimeline(
          obstacleTraining.initial_episode.trajectory,
          `Ep 1 (Untuned · ${obstacleTraining.initial_episode.collisions} hit)`
        );
      }
      if (obstacleTraining.best_episode?.trajectory?.length) {
        return buildSampledTrajectoryTimeline(
          obstacleTraining.best_episode.trajectory,
          `Ep ${obstacleTraining.converged_episode} (Trained · 0 hits)`
        );
      }
    }
    return buildPiecewiseTimeline(waypoints, cruiseSpeed, trackWidth, wheelRadius);
  }, [obstacleTraining, episodeMode, waypoints, cruiseSpeed, trackWidth, wheelRadius]);

  const currentPose = useMemo(
    () => sampleTimeline(timeline, simTime),
    [timeline, simTime]
  );

  // Compute real-time OpenCV obstacle bounding-box detections in the robot's 1st-person camera frustum
  const visibleObstacleDetections = useMemo(() => {
    if (!obstacles.length) return [];
    const cosH = Math.cos(currentPose.heading || 0);
    const sinH = Math.sin(currentPose.heading || 0);
    const detections = [];
    obstacles.forEach((obs) => {
      const dx = Number(obs.x_m) - Number(currentPose.x);
      const dy = Number(obs.y_m) - Number(currentPose.y);
      const relForward = dx * cosH + dy * sinH;
      const relLeft = -dx * sinH + dy * cosH;
      if (relForward > 0.08 && relForward < 2.35) {
        const bearingRad = Math.atan2(relLeft, relForward);
        if (Math.abs(bearingRad) < 0.56) {
          const rad = Number(obs.radius_m || 0.14);
          const h = Number(obs.height_m || 0.28);
          const surfaceDist = Math.max(0.01, Math.hypot(dx, dy) - rad - 0.11);
          const screenX = 50 - (bearingRad / 0.56) * 42;
          const boxW = Math.max(16, Math.min(64, (rad * 2.1 / relForward) * 85));
          const boxH = Math.max(22, Math.min(72, (h / relForward) * 78));
          detections.push({
            id: obs.id,
            label: (obs.label || obs.id || "OBS").split(" ")[0].toUpperCase(),
            distM: surfaceDist,
            leftPct: Math.max(4, Math.min(92 - boxW, screenX - boxW / 2)),
            topPct: Math.max(16, Math.min(82 - boxH, 52 - boxH * 0.52)),
            widthPct: boxW,
            heightPct: boxH,
            threat: surfaceDist < 0.16 ? "critical" : surfaceDist < 0.42 ? "warning" : "clear",
          });
        }
      }
    });
    return detections.sort((a, b) => a.distM - b.distM).slice(0, 3);
  }, [obstacles, currentPose.x, currentPose.y, currentPose.heading]);

  useEffect(() => {
    stateRef.current.playing = playing;
  }, [playing]);

  useEffect(() => {
    stateRef.current.speedMultiplier = speedMultiplier;
  }, [speedMultiplier]);

  useEffect(() => {
    stateRef.current.followCam = followCam;
  }, [followCam]);

  useEffect(() => {
    stateRef.current.showVisionPip = showVisionPip;
  }, [showVisionPip]);

  useEffect(() => {
    stateRef.current.time = 0;
    setSimTime(0);
  }, [episodeMode]);

  useEffect(() => {
    const container = mountRef.current;
    if (!container) return undefined;

    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    } catch {
      setWebglAvailable(false);
      return undefined;
    }

    const width = container.clientWidth || 720;
    const height = 450;
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.12;
    container.innerHTML = "";
    container.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#0f172a");
    scene.fog = new THREE.FogExp2("#0f172a", 0.065);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.05, 60);
    camera.position.set(1.6, 2.5, 3.4);

    // 1st-Person Robot Onboard Optical Camera for PIP HUD
    const fpvCamera = new THREE.PerspectiveCamera(62, 236 / 148, 0.04, 18);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(1.6, 0.15, -1.1);
    controls.update();

    scene.add(new THREE.HemisphereLight("#e2e8f0", "#1e293b", 0.95));
    const dirLight = new THREE.DirectionalLight("#ffffff", 1.35);
    dirLight.position.set(4.0, 6.0, 3.0);
    dirLight.castShadow = true;
    dirLight.shadow.mapSize.set(1024, 1024);
    scene.add(dirLight);

    // Z-up world container: maps robotics (X, Y, Z_up) to Three.js (X, Z_up, -Y)
    const world = new THREE.Group();
    world.rotation.x = -Math.PI / 2;
    scene.add(world);

    // Ground plane & grid
    const groundGeo = new THREE.PlaneGeometry(14, 14);
    const groundMat = new THREE.MeshStandardMaterial({
      color: "#131c31",
      roughness: 0.92,
      metalness: 0.05,
    });
    const groundMesh = new THREE.Mesh(groundGeo, groundMat);
    groundMesh.position.set(1.6, 1.2, -0.002);
    groundMesh.receiveShadow = true;
    world.add(groundMesh);

    const grid = new THREE.GridHelper(12, 24, "#0ea5e9", "#1e293b");
    grid.rotation.x = Math.PI / 2;
    grid.position.set(1.6, 1.2, 0.0);
    world.add(grid);

    // 3D Obstacles in the world
    obstacles.forEach((obs) => {
      const obsGroup = new THREE.Group();
      const ox = Number(obs.x_m);
      const oy = Number(obs.y_m);
      const oh = Number(obs.height_m || 0.28);
      const orad = Number(obs.radius_m || 0.14);
      obsGroup.position.set(ox, oy, 0);

      let mesh;
      if (obs.shape === "box") {
        const w = Number(obs.width_m || orad * 1.6);
        const d = Number(obs.depth_m || orad * 1.6);
        const boxGeo = new THREE.BoxGeometry(w, d, oh);
        const boxMat = new THREE.MeshStandardMaterial({
          color: obs.color || "#d97706",
          roughness: 0.55,
          metalness: 0.25,
        });
        mesh = new THREE.Mesh(boxGeo, boxMat);
      } else {
        const cylGeo = new THREE.CylinderGeometry(orad, orad, oh, 28);
        cylGeo.rotateX(Math.PI / 2);
        const cylMat = new THREE.MeshStandardMaterial({
          color: obs.color || "#ef4444",
          roughness: 0.45,
          metalness: 0.35,
        });
        mesh = new THREE.Mesh(cylGeo, cylMat);
      }
      mesh.position.z = oh / 2;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      obsGroup.add(mesh);

      // Hazard warning ring around each obstacle base
      const hazardRing = new THREE.Mesh(
        new THREE.RingGeometry(orad + 0.01, orad + 0.045, 32),
        new THREE.MeshBasicMaterial({
          color: obs.color || "#ef4444",
          side: THREE.DoubleSide,
          transparent: true,
          opacity: 0.45,
        })
      );
      hazardRing.position.z = 0.003;
      obsGroup.add(hazardRing);

      world.add(obsGroup);
    });

    // Render Untuned Episode 1 Ghost Trajectory (red/amber) for comparison
    const initTraj = obstacleTraining?.initial_episode?.trajectory;
    if (initTraj?.length >= 2) {
      const initPts = initTraj.map((pt) => new THREE.Vector3(Number(pt.x), Number(pt.y), 0.005));
      const initLine = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(initPts),
        new THREE.LineBasicMaterial({
          color: "#f43f5e",
          transparent: true,
          opacity: episodeMode === "initial" ? 0.9 : 0.42,
        })
      );
      world.add(initLine);
    }

    // Render Converged Trained Trajectory (cyan/emerald)
    const bestTraj = obstacleTraining?.best_episode?.trajectory;
    if (bestTraj?.length >= 2) {
      const bestPts = bestTraj.map((pt) => new THREE.Vector3(Number(pt.x), Number(pt.y), 0.007));
      const bestLine = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(bestPts),
        new THREE.LineBasicMaterial({
          color: "#10b981",
          transparent: true,
          opacity: episodeMode === "converged" ? 0.85 : 0.35,
        })
      );
      world.add(bestLine);
    } else {
      const routePoints3D = waypoints.map(([wx, wy]) => new THREE.Vector3(wx, wy, 0.006));
      world.add(
        new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(routePoints3D),
          new THREE.LineBasicMaterial({ color: "#38bdf8" })
        )
      );
    }

    // Waypoint beacons
    waypoints.forEach(([wx, wy], idx) => {
      const beaconGroup = new THREE.Group();
      beaconGroup.position.set(wx, wy, 0);

      const ringGeo = new THREE.RingGeometry(0.08, 0.12, 32);
      const ringMat = new THREE.MeshBasicMaterial({
        color: idx === 0 ? "#94a3b8" : idx === waypoints.length - 1 ? "#22c55e" : "#38bdf8",
        side: THREE.DoubleSide,
      });
      const ring = new THREE.Mesh(ringGeo, ringMat);
      ring.position.z = 0.004;
      beaconGroup.add(ring);

      const postGeo = new THREE.CylinderGeometry(0.014, 0.014, 0.18, 16);
      postGeo.rotateX(Math.PI / 2);
      const postMat = new THREE.MeshStandardMaterial({
        color: idx === waypoints.length - 1 ? "#22c55e" : "#0ea5e9",
        emissive: idx === waypoints.length - 1 ? "#15803d" : "#0369a1",
        emissiveIntensity: 0.6,
      });
      const post = new THREE.Mesh(postGeo, postMat);
      post.position.z = 0.09;
      beaconGroup.add(post);

      world.add(beaconGroup);
    });

    // Dynamic breadcrumb trail
    const maxTrailPts = 240;
    const trailPositions = new Float32Array(maxTrailPts * 3);
    const trailGeo = new THREE.BufferGeometry();
    trailGeo.setAttribute("position", new THREE.BufferAttribute(trailPositions, 3));
    trailGeo.setDrawRange(0, 0);
    const trailLine = new THREE.Line(
      trailGeo,
      new THREE.LineBasicMaterial({ color: episodeMode === "initial" ? "#fb7185" : "#22d3ee" })
    );
    world.add(trailLine);

    // Build 3D robot assembly inside robotRoot
    const robotRoot = new THREE.Group();
    const robotModelGroup = new THREE.Group();
    robotRoot.add(robotModelGroup);
    world.add(robotRoot);

    const previewParts = sim?.robot_preview?.components || [];
    const locomotionProfile = sim?.robot_preview?.locomotion_profile || null;
    const groundZ = Number(sim?.robot_preview?.ground_z_m ?? 0.0);
    const wheelGroups = [];
    const articulatedJoints = [];
    let forwardIsY = true;

    if (previewParts.length > 0) {
      const byId = new Map(previewParts.map((p) => [p.id, p]));
      if (locomotionProfile?.forward_axis) {
        forwardIsY = locomotionProfile.forward_axis === "y";
      } else {
        const wheelParts = previewParts.filter((p) => p.is_wheel);
        if (wheelParts.length >= 2) {
          const spanX =
            Math.max(...wheelParts.map((w) => Number(w.position_m[0]))) -
            Math.min(...wheelParts.map((w) => Number(w.position_m[0])));
          const spanY =
            Math.max(...wheelParts.map((w) => Number(w.position_m[1]))) -
            Math.min(...wheelParts.map((w) => Number(w.position_m[1])));
          forwardIsY = spanX >= spanY;
        } else {
          let pairDx = 0;
          let pairDy = 0;
          previewParts.forEach((p) => {
            if (p.id && p.id.includes("left")) {
              const partner = byId.get(p.id.replace("left", "right"));
              if (partner) {
                pairDx += Math.abs(Number(p.position_m[0]) - Number(partner.position_m[0]));
                pairDy += Math.abs(Number(p.position_m[1]) - Number(partner.position_m[1]));
              }
            }
          });
          forwardIsY = pairDx > pairDy;
        }
      }

      const partGroupMap = new Map();

      const inferRoleFallback = (part) => {
        if (part.anim_role && part.anim_role !== "static") return part.anim_role;
        const idName = `${part.id || ""} ${part.name || ""}`.toLowerCase();
        const parentObj = part.parent_id ? byId.get(part.parent_id) : null;
        const parentName = `${part.parent_id || ""} ${parentObj?.name || ""}`.toLowerCase();
        const lat = forwardIsY ? -Number(part.position_m[0]) : Number(part.position_m[1]);
        const side = idName.includes("left") ? "left" : idName.includes("right") ? "right" : lat >= 0 ? "left" : "right";
        if (part.is_wheel) return `wheel_${side}`;
        if (/hip_mount|hip_joint|\bhip\b/.test(idName)) return `hip_${side}`;
        if (/femur|thigh/.test(idName)) return /hip/.test(parentName) ? `femur_${side}` : `hip_${side}`;
        if (/knee_servo|knee_joint|\bknee\b/.test(idName)) return `knee_${side}`;
        if (/tibia|shin/.test(idName)) return /knee/.test(parentName) ? `tibia_${side}` : `knee_${side}`;
        if (/foot_claw|claw|talon|foot|ankle/.test(idName)) return `ankle_${side}`;
        if (/wing/.test(idName)) return `wing_${side}`;
        if (/tail/.test(idName)) return "tail_stabilizer";
        if (/neck/.test(idName)) return "neck_yaw";
        if (/head/.test(idName)) return /neck/.test(parentName) ? "head_pitch" : "neck_yaw";
        if (/beak|jaw/.test(idName)) return "beak_jaw";
        if (!part.parent_id || /torso|chest|chassis_base/.test(idName)) return "torso_root";
        return "static";
      };

      // Pass 1: build each component's THREE.Group and visual meshes
      previewParts.forEach((part) => {
        const partGroup = new THREE.Group();
        const visuals = part.visuals?.length
          ? part.visuals
          : [{ geometry: part.geometry, dimensions_m: part.dimensions_m, origin_m: [0, 0, 0], rpy: [0, 0, 0], color: part.color }];

        visuals.forEach((vis) => {
          partGroup.add(buildVisualMesh(vis, `${part.name} ${part.role}`));
        });
        partGroupMap.set(part.id, { group: partGroup, part });
      });

      // Pass 2: assemble parent-child kinematic hierarchy so joint rotations propagate to downstream limbs
      previewParts.forEach((part) => {
        const entry = partGroupMap.get(part.id);
        const partGroup = entry.group;
        const [px, py, pz] = part.position_m.map(Number);
        const parentEntry = part.parent_id ? partGroupMap.get(part.parent_id) : null;

        if (parentEntry) {
          const [ppx, ppy, ppz] = parentEntry.part.position_m.map(Number);
          partGroup.position.set(px - ppx, py - ppy, pz - ppz);
          parentEntry.group.add(partGroup);
        } else {
          partGroup.position.set(px, py, pz - groundZ);
          robotModelGroup.add(partGroup);
        }

        const role = inferRoleFallback(part);
        if (part.is_wheel) {
          const isLeft = forwardIsY ? px < 0 : py > 0;
          wheelGroups.push({ group: partGroup, isLeft, axis: forwardIsY ? "x" : "y" });
        } else if (role !== "static") {
          articulatedJoints.push({
            id: part.id,
            role,
            group: partGroup,
            basePos: partGroup.position.clone(),
          });
        }
      });

      // Render 3D Routed Electrical Wiring Harness (16-26 AWG cables + connector spheres)
      const harnessCables =
        sim?.robot_preview?.wiring_harness_3d ||
        sim?.mechatronics?.pcb_wiring?.wiring_harness_3d ||
        [];
      if (showWiring && harnessCables.length > 0) {
        const harnessGroup = new THREE.Group();
        harnessCables.forEach((cable) => {
          const pts = (cable.points_m || []).map(
            (pt) => new THREE.Vector3(Number(pt[0]), Number(pt[1]), Number(pt[2]) - groundZ)
          );
          if (pts.length >= 2) {
            const curve = new THREE.CatmullRomCurve3(pts, false, "catmullrom", 0.12);
            const tubeGeo = new THREE.TubeGeometry(
              curve,
              20,
              Math.max(0.0016, Number(cable.radius_m || 0.002)),
              10,
              false
            );
            const tubeMat = new THREE.MeshStandardMaterial({
              color: cable.color || "#ef4444",
              roughness: 0.4,
              metalness: 0.15,
            });
            const tubeMesh = new THREE.Mesh(tubeGeo, tubeMat);
            tubeMesh.castShadow = true;
            harnessGroup.add(tubeMesh);

            // Connector boot spheres at endpoints
            [pts[0], pts[pts.length - 1]].forEach((endPt) => {
              const boot = new THREE.Mesh(
                new THREE.SphereGeometry(Math.max(0.0024, Number(cable.radius_m || 0.002) * 1.35), 10, 10),
                new THREE.MeshStandardMaterial({ color: "#f8fafc", roughness: 0.5 })
              );
              boot.position.copy(endPt);
              harnessGroup.add(boot);
            });
          }
        });
        robotModelGroup.add(harnessGroup);
      }
    } else {
      forwardIsY = false;
      const chassis = new THREE.Mesh(
        new THREE.BoxGeometry(0.36, 0.24, 0.08),
        new THREE.MeshStandardMaterial({ color: "#287c72", roughness: 0.35, metalness: 0.2 })
      );
      chassis.position.set(0, 0, 0.09);
      chassis.castShadow = true;
      robotModelGroup.add(chassis);

      [[-0.1, 0.14], [0.1, 0.14], [-0.1, -0.14], [0.1, -0.14]].forEach(([wx, wy]) => {
        const wg = new THREE.Group();
        wg.position.set(wx, wy, 0.055);
        const wMesh = new THREE.Mesh(
          new THREE.CylinderGeometry(0.055, 0.055, 0.035, 24),
          new THREE.MeshStandardMaterial({ color: "#1e293b", roughness: 0.85 })
        );
        wg.add(wMesh);
        wheelGroups.push({ group: wg, isLeft: wy > 0, axis: "y" });
        robotModelGroup.add(wg);
      });
    }

    if (forwardIsY) {
      robotModelGroup.rotation.z = -Math.PI / 2;
    }

    // 3D Center of Mass (CoM) sphere + support polygon overlay
    const overlayGroup = new THREE.Group();
    robotModelGroup.add(overlayGroup);

    const com = sim?.mass_properties?.center_of_mass_m;
    if (com?.length === 3) {
      const comZ = Math.max(0.02, com[2] - groundZ);
      const comMesh = new THREE.Mesh(
        new THREE.SphereGeometry(0.016, 16, 16),
        new THREE.MeshBasicMaterial({ color: "#f59e0b" })
      );
      comMesh.position.set(com[0], com[1], comZ);
      overlayGroup.add(comMesh);

      const plumbGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(com[0], com[1], comZ),
        new THREE.Vector3(com[0], com[1], 0.003),
      ]);
      overlayGroup.add(
        new THREE.Line(plumbGeo, new THREE.LineBasicMaterial({ color: "#f59e0b" }))
      );
    }

    const fp = sim?.stability?.support_footprint_m;
    if (fp) {
      const fpPts = [
        new THREE.Vector3(fp.x_min, fp.y_min, 0.004),
        new THREE.Vector3(fp.x_max, fp.y_min, 0.004),
        new THREE.Vector3(fp.x_max, fp.y_max, 0.004),
        new THREE.Vector3(fp.x_min, fp.y_max, 0.004),
        new THREE.Vector3(fp.x_min, fp.y_min, 0.004),
      ];
      const fpLine = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(fpPts),
        new THREE.LineBasicMaterial({ color: "#10b981" })
      );
      overlayGroup.add(fpLine);
    }

    // 3D Ultrasonic / Rangefinder sensor sweep cone along +X of robotRoot
    const coneGeo = new THREE.ConeGeometry(0.24, 0.58, 24, 1, true);
    coneGeo.translate(0, -0.29, 0);
    coneGeo.rotateZ(-Math.PI / 2);
    const coneMat = new THREE.MeshBasicMaterial({
      color: "#38bdf8",
      transparent: true,
      opacity: 0.16,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const sensorCone = new THREE.Mesh(coneGeo, coneMat);
    sensorCone.position.set(0.14, 0, 0.12);
    robotRoot.add(sensorCone);

    let animId;
    let lastClock = performance.now();

    const animate = (now) => {
      animId = requestAnimationFrame(animate);
      const dt = Math.min(0.1, (now - lastClock) / 1000);
      lastClock = now;

      if (stateRef.current.playing) {
        let nextT = stateRef.current.time + dt * stateRef.current.speedMultiplier;
        if (nextT > timeline.totalTime) {
          nextT = 0;
        }
        stateRef.current.time = nextT;
        setSimTime(nextT);
      }

      const pose = sampleTimeline(timeline, stateRef.current.time);
      robotRoot.position.set(pose.x, pose.y, 0);
      const rollRad = ((pose.roll_deg || 0) * Math.PI) / 180.0;
      const pitchRad = ((pose.pitch_deg || 0) * Math.PI) / 180.0;
      robotRoot.rotation.set(rollRad, pitchRad, pose.heading, "ZYX");

      wheelGroups.forEach((wg) => {
        const angle = wg.isLeft ? pose.leftWheelAngle : pose.rightWheelAngle;
        if (wg.axis === "x") {
          wg.group.rotation.x = -angle;
        } else {
          wg.group.rotation.y = angle;
        }
      });

      // Articulated Biped / Avian / Multi-Legged Joint & Wing Animation
      if (articulatedJoints.length > 0 && wheelGroups.length === 0) {
        const walkPhase = pose.leftWheelAngle * 0.62 + stateRef.current.time * 4.2;
        const moveFactor = Math.min(1.0, Math.hypot(pose.speed || 0, (pose.omega || 0) * 0.16) / 0.18);
        const turnRate = Number(pose.omega || 0);
        const isDodging = (pose.clearance_m ?? 1.0) < 0.24 || Math.abs(turnRate) > 0.35;

        articulatedJoints.forEach((j) => {
          const { role, group, basePos } = j;
          const isLeft = role.endsWith("_left");
          const sideSign = isLeft ? 1 : -1;
          const legPhase = isLeft ? walkPhase : walkPhase + Math.PI;
          const stride = Math.sin(legPhase) * 0.44 * moveFactor;
          const swingLift = Math.max(0, Math.cos(legPhase)) * 0.56 * moveFactor;

          if (role === "torso_root") {
            // Double-frequency bipedal stride bob + stance weight shift + forward speed lean
            const bobZ = Math.abs(Math.sin(walkPhase)) * 0.015 * moveFactor;
            group.position.set(basePos.x, basePos.y, basePos.z + bobZ);
            if (forwardIsY) {
              group.rotation.y = Math.sin(walkPhase) * 0.055 * moveFactor;
              group.rotation.x = -Math.min(0.14, (pose.speed || 0) * 0.22);
            } else {
              group.rotation.x = Math.sin(walkPhase) * 0.055 * moveFactor;
              group.rotation.y = Math.min(0.14, (pose.speed || 0) * 0.22);
            }
          } else if (role === "hip_left" || role === "hip_right") {
            if (forwardIsY) {
              group.rotation.x = stride;
            } else {
              group.rotation.y = -stride;
            }
          } else if (role === "knee_left" || role === "knee_right") {
            if (forwardIsY) {
              group.rotation.x = -swingLift;
            } else {
              group.rotation.y = swingLift;
            }
          } else if (role === "ankle_left" || role === "ankle_right") {
            const anklePitch = stride * 0.65 - swingLift * 0.48;
            if (forwardIsY) {
              group.rotation.x = -anklePitch;
            } else {
              group.rotation.y = anklePitch;
            }
          } else if (role === "wing_left" || role === "wing_right") {
            // Active wing-assisted balance: gentle counter-oscillation during stride, rapid wing-beats when turning/dodging
            const flapFreq = isDodging ? 11.5 : 5.5;
            const flapAmp = isDodging ? 0.46 : 0.18 * moveFactor + 0.04;
            const flap = (0.35 + 0.65 * Math.sin(stateRef.current.time * flapFreq + (isLeft ? 0 : 0.15))) * flapAmp;
            if (forwardIsY) {
              group.rotation.y = -sideSign * flap;
              group.rotation.x = Math.cos(walkPhase + (isLeft ? Math.PI : 0)) * 0.12 * moveFactor;
            } else {
              group.rotation.x = sideSign * flap;
              group.rotation.y = Math.cos(walkPhase + (isLeft ? Math.PI : 0)) * 0.12 * moveFactor;
            }
          } else if (role === "tail_stabilizer") {
            // Hydraulic tail fan pitches against stride bob and yaws as a rudder into turns
            const tailPitch = -Math.abs(Math.cos(walkPhase)) * 0.16 * moveFactor - (isDodging ? 0.14 : 0);
            if (forwardIsY) {
              group.rotation.x = -tailPitch;
            } else {
              group.rotation.y = tailPitch;
            }
            group.rotation.z = Math.max(-0.35, Math.min(0.35, -turnRate * 0.25));
          } else if (role === "neck_yaw") {
            // Active optical head scanning into turns and obstacle lookahead
            const scanYaw = Math.max(-0.48, Math.min(0.48, turnRate * 0.38 + Math.sin(stateRef.current.time * 2.6) * 0.16));
            group.rotation.z = scanYaw;
          } else if (role === "head_pitch") {
            const headBob = -Math.abs(Math.sin(walkPhase)) * 0.09 * moveFactor + Math.sin(stateRef.current.time * 3.1) * 0.05;
            if (forwardIsY) {
              group.rotation.x = -headBob;
            } else {
              group.rotation.y = headBob;
            }
          } else if (role === "beak_jaw") {
            const beakOpen = Math.max(0, Math.sin(stateRef.current.time * 4.8)) * 0.09;
            if (forwardIsY) {
              group.rotation.x = -beakOpen;
            } else {
              group.rotation.y = beakOpen;
            }
          }
        });
      }

      // Sensor cone changes color when an obstacle enters close proximity
      if (pose.clearance_m <= 0.01) {
        coneMat.color.set("#ef4444");
      } else if (pose.clearance_m < 0.12) {
        coneMat.color.set("#f59e0b");
      } else {
        coneMat.color.set("#38bdf8");
      }
      const pulse = 0.85 + 0.15 * Math.sin(now * 0.006);
      sensorCone.scale.set(pulse, pulse, pulse);

      const steps = Math.max(2, Math.floor((stateRef.current.time / timeline.totalTime) * (maxTrailPts - 1)));
      for (let i = 0; i <= steps; i += 1) {
        const tStep = (i / Math.max(1, maxTrailPts - 1)) * timeline.totalTime;
        const pStep = sampleTimeline(timeline, Math.min(tStep, stateRef.current.time));
        trailPositions[i * 3] = pStep.x;
        trailPositions[i * 3 + 1] = pStep.y;
        trailPositions[i * 3 + 2] = 0.009;
      }
      trailGeo.attributes.position.needsUpdate = true;
      trailGeo.setDrawRange(0, steps + 1);

      if (stateRef.current.followCam) {
        controls.target.lerp(new THREE.Vector3(pose.x, 0.15, -pose.y), 0.12);
      }

      controls.update();
      const curW = renderer.domElement.clientWidth || width;
      const curH = height;
      renderer.setScissorTest(false);
      renderer.setViewport(0, 0, curW, curH);
      renderer.render(scene, camera);

      // Render 1st-Person Onboard Optical Camera PIP in top-right viewport
      if (stateRef.current.showVisionPip && curW > 420) {
        const pipW = 236;
        const pipH = 148;
        const pipX = curW - pipW - 12;
        const pipY = curH - pipH - 12;
        const cosH = Math.cos(pose.heading || 0);
        const sinH = Math.sin(pose.heading || 0);
        const camHeightM = wheelGroups.length > 0 ? 0.24 : 0.36;
        const camForwardOffset = 0.12;
        const eyeX = pose.x + cosH * camForwardOffset;
        const eyeYWorld = pose.y + sinH * camForwardOffset;
        const lookX = pose.x + cosH * 1.65;
        const lookYWorld = pose.y + sinH * 1.65;
        // Map world (x, y, z_up) -> Three.js scene (x, z_up, -y)
        fpvCamera.position.set(eyeX, camHeightM, -eyeYWorld);
        fpvCamera.lookAt(lookX, camHeightM * 0.55, -lookYWorld);
        sensorCone.visible = false;
        renderer.setScissorTest(true);
        renderer.setViewport(pipX, pipY, pipW, pipH);
        renderer.setScissor(pipX, pipY, pipW, pipH);
        renderer.render(scene, fpvCamera);
        renderer.setScissorTest(false);
        sensorCone.visible = true;
      }
    };

    animId = requestAnimationFrame(animate);

    const handleResize = () => {
      if (!container) return;
      const w = container.clientWidth || 720;
      camera.aspect = w / height;
      camera.updateProjectionMatrix();
      renderer.setSize(w, height);
    };
    window.addEventListener("resize", handleResize);

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener("resize", handleResize);
      controls.dispose();
      renderer.dispose();
    };
  }, [sim, timeline, waypoints, obstacles, obstacleTraining, episodeMode, showWiring]);

  useEffect(() => {
    if (webglAvailable) return;
    const canvas = fallbackCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }, [webglAvailable, sim]);

  const wheelRpm = wheelRadius > 0 ? Math.abs((currentPose.speed / wheelRadius) * (60 / (2 * Math.PI))) : 0;
  const locomotionMode = sim?.robot_preview?.locomotion_profile?.mode || ((sim?.drivetrain?.wheel_count ?? 2) > 0 ? "wheeled" : "bipedal_avian");
  const gaitLabel = sim?.robot_preview?.locomotion_profile?.gait_label || (locomotionMode === "wheeled" ? "Differential-Drive" : "Digitigrade Bipedal Stride + Wing Assist");
  const strideCadenceSpm = Math.round(Math.abs(currentPose.speed || 0) * 285);

  return (
    <figure className="viz" style={{ margin: "0.75rem 0" }}>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "0.5rem",
          padding: "0.55rem 0.75rem",
          background: "#0f172a",
          color: "#e2e8f0",
          borderTopLeftRadius: "8px",
          borderTopRightRadius: "8px",
          borderBottom: "1px solid #1e293b",
          fontSize: "0.82rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
          <button
            type="button"
            className="secondary-button sim-control-button"
            style={{ padding: "0.25rem 0.65rem", fontSize: "0.8rem" }}
            onClick={() => setPlaying((p) => !p)}
          >
            {playing ? "⏸ Pause" : "▶ Play"}
          </button>
          <button
            type="button"
            className="secondary-button sim-control-button"
            style={{ padding: "0.25rem 0.6rem", fontSize: "0.8rem" }}
            onClick={() => {
              stateRef.current.time = 0;
              setSimTime(0);
            }}
          >
            ↺ Restart
          </button>
          <input
            type="range"
            min={0}
            max={timeline.totalTime}
            step={0.02}
            value={simTime}
            onChange={(e) => {
              const t = Number(e.target.value);
              stateRef.current.time = t;
              setSimTime(t);
            }}
            style={{ width: "120px", accentColor: "#38bdf8" }}
            aria-label="Simulation timeline scrubber"
          />
          <span style={{ fontFamily: "monospace", color: "#94a3b8" }}>
            {simTime.toFixed(1)}s / {timeline.totalTime.toFixed(1)}s
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }}>
          {obstacleTraining && (
            <>
              <button
                type="button"
                onClick={() => setEpisodeMode("converged")}
                style={{
                  padding: "0.2rem 0.55rem",
                  fontSize: "0.75rem",
                  borderRadius: "4px",
                  border: "1px solid #10b981",
                  background: episodeMode === "converged" ? "#059669" : "#1e293b",
                  color: "#f8fafc",
                  cursor: "pointer",
                }}
              >
                ✓ Trained Ep {obstacleTraining.converged_episode} (0 hits)
              </button>
              <button
                type="button"
                onClick={() => setEpisodeMode("initial")}
                style={{
                  padding: "0.2rem 0.55rem",
                  fontSize: "0.75rem",
                  borderRadius: "4px",
                  border: "1px solid #f43f5e",
                  background: episodeMode === "initial" ? "#e11d48" : "#1e293b",
                  color: "#f8fafc",
                  cursor: "pointer",
                }}
              >
                ✕ Untuned Ep 1 ({obstacleTraining.initial_episode?.collisions || 1} hit)
              </button>
            </>
          )}
          {[0.5, 1, 2].map((mult) => (
            <button
              key={mult}
              type="button"
              onClick={() => setSpeedMultiplier(mult)}
              style={{
                padding: "0.18rem 0.45rem",
                fontSize: "0.75rem",
                borderRadius: "4px",
                border: "1px solid #334155",
                background: speedMultiplier === mult ? "#0284c7" : "#1e293b",
                color: "#f8fafc",
                cursor: "pointer",
              }}
            >
              {mult}×
            </button>
          ))}
          <button
            type="button"
            onClick={() => setShowWiring((w) => !w)}
            style={{
              padding: "0.18rem 0.55rem",
              fontSize: "0.75rem",
              borderRadius: "4px",
              border: "1px solid #334155",
              background: showWiring ? "#b45309" : "#1e293b",
              color: "#f8fafc",
              cursor: "pointer",
            }}
          >
            {showWiring ? "⌁ 3D Wiring: ON" : "⌁ 3D Wiring: OFF"}
          </button>
          <button
            type="button"
            onClick={() => setShowVisionPip((v) => !v)}
            style={{
              padding: "0.18rem 0.55rem",
              fontSize: "0.75rem",
              borderRadius: "4px",
              border: "1px solid #334155",
              background: showVisionPip ? "#4f46e5" : "#1e293b",
              color: "#f8fafc",
              cursor: "pointer",
            }}
          >
            {showVisionPip ? "◉ Vision PIP: ON" : "◉ Vision PIP: OFF"}
          </button>
          <button
            type="button"
            onClick={() => setFollowCam((f) => !f)}
            style={{
              padding: "0.18rem 0.55rem",
              fontSize: "0.75rem",
              borderRadius: "4px",
              border: "1px solid #334155",
              background: followCam ? "#0d9488" : "#1e293b",
              color: "#f8fafc",
              cursor: "pointer",
            }}
          >
            {followCam ? "◎ Follow: ON" : "◎ Free Orbit"}
          </button>
        </div>
      </div>

      <div style={{ position: "relative" }}>
        {webglAvailable ? (
          <div
            ref={mountRef}
            style={{
              width: "100%",
              height: "450px",
              background: "#0f172a",
              borderBottomLeftRadius: "8px",
              borderBottomRightRadius: "8px",
              overflow: "hidden",
            }}
            aria-label="3D Rover obstacle world simulation viewport"
          />
        ) : (
          <canvas ref={fallbackCanvasRef} width={600} height={340} aria-label="Rover route preview" />
        )}

        {/* 1st-Person Robot Onboard Camera / OpenCV Vision HUD Overlay */}
        {webglAvailable && showVisionPip && (
          <div
            style={{
              position: "absolute",
              top: "12px",
              right: "12px",
              width: "236px",
              height: "148px",
              borderRadius: "6px",
              border: `1.5px solid ${
                currentPose.clearance_m < 0.08
                  ? "#ef4444"
                  : currentPose.clearance_m < 0.22
                  ? "#f59e0b"
                  : "rgba(56, 189, 248, 0.75)"
              }`,
              boxShadow: "0 8px 24px rgba(0, 0, 0, 0.55)",
              pointerEvents: "none",
              overflow: "hidden",
              fontFamily: "monospace",
            }}
          >
            {/* Top camera telemetry banner */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "2px 6px",
                background: "rgba(15, 23, 42, 0.82)",
                borderBottom: "1px solid rgba(56, 189, 248, 0.25)",
                fontSize: "0.64rem",
                color: "#38bdf8",
              }}
            >
              <span>◉ CAM0 · OpenCV + ROS 2</span>
              <span style={{ color: "#a7f3d0" }}>FOV 62°</span>
            </div>

            {/* Center crosshair reticle */}
            <div
              style={{
                position: "absolute",
                left: "50%",
                top: "52%",
                width: "18px",
                height: "18px",
                transform: "translate(-50%, -50%)",
                border: "1px dashed rgba(148, 163, 184, 0.35)",
                borderRadius: "50%",
              }}
            />

            {/* Live OpenCV Obstacle Detection Bounding Boxes */}
            {visibleObstacleDetections.map((det) => {
              const boxColor =
                det.threat === "critical"
                  ? "#ef4444"
                  : det.threat === "warning"
                  ? "#f59e0b"
                  : "#10b981";
              return (
                <div
                  key={det.id}
                  style={{
                    position: "absolute",
                    left: `${det.leftPct}%`,
                    top: `${det.topPct}%`,
                    width: `${det.widthPct}%`,
                    height: `${det.heightPct}%`,
                    border: `1.5px solid ${boxColor}`,
                    background: `${boxColor}18`,
                    borderRadius: "3px",
                    transition: "all 60ms linear",
                  }}
                >
                  <span
                    style={{
                      position: "absolute",
                      top: "-13px",
                      left: "-1px",
                      background: boxColor,
                      color: "#0f172a",
                      fontSize: "0.56rem",
                      fontWeight: 800,
                      padding: "0px 3px",
                      borderRadius: "2px",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {det.label} {(det.distM * 100).toFixed(0)}cm
                  </span>
                </div>
              );
            })}

            {/* Bottom Steering & Obstacle Avoidance Command Banner */}
            <div
              style={{
                position: "absolute",
                bottom: 0,
                left: 0,
                right: 0,
                padding: "2px 6px",
                background: "rgba(15, 23, 42, 0.86)",
                borderTop: "1px solid rgba(56, 189, 248, 0.25)",
                fontSize: "0.62rem",
                display: "flex",
                justifyContent: "space-between",
                color: "#e2e8f0",
              }}
            >
              <span
                style={{
                  color:
                    Math.abs(currentPose.omega || 0) > 0.22
                      ? "#fbbf24"
                      : "#34d399",
                  fontWeight: 700,
                }}
              >
                {(currentPose.omega || 0) > 0.22
                  ? `◀ STEER LEFT +${(((currentPose.omega || 0) * 180) / Math.PI).toFixed(0)}°/s`
                  : (currentPose.omega || 0) < -0.22
                  ? `STEER RIGHT ${(((currentPose.omega || 0) * 180) / Math.PI).toFixed(0)}°/s ▶`
                  : "▲ PATH CLEAR · CRUISE"}
              </span>
              <span style={{ color: "#94a3b8" }}>
                {visibleObstacleDetections.length} obj
              </span>
            </div>
          </div>
        )}

        <div
          style={{
            position: "absolute",
            left: "12px",
            bottom: "12px",
            background: "rgba(15, 23, 42, 0.86)",
            border: "1px solid rgba(56, 189, 248, 0.3)",
            borderRadius: "6px",
            padding: "0.45rem 0.65rem",
            color: "#e2e8f0",
            fontFamily: "monospace",
            fontSize: "0.76rem",
            pointerEvents: "none",
            lineHeight: 1.45,
          }}
        >
          <div>
            <strong style={{ color: currentPose.clearance_m <= 0.005 ? "#f43f5e" : "#38bdf8" }}>
              {currentPose.phase}
            </strong>
            {obstacleTraining?.physics_engine && (
              <span style={{ marginLeft: 8, color: "#a7f3d0", fontSize: "0.7rem" }}>
                [{obstacleTraining.physics_engine.split("(")[0].trim()}]
              </span>
            )}
          </div>
          <div>
            Pose: x={currentPose.x.toFixed(2)}m, y={currentPose.y.toFixed(2)}m, θ=
            {((currentPose.heading * 180) / Math.PI).toFixed(0)}° · Roll={(currentPose.roll_deg || 0).toFixed(1)}° / Pitch={(currentPose.pitch_deg || 0).toFixed(1)}°
          </div>
          <div>
            Speed: {currentPose.speed.toFixed(2)} m/s ·{" "}
            {locomotionMode === "wheeled"
              ? `Wheels: ${wheelRpm.toFixed(0)} RPM`
              : `Gait: ${gaitLabel} (${strideCadenceSpm} SPM)`}{" "}
            · Obstacle Clearance:{" "}
            <strong style={{ color: currentPose.clearance_m < 0.06 ? "#f59e0b" : "#10b981" }}>
              {(currentPose.clearance_m * 1000).toFixed(0)} mm
            </strong>
          </div>
          <div style={{ color: "#94a3b8", fontSize: "0.7rem" }}>
            <span style={{ color: "#10b981" }}>━</span> Trained path ·{" "}
            <span style={{ color: "#f43f5e" }}>━</span> Ep 1 untuned path ·{" "}
            <span style={{ color: "#ef4444" }}>━</span> 3D Wire Harness ·{" "}
            <span style={{ color: "#f59e0b" }}>●</span> CoM plumb
          </div>
        </div>
      </div>

      <figcaption style={{ marginTop: "0.45rem" }}>
        3D obstacle-world navigation & co-design simulation — {obstacles.length} obstacles · Waypoints:{" "}
        {waypoints.map((p, i) => `W${i}(${p[0]}, ${p[1]})`).join(" → ")}
        {sim && (
          <>
            {" "}· {sim.passed ? "course passed" : "course failed"}
            {sim.collisions ? ` · ${sim.collisions} collision(s)` : " · 0 collisions (converged)"}
          </>
        )}
      </figcaption>
    </figure>
  );
}
