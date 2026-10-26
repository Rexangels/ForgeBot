import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { parseRobotUrdf } from "./armKinematics.js";

function visualColor(linkName) {
  if (linkName.includes("head")) return "#d9864c";
  if (linkName.includes("hand") || linkName.includes("finger")) return "#e5a74a";
  if (linkName.includes("foot")) return "#344d58";
  return "#267c72";
}

function geometryFor(visual) {
  const attributes = visual.attributes;
  if (visual.type === "box") {
    return new THREE.BoxGeometry(attributes.size_x, attributes.size_y, attributes.size_z);
  }
  if (visual.type === "cylinder") {
    return new THREE.CylinderGeometry(attributes.radius, attributes.radius, attributes.length, 20);
  }
  if (visual.type === "sphere") return new THREE.SphereGeometry(attributes.radius, 16, 12);
  return null;
}

function rpyQuaternion(rpy) {
  return new THREE.Quaternion().setFromEuler(new THREE.Euler(rpy[0], rpy[1], rpy[2], "ZYX"));
}

function buildAssembly(model, scene) {
  const childJoints = new Map();
  for (const joint of model.joints) {
    if (!childJoints.has(joint.parent)) childJoints.set(joint.parent, []);
    childJoints.get(joint.parent).push(joint);
  }
  const buildLink = (name) => {
    const frame = new THREE.Group();
    frame.name = `link:${name}`;
    const link = model.links.get(name);
    for (const visual of link.visuals) {
      const geometry = geometryFor(visual);
      if (!geometry) continue;
      const visualFrame = new THREE.Group();
      visualFrame.position.set(...visual.origin);
      visualFrame.quaternion.copy(rpyQuaternion(visual.rpy));
      const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
          color: visual.color || visualColor(name), roughness: 0.62, metalness: 0.06,
      }));
      if (visual.type === "cylinder") mesh.rotation.x = Math.PI / 2;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      visualFrame.add(mesh);
      frame.add(visualFrame);
    }
    for (const joint of childJoints.get(name) || []) {
      const origin = new THREE.Group();
      origin.name = `joint:${joint.name}`;
      origin.position.set(...joint.origin);
      origin.quaternion.copy(rpyQuaternion(joint.rpy));
      origin.add(buildLink(joint.child));
      frame.add(origin);
    }
    return frame;
  };
  const root = buildLink(model.root);
  scene.add(root);
  return root;
}

export default function UrdfModelViewer({ urdf, geometry = null, autoFrame = false }) {
  const mountRef = useRef(null);
  const [renderError, setRenderError] = useState("");
  const parsed = useMemo(() => {
    if (!urdf) return null;
    try { return parseRobotUrdf(urdf); }
    catch (error) { return { parseError: error.message }; }
  }, [urdf]);
  const floorZ = geometry
    ? -(geometry.pelvis_height_m / 2 + geometry.thigh_length_m + geometry.shin_length_m + geometry.foot_height_m)
    : -0.75;
  const topZ = geometry
    ? geometry.pelvis_height_m / 2 + geometry.torso_length_m + geometry.neck_height_m + geometry.head_height_m
    : 0.75;
  const centerZ = (floorZ + topZ) / 2;

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !parsed || parsed.parseError) return undefined;
    let renderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false }); }
    catch {
      setRenderError("WebGL is unavailable in this browser, so the robot assembly cannot be shown.");
      return undefined;
    }
    setRenderError("");
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#f5f8f6");
    const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 12);
    if (autoFrame) camera.far = 100;
    camera.up.set(0, 0, 1);
    camera.position.set(2.15, -2.5, centerZ + 1.15);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    mount.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight("#ffffff", "#aebbb4", 2.2));
    const keyLight = new THREE.DirectionalLight("#ffffff", 2.4);
    keyLight.position.set(2, -2, 3);
    keyLight.castShadow = true;
    scene.add(keyLight);
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(autoFrame ? 8 : 2.4, autoFrame ? 8 : 2.4),
      new THREE.MeshStandardMaterial({ color: "#e5e9e5", roughness: 0.94 }),
    );
    floor.position.z = floorZ;
    floor.receiveShadow = true;
    scene.add(floor);
    const grid = new THREE.GridHelper(autoFrame ? 8 : 2.4, autoFrame ? 40 : 24, "#bfcac4", "#dce3df");
    grid.rotation.x = Math.PI / 2;
    grid.position.z = floorZ + 0.002;
    scene.add(grid);
    const root = buildAssembly(parsed, scene);
    let viewCenter = new THREE.Vector3(0, 0, centerZ);
    let viewSpan = 1.5;
    if (autoFrame) {
      root.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(root);
      if (!bounds.isEmpty()) {
        const size = bounds.getSize(new THREE.Vector3());
        viewCenter = bounds.getCenter(new THREE.Vector3());
        viewSpan = Math.max(size.x, size.y, size.z, 0.15);
        floor.position.z = bounds.min.z - Math.max(0.03, viewSpan * 0.04);
        grid.position.z = floor.position.z + 0.002;
      }
    }

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.copy(viewCenter);
    controls.enableDamping = true;
    controls.minDistance = autoFrame ? 0.08 : 0.45;
    controls.maxDistance = autoFrame ? 15 : 5;
    if (autoFrame) camera.position.set(viewCenter.x + viewSpan * 1.5, viewCenter.y - viewSpan * 2.1, viewCenter.z + viewSpan * 1.35);
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
    let frame = 0;
    const render = () => {
      controls.update();
      renderer.render(scene, camera);
      frame = window.requestAnimationFrame(render);
    };
    render();
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
    };
  }, [parsed, floorZ, centerZ, autoFrame]);

  if (!urdf) return <div className="arm-placeholder"><div className="arm-viewport placeholder-viewport"><span className="viewport-tag">URDF ROBOT ASSEMBLY</span><div className="vision-empty"><strong>Robot model appears after approval</strong><small>ForgeBot will build a reviewable 3D assembly from the approved concept.</small></div></div></div>;
  if (parsed?.parseError) return <div className="arm-placeholder"><p className="error">{parsed.parseError}</p></div>;

  const actuatedJoints = parsed.joints.filter((joint) => joint.type !== "fixed").length;
  return (
    <div className="arm-editor humanoid-editor">
      <div className="arm-viewport humanoid-viewport" ref={mountRef}>
        <span className="viewport-tag">URDF ASSEMBLY · DRAG TO ORBIT · SCROLL TO ZOOM</span>
        {renderError && <p className="error viewport-error">{renderError}</p>}
      </div>
      <div className="humanoid-model-caption">
        <span>{autoFrame ? "Primitive geometry concept from image" : "Neutral kinematic pose"}</span>
        <span>{parsed.links.size} links · {actuatedJoints} actuated joints</span>
        <small>{autoFrame ? "Estimated concept · not manufacturing CAD or physics" : "Visualization only · no balance or physics simulation yet"}</small>
      </div>
    </div>
  );
}
