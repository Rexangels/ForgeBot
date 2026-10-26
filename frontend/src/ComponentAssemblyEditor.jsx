import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

const supported = new Set(["glb", "gltf", "stl"]);
const uid = () => `part-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

function makeMesh(file, url) {
  const ext = file.name.split(".").pop().toLowerCase();
  if (ext === "stl") return new STLLoader().loadAsync(url).then((geometry) => {
    geometry.computeVertexNormals();
    return new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: "#4c9b8c", metalness: 0.15, roughness: 0.58 }));
  });
  return new GLTFLoader().loadAsync(url).then((gltf) => gltf.scene);
}

function boundsOverlap(a, b) {
  return a.min[0] < b.max[0] && a.max[0] > b.min[0] && a.min[1] < b.max[1] && a.max[1] > b.min[1] && a.min[2] < b.max[2] && a.max[2] > b.min[2];
}

export default function ComponentAssemblyEditor() {
  const mountRef = useRef(null);
  const [parts, setParts] = useState([]);
  const [unit, setUnit] = useState("m");
  const [error, setError] = useState("");
  const [overlaps, setOverlaps] = useState([]);
  const [assemblyFindings, setAssemblyFindings] = useState([]);

  const update = (id, key, value) => setParts((current) => current.map((part) => part.id === id ? { ...part, [key]: value } : part));
  const addFiles = (event) => {
    setError("");
    const accepted = [...event.target.files];
    const bad = accepted.filter((file) => !supported.has(file.name.split(".").pop().toLowerCase()));
    if (bad.length) setError(`Unsupported file type: ${bad.map((file) => file.name).join(", ")}. Choose GLB, glTF, or STL.`);
    const good = accepted.filter((file) => supported.has(file.name.split(".").pop().toLowerCase()));
    setParts((current) => [...current, ...good.map((file, index) => ({ id: uid(), name: file.name, file, url: URL.createObjectURL(file), mount: "mount", parent: index === 0 && current.length === 0 ? "world" : current[0]?.id || "world", position: [index * 0.12, 0, 0], scale: 1, bounds: null, loadError: "" }))]);
    event.target.value = "";
  };

  const assembly = useMemo(() => {
    const findings = [];
    const byId = new Map(parts.map((part) => [part.id, part]));
    const worldPosition = (part, seen = new Set()) => {
      if (seen.has(part.id)) { findings.push(`${part.name} creates a parent cycle.`); return [0, 0, 0]; }
      if (part.parent === "world") return part.position.map(Number);
      const parent = byId.get(part.parent);
      if (!parent) { findings.push(`${part.name} references a missing parent.`); return part.position.map(Number); }
      seen.add(part.id);
      const parentPosition = worldPosition(parent, seen);
      seen.delete(part.id);
      return part.position.map((value, index) => Number(value) + parentPosition[index]);
    };
    const positions = new Map(parts.map((part) => [part.id, worldPosition(part)]));
    for (let i = 0; i < parts.length; i += 1) for (let j = i + 1; j < parts.length; j += 1) {
      const a = parts[i]; const b = parts[j];
      if (a.bounds && b.bounds && boundsOverlap(a.bounds, b.bounds)) findings.push(`${a.name} and ${b.name} have overlapping approximate mesh bounds.`);
    }
    return { findings: [...new Set(findings)], positions };
  }, [parts]);
  useEffect(() => { setOverlaps(assembly.findings.filter((finding) => finding.includes("overlapping"))); setAssemblyFindings(assembly.findings.filter((finding) => !finding.includes("overlapping"))); }, [assembly]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return undefined;
    const scene = new THREE.Scene(); scene.background = new THREE.Color("#f5f8f6");
    const camera = new THREE.PerspectiveCamera(42, 1, 0.001, 100); camera.up.set(0, 0, 1); camera.position.set(1.8, -2.2, 1.65);
    const renderer = new THREE.WebGLRenderer({ antialias: true }); renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); mount.appendChild(renderer.domElement);
    scene.add(new THREE.HemisphereLight("#ffffff", "#9eaaa4", 2)); const light = new THREE.DirectionalLight("#ffffff", 2.4); light.position.set(2, -1, 3); scene.add(light);
    const grid = new THREE.GridHelper(2, 20, "#bfcac4", "#dce3df"); grid.rotation.x = Math.PI / 2; scene.add(grid);
    const controls = new OrbitControls(camera, renderer.domElement); controls.target.set(0, 0, 0.25); controls.enableDamping = true;
    let alive = true; const resources = [];
    parts.forEach((part) => {
      makeMesh(part.file, part.url).then((object) => {
        if (!alive) { object.traverse((item) => { item.geometry?.dispose(); }); return; }
        const unitFactor = unit === "mm" ? 0.001 : 1;
        object.name = part.name; object.scale.setScalar((Number(part.scale) || 1) * unitFactor); object.position.set(...(assembly.positions.get(part.id) || part.position.map(Number)).map((value) => Number(value) * unitFactor));
        object.traverse((item) => { if (item.isMesh) { item.castShadow = true; item.material = item.material || new THREE.MeshStandardMaterial({ color: "#4c9b8c" }); } });
        scene.add(object); resources.push(object);
        const box = new THREE.Box3().setFromObject(object);
        setParts((current) => current.map((item) => item.id === part.id ? { ...item, bounds: { min: box.min.toArray(), max: box.max.toArray() } } : item));
      }).catch((err) => setParts((current) => current.map((item) => item.id === part.id ? { ...item, loadError: err.message || "Could not parse this mesh." } : item)));
    });
    const resize = () => { const w = Math.max(mount.clientWidth, 1); const h = Math.max(mount.clientHeight, 1); camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.setSize(w, h, false); };
    const observer = new ResizeObserver(resize); observer.observe(mount); resize();
    let frame; const render = () => { controls.update(); renderer.render(scene, camera); frame = requestAnimationFrame(render); }; render();
    return () => { alive = false; cancelAnimationFrame(frame); observer.disconnect(); controls.dispose(); resources.forEach((object) => { object.traverse((item) => { item.geometry?.dispose(); if (Array.isArray(item.material)) item.material.forEach((material) => material.dispose()); else item.material?.dispose(); }); }); renderer.dispose(); renderer.domElement.remove(); };
  }, [unit, parts.map((part) => `${part.id}:${part.url}:${part.scale}:${part.parent}:${part.position.join(",")}`).join("|")]);

  const exportManifest = () => {
    const manifest = { schema_version: "forgebot-component-assembly/v1", units: unit, components: parts.map(({ id, name, mount, parent, position, scale }) => { const parentPart = parts.find((item) => item.id === parent); return { id, name, source_file: name, visual_format: name.split(".").pop().toLowerCase(), scale: Number(scale), mounts: [{ name: mount, pose: { xyz: [0, 0, 0], rpy: [0, 0, 0] }, interface_key: "unverified" }], assembly: { parent_component: parent, parent_mount: parentPart?.mount || "world", child_mount: mount, relative_xyz: position.map(Number), relative_rpy: [0, 0, 0] } }; }), findings: [...overlaps, ...assemblyFindings].map((message) => ({ severity: "warning", check: "approximate_assembly_validation", message })) };
    const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = "forgebot-assembly-v1.json"; anchor.click(); URL.revokeObjectURL(url);
  };

  return <div className="component-editor">
    <div className="component-toolbar"><div><strong>Local component assembly</strong><small>Import meshes from FreeCAD or Blender. STEP files remain your mechanical source.</small></div><div className="component-actions"><label className="component-upload">Import parts<input type="file" accept=".glb,.gltf,.stl" multiple onChange={addFiles} /></label><select aria-label="Assembly units" value={unit} onChange={(event) => setUnit(event.target.value)}><option value="m">Metres</option><option value="mm">Millimetres</option></select><button className="secondary-button" onClick={exportManifest} disabled={!parts.length}>Export manifest</button></div></div>
    {error && <p className="error component-message">{error}</p>}
    <div className="component-layout"><div className="component-view" ref={mountRef}><span className="viewport-tag">ASSEMBLY PREVIEW · DRAG TO ORBIT</span>{!parts.length && <div className="component-empty"><strong>Start with component geometry</strong><span>Import GLB, glTF, or STL parts exported from your CAD tool.</span></div>}</div>
      <div className="component-inspector"><div className="component-section-title"><strong>Assembly tree</strong><span>{parts.length} parts</span></div>{parts.length === 0 ? <p className="muted">Your imported components will appear here.</p> : parts.map((part) => <article className="component-card" key={part.id}><div className="component-card-head"><strong>{part.name}</strong><button aria-label={`Remove ${part.name}`} onClick={() => { URL.revokeObjectURL(part.url); setParts((current) => current.filter((item) => item.id !== part.id)); }}>Remove</button></div><label>Parent component<select value={part.parent} onChange={(event) => update(part.id, "parent", event.target.value)}><option value="world">World / root</option>{parts.filter((other) => other.id !== part.id).map((other) => <option key={other.id} value={other.id}>{other.name}</option>)}</select></label><label>Named mount frame<input value={part.mount} onChange={(event) => update(part.id, "mount", event.target.value)} /></label><div className="component-coordinates">{["X", "Y", "Z"].map((axis, index) => <label key={axis}>{axis} ({unit})<input type="number" step="0.01" value={part.position[index]} onChange={(event) => { const position = [...part.position]; position[index] = Number(event.target.value); update(part.id, "position", position); }} /></label>)}</div><label>Visual scale<input type="number" min="0.0001" step="0.1" value={part.scale} onChange={(event) => update(part.id, "scale", Number(event.target.value))} /></label>{part.loadError && <p className="error">{part.loadError}</p>}</article>)}
      {(overlaps.length > 0 || assemblyFindings.length > 0) && <div className="component-findings"><strong>Assembly findings</strong>{[...assemblyFindings, ...overlaps].map((message) => <p key={message}>{message}</p>)}</div>}<p className="panel-footnote">Mesh bounds are a coarse visual warning. This does not check solid geometry, fasteners, clearances, tolerances, strength, or manufacturability.</p></div></div>
  </div>;
}
