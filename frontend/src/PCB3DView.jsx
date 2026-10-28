import React, { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";

function createComponent(fp) {
  const group = new THREE.Group();
  group.position.set(fp.x_mm, fp.y_mm, 0);
  group.name = fp.ref;
  group.userData.footprint = fp;
  const lower = fp.label?.toLowerCase() || "";
  const kind = `${lower} ${fp.role?.toLowerCase()} ${fp.footprint_lib?.toLowerCase()}`;
  const isXT60 = /xt60/.test(kind);
  const isGpio = /2x20|40p|gpio/.test(kind);
  const isJst = /jst|xh/.test(kind);
  const isConnector = isXT60 || isGpio || isJst;
  const isFuse = /fuse/.test(lower);
  const isBuck = /buck|regulator/.test(kind);
  const bodyHeight = isXT60 ? 7 : isGpio ? 3 : isJst ? 5.5 : isFuse ? 5.5 : isBuck ? 1.6 : 2.2;
  const bodyColor = isXT60 ? "#e6b92f" : isGpio ? "#171a1d" : isJst ? "#e9e5d8" : isFuse ? "#be463b" : isBuck ? "#176348" : "#242a2d";
  const body = new THREE.Mesh(
    new RoundedBoxGeometry(fp.width_mm, fp.height_mm, bodyHeight, 3, Math.min(0.7, bodyHeight * 0.12)),
    new THREE.MeshStandardMaterial({ color: bodyColor, roughness: isFuse ? 0.28 : 0.54, metalness: isBuck ? 0.08 : 0.06 })
  );
  body.position.z = bodyHeight / 2 + 0.9;
  body.castShadow = true;
  body.receiveShadow = true;
  body.userData.footprint = fp;
  group.add(body);

  const capHeight = isBuck || isFuse ? 0 : isXT60 ? 1 : isJst ? 1 : 0.8;
  if (capHeight) {
    const cap = new THREE.Mesh(
      new RoundedBoxGeometry(fp.width_mm * (isConnector ? 0.68 : 0.52), fp.height_mm * 0.62, capHeight, 3, 0.35),
      new THREE.MeshStandardMaterial({ color: isXT60 ? "#262b2b" : isGpio ? "#111417" : isJst ? "#d7d2c7" : isFuse ? "#e97868" : "#11181d", roughness: 0.38, metalness: 0.18 })
    );
    cap.position.set(0, 0, bodyHeight + capHeight / 2 + 0.9);
    cap.castShadow = true;
    cap.userData.footprint = fp;
    group.add(cap);
  }

  const selectOutline = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(fp.width_mm + 2, fp.height_mm + 2, bodyHeight + capHeight + 2)),
    new THREE.LineBasicMaterial({ color: "#0b7a6f", transparent: true, opacity: 0.95, depthTest: false })
  );
  selectOutline.position.z = (bodyHeight + capHeight) / 2 + 1.9;
  selectOutline.visible = false;
  selectOutline.renderOrder = 5;
  group.add(selectOutline);
  group.userData.selectOutline = selectOutline;

  (fp.pads || []).forEach((pad) => {
    const copper = new THREE.Mesh(
      new THREE.CylinderGeometry(1.15, 1.15, 0.2, 16),
      new THREE.MeshStandardMaterial({ color: "#c58b45", metalness: 0.78, roughness: 0.28 })
    );
    copper.rotation.x = Math.PI / 2;
    copper.position.set(pad.dx_mm, pad.dy_mm, 1.08);
    copper.userData.footprint = fp;
    group.add(copper);
    if (isConnector && !isGpio) {
      const pinLength = Math.max(2, bodyHeight - 1.2);
      const pin = new THREE.Mesh(
        new THREE.CylinderGeometry(0.38, 0.38, pinLength, 8),
        new THREE.MeshStandardMaterial({ color: "#d3a85d", metalness: 0.84, roughness: 0.24 })
      );
      pin.rotation.x = Math.PI / 2;
      pin.position.set(pad.dx_mm, pad.dy_mm, pinLength / 2 + 1);
      pin.userData.footprint = fp;
      group.add(pin);
    }
  });

  if (isGpio) {
    for (let row = -1; row <= 1; row += 2) {
      for (let col = 0; col < 20; col += 1) {
        const pin = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.34, 7.5, 8), new THREE.MeshStandardMaterial({ color: "#d2a85d", metalness: 0.84, roughness: 0.23 }));
        pin.rotation.x = Math.PI / 2;
        pin.position.set(-fp.width_mm / 2 + 2.7 + col * 1.27, row * 1.27, 4.6);
        pin.userData.footprint = fp;
        group.add(pin);
      }
    }
  }
  if (isXT60) {
    [-3.6, 3.6].forEach((x, index) => {
      const terminal = new THREE.Mesh(new THREE.CylinderGeometry(1.9, 1.9, 1.1, 20), new THREE.MeshStandardMaterial({ color: index ? "#252a2d" : "#d2a85d", metalness: 0.78, roughness: 0.3 }));
      terminal.rotation.x = Math.PI / 2;
      terminal.position.set(x, 0, bodyHeight + 1.5);
      terminal.userData.footprint = fp;
      group.add(terminal);
      const recess = new THREE.Mesh(new THREE.CylinderGeometry(0.92, 0.92, 0.25, 16), new THREE.MeshStandardMaterial({ color: "#171b1d", roughness: 0.72 }));
      recess.rotation.x = Math.PI / 2;
      recess.position.set(x, 0, bodyHeight + 2.12);
      recess.userData.footprint = fp;
      group.add(recess);
    });
  }
  if (isBuck) {
    const ic = new THREE.Mesh(new RoundedBoxGeometry(5, 4, 1.6, 2, 0.45), new THREE.MeshStandardMaterial({ color: "#171b1e", roughness: 0.42, metalness: 0.14 }));
    ic.position.set(-fp.width_mm * 0.15, -fp.height_mm * 0.16, 2.35);
    ic.userData.footprint = fp;
    group.add(ic);
    const coil = new THREE.Mesh(new THREE.CylinderGeometry(2.8, 2.8, 3.4, 20), new THREE.MeshStandardMaterial({ color: "#343b3d", metalness: 0.42, roughness: 0.48 }));
    coil.rotation.x = Math.PI / 2;
    coil.position.set(fp.width_mm * 0.23, fp.height_mm * 0.17, bodyHeight + 2.6);
    coil.userData.footprint = fp;
    group.add(coil);
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(1.35, 1.35, 2.2, 16), new THREE.MeshStandardMaterial({ color: "#a5b0ad", metalness: 0.66, roughness: 0.3 }));
    cap.rotation.x = Math.PI / 2;
    cap.position.set(-fp.width_mm * 0.28, -fp.height_mm * 0.2, bodyHeight + 2.0);
    cap.userData.footprint = fp;
    group.add(cap);
    for (const x of [-fp.width_mm * 0.36, fp.width_mm * 0.37]) {
      const passive = new THREE.Mesh(new THREE.CylinderGeometry(1.25, 1.25, 2.4, 16), new THREE.MeshStandardMaterial({ color: "#aeb9b6", metalness: 0.62, roughness: 0.3 }));
      passive.rotation.x = Math.PI / 2;
      passive.position.set(x, fp.height_mm * 0.32, 2.4);
      passive.userData.footprint = fp;
      group.add(passive);
    }
  }
  if (isFuse) {
    const window = new THREE.Mesh(
      new RoundedBoxGeometry(fp.width_mm * 0.48, fp.height_mm * 0.56, 0.18, 3, 0.35),
      new THREE.MeshStandardMaterial({ color: "#ef8b7c", transparent: true, opacity: 0.62, roughness: 0.2, metalness: 0.08 })
    );
    window.position.set(0, 0, bodyHeight + 1.02);
    window.userData.footprint = fp;
    group.add(window);
    const link = new THREE.Mesh(new THREE.BoxGeometry(fp.width_mm * 0.32, 0.45, 0.14), new THREE.MeshStandardMaterial({ color: "#d7b77d", metalness: 0.72, roughness: 0.3 }));
    link.position.set(0, 0, bodyHeight + 1.18);
    link.userData.footprint = fp;
    group.add(link);
    for (const x of [-fp.width_mm * 0.32, fp.width_mm * 0.32]) {
      const blade = new THREE.Mesh(new RoundedBoxGeometry(2.3, 1.5, 3.2, 2, 0.18), new THREE.MeshStandardMaterial({ color: "#c49a55", metalness: 0.8, roughness: 0.25 }));
      blade.position.set(x, 0, 2.5);
      blade.userData.footprint = fp;
      group.add(blade);
    }
  }
  return group;
}

export default function PCB3DView({ board, selectedRef, onSelect, showTopCu, showBotCu, showWiring, wiringConnections = [], nativeModelUrl = "", onNativeModelStatus }) {
  const mountRef = useRef(null);
  const boardGroupRef = useRef(null);
  const nativeModelGroupRef = useRef(null);
  const cameraRef = useRef(null);
  const controlsRef = useRef(null);
  const viewHomeRef = useRef(null);
  const selectRef = useRef(onSelect);
  const selectedRefRef = useRef(selectedRef);
  const nativeModelStatusRef = useRef(onNativeModelStatus);
  useEffect(() => { selectRef.current = onSelect; }, [onSelect]);
  useEffect(() => { selectedRefRef.current = selectedRef; }, [selectedRef]);
  useEffect(() => { nativeModelStatusRef.current = onNativeModelStatus; }, [onNativeModelStatus]);
  useEffect(() => {
    const group = boardGroupRef.current;
    if (!group) return;
    group.children.forEach((child) => {
      if (child.userData.selectOutline) child.userData.selectOutline.visible = child.name === selectedRef;
    });
    nativeModelGroupRef.current?.traverse((object) => {
      if (object.userData.selectOutline) {
        object.userData.selectOutline.visible = object.userData.nativeFootprint?.ref === selectedRef;
        object.userData.selectOutline.update();
      }
    });
  }, [selectedRef]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !board) return undefined;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#edf2ef");
    const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 1200);
    camera.up.set(0, 0, 1);
    camera.position.set(board.width_mm * 0.9, -board.height_mm * 1.05, Math.max(board.width_mm, board.height_mm) * 0.95);
    cameraRef.current = camera;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.12;
    mount.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.075;
    controls.target.set(0, 0, 2);
    controls.minDistance = Math.max(board.width_mm, board.height_mm) * 0.7;
    controls.maxDistance = Math.max(board.width_mm, board.height_mm) * 2.6;
    controlsRef.current = controls;
    viewHomeRef.current = () => {
      const center = new THREE.Vector3(0, 0, 2);
      controls.target.copy(center);
      camera.position.set(board.width_mm * 0.78, -board.height_mm * 0.92, Math.max(board.width_mm, board.height_mm) * 1.08);
      camera.up.set(0, 0, 1);
      camera.lookAt(center);
      camera.updateProjectionMatrix();
      controls.update();
    };
    scene.add(new THREE.HemisphereLight("#ffffff", "#64746d", 2.1));
    const key = new THREE.DirectionalLight("#ffffff", 3.1);
    key.position.set(-board.width_mm, -board.height_mm, board.width_mm * 1.4);
    key.castShadow = true;
    scene.add(key);
    const fill = new THREE.DirectionalLight("#d9e8ff", 1.2);
    fill.position.set(board.width_mm, board.height_mm, board.width_mm * 0.65);
    scene.add(fill);

    const floor = new THREE.Mesh(new THREE.PlaneGeometry(board.width_mm * 4, board.width_mm * 4), new THREE.MeshStandardMaterial({ color: "#edf2ef", roughness: 0.92 }));
    floor.rotation.x = -Math.PI / 2;
    floor.position.z = -2;
    floor.receiveShadow = true;
    // Keep Three's Z-up board convention and orient the floor beneath it.
    floor.rotation.set(0, 0, 0);
    scene.add(floor);

    const boardShape = new THREE.Shape();
    boardShape.moveTo(-board.width_mm / 2, -board.height_mm / 2);
    boardShape.lineTo(board.width_mm / 2, -board.height_mm / 2);
    boardShape.lineTo(board.width_mm / 2, board.height_mm / 2);
    boardShape.lineTo(-board.width_mm / 2, board.height_mm / 2);
    boardShape.closePath();
    for (const hole of board.mounting_holes_mm || []) {
      const cutout = new THREE.Path();
      cutout.absarc(hole.x_mm, hole.y_mm, hole.diameter_mm / 2, 0, Math.PI * 2, true);
      boardShape.holes.push(cutout);
    }
    const boardGeometry = new THREE.ExtrudeGeometry(boardShape, { depth: 1.6, bevelEnabled: true, bevelSegments: 2, steps: 1, bevelSize: 0.18, bevelThickness: 0.16, curveSegments: 20 });
    boardGeometry.translate(0, 0, -0.8);
    const pcb = new THREE.Mesh(boardGeometry, new THREE.MeshStandardMaterial({ color: "#176348", roughness: 0.62, metalness: 0.12 }));
    pcb.receiveShadow = true;
    pcb.castShadow = true;
    scene.add(pcb);

    const boardGroup = new THREE.Group();
    boardGroupRef.current = boardGroup;
    scene.add(boardGroup);
    const makeSilkscreen = (text, width, height = 1.7) => {
      const canvas = document.createElement("canvas");
      canvas.width = 512;
      canvas.height = 96;
      const context = canvas.getContext("2d");
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = "#f2f4df";
      context.font = "600 54px Arial, sans-serif";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(text, canvas.width / 2, canvas.height / 2, canvas.width - 12);
      const texture = new THREE.CanvasTexture(canvas);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
      return new THREE.Mesh(
        new THREE.PlaneGeometry(width, height),
        new THREE.MeshBasicMaterial({ map: texture, transparent: true, side: THREE.DoubleSide, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, toneMapped: false })
      );
    };
    // Keep copper overlays legible against the green solder mask. On a real
    // board the solder mask normally covers these very thin copper layers.
    const topMaterial = new THREE.MeshStandardMaterial({ color: "#e6a66c", metalness: 0.48, roughness: 0.38 });
    const bottomMaterial = new THREE.MeshStandardMaterial({ color: "#d8965d", metalness: 0.48, roughness: 0.4 });
    for (const track of board.tracks || []) {
      const show = track.layer === "F.Cu" ? showTopCu : showBotCu;
      if (!show) continue;
      const start = new THREE.Vector2(track.start_mm[0], track.start_mm[1]);
      const end = new THREE.Vector2(track.end_mm[0], track.end_mm[1]);
      const delta = end.clone().sub(start);
      const length = delta.length();
      const z = track.layer === "F.Cu" ? 0.835 : -0.835;
      const trace = new THREE.Mesh(
        new THREE.BoxGeometry(length, Math.max(0.42, Number(track.width_mm)), 0.06),
        track.layer === "F.Cu" ? topMaterial : bottomMaterial
      );
      trace.position.set((start.x + end.x) / 2, (start.y + end.y) / 2, z);
      trace.rotation.z = Math.atan2(delta.y, delta.x);
      trace.userData.netName = track.net_name;
      boardGroup.add(trace);
      for (const point of [start, end]) {
        const pad = new THREE.Mesh(new THREE.CylinderGeometry(Math.max(0.62, Number(track.width_mm) / 2), Math.max(0.62, Number(track.width_mm) / 2), 0.06, 20), track.layer === "F.Cu" ? topMaterial : bottomMaterial);
        pad.rotation.x = Math.PI / 2;
        pad.position.set(point.x, point.y, z);
        pad.userData.netName = track.net_name;
        boardGroup.add(pad);
      }
    }
    for (const hole of board.mounting_holes_mm || []) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(hole.diameter_mm * 0.58, 0.28, 8, 24), new THREE.MeshStandardMaterial({ color: "#d8b365", metalness: 0.8, roughness: 0.26 }));
      ring.position.set(hole.x_mm, hole.y_mm, 0.88);
      ring.userData.label = hole.id;
      scene.add(ring);
    }
    for (const fp of board.footprints || []) {
      boardGroup.add(createComponent(fp));
      const refLabel = makeSilkscreen(fp.ref, Math.max(4.5, fp.ref.length * 0.78), 1.35);
      refLabel.position.set(fp.x_mm, fp.y_mm - fp.height_mm / 2 - 1.6, 0.92);
      refLabel.userData.footprint = fp;
      boardGroup.add(refLabel);
    }
    const boardLabel = makeSilkscreen("FORGEBOT · POWER CARRIER · REV A", Math.min(board.width_mm * 0.42, 29), 1.7);
    boardLabel.position.set(0, board.height_mm * 0.38, 0.92);
    boardGroup.add(boardLabel);

    let nativeModelGroup = null;
    let cancelled = false;
    if (nativeModelUrl) {
      const modelledRefs = new Set(["J1_BAT", "F1_FUSE", "J2_SBC", "J3_MDRV", "J4_SONAR_L", "J5_SONAR_R"]);
      const loader = new GLTFLoader();
      nativeModelStatusRef.current?.({ state: "loading", count: 0 });
      loader.load(nativeModelUrl, (gltf) => {
        if (cancelled) return;
        nativeModelGroup = gltf.scene;
        // KiCad's GLB stores board coordinates in metres with Y as up and the
        // board's Y axis mapped to negative GLB Z. Convert to this viewer's
        // millimetre, Z-up, positive-Y-up board frame before replacing previews.
        nativeModelGroup.rotation.x = Math.PI / 2;
        nativeModelGroup.scale.set(1000, 1000, -1000);
        // KiCad places footprint models above the board's bottom datum; this
        // viewer centers the board thickness around Z=0, so shift by half the
        // board thickness plus KiCad's small surface offset.
        nativeModelGroup.position.z = -(Number(board.thickness_mm || 1.6) / 2) - 0.015;
        nativeModelGroupRef.current = nativeModelGroup;
        nativeModelGroup.updateMatrixWorld(true);
        let matchedCount = 0;
        const matchedRefs = new Set();
        for (const fp of board.footprints || []) {
          if (!modelledRefs.has(fp.ref)) continue;
          const root = nativeModelGroup.getObjectByName(fp.ref)
            || nativeModelGroup.children.find((child) => child.name?.includes(fp.ref));
          if (!root) continue;
          root.updateWorldMatrix(true, true);
          const modelBounds = new THREE.Box3().setFromObject(root);
          if (modelBounds.isEmpty()) continue;
          const boundsCenter = modelBounds.getCenter(new THREE.Vector3());
          const parent = root.parent || nativeModelGroup;
          parent.updateWorldMatrix(true, false);
          const currentCenterLocal = parent.worldToLocal(boundsCenter.clone());
          const targetCenterLocal = parent.worldToLocal(new THREE.Vector3(fp.x_mm, fp.y_mm, boundsCenter.z));
          root.position.add(targetCenterLocal.sub(currentCenterLocal));
          root.updateWorldMatrix(true, true);
          matchedCount += 1;
          matchedRefs.add(fp.ref);
          root.userData.nativeFootprint = fp;
          root.traverse((object) => { object.userData.footprint = fp; });
          const approximation = boardGroup.children.find((child) => child.name === fp.ref);
          if (approximation) approximation.visible = false;
          const highlight = new THREE.BoxHelper(root, new THREE.Color("#0b7a6f"));
          highlight.visible = selectedRefRef.current === fp.ref;
          scene.add(highlight);
          root.userData.selectOutline = highlight;
        }
        if (matchedCount > 0) scene.add(nativeModelGroup);
        nativeModelGroupRef.current = matchedCount > 0 ? nativeModelGroup : null;
        const missingRefs = [...modelledRefs].filter((ref) => !matchedRefs.has(ref));
        const nodeNames = [];
        nativeModelGroup.traverse((object) => { if (object.name) nodeNames.push(object.name); });
        nativeModelStatusRef.current?.({
          state: matchedCount === modelledRefs.size ? "loaded" : matchedCount ? "partial" : "no-match",
          count: matchedCount,
          missingRefs,
          nodeNames: nodeNames.slice(0, 20),
        });
      }, undefined, (error) => {
        nativeModelGroupRef.current = null;
        nativeModelStatusRef.current?.({ state: "load-error", count: 0, error: error?.message || "Unknown GLB loader error" });
      });
    }

    if (showWiring) {
      // Only external cable connectors get 3D harness leads. J2 is the SBC's
      // 2x20 board header; routing its unused pins to cable ends made loose
      // strands appear around the long black socket.
      const edgeTargets = (board.footprints || []).filter((fp) => ["J1_BAT", "J3_MDRV", "J4_SONAR_L", "J5_SONAR_R"].includes(fp.ref));
      const netColors = { BAT_11V1_RAW: "#d8a348", VMOT_11V1_FUSED: "#b9443e", VCC_5V_BUCK: "#d67a37", VCC_3V3_LOGIC: "#398463", GND: "#252b30", GPIO12_PWM_L: "#d3ae42", GPIO13_PWM_R: "#647eb0", GPIO23_SONAR_TRIG_L: "#53aab4", GPIO24_SONAR_ECHO_L_3V3: "#59a57e", GPIO17_SONAR_TRIG_R: "#597fc0", GPIO27_SONAR_ECHO_R_3V3: "#388d79" };
      edgeTargets.forEach((fp) => {
        const edgeX = board.width_mm / 2 - Math.abs(fp.x_mm);
        const edgeY = board.height_mm / 2 - Math.abs(fp.y_mm);
        const exitsX = edgeX < edgeY;
        const side = exitsX ? Math.sign(fp.x_mm || 1) : Math.sign(fp.y_mm || 1);
        const endX = exitsX ? fp.x_mm + side * 16 : fp.x_mm;
        const endY = exitsX ? fp.y_mm : fp.y_mm + side * 16;
        const sheath = new THREE.Mesh(
          new RoundedBoxGeometry(6, 4, 3.6, 3, 0.8),
          new THREE.MeshStandardMaterial({ color: /XT60/i.test(fp.label) ? "#e3b72d" : "#e5e2d7", roughness: 0.5 })
        );
        sheath.position.set(endX, endY, 3.8);
        sheath.userData.connectorRef = fp.ref;
        boardGroup.add(sheath);
        const connectedPads = (fp.pads || []).filter((pad) => pad.net_name);
        connectedPads.forEach((pad, index) => {
          const color = netColors[pad.net_name] || ["#b9443e", "#252b30", "#d3ae42", "#597fc0"][index % 4];
          const offset = (index - ((connectedPads.length - 1) / 2)) * 0.85;
          const startX = fp.x_mm + pad.dx_mm;
          const startY = fp.y_mm + pad.dy_mm;
          const targetX = exitsX ? endX - side * 2 : endX + offset;
          const targetY = exitsX ? endY + offset : endY - side * 2;
          // Start each cable inside its pad barrel at the PCB top face so the
          // rendered tube visibly meets copper instead of appearing suspended.
          const padStart = new THREE.Vector3(startX, startY, 0.84);
          const liftedStart = exitsX
            ? new THREE.Vector3(startX + side * 1.6, startY, 1.45)
            : new THREE.Vector3(startX, startY + side * 1.6, 1.45);
          const points = exitsX
            ? [padStart, liftedStart, new THREE.Vector3(startX + side * 4, startY, 2.6), new THREE.Vector3(endX - side * 5, targetY, 3.8), new THREE.Vector3(targetX, targetY, 3.8)]
            : [padStart, liftedStart, new THREE.Vector3(startX, startY + side * 4, 2.6), new THREE.Vector3(targetX, endY - side * 5, 3.8), new THREE.Vector3(targetX, targetY, 3.8)];
          const wire = new THREE.Mesh(
            new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points), 24, pad.net_name?.includes("VMOT") || pad.net_name?.includes("BAT") ? 0.42 : 0.28, 8, false),
            new THREE.MeshStandardMaterial({ color, roughness: 0.48, metalness: 0.06 })
          );
          wire.userData.connectorRef = fp.ref;
          wire.userData.wiring = wiringConnections.find((item) => item.net_name?.includes(pad.net_name)) || null;
          wire.castShadow = true;
          boardGroup.add(wire);
        });
      });
    }

    const grid = new THREE.GridHelper(Math.max(board.width_mm, board.height_mm) * 2, 20, "#bdc9c3", "#d7dfda");
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -1.8;
    scene.add(grid);
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const handleClick = (event) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const targets = [...boardGroup.children, ...(nativeModelGroupRef.current?.children || [])];
      const hits = raycaster.intersectObjects(targets, true);
      const fp = hits.find((hit) => hit.object.userData.footprint || hit.object.userData.connectorRef);
      const value = fp?.object.userData.footprint || (board.footprints || []).find((item) => item.ref === fp?.object.userData.connectorRef);
      if (value) selectRef.current?.(value);
    };
    renderer.domElement.addEventListener("click", handleClick);

    let frameId;
    let width = 0;
    let height = 0;
    const resize = () => {
      const rect = mount.getBoundingClientRect();
      width = Math.max(320, rect.width);
      height = Math.max(300, rect.height);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);
    resize();
    const render = () => {
      controls.update();
      const viewingUnderside = camera.position.z < 0;
      grid.visible = !viewingUnderside;
      floor.visible = !viewingUnderside;
      renderer.render(scene, camera);
      frameId = requestAnimationFrame(render);
    };
    render();

    return () => {
      cancelled = true;
      cancelAnimationFrame(frameId);
      observer.disconnect();
      renderer.domElement.removeEventListener("click", handleClick);
      controls.dispose();
      cameraRef.current = null;
      controlsRef.current = null;
      viewHomeRef.current = null;
      scene.traverse((object) => {
        object.geometry?.dispose?.();
        if (Array.isArray(object.material)) object.material.forEach((material) => material.dispose());
        else object.material?.dispose?.();
      });
      renderer.dispose();
      renderer.domElement.remove();
      boardGroupRef.current = null;
      nativeModelGroupRef.current = null;
    };
  }, [board, showTopCu, showBotCu, showWiring, wiringConnections, nativeModelUrl]);

  return <div className="pcb-3d-frame">
    <div className="pcb-3d-viewport" ref={mountRef} aria-label={`Interactive 3D PCB view${selectedRef ? `, selected ${selectedRef}` : ""}`} role="img" />
    <div className="pcb-camera-controls" aria-label="Camera view controls">
      <button type="button" onClick={() => viewHomeRef.current?.()} title="Return to the fitted three-quarter view">Reset view</button>
      <span>Drag to orbit · scroll to zoom</span>
    </div>
  </div>;
}
