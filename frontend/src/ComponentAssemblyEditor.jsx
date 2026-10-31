import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

const supported = new Set(["glb", "gltf", "stl"]);
const uid = () => `part-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

function partMaterialParams(part = {}) {
  const color = /^#[0-9a-fA-F]{6}$/.test(part.color || "") ? part.color : "#4c9b8c";
  const text = `${part.name || ""} ${part.role || ""} ${part.functionalCategory || ""}`.toLowerCase();
  if (/(standoff|spacer|pillar|caster|shaft|bearing|sonar|transducer|aluminum|steel|metal)/.test(text)) {
    return { color, metalness: 0.72, roughness: 0.28 };
  }
  if (/(wheel|tire|roller|tread|foot|feet|rubber)/.test(text)) {
    return { color, metalness: 0.08, roughness: 0.84 };
  }
  if (/(plate|deck|acrylic|panel|chassis)/.test(text)) {
    return { color, metalness: 0.14, roughness: 0.24, transparent: true, opacity: 0.9 };
  }
  if (/(pcb|board|controller|arduino|raspberry|breadboard|ultrasonic|sensor)/.test(text)) {
    return { color, metalness: 0.24, roughness: 0.44 };
  }
  if (/(motor|gear|servo|actuator)/.test(text)) {
    return { color, metalness: 0.32, roughness: 0.38 };
  }
  return { color, metalness: 0.18, roughness: 0.52 };
}

function applySubVisualVertexColors(geometry, visuals, fallbackHex) {
  if (!Array.isArray(visuals) || visuals.length <= 1 || !geometry.attributes?.position) return false;
  const pos = geometry.attributes.position;
  const count = pos.count;
  const colors = new Float32Array(count * 3);
  const baseColor = new THREE.Color(/^#[0-9a-fA-F]{6}$/.test(visuals[0]?.color || "") ? visuals[0].color : fallbackHex);
  const details = visuals.slice(1).map((vis) => {
    const dims = (vis.dimensions_m || [0.01, 0.01, 0.01]).map((v) => Number(v) * 1000);
    const orig = (vis.origin_m || [0, 0, 0]).map((v) => Number(v) * 1000);
    const rpy = (vis.rpy || [0, 0, 0]).map(Number);
    const rot = new THREE.Euler(rpy[0] || 0, rpy[1] || 0, rpy[2] || 0, "XYZ");
    const invQuat = new THREE.Quaternion().setFromEuler(rot).invert();
    return {
      dims,
      orig: new THREE.Vector3(orig[0], orig[1], orig[2]),
      invQuat,
      color: new THREE.Color(/^#[0-9a-fA-F]{6}$/.test(vis.color || "") ? vis.color : fallbackHex),
      volume: dims[0] * dims[1] * dims[2],
    };
  }).sort((a, b) => a.volume - b.volume);

  const v = new THREE.Vector3();
  const tol = 0.45;
  for (let i = 0; i < count; i += 3) {
    let picked = baseColor;
    for (const d of details) {
      const hx = d.dims[0] / 2 + tol;
      const hy = d.dims[1] / 2 + tol;
      const hz = d.dims[2] / 2 + tol;
      let allInside = true;
      for (let k = 0; k < 3; k += 1) {
        v.set(
          pos.getX(i + k) - d.orig.x,
          pos.getY(i + k) - d.orig.y,
          pos.getZ(i + k) - d.orig.z
        ).applyQuaternion(d.invQuat);
        if (Math.abs(v.x) > hx || Math.abs(v.y) > hy || Math.abs(v.z) > hz) {
          allInside = false;
          break;
        }
      }
      if (allInside) {
        picked = d.color;
        break;
      }
    }
    for (let k = 0; k < 3; k += 1) {
      const idx = (i + k) * 3;
      colors[idx] = picked.r;
      colors[idx + 1] = picked.g;
      colors[idx + 2] = picked.b;
    }
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  return true;
}

function pointToSegmentDistSq(px, py, pz, ax, ay, az, bx, by, bz) {
  const abx = bx - ax;
  const aby = by - ay;
  const abz = bz - az;
  const lenSq = abx * abx + aby * aby + abz * abz;
  let t = 0;
  if (lenSq > 1e-6) {
    t = ((px - ax) * abx + (py - ay) * aby + (pz - az) * abz) / lenSq;
    t = Math.max(0, Math.min(1, t));
  }
  const dx = px - (ax + t * abx);
  const dy = py - (ay + t * aby);
  const dz = pz - (az + t * abz);
  return dx * dx + dy * dy + dz * dz;
}

function applyCableSegmentVertexColors(geometry, cables, fallbackHex) {
  if (!Array.isArray(cables) || !cables.length || !geometry.attributes?.position) return false;
  const pos = geometry.attributes.position;
  const count = pos.count;
  const colors = new Float32Array(count * 3);
  const defaultColor = new THREE.Color(fallbackHex || "#ef4444");
  const segments = [];
  for (const cable of cables) {
    const pts = cable.points_m || [];
    const col = new THREE.Color(/^#[0-9a-fA-F]{6}$/.test(cable.color || "") ? cable.color : "#ef4444");
    for (let s = 0; s < pts.length - 1; s += 1) {
      segments.push({
        ax: Number(pts[s][0]) * 1000,
        ay: Number(pts[s][1]) * 1000,
        az: Number(pts[s][2]) * 1000,
        bx: Number(pts[s + 1][0]) * 1000,
        by: Number(pts[s + 1][1]) * 1000,
        bz: Number(pts[s + 1][2]) * 1000,
        color: col,
      });
    }
  }
  if (!segments.length) return false;
  for (let i = 0; i < count; i += 3) {
    const cx = (pos.getX(i) + pos.getX(i + 1) + pos.getX(i + 2)) / 3;
    const cy = (pos.getY(i) + pos.getY(i + 1) + pos.getY(i + 2)) / 3;
    const cz = (pos.getZ(i) + pos.getZ(i + 1) + pos.getZ(i + 2)) / 3;
    let picked = defaultColor;
    let bestDistSq = Infinity;
    for (const seg of segments) {
      const dSq = pointToSegmentDistSq(cx, cy, cz, seg.ax, seg.ay, seg.az, seg.bx, seg.by, seg.bz);
      if (dSq < bestDistSq) {
        bestDistSq = dSq;
        picked = seg.color;
      }
    }
    for (let k = 0; k < 3; k += 1) {
      const idx = (i + k) * 3;
      colors[idx] = picked.r;
      colors[idx + 1] = picked.g;
      colors[idx + 2] = picked.b;
    }
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  return true;
}

function makeMesh(file, url, part = {}) {
  const ext = file.name.split(".").pop().toLowerCase();
  if (ext === "stl") return new STLLoader().loadAsync(url).then((geometry) => {
    geometry.computeVertexNormals();
    const matParams = partMaterialParams(part);
    const hasVertexColors =
      applySubVisualVertexColors(geometry, part.visuals, matParams.color) ||
      applyCableSegmentVertexColors(geometry, part.cables, matParams.color);
    const material = new THREE.MeshStandardMaterial(
      hasVertexColors ? { ...matParams, color: "#ffffff", vertexColors: true } : matParams
    );
    return new THREE.Mesh(geometry, material);
  });
  return new GLTFLoader().loadAsync(url).then((gltf) => gltf.scene);
}

function boundsOverlap(a, b) {
  return a.min[0] < b.max[0] && a.max[0] > b.min[0] && a.min[1] < b.max[1] && a.max[1] > b.min[1] && a.min[2] < b.max[2] && a.max[2] > b.min[2];
}

export default function ComponentAssemblyEditor({ freecadExport = null, designSpec = null, onInspectComponent, onProposeSketchEdit, onApplySketchEdit, onProposeDesignRevision, onApplyDesignRevision, busy = false }) {
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
  const [revisionProposal, setRevisionProposal] = useState(null);
  const [revisionNotice, setRevisionNotice] = useState("");
  const [expandedCritique, setExpandedCritique] = useState(false);

  useEffect(() => {
    if (!expandedCritique) return undefined;
    const closeOnEscape = (event) => { if (event.key === "Escape") setExpandedCritique(false); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [expandedCritique]);
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
          designId: component.id, role: designComponent?.role || component.role || "", geometry: designComponent?.geometry || "", dimensions_m: designComponent?.dimensions_m || null,
          color: designComponent?.color || component.color || "#4c9b8c",
          functionalCategory: designComponent?.functional_category || component.functional_category || "",
          visuals: component.visuals || null,
          cables: component.cables || null,
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
  const proposeDesignRevision = async () => {
    if (!onProposeDesignRevision) return;
    setRevisionNotice("");
    try {
      setRevisionProposal(await onProposeDesignRevision(""));
    } catch (err) {
      setRevisionNotice(err.message || "Could not propose a revision.");
    }
  };
  const applyDesignRevision = async () => {
    if (!revisionProposal || !onApplyDesignRevision) return;
    setRevisionNotice("");
    try {
      await onApplyDesignRevision(revisionProposal);
      setRevisionProposal(null);
      setRevisionNotice("Revision applied and CAD rebuilt. Review the new validation below.");
    } catch (err) {
      setRevisionNotice(err.message || "Could not apply the revision.");
    }
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
        const designComponent = designSpec?.components?.find((item) => item.id === component.id);
        imported.push({
          id: ids.get(component.id), name: component.name || file.name, file, url,
          designId: component.id, role: designComponent?.role || component.role || "", geometry: designComponent?.geometry || "", dimensions_m: designComponent?.dimensions_m || null,
          color: designComponent?.color || component.color || "#4c9b8c",
          functionalCategory: designComponent?.functional_category || component.functional_category || "",
          visuals: component.visuals || null,
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
      makeMesh(part.file, part.url, part).then((object) => {
        if (!alive) { object.traverse((item) => { item.geometry?.dispose(); }); return; }
        const unitFactor = unit === "mm" ? 0.001 : 1;
        object.name = part.name; object.userData.partId = part.id; object.scale.setScalar((Number(part.scale) || 1) * unitFactor); object.position.set(...(assembly.positions.get(part.id) || part.position.map(Number)).map((value) => Number(value) * unitFactor));
        object.traverse((item) => { if (item.isMesh) { item.castShadow = true; item.material = item.material || new THREE.MeshStandardMaterial(partMaterialParams(part)); } });
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
  }, [unit, parts.map((part) => `${part.id}:${part.url}:${part.scale}:${part.parent}:${part.position.join(",")}:${part.color || ""}`).join("|"), selectPart]);

  const exportManifest = () => {
    const manifest = { schema_version: "forgebot-component-assembly/v1", units: unit, components: parts.map(({ id, name, mount, parentMount, parent, position, scale, interfaces }) => { const parentPart = parts.find((item) => item.id === parent); return { id, name, source_file: name, visual_format: name.split(".").pop().toLowerCase(), scale: Number(scale), mounts: interfaces?.length ? interfaces : [{ name: mount, pose: { xyz: [0, 0, 0], rpy: [0, 0, 0] }, interface_key: "unverified" }], assembly: { parent_component: parent, parent_mount: parent === "world" ? "world" : parentMount || parentPart?.mount || "origin", child_mount: mount, relative_xyz: position.map(Number), relative_rpy: [0, 0, 0] } }; }), findings: [...overlaps, ...assemblyFindings].map((message) => ({ severity: "warning", check: "approximate_assembly_validation", message })) };
    const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = "forgebot-assembly-v1.json"; anchor.click(); URL.revokeObjectURL(url);
  };

  return <div className="component-editor">
    <div className="component-toolbar"><div><strong>CAD assembly & component AI</strong><small>Select a robot part in the 3D view or assembly tree to inspect and improve its design.</small></div><div className="component-actions"><label className="component-upload">Import FreeCAD export<input type="file" webkitdirectory="" directory="" onChange={importFreecadFolder} /></label><label className="component-upload component-upload-secondary">Import mesh files<input type="file" accept=".glb,.gltf,.stl" multiple onChange={addFiles} /></label><select aria-label="Assembly units" value={unit} onChange={(event) => setUnit(event.target.value)}><option value="m">Metres</option><option value="mm">Millimetres</option></select><button className="secondary-button" onClick={exportManifest} disabled={!parts.length}>Export manifest</button></div></div>
    {error && <p className="error component-message">{error}</p>}{importMessage && <p className="component-import-message">{importMessage}</p>}
    <div className="component-layout"><div className="component-view" ref={mountRef}><span className="viewport-tag">ASSEMBLY PREVIEW · DRAG TO ORBIT</span>{!parts.length && <div className="component-empty"><strong>Start with component geometry</strong><span>Import GLB, glTF, or STL parts exported from your CAD tool.</span></div>}</div>
      <div className="component-inspector"><div className="component-section-title"><strong>Assembly tree</strong><span>{parts.length} parts</span></div>{parts.length === 0 ? <p className="muted">Your imported components will appear here.</p> : parts.map((part) => { const parentPart = parts.find((other) => other.id === part.parent); return <article className={`component-card ${selectedPartId === part.id ? "component-card-selected" : ""}`} key={part.id}><div className="component-card-head"><button className="component-select-button" type="button" aria-pressed={selectedPartId === part.id} onClick={() => selectPart(part.id)}><span style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", backgroundColor: part.color || "#4c9b8c", marginRight: 6, border: "1px solid rgba(0,0,0,0.2)", verticalAlign: "middle" }} />{part.name}</button><button aria-label={`Remove ${part.name}`} onClick={() => { if (selectedPartId === part.id) selectPart(null); URL.revokeObjectURL(part.url); setParts((current) => current.filter((item) => item.id !== part.id)); }}>Remove</button></div>{part.role && <small className="component-role">{part.role} · {part.geometry}</small>}<label>Parent component<select value={part.parent} onChange={(event) => updateParent(part.id, event.target.value)}><option value="world">World / root</option>{parts.filter((other) => other.id !== part.id).map((other) => <option key={other.id} value={other.id}>{other.name}</option>)}</select></label>{part.parent !== "world" && <label>Parent mount frame{parentPart?.interfaces?.length ? <select value={part.parentMount || parentPart.interfaces[0].name} onChange={(event) => update(part.id, "parentMount", event.target.value)}>{parentPart.interfaces.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.type}</option>)}</select> : <input value={part.parentMount || "origin"} onChange={(event) => update(part.id, "parentMount", event.target.value)} />}</label>}<label>Child mount frame{part.interfaces?.length ? <select value={part.mount} onChange={(event) => update(part.id, "mount", event.target.value)}>{part.interfaces.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.type}</option>)}</select> : <input value={part.mount} onChange={(event) => update(part.id, "mount", event.target.value)} />}</label><div className="component-coordinates">{["X", "Y", "Z"].map((axis, index) => <label key={axis}>{axis} ({unit})<input type="number" step="0.01" value={part.position[index]} onChange={(event) => { const position = [...part.position]; position[index] = Number(event.target.value); update(part.id, "position", position); }} /></label>)}</div><label>Visual scale<input type="number" min="0.0001" step="0.1" value={part.scale} onChange={(event) => update(part.id, "scale", Number(event.target.value))} /></label>{part.loadError && <p className="error">{part.loadError}</p>}</article>; })}
      {designSpec && (designSpec.visual_observation || designSpec.grounding) && <section className="component-ai-panel"><div className="component-section-title"><strong>Visual grounding</strong><span>how the spec was derived</span></div>{(Array.isArray(designSpec.visual_observation) ? designSpec.visual_observation : [designSpec.visual_observation]).filter(Boolean).map((observation, index) => <div key={observation.view || index}><p className="muted">{observation.view && <strong>[{observation.view}] </strong>}{observation.scene_summary}</p><p><small>{observation.observed_assemblies?.length || 0} visible assemblies observed{observation.view ? ` in the ${observation.view} view` : ""}.</small></p></div>)}{designSpec.grounding && <ul>{(designSpec.grounding.notes || []).map((note, index) => <li key={index}><small>{note}</small></li>)}</ul>}</section>}
      {designSpec?.visual_critique && (
        <section className="component-ai-panel">
          <div className="component-section-title">
            <strong>Vision-in-the-loop verification</strong>
            {designSpec.visual_critique.status === "skipped" ? (
              <span style={{ color: "#94a3b8", fontWeight: 600 }}>Skipped</span>
            ) : (
              <span style={{ color: designSpec.visual_critique.passed_verification ? "#10b981" : "#f59e0b", fontWeight: 600 }}>
                {designSpec.visual_critique.passed_verification ? "Verified" : "Review advisory"} · {Math.round((designSpec.visual_critique.visual_match_score || 0) * 100)}% match
              </span>
            )}
          </div>
          {designSpec.visual_critique.status === "skipped" ? (
            <p className="muted">
              Visual critique pass was skipped{designSpec.visual_critique.reason ? `: ${designSpec.visual_critique.reason}` : "."}
            </p>
          ) : (
            <>
              <p className="muted">{designSpec.visual_critique.critique_summary}</p>
              {designSpec.visual_critique.multiview_render_base64 && (
                <div
                  role="button"
                  tabIndex={0}
                  aria-label="Open four-view CAD inspection"
                  onClick={() => setExpandedCritique(true)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setExpandedCritique(true); }}
                  className="visual-critique-thumbnail"
                  title="Click to expand high-res 4-view CAD inspection modal"
                >
                  <img
                    src={`data:image/png;base64,${designSpec.visual_critique.multiview_render_base64}`}
                    alt="4-view 3D CAD render verification"
                    style={{ width: "100%", display: "block" }}
                  />
                  <div
                    style={{
                      position: "absolute",
                      bottom: 0,
                      left: 0,
                      right: 0,
                      background: "linear-gradient(transparent, rgba(15, 23, 42, 0.94))",
                      padding: "16px 8px 6px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "#e2e8f0",
                      fontSize: "0.78rem",
                      fontWeight: 600,
                    }}
                  >
                    <span>Open four-view inspection</span>
                  </div>
                </div>
              )}
              {(designSpec.visual_critique.symmetry_assessment || designSpec.visual_critique.stance_and_mounting_assessment) && (
                <div className="component-ai-answer">
                  <strong>Visual-spatial assessments</strong>
                  <ul>
                    {designSpec.visual_critique.symmetry_assessment && (
                      <li>Symmetry: {designSpec.visual_critique.symmetry_assessment}</li>
                    )}
                    {designSpec.visual_critique.stance_and_mounting_assessment && (
                      <li>Stance & mounting: {designSpec.visual_critique.stance_and_mounting_assessment}</li>
                    )}
                  </ul>
                </div>
              )}
            </>
          )}
        </section>
      )}
      {expandedCritique && designSpec?.visual_critique && (
        <div
          className="visual-critique-backdrop"
          role="presentation"
          onClick={() => setExpandedCritique(false)}
        >
          <div
            className="visual-critique-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="visual-critique-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div
              className="visual-critique-header"
            >
              <div className="visual-critique-title-group">
                <strong id="visual-critique-title">
                  Vision-in-the-loop review
                </strong>
                <span role="status" className={`visual-critique-status ${designSpec.visual_critique.passed_verification ? "verified" : "advisory"}`}
                >
                  {designSpec.visual_critique.passed_verification ? "Verified" : "Review advisory"} · {Math.round((designSpec.visual_critique.visual_match_score || 0) * 100)}% match
                </span>
              </div>
              <button
                type="button"
                className="visual-critique-close"
                onClick={() => setExpandedCritique(false)}
                aria-label="Close vision review"
              >
                Close
              </button>
            </div>
            <div className="visual-critique-body">
              <div className="visual-critique-render">
                <img
                  src={`data:image/png;base64,${designSpec.visual_critique.multiview_render_base64}`}
                  alt="CAD assembly shown in isometric, front, side, and top views"
                />
              </div>
              <div className="visual-critique-findings">
                <div>
                  <div className="visual-critique-section-label">
                    Review summary
                  </div>
                  <p className="visual-critique-summary">
                    {designSpec.visual_critique.critique_summary}
                  </p>
                </div>
                {(designSpec.visual_critique.symmetry_assessment || designSpec.visual_critique.stance_and_mounting_assessment) && (
                  <div className="visual-critique-assessments">
                    <div className="visual-critique-section-label">
                      Geometry and stance
                    </div>
                    <ul>
                      {designSpec.visual_critique.symmetry_assessment && (
                        <li><strong>Symmetry:</strong> {designSpec.visual_critique.symmetry_assessment}</li>
                      )}
                      {designSpec.visual_critique.stance_and_mounting_assessment && (
                        <li><strong>Stance and mounting:</strong> {designSpec.visual_critique.stance_and_mounting_assessment}</li>
                      )}
                    </ul>
                  </div>
                )}
                {designSpec.visual_critique.component_adjustments?.length > 0 && (
                  <div>
                    <div className="visual-critique-section-label">
                      Recommended changes
                    </div>
                    <div className="visual-critique-adjustments">
                      {designSpec.visual_critique.component_adjustments.map((adj, i) => (
                        <div key={i} className="visual-critique-adjustment">
                          <strong>{adj.component_id}</strong>
                          <p>{adj.issue}</p>
                          <div><b>Suggested change:</b> {adj.suggested_action}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
      {selectedPart && <section className="component-ai-panel"><div className="component-section-title"><strong>Selected component</strong><span>{selectedPart.name}</span></div>{selectedModelComponent ? <><p className="muted">{selectedModelComponent.role} · {selectedModelComponent.geometry} · {selectedModelComponent.dimensions_m.map((value) => `${(value * 1000).toFixed(0)} mm`).join(" × ")}</p>{(selectedModelComponent.functional_category || Number.isFinite(selectedModelComponent.confidence) || selectedModelComponent.visible_vs_inferred || selectedModelComponent.mount_intent || selectedModelComponent.parent_reason) && <div className="component-ai-answer"><strong>Component reasoning</strong><ul>{selectedModelComponent.functional_category && <li>Function: {selectedModelComponent.functional_category}</li>}{selectedModelComponent.visible_vs_inferred && <li>Evidence: {String(selectedModelComponent.visible_vs_inferred).replace(/_/g, " ")}{Number.isFinite(selectedModelComponent.confidence) ? ` · ${(selectedModelComponent.confidence * 100).toFixed(0)}% confidence` : ""}</li>}{selectedModelComponent.mount_intent && <li>Mount intent: {selectedModelComponent.mount_intent}</li>}{selectedModelComponent.parent_reason && <li>Attached here because: {selectedModelComponent.parent_reason}</li>}{selectedModelComponent.symmetry_group && <li>Mirrored pair: {selectedModelComponent.symmetry_group}</li>}{selectedModelComponent.sub_assembly && <li>Sub-assembly: {selectedModelComponent.sub_assembly}</li>}</ul></div>}<label>Ask about this component<textarea rows="2" maxLength="1000" placeholder="What is this part for? How does it connect to the robot?" value={componentQuestion} onChange={(event) => setComponentQuestion(event.target.value)} /></label><div className="component-ai-actions"><button className="component-ai-action primary" type="button" disabled={busy} onClick={() => askAboutSelected().catch(() => {})}>{busy ? "Asking Meta…" : "Ask AI about this part"}</button></div>{componentAnswer && <div className="component-ai-answer"><strong>{componentAnswer.what_it_is}</strong><p>{componentAnswer.intended_role}</p><strong>In the design model</strong><ul>{componentAnswer.known_from_design.map((item, index) => <li key={index}>{item}</li>)}</ul><strong>Unknown / unverified</strong><ul>{componentAnswer.unknown_or_unverified.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}<label>How should this part improve?<textarea rows="2" maxLength="1200" placeholder="Example: widen the deck to carry a larger sensor" value={editInstruction} onChange={(event) => setEditInstruction(event.target.value)} /></label><div className="component-ai-actions"><button className="component-ai-action primary" type="button" disabled={busy || !editInstruction.trim()} title={!editInstruction.trim() ? "Describe the change in the field above to enable this action." : "Ask Meta to propose a bounded edit."} onClick={() => proposeSelectedEdit().catch(() => {})}>{busy ? "Preparing edit…" : !editInstruction.trim() ? "Describe a change above to enable" : "Propose an edit for this part"}</button>{kernelValidation?.intersections?.some((item) => item.component_ids?.includes(selectedModelComponent.id)) && <button className="component-ai-action secondary" type="button" disabled={busy} onClick={() => proposeKernelRepair().catch(() => {})}>{busy ? "Reviewing kernel finding…" : "Propose repair for kernel finding"}</button>}</div>{editProposal && <div className="cad-sketch-proposal"><strong>Proposal · human review needed</strong><p>{editProposal.rationale}</p><small>{editProposal.review_notes}</small><div className="cad-sketch-dimensions">{(selectedModelComponent.geometry === "cylinder" ? [{ label: "Diameter", index: 0 }, { label: "Length", index: 2 }] : selectedModelComponent.geometry === "sphere" ? [{ label: "Diameter", index: 0 }] : [{ label: "Width", index: 0 }, { label: "Depth", index: 1 }, { label: "Height", index: 2 }]).map(({ label, index }) => <label key={label}>{label} (mm)<input type="number" min="5" max="5000" step="0.1" value={proposalDimensions[index]} onChange={(event) => setProposalDimensions((current) => current.map((value, i) => (i === index || (selectedModelComponent.geometry === "cylinder" && index === 0 && i === 1)) ? event.target.value : value))} /></label>)}</div><button className="artifact-download" type="button" disabled={busy} onClick={applySelectedEdit}>Accept edit & rebuild CAD</button></div>}<small className="muted">The model reads the saved robot wireframe and latest local kernel findings when available. Its proposal is not verified until rebuilt and checked again; it cannot certify fit, strength, or safety.</small></> : <p className="muted">This is an imported mesh without a matching component in the saved robot design, so AI inspection and sketch editing are unavailable.</p>}</section>}
      {designSpec && onProposeDesignRevision && <section className="component-ai-panel"><div className="component-section-title"><strong>Design revision</strong><span>kernel + assembly findings</span></div><p className="muted">Ask Meta for one bounded revision (at most four part edits) against the current findings. Nothing changes until you approve and rebuild.</p><div className="component-ai-actions"><button className="component-ai-action primary" type="button" disabled={busy} onClick={() => proposeDesignRevision().catch(() => {})}>{busy ? "Reviewing findings…" : "Propose revision from findings"}</button></div>{revisionProposal && <div className="cad-sketch-proposal"><strong>Revision · human review needed</strong><p>{revisionProposal.rationale}</p><ul>{revisionProposal.edits.map((edit) => <li key={edit.component_id}><small><strong>{edit.component_id}</strong>{edit.dimensions_m && <> · dims {(edit.dimensions_m.map((value) => `${(value * 1000).toFixed(0)}`).join(" × "))} mm</>}{edit.position_m && <> · at [{edit.position_m.map((value) => `${(value * 1000).toFixed(0)}`).join(", ")}] mm</>}{edit.reason && <> — {edit.reason}</>}</small></li>)}</ul><button className="artifact-download" type="button" disabled={busy || !onApplyDesignRevision} onClick={() => applyDesignRevision().catch(() => {})}>Accept revision & rebuild CAD</button> <button className="secondary-button" type="button" onClick={() => setRevisionProposal(null)}>Dismiss</button></div>}{revisionNotice && <p className="component-import-message">{revisionNotice}</p>}</section>}
      {(overlaps.length > 0 || assemblyFindings.length > 0) && <div className="component-findings"><strong>Browser assembly findings · approximate</strong>{[...assemblyFindings, ...overlaps].map((message) => <p key={message}>{message}</p>)}</div>}{kernelValidation && <div className="component-findings"><strong>FreeCAD solid-kernel check · {kernelValidation.status?.replace(/_/g, " ")}</strong><p>{kernelValidation.component_validity?.filter((item) => item.valid).length || 0}/{kernelValidation.component_validity?.length || 0} components have valid solids · {kernelValidation.intersections?.length || 0} intersecting or failed pair checks</p>{kernelValidation.intersections?.slice(0, 12).map((item, index) => <p key={`${item.component_ids?.join("-")}-${index}`}>{item.component_ids?.join(" ↔ ")} · {item.status === "check_failed" ? item.message : `${Number(item.intersection_volume_mm3).toFixed(2)} mm³ overlap; review mount intent`}</p>)}{kernelValidation.intersections?.length > 12 && <small>{kernelValidation.intersections.length - 12} more pairs are recorded in the export report.</small>}</div>}<p className="panel-footnote">FreeCAD exact solid checks detect geometric validity and static intersections. Approximate mesh bounds remain visual warnings. Neither check covers moving collisions, mount tolerances, fasteners, strength, or manufacturability.</p></div></div>
  </div>;
}
