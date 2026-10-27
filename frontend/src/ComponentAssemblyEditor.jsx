import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

export default function ComponentAssemblyEditor({ freecadExport = null, designSpec = null, onInspectComponent, onProposeSketchEdit, onApplySketchEdit, busy = false }) {
  const mountRef = useRef(null);
  const sceneRef = useRef(null);
  const meshByPartIdRef = useRef(new Map());
  const selectedPartIdRef = useRef(null);
  const selectionHelperRef = useRef(null);
  const [parts, setParts] = useState([]);
  const [selectedPartId, setSelectedPartId] = useState(null);
  const [unit, setUnit] = useState("m");
  const [error, setError] = useState("");
  const [importMessage, setImportMessage] = useState("");
  const [overlaps, setOverlaps] = useState([]);
  const [assemblyFindings, setAssemblyFindings] = useState([]);
  const [componentQuestion, setComponentQuestion] = useState("");
  const [componentAnswer, setComponentAnswer] = useState(null);
  const [editInstruction, setEditInstruction] = useState("");
  const [editProposal, setEditProposal] = useState(null);
  const [proposalDimensions, setProposalDimensions] = useState(["", "", ""]);
  const autoImportRef = useRef(null);
  const autoPartIdsRef = useRef(new Set());

  const selectPart = useCallback((partId) => {
    selectedPartIdRef.current = partId;
    setSelectedPartId(partId);
    const scene = sceneRef.current;
    const object = meshByPartIdRef.current.get(partId);
    if (selectionHelperRef.current && scene) {
      scene.remove(selectionHelperRef.current);
      selectionHelperRef.current.geometry.dispose();
      selectionHelperRef.current.material.dispose();
      selectionHelperRef.current = null;
    }
    if (object && scene) {
      const helper = new THREE.BoxHelper(object, "#e78a3c");
      helper.material.depthTest = false;
      helper.renderOrder = 10;
      scene.add(helper);
      selectionHelperRef.current = helper;
    }
    setComponentAnswer(null);
    setEditProposal(null);
  }, []);

  useEffect(() => {
    if (!freecadExport?.artifact_id || freecadExport.status !== "completed" || autoImportRef.current === freecadExport.artifact_id) return undefined;
    const urls = [];
    try {
      const manifest = freecadExport.assembly_manifest;
      if (manifest?.schema_version !== "forgebot-freecad-preview/v1" || manifest.units !== "mm" || !Array.isArray(manifest.components)) {
        throw new Error("The automatic FreeCAD export has an unsupported assembly manifest.");
      }
      const ids = new Map(manifest.components.map((component) => [component.id, uid()]));
      const imported = manifest.components.map((component, index) => {
        const encodedMesh = freecadExport.preview_meshes?.[component.source_file];
        if (!encodedMesh) throw new Error(`FreeCAD preview mesh is missing for ${component.name || component.id}.`);
        const binary = atob(encodedMesh);
        const bytes = new Uint8Array(binary.length);
        for (let byteIndex = 0; byteIndex < binary.length; byteIndex += 1) bytes[byteIndex] = binary.charCodeAt(byteIndex);
        const file = new File([bytes], component.source_file.split("/").at(-1), { type: "model/stl" });
        const url = URL.createObjectURL(file);
        urls.push(url);
        const parent = component.parent_component ? ids.get(component.parent_component) : "world";
        const position = component.relative_position_mm || component.relative_xyz;
        if (component.parent_component && !parent) throw new Error(`Parent part ${component.parent_component} is missing from the FreeCAD manifest.`);
        if (!Array.isArray(position) || position.length !== 3 || position.some((value) => !Number.isFinite(Number(value)))) throw new Error(`FreeCAD placement is invalid for ${component.name || component.id}.`);
        const designComponent = designSpec?.components?.find((item) => item.id === component.id);
        return {
          id: ids.get(component.id), name: component.name || file.name, file, url,
          designId: component.id, role: designComponent?.role || "", geometry: designComponent?.geometry || "", dimensions_m: designComponent?.dimensions_m || null,
          mount: component.child_mount || "origin", parentMount: component.parent_mount || "origin",
          interfaces: component.mount_interfaces || [], parent, position: position.map(Number), scale: 1,
          bounds: null, loadError: "", importOrder: index,
        };
      });
      autoImportRef.current = freecadExport.artifact_id;
      const replacedAutoIds = autoPartIdsRef.current;
      autoPartIdsRef.current = new Set(imported.map((part) => part.id));
      const unitScale = unit === "m" ? 1000 : 1;
      setUnit("mm");
      setParts((current) => [
        ...current.filter((part) => !replacedAutoIds.has(part.id)).map((part) => ({
          ...part,
          parent: replacedAutoIds.has(part.parent) ? "world" : part.parent,
          position: part.position.map((value) => Number(value) * unitScale),
          scale: Number(part.scale) * unitScale,
          bounds: null,
        })),
        ...imported,
      ]);
      selectPart(imported[0]?.id || null);
      setError("");
      setImportMessage(`Automatically loaded ${imported.length} FreeCAD parts with their assembly links and placements; other assembly parts were preserved.`);
    } catch (importError) {
      urls.forEach((url) => URL.revokeObjectURL(url));
      setError(importError.message || "Could not load the generated FreeCAD assembly.");
    }
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, [freecadExport?.artifact_id, selectPart]);

  const update = (id, key, value) => setParts((current) => current.map((part) => part.id === id ? { ...part, [key]: value } : part));
  const selectedPart = parts.find((part) => part.id === selectedPartId);
  const selectedModelComponent = designSpec?.components?.find((component) => component.id === selectedPart?.designId);
  const kernelValidation = freecadExport?.export_report?.solid_kernel_validation;
  const askAboutSelected = async () => {
    if (!selectedModelComponent || !onInspectComponent) return;
    const answer = await onInspectComponent(selectedModelComponent.id, componentQuestion);
    setComponentAnswer(answer);
  };
  const proposeSelectedEdit = async () => {
    if (!selectedModelComponent || !onProposeSketchEdit || !editInstruction.trim()) return;
    const proposal = await onProposeSketchEdit(selectedModelComponent.id, editInstruction.trim());
    setEditProposal(proposal);
    setProposalDimensions(proposal.dimensions_m.map((value) => (Number(value) * 1000).toFixed(1)));
  };
  const proposeKernelRepair = async () => {
    if (!selectedModelComponent || !onProposeSketchEdit) return;
    const findings = kernelValidation?.intersections?.filter((item) => item.component_ids?.includes(selectedModelComponent.id)) || [];
    if (!findings.length) return;
    const instruction = `Review the selected component against these exact FreeCAD solid-kernel findings: ${findings.map((item) => `${item.component_ids.join(" and ")}: ${item.intersection_volume_mm3} mm3 overlap`).join("; ")}. Propose a conservative dimensional change that may reduce unintended interference while preserving its intended role. Do not claim this repair is verified; the assembly must be rebuilt and checked again.`;
    setEditInstruction(instruction);
    const proposal = await onProposeSketchEdit(selectedModelComponent.id, instruction);
    setEditProposal(proposal);
    setProposalDimensions(proposal.dimensions_m.map((value) => (Number(value) * 1000).toFixed(1)));
  };
  const applySelectedEdit = () => {
    if (!selectedModelComponent || !editProposal || !onApplySketchEdit) return;
    const geometry = selectedModelComponent.geometry;
    const values = geometry === "cylinder"
      ? [Number(proposalDimensions[0]) / 1000, Number(proposalDimensions[0]) / 1000, Number(proposalDimensions[2]) / 1000]
      : geometry === "sphere"
        ? [Number(proposalDimensions[0]) / 1000, Number(proposalDimensions[0]) / 1000, Number(proposalDimensions[0]) / 1000]
        : proposalDimensions.map((value) => Number(value) / 1000);
    if (values.some((value) => !Number.isFinite(value) || value < 0.005 || value > 5)) return;
    onApplySketchEdit([{ id: selectedModelComponent.id, dimensions_m: values }]);
  };
  const updateParent = (id, parentId) => {
    const parentPart = parts.find((part) => part.id === parentId);
    const parentMount = parentId === "world" ? "world" : parentPart?.interfaces?.[0]?.name || parentPart?.mount || "origin";
    setParts((current) => current.map((part) => part.id === id ? { ...part, parent: parentId, parentMount } : part));
  };
  const addFiles = (event) => {
    setError("");
    setImportMessage("");
    const accepted = [...event.target.files];
    const bad = accepted.filter((file) => !supported.has(file.name.split(".").pop().toLowerCase()));
    if (bad.length) setError(`Unsupported file type: ${bad.map((file) => file.name).join(", ")}. Choose GLB, glTF, or STL.`);
    const good = accepted.filter((file) => supported.has(file.name.split(".").pop().toLowerCase()));
    setParts((current) => [...current, ...good.map((file, index) => ({ id: uid(), name: file.name, file, url: URL.createObjectURL(file), mount: "mount", parent: index === 0 && current.length === 0 ? "world" : current[0]?.id || "world", position: [index * 0.12, 0, 0], scale: 1, bounds: null, loadError: "" }))]);
    event.target.value = "";
  };

  const importFreecadFolder = async (event) => {
    setError("");
    setImportMessage("");
    const files = [...(event.target.files || [])];
    event.target.value = "";
    try {
      const manifestFile = files.find((file) => file.name.toLowerCase() === "assembly_manifest.json");
      if (!manifestFile || !manifestFile.webkitRelativePath) throw new Error("Choose the generated forgebot_step_export folder so ForgeBot can read its assembly_manifest.json and parts folder.");
      const rootPath = manifestFile.webkitRelativePath.split("/").slice(0, -1).join("/");
      const relativePath = (file) => file.webkitRelativePath.slice(rootPath ? rootPath.length + 1 : 0).replace(/\\/g, "/");
      const filesByPath = new Map(files.map((file) => [relativePath(file), file]));
      const manifest = JSON.parse(await manifestFile.text());
      if (manifest.schema_version !== "forgebot-freecad-preview/v1" || manifest.units !== "mm" || !Array.isArray(manifest.components)) {
        throw new Error("This folder does not contain a supported ForgeBot FreeCAD preview manifest.");
      }
      const ids = new Map(manifest.components.map((component) => [component.id, uid()]));
      const imported = [];
      for (const [index, component] of manifest.components.entries()) {
        const file = filesByPath.get(String(component.source_file || "").replace(/\\/g, "/"));
        if (!file || !file.name.toLowerCase().endsWith(".stl")) throw new Error(`Preview mesh is missing for ${component.name || component.id}.`);
        const parent = component.parent_component ? ids.get(component.parent_component) : "world";
        if (component.parent_component && !parent) throw new Error(`Parent part ${component.parent_component} is missing from the assembly manifest.`);
        const position = component.relative_position_mm || component.relative_xyz;
        if (!Array.isArray(position) || position.length !== 3 || position.some((value) => !Number.isFinite(Number(value)))) throw new Error(`Placement coordinates are invalid for ${component.name || component.id}.`);
        const url = URL.createObjectURL(file);
        imported.push({
          id: ids.get(component.id), name: component.name || file.name, file, url,
          mount: component.child_mount || "origin", parentMount: component.parent_mount || "origin",
          interfaces: component.mount_interfaces || [], parent, position: position.map(Number), scale: 1,
          bounds: null, loadError: "", importOrder: index,
        });
      }
      const oldToMillimetres = unit === "m" ? 1000 : 1;
      setUnit("mm");
      setParts((current) => [...current.map((part) => ({
        ...part,
        position: part.position.map((value) => Number(value) * oldToMillimetres),
        scale: Number(part.scale) * oldToMillimetres,
        bounds: null,
      })), ...imported]);
      setImportMessage(`Imported ${imported.length} FreeCAD component meshes with their parent links and placements. Existing assembly parts were converted to millimetres. The STEP file remains the CAD source.`);
    } catch (importError) {
      setError(importError.message || "Could not import this FreeCAD export folder.");
    }
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
    const scene = new THREE.Scene(); sceneRef.current = scene; meshByPartIdRef.current = new Map(); scene.background = new THREE.Color("#f5f8f6");
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
        object.name = part.name; object.userData.partId = part.id; object.scale.setScalar((Number(part.scale) || 1) * unitFactor); object.position.set(...(assembly.positions.get(part.id) || part.position.map(Number)).map((value) => Number(value) * unitFactor));
        object.traverse((item) => { if (item.isMesh) { item.castShadow = true; item.material = item.material || new THREE.MeshStandardMaterial({ color: "#4c9b8c" }); } });
        scene.add(object); resources.push(object); meshByPartIdRef.current.set(part.id, object);
        if (selectedPartIdRef.current === part.id) selectPart(part.id);
        const box = new THREE.Box3().setFromObject(object);
        setParts((current) => current.map((item) => item.id === part.id ? { ...item, bounds: { min: box.min.toArray(), max: box.max.toArray() } } : item));
      }).catch((err) => setParts((current) => current.map((item) => item.id === part.id ? { ...item, loadError: err.message || "Could not parse this mesh." } : item)));
    });
    const resize = () => { const w = Math.max(mount.clientWidth, 1); const h = Math.max(mount.clientHeight, 1); camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.setSize(w, h, false); };
    let pointerStart = null;
    const raycaster = new THREE.Raycaster(); const pointer = new THREE.Vector2();
    const onPointerDown = (event) => { pointerStart = [event.clientX, event.clientY]; };
    const onPointerUp = (event) => {
      if (!pointerStart || Math.hypot(event.clientX - pointerStart[0], event.clientY - pointerStart[1]) > 5) { pointerStart = null; return; }
      pointerStart = null;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects([...meshByPartIdRef.current.values()], true)[0];
      let object = hit?.object;
      while (object && !object.userData.partId) object = object.parent;
      if (object?.userData.partId) selectPart(object.userData.partId);
    };
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("pointerup", onPointerUp);
    const observer = new ResizeObserver(resize); observer.observe(mount); resize();
    let frame; const render = () => { controls.update(); renderer.render(scene, camera); frame = requestAnimationFrame(render); }; render();
    return () => { alive = false; cancelAnimationFrame(frame); observer.disconnect(); controls.dispose(); renderer.domElement.removeEventListener("pointerdown", onPointerDown); renderer.domElement.removeEventListener("pointerup", onPointerUp); if (selectionHelperRef.current) { scene.remove(selectionHelperRef.current); selectionHelperRef.current.geometry.dispose(); selectionHelperRef.current.material.dispose(); selectionHelperRef.current = null; } resources.forEach((object) => { object.traverse((item) => { item.geometry?.dispose(); if (Array.isArray(item.material)) item.material.forEach((material) => material.dispose()); else item.material?.dispose(); }); }); meshByPartIdRef.current.clear(); sceneRef.current = null; renderer.dispose(); renderer.domElement.remove(); };
  }, [unit, parts.map((part) => `${part.id}:${part.url}:${part.scale}:${part.parent}:${part.position.join(",")}`).join("|"), selectPart]);

  const exportManifest = () => {
    const manifest = { schema_version: "forgebot-component-assembly/v1", units: unit, components: parts.map(({ id, name, mount, parentMount, parent, position, scale, interfaces }) => { const parentPart = parts.find((item) => item.id === parent); return { id, name, source_file: name, visual_format: name.split(".").pop().toLowerCase(), scale: Number(scale), mounts: interfaces?.length ? interfaces : [{ name: mount, pose: { xyz: [0, 0, 0], rpy: [0, 0, 0] }, interface_key: "unverified" }], assembly: { parent_component: parent, parent_mount: parent === "world" ? "world" : parentMount || parentPart?.mount || "origin", child_mount: mount, relative_xyz: position.map(Number), relative_rpy: [0, 0, 0] } }; }), findings: [...overlaps, ...assemblyFindings].map((message) => ({ severity: "warning", check: "approximate_assembly_validation", message })) };
    const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = "forgebot-assembly-v1.json"; anchor.click(); URL.revokeObjectURL(url);
  };

  return <div className="component-editor">
    <div className="component-toolbar"><div><strong>CAD assembly & component AI</strong><small>Select a robot part in the 3D view or assembly tree to inspect and improve its design.</small></div><div className="component-actions"><label className="component-upload">Import FreeCAD export<input type="file" webkitdirectory="" directory="" onChange={importFreecadFolder} /></label><label className="component-upload component-upload-secondary">Import mesh files<input type="file" accept=".glb,.gltf,.stl" multiple onChange={addFiles} /></label><select aria-label="Assembly units" value={unit} onChange={(event) => setUnit(event.target.value)}><option value="m">Metres</option><option value="mm">Millimetres</option></select><button className="secondary-button" onClick={exportManifest} disabled={!parts.length}>Export manifest</button></div></div>
    {error && <p className="error component-message">{error}</p>}{importMessage && <p className="component-import-message">{importMessage}</p>}
    <div className="component-layout"><div className="component-view" ref={mountRef}><span className="viewport-tag">ASSEMBLY PREVIEW · DRAG TO ORBIT</span>{!parts.length && <div className="component-empty"><strong>Start with component geometry</strong><span>Import GLB, glTF, or STL parts exported from your CAD tool.</span></div>}</div>
      <div className="component-inspector"><div className="component-section-title"><strong>Assembly tree</strong><span>{parts.length} parts</span></div>{parts.length === 0 ? <p className="muted">Your imported components will appear here.</p> : parts.map((part) => { const parentPart = parts.find((other) => other.id === part.parent); return <article className={`component-card ${selectedPartId === part.id ? "component-card-selected" : ""}`} key={part.id}><div className="component-card-head"><button className="component-select-button" type="button" aria-pressed={selectedPartId === part.id} onClick={() => selectPart(part.id)}>{part.name}</button><button aria-label={`Remove ${part.name}`} onClick={() => { if (selectedPartId === part.id) selectPart(null); URL.revokeObjectURL(part.url); setParts((current) => current.filter((item) => item.id !== part.id)); }}>Remove</button></div>{part.role && <small className="component-role">{part.role} · {part.geometry}</small>}<label>Parent component<select value={part.parent} onChange={(event) => updateParent(part.id, event.target.value)}><option value="world">World / root</option>{parts.filter((other) => other.id !== part.id).map((other) => <option key={other.id} value={other.id}>{other.name}</option>)}</select></label>{part.parent !== "world" && <label>Parent mount frame{parentPart?.interfaces?.length ? <select value={part.parentMount || parentPart.interfaces[0].name} onChange={(event) => update(part.id, "parentMount", event.target.value)}>{parentPart.interfaces.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.type}</option>)}</select> : <input value={part.parentMount || "origin"} onChange={(event) => update(part.id, "parentMount", event.target.value)} />}</label>}<label>Child mount frame{part.interfaces?.length ? <select value={part.mount} onChange={(event) => update(part.id, "mount", event.target.value)}>{part.interfaces.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.type}</option>)}</select> : <input value={part.mount} onChange={(event) => update(part.id, "mount", event.target.value)} />}</label><div className="component-coordinates">{["X", "Y", "Z"].map((axis, index) => <label key={axis}>{axis} ({unit})<input type="number" step="0.01" value={part.position[index]} onChange={(event) => { const position = [...part.position]; position[index] = Number(event.target.value); update(part.id, "position", position); }} /></label>)}</div><label>Visual scale<input type="number" min="0.0001" step="0.1" value={part.scale} onChange={(event) => update(part.id, "scale", Number(event.target.value))} /></label>{part.loadError && <p className="error">{part.loadError}</p>}</article>; })}
      {selectedPart && <section className="component-ai-panel"><div className="component-section-title"><strong>Selected component</strong><span>{selectedPart.name}</span></div>{selectedModelComponent ? <><p className="muted">{selectedModelComponent.role} · {selectedModelComponent.geometry} · {selectedModelComponent.dimensions_m.map((value) => `${(value * 1000).toFixed(0)} mm`).join(" × ")}</p><label>Ask about this component<textarea rows="2" maxLength="1000" placeholder="What is this part for? How does it connect to the robot?" value={componentQuestion} onChange={(event) => setComponentQuestion(event.target.value)} /></label><button className="secondary-button" type="button" disabled={busy} onClick={() => askAboutSelected().catch(() => {})}>{busy ? "Asking Meta…" : "Ask AI about this part"}</button>{componentAnswer && <div className="component-ai-answer"><strong>{componentAnswer.what_it_is}</strong><p>{componentAnswer.intended_role}</p><strong>In the design model</strong><ul>{componentAnswer.known_from_design.map((item, index) => <li key={index}>{item}</li>)}</ul><strong>Unknown / unverified</strong><ul>{componentAnswer.unknown_or_unverified.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}<label>How should this part improve?<textarea rows="2" maxLength="1200" placeholder="Example: widen the deck to carry a larger sensor" value={editInstruction} onChange={(event) => setEditInstruction(event.target.value)} /></label><button className="secondary-button" type="button" disabled={busy || !editInstruction.trim()} onClick={() => proposeSelectedEdit().catch(() => {})}>{busy ? "Working…" : "Ask AI to propose an edit"}</button>{kernelValidation?.intersections?.some((item) => item.component_ids?.includes(selectedModelComponent.id)) && <button className="secondary-button" type="button" disabled={busy} onClick={() => proposeKernelRepair().catch(() => {})}>{busy ? "Reviewing kernel finding…" : "Ask AI to propose a repair for this finding"}</button>}{editProposal && <div className="cad-sketch-proposal"><strong>Proposal · human review needed</strong><p>{editProposal.rationale}</p><small>{editProposal.review_notes}</small><div className="cad-sketch-dimensions">{(selectedModelComponent.geometry === "cylinder" ? [{ label: "Diameter", index: 0 }, { label: "Length", index: 2 }] : selectedModelComponent.geometry === "sphere" ? [{ label: "Diameter", index: 0 }] : [{ label: "Width", index: 0 }, { label: "Depth", index: 1 }, { label: "Height", index: 2 }]).map(({ label, index }) => <label key={label}>{label} (mm)<input type="number" min="5" max="5000" step="0.1" value={proposalDimensions[index]} onChange={(event) => setProposalDimensions((current) => current.map((value, i) => i === index || (selectedModelComponent.geometry === "cylinder" && index === 0 && i === 1) ? event.target.value : value))} /></label>)}</div><button className="artifact-download" type="button" disabled={busy} onClick={applySelectedEdit}>Accept edit & rebuild CAD</button></div>}<small className="muted">The model reads the saved robot wireframe and latest local kernel findings when available. Its proposal is not verified until rebuilt and checked again; it cannot certify fit, strength, or safety.</small></> : <p className="muted">This is an imported mesh without a matching component in the saved robot design, so AI inspection and sketch editing are unavailable.</p>}</section>}
      {(overlaps.length > 0 || assemblyFindings.length > 0) && <div className="component-findings"><strong>Browser assembly findings · approximate</strong>{[...assemblyFindings, ...overlaps].map((message) => <p key={message}>{message}</p>)}</div>}{kernelValidation && <div className="component-findings"><strong>FreeCAD solid-kernel check · {kernelValidation.status?.replace(/_/g, " ")}</strong><p>{kernelValidation.component_validity?.filter((item) => item.valid).length || 0}/{kernelValidation.component_validity?.length || 0} components have valid solids · {kernelValidation.intersections?.length || 0} intersecting or failed pair checks</p>{kernelValidation.intersections?.slice(0, 12).map((item, index) => <p key={`${item.component_ids?.join("-")}-${index}`}>{item.component_ids?.join(" ↔ ")} · {item.status === "check_failed" ? item.message : `${Number(item.intersection_volume_mm3).toFixed(2)} mm³ overlap; review mount intent`}</p>)}{kernelValidation.intersections?.length > 12 && <small>{kernelValidation.intersections.length - 12} more pairs are recorded in the export report.</small>}</div>}<p className="panel-footnote">FreeCAD exact solid checks detect geometric validity and static intersections. Approximate mesh bounds remain visual warnings. Neither check covers moving collisions, mount tolerances, fasteners, strength, or manufacturability.</p></div></div>
  </div>;
}
