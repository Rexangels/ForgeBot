import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { ArtifactViewer, StateChip } from "./components.jsx";
import RoverView from "./RoverView.jsx";
import ArmModelViewer from "./ArmModelViewer.jsx";
import UrdfModelViewer from "./UrdfModelViewer.jsx";
import ComponentAssemblyEditor from "./ComponentAssemblyEditor.jsx";
import { imagePointToWorkcell, solvePlanarHomography } from "./cameraCalibration.js";

const TERMINAL = ["succeeded", "failed", "cancelled"];

function parseArtifactDocument(artifact) {
  if (!artifact?.body) return null;
  try {
    return JSON.parse(artifact.body);
  } catch {
    return null;
  }
}

function usePolling(jobId, onEvents) {
  const afterRef = useRef(0);
  useEffect(() => {
    if (!jobId) return;
    afterRef.current = 0;
    let alive = true;
    const tick = async () => {
      try {
        const data = await api.events(jobId, afterRef.current);
        if (!alive) return;
        if (data.events.length > 0) {
          afterRef.current = data.events[data.events.length - 1].id;
        }
        onEvents(data);
        if (!TERMINAL.includes(data.state)) {
          setTimeout(tick, 2000);
        }
      } catch {
        if (alive) setTimeout(tick, 5000);
      }
    };
    tick();
    return () => {
      alive = false;
    };
  }, [jobId]);
}

function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    setError("");
    try {
      const data = await api.login(username, password);
      onLogin(data.username);
    } catch (err) {
      setError(err.message);
    }
  };
  return (
    <section className="card narrow">
      <h2>Sign in</h2>
      <p className="muted">Team demo access. Ask the project owner for an account.</p>
      <form onSubmit={submit}>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit">Sign in</button>
      </form>
    </section>
  );
}

function ProjectsScreen({ onOpen }) {
  const [projects, setProjects] = useState(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const data = await api.projects();
      setProjects(data.projects);
    } catch (err) {
      setError(err.message);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const create = async (e) => {
    e.preventDefault();
    setError("");
    try {
      await api.createProject(name);
      setName("");
      load();
    } catch (err) {
      setError(err.message);
    }
  };
  return (
    <section>
      <h2>Projects</h2>
      {error && (
        <p className="error">
          {error} <button onClick={load}>Retry</button>
        </p>
      )}
      {projects === null && !error && <p className="muted">Loading projects…</p>}
      {projects !== null && projects.length === 0 && (
        <p className="muted">No projects yet. Create one to run your first rover pipeline.</p>
      )}
      <ul className="rows">
        {(projects || []).map((p) => (
          <li key={p.id} className="row">
            <div>
              <strong>#{p.id} — {p.name}</strong>
            </div>
            <button onClick={() => onOpen(p)}>Open jobs</button>
          </li>
        ))}
      </ul>
      <form onSubmit={create} className="inline-form">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New project name" aria-label="New project name" />
        <button type="submit">Create project</button>
      </form>
    </section>
  );
}

const WORK_AREA_CORNERS = [[-0.1, -0.3], [0.7, -0.3], [0.7, 0.3], [-0.1, 0.3]];

function VisionPanel({ projectId, onOpenJob, onUseTarget }) {
  const videoRef = useRef(null);
  const imageRef = useRef(null);
  const streamRef = useRef(null);
  const [image, setImage] = useState(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [observation, setObservation] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [cameraActive, setCameraActive] = useState(false);
  const [calibrating, setCalibrating] = useState(false);
  const [calibrationPoints, setCalibrationPoints] = useState([]);
  const [calibration, setCalibration] = useState(null);
  const [selectedObject, setSelectedObject] = useState(0);
  const [targetEdit, setTargetEdit] = useState({ x: 0.3, y: 0, z: 0.04 });
  const [calibrationAccuracy, setCalibrationAccuracy] = useState(null);
  const [trackedObjects, setTrackedObjects] = useState([]);
  const [measuredDepth, setMeasuredDepth] = useState("");

  useEffect(() => {
    let cancelled = false;
    api.latestCalibration(projectId)
      .then((saved) => {
        if (cancelled) return;
        setCalibration(saved.homography);
        setCalibrationPoints(saved.image_points || []);
        setCalibrationAccuracy(saved.accuracy_m || null);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [projectId]);
  const [designBrief, setDesignBrief] = useState("Create an original compact robot inspired by this image. Keep the visible silhouette and component arrangement, but make the design suitable for a simple research prototype.");
  const [designHeight, setDesignHeight] = useState(0.45);
  const [designConstraints, setDesignConstraints] = useState("");
  const [designBusy, setDesignBusy] = useState(false);
  const [designViews, setDesignViews] = useState([]);
  const [designLocalOnly, setDesignLocalOnly] = useState(false);

  const addDesignView = (file) => {
    if (!file || designViews.length >= 3) return;
    const defaults = ["front", "side", "top"];
    setDesignViews((previous) => [...previous, { file, url: URL.createObjectURL(file), view: defaults[previous.length] || "other" }]);
    setError("");
  };
  const removeDesignView = (index) => {
    setDesignViews((previous) => {
      const removed = previous[index];
      if (removed) URL.revokeObjectURL(removed.url);
      return previous.filter((_, i) => i !== index);
    });
  };
  const setDesignViewLabel = (index, view) => {
    setDesignViews((previous) => previous.map((item, i) => i === index ? { ...item, view } : item));
  };

  const selectReferenceImage = (file) => {
    if (!file) return;
    setImage(file);
    setObservation(null);
    setError("");
    setCalibration(null);
    setCalibrationAccuracy(null);
    setCalibrationPoints([]);
    setTrackedObjects([]);
    onUseTarget(null);
  };

  useEffect(() => {
    if (!image) { setPreviewUrl(""); return undefined; }
    const url = URL.createObjectURL(image);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

  useEffect(() => () => streamRef.current?.getTracks().forEach((track) => track.stop()), []);

  useEffect(() => {
    if (cameraActive && videoRef.current && streamRef.current) videoRef.current.srcObject = streamRef.current;
  }, [cameraActive]);

  const mappedObject = useMemo(() => {
    const object = observation?.objects?.[selectedObject];
    if (!object || !calibration) return null;
    try {
      const region = object.image_region;
      // The lower-center of the visible box is a better tabletop contact estimate than its visual center.
      return imagePointToWorkcell([(region.x_min + region.x_max) / 2, region.y_max], calibration);
    } catch {
      return null;
    }
  }, [observation, selectedObject, calibration]);

  useEffect(() => {
    if (mappedObject) setTargetEdit({ x: Number(mappedObject.x.toFixed(3)), y: Number(mappedObject.y.toFixed(3)), z: 0.04 });
  }, [mappedObject?.x, mappedObject?.y]);

  const selectedTargetUncertaintyM = () => {
    const object = observation?.objects?.[selectedObject];
    if (!object || !calibration) return 0.04;
    try {
      const region = object.image_region;
      const left = imagePointToWorkcell([region.x_min, region.y_max], calibration);
      const right = imagePointToWorkcell([region.x_max, region.y_max], calibration);
      const footprintSigma = Math.hypot(right.x - left.x, right.y - left.y) / 2;
      const calibrationSigma = calibrationAccuracy?.max_m ?? 0.02;
      const depthSigma = measuredDepth ? 0.005 : 0.03;
      return Number(Math.hypot(calibrationSigma, footprintSigma, depthSigma).toFixed(5));
    } catch {
      return 0.04;
    }
  };

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setCameraActive(false);
  };

  const startCamera = async () => {
    setError("");
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera access is unavailable. Use a secure localhost connection or choose an image file.");
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
      streamRef.current = stream;
      onUseTarget(null);
      setCameraActive(true);
      if (videoRef.current) videoRef.current.srcObject = stream;
    } catch (err) {
      setError(err.message || "Could not open the camera.");
    }
  };

  const capture = () => {
    const video = videoRef.current;
    if (!video?.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob((blob) => {
      if (blob) {
        selectReferenceImage(new File([blob], `workcell-${Date.now()}.jpg`, { type: "image/jpeg" }));
        setObservation(null);
        stopCamera();
      }
    }, "image/jpeg", 0.88);
  };

  const analyze = async () => {
    if (!image) return;
    setBusy(true);
    setError("");
    setObservation(null);
    onUseTarget(null);
    try {
      const response = await api.analyzeScene(image);
      setObservation(response.observation);
      const objects = response.observation?.objects || [];
      try {
        const matched = await api.trackDetections(trackedObjects, objects);
        setTrackedObjects(matched.tracks || objects);
      } catch {
        setTrackedObjects(objects);
      }
      setCalibration(null);
      setCalibrationAccuracy(null);
      setCalibrationPoints([]);
      setSelectedObject(0);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const createImageDesign = async () => {
    if (!designViews.length || !designBrief.trim()) return;
    setDesignBusy(true);
    setError("");
    try {
      const created = await api.createImageDesign(projectId, designViews, { brief: designBrief, targetHeightM: designHeight, constraints: designConstraints, localOnly: designLocalOnly });
      onOpenJob(created.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setDesignBusy(false);
    }
  };

  const captureCalibrationPoint = (event) => {
    if (!calibrating || calibrationPoints.length >= 4 || !imageRef.current) return;
    const rect = imageRef.current.getBoundingClientRect();
    const point = [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
    if (point.some((value) => value < 0 || value > 1)) return;
    setCalibrationPoints((previous) => [...previous, point]);
  };

  const finishCalibration = async () => {
    try {
      const homography = solvePlanarHomography(calibrationPoints, WORK_AREA_CORNERS);
      setCalibration(homography);
      setCalibrating(false);
      setError("");
      try {
        const saved = await api.saveCalibration(projectId, { label: "workcell", image_points: calibrationPoints, world_points: WORK_AREA_CORNERS });
        setCalibrationAccuracy(saved.accuracy_m || null);
      } catch (saveErr) {
        setCalibrationAccuracy(null);
        setError(`Calibration applied locally but not saved: ${saveErr.message}`);
      }
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <section className="vision-panel studio-panel">
      <div className="section-heading"><div><p className="eyebrow">MODEL VISION · HUMAN REVIEW</p><h3>Look at the work area</h3><p className="muted">Capture a still image or choose a photo. Meta describes the scene and visible objects.</p></div><span className="vision-provider">Meta · {"muse-spark-1.3"}</span></div>
      <div className="vision-grid">
        <div className="vision-capture">
          {cameraActive ? <video ref={videoRef} autoPlay playsInline muted className="vision-preview" /> : previewUrl ? <div className={`scene-image-frame ${calibrating ? "calibration-mode" : ""}`}><img ref={imageRef} src={previewUrl} alt="Selected workcell image" onClick={captureCalibrationPoint} />{observation?.objects?.map((object, index) => <span key={`${object.label}-${index}`} className={`vision-box ${index === selectedObject ? "selected" : ""}`} style={{ left: `${object.image_region.x_min * 100}%`, top: `${object.image_region.y_min * 100}%`, width: `${(object.image_region.x_max - object.image_region.x_min) * 100}%`, height: `${(object.image_region.y_max - object.image_region.y_min) * 100}%` }}><i>{object.label}</i></span>)}{calibrationPoints.map((point, index) => <span className="calibration-pin" key={`cal-${index}`} style={{ left: `${point[0] * 100}%`, top: `${point[1] * 100}%` }}>{index + 1}</span>)}{calibrating && <span className="calibration-hint">Click corner {calibrationPoints.length + 1} of 4</span>}</div> : <div className="vision-empty"><span>◉</span><strong>No image captured</strong><small>Use the camera or select a workcell photo.</small></div>}
          <div className="vision-actions">
            {cameraActive ? <><button className="primary-action" onClick={capture}>Capture frame</button><button className="secondary-action" onClick={stopCamera}>Close camera</button></> : <><button className="secondary-action" onClick={startCamera}>Open camera</button><label className="secondary-action vision-file">Choose image<input type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => { selectReferenceImage(event.target.files?.[0]); event.target.value = ""; }} /></label></>}
            <button className="primary-action" disabled={!image || busy || cameraActive} onClick={analyze}>{busy ? "Analyzing image…" : "Ask Meta to inspect →"}</button>
          </div>
          <div className="image-design-card"><div><span className="eyebrow">IMAGE-TO-ROBOT CONCEPT</span><strong>Design a robot from this reference</strong><small>Meta proposes the visible shape and part layout. ForgeBot builds separate editable part modules, an assembled CAD source, URDF, and a 3D concept for review.</small></div>
            <div className="design-reference-picker"><label htmlFor="robot-reference-image">Robot reference views · up to 3</label><label className="design-reference-button" htmlFor="robot-reference-image">{designViews.length >= 3 ? "Three views selected" : "Add a front, side, or top view"}<input id="robot-reference-image" type="file" accept="image/jpeg,image/png,image/webp" disabled={designViews.length >= 3} onChange={(event) => { addDesignView(event.target.files?.[0]); event.target.value = ""; }} /></label>{designViews.length ? <div className="design-reference-views">{designViews.map((item, index) => <div className="design-reference-selected" key={`${item.file.name}-${index}`}><img src={item.url} alt={`Reference view ${index + 1}`} /><span><strong>{item.file.name}</strong><label>Viewpoint<select value={item.view} onChange={(event) => setDesignViewLabel(index, event.target.value)}><option value="front">Front</option><option value="side">Side</option><option value="top">Top</option><option value="other">Other</option></select></label><button className="secondary-action" type="button" onClick={() => removeDesignView(index)}>Remove</button></span></div>)}</div> : <small className="image-design-note">Add one photo, or three labeled viewpoints so widths, depths, and footprints stop being guesses. The workcell image above stays available to AI eyes separately.</small>}</div>
            <label>What should the robot do?<textarea rows={3} maxLength={2000} value={designBrief} onChange={(event) => setDesignBrief(event.target.value)} /></label>
            <div className="target-edit-grid"><label>Approximate robot height (m)<input type="number" min="0.05" max="3" step="0.01" value={designHeight} onChange={(event) => setDesignHeight(Number(event.target.value))} /></label></div>
            <label>Constraints (optional)<input maxLength={2000} value={designConstraints} onChange={(event) => setDesignConstraints(event.target.value)} placeholder="Budget, workspace, wheels or legs, payload…" /></label>
            <label className="image-design-note" style={{ display: "flex", gap: "6px", alignItems: "flex-start" }}><input type="checkbox" checked={designLocalOnly} onChange={(event) => setDesignLocalOnly(event.target.checked)} /> Keep reference pixels on this machine (text-only brief, less accurate proportions)</label>
            <button className="primary-action" disabled={!designViews.length || !designBrief.trim() || designBusy || cameraActive} onClick={createImageDesign}>{designBusy ? (designLocalOnly ? "Designing from text brief…" : "Sending references to Meta…") : "Generate robot concept →"}</button>
            <small className="image-design-note">{designLocalOnly ? "Local-only: reference pixels stay on this machine; the concept is designed from your brief plus local view facts. Expect rougher proportions." : "Reference views are sent to Meta only when you generate."} The model does not recover true dimensions or hidden mechanisms. No reference image is retained by ForgeBot.</small>
          </div>
          {error && <p className="error">{error}</p>}
          <p className="vision-privacy">Images are sent to Meta only when you select <strong>Ask Meta</strong>. ForgeBot does not store the image or analysis. Use a non-sensitive workcell image.</p>
        </div>
        <div className="vision-results">
          {!observation ? <div className="vision-empty result-empty"><strong>Scene understanding will appear here</strong><small>Includes object appearance, condition, image regions, relationships, and uncertainty.</small></div> : <>
            <div className="scene-summary"><span className="eyebrow">SCENE SUMMARY</span><p>{observation.scene_summary}</p><small>{observation.work_surface?.description || "Work surface not confidently identified"}</small></div>
            <div className="object-list"><div className="object-list-heading"><strong>Visible objects</strong><span>{observation.objects.length}</span></div>{observation.objects.map((object, index) => { const track = trackedObjects[index]; const sameTrack = track && track.label === object.label ? track : null; return <article className="vision-object" key={`${object.label}-${index}`}><div><strong>{object.label}{sameTrack?.track_id ? ` #${sameTrack.track_id}` : ""}</strong><span className={`confidence-tag ${object.graspability}`}>{Math.round(object.confidence * 100)}% · {object.graspability.replace(/_/g, " ")}{sameTrack?.frames_seen > 1 ? ` · seen ${sameTrack.frames_seen} frames` : ""}</span></div><p>{object.appearance} {object.state}</p>{object.uncertainty && <small>Uncertainty: {object.uncertainty}</small>}</article>; })}</div>
            <div className="calibration-card"><div><strong>Map the image to the arm workspace</strong><small>Click the four table corners in this order: X−/Y−, X+/Y−, X+/Y+, X−/Y+. Bounds are −0.1..0.7 m by −0.3..0.3 m in the arm-base frame. This assumes the arm base is at (0,0), 0.1 m from the table’s X− edge and centered across its width.</small></div><div className="calibration-actions"><button className="secondary-action" onClick={() => { setCalibration(null); setCalibrationPoints([]); setCalibrating(true); setError(""); }}>Calibrate from table corners</button>{calibrating && <><span>{calibrationPoints.length}/4 corners</span><button className="secondary-action" disabled={calibrationPoints.length !== 4} onClick={finishCalibration}>Apply calibration</button><button className="link" onClick={() => { setCalibrating(false); setCalibrationPoints([]); }}>Cancel</button></>}</div>{calibration && <p className="calibration-ok">Planar calibration ready · positions are estimates at the table surface.{calibrationAccuracy ? ` Measured click accuracy: mean ${(calibrationAccuracy.mean_m * 1000).toFixed(1)} mm, worst ${(calibrationAccuracy.max_m * 1000).toFixed(1)} mm across held-out corners.` : " Accuracy not measured for this session."}</p>}</div>
            {observation.objects.length > 0 && <div className="object-target"><label>Object to target<select value={selectedObject} onChange={(event) => setSelectedObject(Number(event.target.value))}>{observation.objects.map((object, index) => <option key={`${object.label}-${index}`} value={index}>{object.label}</option>)}</select></label>{mappedObject ? <><p>Estimated from box lower-center: X {mappedObject.x.toFixed(3)} m · Y {mappedObject.y.toFixed(3)} m</p><div className="target-edit-grid">{["x", "y", "z"].map((axis) => <label key={axis}>{axis.toUpperCase()} m<input type="number" step="0.005" value={targetEdit[axis]} onChange={(event) => setTargetEdit((old) => ({ ...old, [axis]: Number(event.target.value) }))} /></label>)}</div><label>Measured depth m (optional)<input type="number" min="0.005" max="1" step="0.005" placeholder="0.04" value={measuredDepth} onChange={(event) => { setMeasuredDepth(event.target.value); const depth = Number(event.target.value); if (Number.isFinite(depth) && depth > 0) setTargetEdit((old) => ({ ...old, z: depth })); }} /></label><button className="secondary-action" onClick={() => onUseTarget({ ...targetEdit, source: measuredDepth ? "Measured depth (user-supplied) · manually review before motion" : "Meta vision estimate on assumed 40 mm plane · manually review before motion", depth_source: measuredDepth ? "measured" : "assumed_plane_40mm", uncertainty_m: selectedTargetUncertaintyM(), label: observation.objects[selectedObject].label })}>Use reviewed estimate as pick target</button><small>Metric location is inferred through your four-corner calibration{calibrationAccuracy ? ` (worst held-out corner ${(calibrationAccuracy.max_m * 1000).toFixed(1)} mm)` : ""}. Depth defaults to the 40 mm plane unless you measured it. Review and adjust the coordinates before using it.</small></> : <small>Calibrate the tabletop before converting this image observation into a robot-frame target.</small>}</div>}
            {observation.relationships.length > 0 && <div className="scene-summary"><span className="eyebrow">SPATIAL RELATIONSHIPS</span><ul>{observation.relationships.map((relation) => <li key={relation}>{relation}</li>)}</ul></div>}
            {observation.safety_notes.length > 0 && <div className="vision-caution"><strong>Visible cautions</strong><ul>{observation.safety_notes.map((note) => <li key={note}>{note}</li>)}</ul></div>}
          </>}
        </div>
      </div>
      {observation && <p className="vision-limit">Meta regions are image-relative. Four-corner calibration maps a selected box's lower-center to an approximate tabletop target; review and edit it. Depth, object weight, grasp success, and safe motion are not inferred. Suggested task: {observation.suggested_task}</p>}
    </section>
  );
}

function ProjectScreen({ project, onBack, onOpenJob, onUseVisionTarget }) {
  const [jobs, setJobs] = useState(null);
  const [task, setTask] = useState("Pick up small objects and sort them into bins on a tabletop");
  const [units, setUnits] = useState("si");
  const [environment, setEnvironment] = useState("indoor tabletop, 0.8 x 0.6 m work area");
  const [robotClass, setRobotClass] = useState("tabletop_robot_arm");
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const load = useCallback(async () => {
    try {
      const data = await api.jobs(project.id);
      setJobs(data.jobs);
    } catch (err) {
      setError(err.message);
    }
  }, [project.id]);
  useEffect(() => {
    load();
  }, [load]);
  const run = async (e) => {
    e.preventDefault();
    setError("");
    setRunning(true);
    try {
      const data = await api.createDemoJob(project.id, robotClass, { task, units, environment });
      onOpenJob(data.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  };
  const pipelineLabels = robotClass === "research_humanoid"
    ? ["Send mission to Meta", "Review humanoid concept", "Generate 71-DOF robot description", "Validate topology & limits", "Inspect 3D model"]
    : ["Send mission to Meta", "Review generated arm design", "Generate URDF & controls", "Validate design package", "3D pose editor"];
  return (
    <section className="workbench">
      <div className="page-heading">
        <button className="link" onClick={onBack}>← All projects</button>
        <div>
          <p className="eyebrow">ROBOTICS WORKSPACE · PROJECT #{project.id}</p>
          <h2>{project.name}</h2>
          <p className="muted">Shape the mission, inspect the robot, then build and review its software.</p>
        </div>
          <span className="robot-class">{robotClass === "tabletop_robot_arm" ? "AI design · tabletop arm" : robotClass === "research_humanoid" ? "AI design · humanoid concept" : "Prototype · rover"}</span>
      </div>

      <div className="studio-grid project-studio">
        <aside className="studio-panel mission-panel">
          <div className="panel-heading"><span className="step-number">01</span><div><h3>Mission brief</h3><p className="muted">Describe what the robot should do.</p></div></div>
          <form onSubmit={run}>
            <label>Robot platform<select value={robotClass} onChange={(e) => {
              const nextClass = e.target.value;
              setRobotClass(nextClass);
              if (task === "Pick up small objects and sort them into bins on a tabletop" && nextClass === "research_humanoid") {
                setTask("Design a compact research humanoid for indoor object pickup and placement, with stable standing, two arms, dexterous hands, and onboard vision.");
                setEnvironment("indoor research lab with a level floor and a clear 3 x 3 m work area");
              }
            }}><option value="tabletop_robot_arm">Tabletop robot arm · Meta generated</option><option value="research_humanoid">Research humanoid · Meta generated</option><option value="differential-drive-rover">Differential-drive rover · demo</option></select></label>
            <label>Mission task<textarea value={task} onChange={(e) => setTask(e.target.value)} rows={4} /></label>
            <label>Operating environment<input value={environment} onChange={(e) => setEnvironment(e.target.value)} /></label>
            <label>Units<select value={units} onChange={(e) => setUnits(e.target.value)}><option value="si">SI units</option><option value="metric">Metric</option><option value="imperial">Imperial</option></select></label>
            {error && <p className="error">{error}</p>}
            <button className="primary-action" type="submit" disabled={running}>{running ? "Preparing workspace…" : "Start design run"}<span aria-hidden="true">→</span></button>
          </form>
          <p className="panel-footnote">{robotClass === "research_humanoid" ? "Meta proposes an original humanoid concept. ForgeBot builds and validates a kinematic URDF; physics, balance, and manufacturing design remain future milestones." : "Arm proposals come from Meta Model API, then pass local checks. They are conceptual designs, not manufacturing or safety approvals."}</p>
        </aside>

        <section className="studio-panel canvas-panel">
          <div className="panel-heading canvas-heading"><div><p className="eyebrow">DESIGN CANVAS</p><h3>{robotClass === "tabletop_robot_arm" ? "Tabletop arm assembly" : robotClass === "research_humanoid" ? "Humanoid kinematic concept" : "Rover route concept"}</h3></div><span className="canvas-mode">{robotClass === "differential-drive-rover" ? "2D PREVIEW" : "3D MODEL"}</span></div>
          <div className="canvas-stage">{robotClass === "tabletop_robot_arm" ? <ArmModelViewer /> : robotClass === "research_humanoid" ? <UrdfModelViewer /> : <RoverView />}</div>
          <div className="canvas-caption"><span className="legend-dot" /> {robotClass === "tabletop_robot_arm" ? "Arm design preview" : robotClass === "research_humanoid" ? "New articulated robot platform" : "Rover route preview"} <span>·</span> <span className="muted">{robotClass === "tabletop_robot_arm" ? "URDF assembly and target planning open in a design run" : robotClass === "research_humanoid" ? "Generate a humanoid proposal to create its robot model" : "Preview only, no physics simulation"}</span></div>
          <div className="canvas-specs"><div><span>Platform</span><strong>{robotClass === "tabletop_robot_arm" ? "4-axis tabletop arm" : robotClass === "research_humanoid" ? "31-DOF body concept" : "Differential drive"}</strong></div><div><span>Editor</span><strong>{robotClass === "tabletop_robot_arm" ? "Orbit + joint controls" : robotClass === "research_humanoid" ? "URDF orbit viewer" : "Route preview"}</strong></div><div><span>Simulation</span><strong>{robotClass === "research_humanoid" ? "Physics milestone next" : robotClass === "tabletop_robot_arm" ? "Kinematic preview" : "Route estimate"}</strong></div></div>
        </section>

        <aside className="studio-panel agent-panel">
          <div className="panel-heading"><span className="agent-orb">✦</span><div><p className="eyebrow">FORGEBOT</p><h3>Build workflow</h3></div></div>
          <p className="agent-note">{robotClass === "research_humanoid" ? "Meta proposes an original humanoid body and sensor layout. ForgeBot generates a kinematic URDF, conceptual parts list, and control interface for review." : "Meta drafts the arm specification and parts list. ForgeBot validates the proposal, then generates a URDF and starter controls for review."}</p>
          <ol className="pipeline-list">
            {pipelineLabels.map((label, i) => <li key={label}><span className="pipeline-dot">{String(i + 1).padStart(2, "0")}</span><span>{label}</span><span className="pipeline-pending">WAITING</span></li>)}
          </ol>
          <div className="agent-status"><span className="status-light" /> Ready when you are</div>
        </aside>
      </div>

      <VisionPanel projectId={project.id} onOpenJob={onOpenJob} onUseTarget={onUseVisionTarget} />

      <section className="runs-section">
        <div className="section-heading"><div><p className="eyebrow">PROJECT HISTORY</p><h3>Recent design runs</h3></div><span className="muted">{jobs?.length || 0} runs</span></div>
        {jobs === null && <p className="muted">Loading runs…</p>}
        {jobs !== null && jobs.length === 0 && <p className="empty-state">No design runs yet. Start with the mission brief above.</p>}
        <ul className="rows run-rows">{(jobs || []).map((j) => <li key={j.id} className="row"><div className="run-title"><span className="run-icon">◈</span><div><strong>Design run #{j.id}</strong><small>Mission → design → code → validation</small></div></div><StateChip state={j.state} /><button className="secondary-action" onClick={() => onOpenJob(j.id)}>Open workspace <span aria-hidden="true">→</span></button></li>)}</ul>
      </section>
    </section>
  );
}

function JobScreen({ jobId, onBack, visionPickTarget }) {
  const [job, setJob] = useState(null);
  const [events, setEvents] = useState([]);
  const [selectedPanel, setSelectedPanel] = useState("canvas");
  const [bodies, setBodies] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [trainArmController, setTrainArmController] = useState(false);
  const [seedFromVision, setSeedFromVision] = useState(false);
  const [simulationPick, setSimulationPick] = useState([0.28, 0, 0.055]);
  const [simulationPlace, setSimulationPlace] = useState([0.2, 0.18, 0.055]);
  const [armBenchmark, setArmBenchmark] = useState(null);
  const [armRepairProposal, setArmRepairProposal] = useState(null);
  const refresh = useCallback(async () => {
    const data = await api.job(jobId);
    setJob(data);
    return data;
  }, [jobId]);
  usePolling(
    jobId,
    useCallback(
      (data) => {
        setEvents((prev) => {
          const known = new Set(prev.map((e) => e.id));
          return [...prev, ...data.events.filter((e) => !known.has(e.id))];
        });
        refresh().catch(() => {});
      },
      [refresh]
    )
  );
  useEffect(() => {
    refresh().catch((err) => setError(err.message));
  }, [refresh]);
  const openPanel = async (panel) => {
    setSelectedPanel(panel);
    const kind = ({ design: "spec", urdf: "urdf", parts: "bom", cad: "cad", code: "code", validation: "report", simulation: "sim" })[panel];
    const artifact = job?.artifacts.find((item) => item.kind === kind);
    if (artifact && !bodies[artifact.id]) {
      try {
        const full = await api.artifact(artifact.id);
        setBodies((prev) => ({ ...prev, [artifact.id]: full }));
      } catch (err) {
        setError(err.message);
      }
    }
  };
  const activeKind = ({ design: "spec", urdf: "urdf", parts: "bom", cad: "cad", code: "code", validation: "report", simulation: "sim" })[selectedPanel];
  const activeArtifact = job?.artifacts.find((item) => item.kind === activeKind);
  const activeBody = activeArtifact && bodies[activeArtifact.id];
  const cadExportArtifact = job?.artifacts.find((item) => item.kind === "cad_export");
  const cadExportBody = cadExportArtifact && bodies[cadExportArtifact.id];
  const cadExportDocument = cadExportBody ? { ...(parseArtifactDocument(cadExportBody) || {}), artifact_id: cadExportArtifact.id } : null;
  const designArtifact = job?.artifacts.find((item) => item.kind === "spec");
  const urdfArtifact = job?.artifacts.find((item) => item.kind === "urdf");
  const simulationArtifact = job?.artifacts.find((item) => item.kind === "sim");
  const simulationDoc = simulationArtifact ? parseArtifactDocument(bodies[simulationArtifact.id]) : null;
  const perceivedTarget = visionPickTarget ? {
    x: Number(visionPickTarget.x),
    y: Number(visionPickTarget.y),
    z: Number(visionPickTarget.z ?? 0.055),
    uncertainty_m: Number(visionPickTarget.uncertainty_m ?? 0.04),
    depth_source: visionPickTarget.depth_source || "unknown",
  } : null;
  const benchmarkArtifact = job?.artifacts.find((item) => item.kind === "sim_benchmark");
  const benchmarkDoc = benchmarkArtifact ? parseArtifactDocument(bodies[benchmarkArtifact.id]) : armBenchmark;
  const designSpec = designArtifact ? parseArtifactDocument(bodies[designArtifact.id]) : null;
  const urdfDocument = urdfArtifact ? bodies[urdfArtifact.id] : null;
  useEffect(() => {
    if (selectedPanel !== "simulation" || job?.robot_class !== "tabletop_robot_arm" || !designArtifact) return undefined;
    let active = true;
    api.armSimulationDefaults(jobId)
      .then((defaults) => {
        if (active) {
          setSimulationPick(defaults.pick_target_m);
          setSimulationPlace(defaults.place_target_m);
        }
      })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [jobId, selectedPanel, job?.robot_class, designArtifact?.id]);
  useEffect(() => {
    if (!simulationArtifact || bodies[simulationArtifact.id]) return;
    let active = true;
    api.artifact(simulationArtifact.id)
      .then((artifact) => {
        if (active) setBodies((prev) => ({ ...prev, [artifact.id]: artifact }));
      })
      .catch((err) => {
        if (active) setError(err.message);
      });
    return () => { active = false; };
  }, [simulationArtifact?.id, bodies]);
  useEffect(() => {
    if (!benchmarkArtifact || bodies[benchmarkArtifact.id]) return;
    let active = true;
    api.artifact(benchmarkArtifact.id)
      .then((artifact) => {
        if (active) setBodies((prev) => ({ ...prev, [artifact.id]: artifact }));
      })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [benchmarkArtifact?.id, bodies]);
  useEffect(() => {
    if (!designArtifact || bodies[designArtifact.id]) return;
    let active = true;
    api.artifact(designArtifact.id)
      .then((artifact) => {
        if (active) setBodies((prev) => ({ ...prev, [artifact.id]: artifact }));
      })
      .catch((err) => {
        if (active) setError(err.message);
      });
    return () => { active = false; };
  }, [designArtifact?.id, bodies]);
  useEffect(() => {
    if (!urdfArtifact || bodies[urdfArtifact.id]) return;
    let active = true;
    api.artifact(urdfArtifact.id)
      .then((artifact) => {
        if (active) setBodies((prev) => ({ ...prev, [artifact.id]: artifact }));
      })
      .catch((err) => {
        if (active) setError(err.message);
      });
    return () => { active = false; };
  }, [urdfArtifact?.id, bodies]);
  useEffect(() => {
    if (!cadExportArtifact || bodies[cadExportArtifact.id]) return;
    let active = true;
    api.artifact(cadExportArtifact.id)
      .then((artifact) => {
        if (active) setBodies((prev) => ({ ...prev, [artifact.id]: artifact }));
      })
      .catch((err) => {
        if (active) setError(err.message);
      });
    return () => { active = false; };
  }, [cadExportArtifact?.id, bodies]);
  const approve = async () => {
    setBusy(true);
    setError("");
    try {
      await api.approve(jobId);
      refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const rebuildCad = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await api.rebuildCad(jobId);
      const artifactIds = [result.artifact_id, result.freecad_artifact_id].filter(Boolean);
      const artifacts = await Promise.all(artifactIds.map((artifactId) => api.artifact(artifactId)));
      setBodies((previous) => ({ ...previous, ...Object.fromEntries(artifacts.map((artifact) => [artifact.id, artifact])) }));
      await refresh();
      setSelectedPanel("assembly");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const editCadSketches = async (components) => {
    setBusy(true);
    setError("");
    try {
      const result = await api.editCadSketches(jobId, components);
      const artifactIds = [result.spec_artifact_id, result.urdf_artifact_id, result.artifact_id, result.freecad_artifact_id, result.sim_artifact_id, result.code_artifact_id].filter(Boolean);
      const artifacts = await Promise.all(artifactIds.map((artifactId) => api.artifact(artifactId)));
      setBodies((previous) => ({ ...previous, ...Object.fromEntries(artifacts.map((artifact) => [artifact.id, artifact])) }));
      await refresh();
      if (selectedPanel !== "simulation" && selectedPanel !== "code") {
        setSelectedPanel("assembly");
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const runArmPhysics = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await api.simulateArm(jobId, { trainController: trainArmController, pickTargetM: simulationPick.map(Number), placeTargetM: simulationPlace.map(Number), perceivedTarget: seedFromVision ? perceivedTarget : undefined });
      const artifact = await api.artifact(result.artifact_id);
      setBodies((previous) => ({ ...previous, [artifact.id]: artifact }));
      await refresh();
      setSelectedPanel("simulation");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const benchmarkArmDesigns = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await api.benchmarkArmDesigns(jobId);
      const artifact = await api.artifact(result.artifact_id);
      setBodies((previous) => ({ ...previous, [artifact.id]: artifact }));
      setArmBenchmark(result.benchmark);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const requestArmRepair = async () => {
    setBusy(true);
    setError("");
    try {
      setArmRepairProposal(await api.proposeArmRepair(jobId));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const applyArmRepair = async () => {
    if (!armRepairProposal) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.applyArmRepair(jobId, armRepairProposal);
      const ids = [result.spec_artifact_id, result.urdf_artifact_id, result.code_artifact_id, result.report_artifact_id, result.simulation_artifact_id];
      const artifacts = await Promise.all(ids.map((id) => api.artifact(id)));
      setBodies((previous) => ({ ...previous, ...Object.fromEntries(artifacts.map((artifact) => [artifact.id, artifact])) }));
      setArmRepairProposal(null);
      await refresh();
      setSelectedPanel("simulation");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const proposeCadSketchEdit = async (componentId, instruction) => {
    setBusy(true);
    setError("");
    try {
      return await api.proposeCadSketchEdit(jobId, componentId, instruction);
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setBusy(false);
    }
  };
  const inspectDesignComponent = async (componentId, question) => {
    setBusy(true);
    setError("");
    try {
      return await api.inspectComponent(jobId, componentId, question);
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setBusy(false);
    }
  };
  const askPcbComponent = async (component, connections, question) => {
    setBusy(true);
    setError("");
    try {
      return await api.askPcbComponent(jobId, component, connections, question);
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setBusy(false);
    }
  };
  const proposeDesignRevision = async (instruction = "") => {
    setBusy(true);
    setError("");
    try {
      return await api.proposeDesignRevision(jobId, instruction);
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setBusy(false);
    }
  };
  const applyDesignRevision = async (proposal) => {
    setBusy(true);
    setError("");
    try {
      const result = await api.applyDesignRevision(jobId, proposal);
      const artifactIds = [result.spec_artifact_id, result.urdf_artifact_id, result.artifact_id, result.freecad_artifact_id].filter(Boolean);
      const artifacts = await Promise.all(artifactIds.map((artifactId) => api.artifact(artifactId)));
      setBodies((previous) => ({ ...previous, ...Object.fromEntries(artifacts.map((artifact) => [artifact.id, artifact])) }));
      await refresh();
      setSelectedPanel("assembly");
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setBusy(false);
    }
  };
  const isHumanoid = job?.robot_class === "research_humanoid";
  const isImageDesign = job?.robot_class === "image_robot_design";
  const workflowSteps = isImageDesign
    ? [
        { state: "specifying", label: "Interpret reference image" },
        { state: "awaiting_review", label: "Review concept & assumptions" },
        { state: "generating", label: "Build primitive CAD & URDF" },
        { state: "validating", label: "Validate component tree" },
        { state: "succeeded", label: "Inspect 3D concept" },
      ]
    : job?.robot_class === "tabletop_robot_arm" || isHumanoid
    ? [
        { state: "specifying", label: "Generate design proposal" },
        { state: "awaiting_review", label: "Review assumptions & BOM" },
        { state: "generating", label: isHumanoid ? "Build humanoid URDF & controls" : "Build URDF & controls" },
        { state: "validating", label: "Validate robot package" },
        { state: "succeeded", label: isHumanoid ? "Prepare humanoid model" : "Prepare design artifacts" },
      ]
    : [
        { state: "specifying", label: "Interpret mission" },
        { state: "awaiting_review", label: "Review design assumptions" },
        { state: "generating", label: "Generate robot files" },
        { state: "validating", label: "Validate design & code" },
        { state: "simulating", label: "Run route simulation" },
        { state: "succeeded", label: "Package prototype" },
      ];
  const currentIndex = workflowSteps.findIndex((step) => step.state === job?.state);
  const lastRecordedStep = Math.max(-1, ...events.map((event) => workflowSteps.findIndex((step) => step.state === event.state)));
  const progressIndex = currentIndex >= 0 ? currentIndex : lastRecordedStep;
  const isFailed = job?.state === "failed" || job?.state === "cancelled";
  return (
    <section className="workbench job-workbench">
      <div className="workbench-titlebar" style={{ flexWrap: "wrap", gap: "12px" }}>
        <button className="link" onClick={onBack}>← Project workspace</button>
        <div className="job-title" style={{ flex: 1, minWidth: "280px" }}>
          <div>
            <p className="eyebrow">BUILD SESSION · #{jobId} · {designSpec?.title || (isImageDesign ? "Image-to-CAD Mechatronics" : "Robot Prototype")}</p>
            <h2>{designSpec?.title || (job?.robot_class === "tabletop_robot_arm" ? "Tabletop arm design" : isHumanoid ? "Research humanoid concept" : isImageDesign ? "Image-guided robot concept" : "Robot prototype")}</h2>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <div style={{ display: "inline-flex", alignItems: "center", gap: "8px", background: "rgba(15, 23, 42, 0.85)", border: "1px solid rgba(56, 189, 248, 0.35)", borderRadius: "999px", padding: "4px 12px", fontSize: "11px", color: "#e2e8f0" }}>
              <span style={{ color: "#38bdf8", fontWeight: 700 }}>✦ Meta AI ({job?.model || "muse-spark"})</span>
              <span style={{ color: "#475569" }}>·</span>
              <span style={{ color: "#a7f3d0" }}>⬡ FreeCAD 1.0</span>
              <span style={{ color: "#475569" }}>·</span>
              <span style={{ color: "#fde68a" }}>⚙ MuJoCo 3.17</span>
              <span style={{ color: "#475569" }}>·</span>
              <span style={{ color: "#c4b5fd" }}>⚡ KiCad 8.0 + ROS 2</span>
            </div>
            {job && <StateChip state={job.state} />}
          </div>
        </div>
      </div>
      {isImageDesign && (
        <div className="stage-flow">
          <div className="stage-flow-intro">
            <span className="stage-flow-label">WORKFLOW</span>
            <span>Move between design, simulation, firmware, electronics, and fabrication.</span>
          </div>
          <nav className="stage-flow-tabs" aria-label="Design workflow stages">
            {[
              { id: "assembly", number: "01", label: "CAD assembly & inspection" },
              { id: "simulation", number: "02", label: "Simulation & co-design" },
              { id: "code", number: "03", label: "Vision & firmware" },
              { id: "cad", number: "04", label: "PCB & wiring" },
              { id: "parts", number: "05", label: "Print & cost" },
            ].map((pill) => (
              <button
                key={pill.id}
                type="button"
                className={`stage-flow-tab ${selectedPanel === pill.id ? "active" : ""}`}
                onClick={() => openPanel(pill.id)}
              >
                <span className="stage-flow-number">{pill.number}</span>
                <span>{pill.label}</span>
              </button>
            ))}
          </nav>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      {job?.state === "awaiting_review" && <div className="review-banner"><div><strong>Design proposal is ready for your review</strong><span>Check the specification and parts list before generating robot files.</span></div><button onClick={approve} disabled={busy}>{busy ? "Building files…" : "Approve & continue →"}</button></div>}
      <div className="job-studio-grid">
        <aside className="studio-panel asset-panel">
          <div className="panel-heading"><div><p className="eyebrow">PROJECT FILES</p><h3>Robot package</h3></div></div>
          <button className={`asset-button ${selectedPanel === "canvas" ? "active" : ""}`} onClick={() => openPanel("canvas")}><span>▦</span><div><strong>Robot canvas</strong><small>{job?.robot_class === "tabletop_robot_arm" ? "3D assembly & joint pose" : isHumanoid ? "3D humanoid assembly" : isImageDesign ? "Image-derived robot" : "Route & prototype view"}</small></div></button>
          <button className={`asset-button ${selectedPanel === "assembly" ? "active" : ""}`} onClick={() => openPanel("assembly")}><span>⬡</span><div><strong>CAD assembly</strong><small>Import & connect parts</small></div></button>
          {[{ key: "design", title: "Robot design", sub: "Specification", icon: "◇", kind: "spec" }, { key: "urdf", title: "URDF model", sub: "Robot description", icon: "⌘", kind: "urdf" }, { key: "parts", title: "Parts list", sub: "Bill of materials", icon: "▤", kind: "bom" }, { key: "cad", title: "CAD package", sub: "Parts + assembly", icon: "⬡", kind: "cad" }, { key: "code", title: "Control software", sub: "Starter code", icon: "‹/›", kind: "code" }, { key: "validation", title: "Validation", sub: "Checks & findings", icon: "✓", kind: "report" }, { key: "simulation", title: "Simulation", sub: "Physics run", icon: "↗", kind: "sim" }].map((item) => {
            const found = job?.artifacts.find((a) => a.kind === item.kind);
            const canRunArmSimulation = item.key === "simulation" && job?.robot_class === "tabletop_robot_arm" && job?.state === "succeeded";
            const unavailableLabel = item.key === "simulation" && job?.robot_class === "tabletop_robot_arm" ? (canRunArmSimulation ? "Run MuJoCo task" : "Build robot files first") : item.key === "simulation" && isHumanoid ? "Humanoid physics is next" : "Created during build";
            const sublabel = found && item.key === "simulation" && isHumanoid ? "Physics not run" : found ? item.sub : unavailableLabel;
            return <button key={item.key} className={`asset-button ${selectedPanel === item.key ? "active" : ""} ${!found && !canRunArmSimulation ? "unavailable" : ""}`} onClick={() => openPanel(item.key)} disabled={!found && !canRunArmSimulation}><span>{item.icon}</span><div><strong>{item.title}</strong><small>{sublabel}</small></div>{found && <i />}</button>;
          })}
          <div className="agent-disclaimer"><span className="agent-orb small">✦</span><p><strong>Meta Multimodal Co-Design</strong><br />{isImageDesign ? "Meta vision decomposes your concept image into parametric FreeCAD solids, KiCad PCB, ROS 2 nodes, and MuJoCo physics." : isHumanoid ? "Meta proposes the humanoid concept; local code builds and validates files." : job?.robot_class === "tabletop_robot_arm" ? "Meta proposes the arm spec; local code builds and validates files." : "Rover artifacts are generated by the local demo pipeline."}</p></div>
        </aside>

        <section className="studio-panel editor-panel">
          <div className="editor-toolbar"><div><p className="eyebrow">{selectedPanel === "canvas" || selectedPanel === "assembly" ? "VISUAL WORKSPACE" : "ARTIFACT INSPECTOR"}</p><h3>{({ canvas: "Robot canvas", assembly: "CAD assembly", design: "Robot design", urdf: "URDF model", parts: "Bill of materials", cad: "CAD parts and assembly", code: "Control software", validation: "Validation report", simulation: "Simulation results" })[selectedPanel]}</h3></div><span className="canvas-mode">{selectedPanel === "assembly" ? "LOCAL CAD IMPORT" : selectedPanel === "cad" ? "PARAMETRIC CONCEPT" : selectedPanel === "code" ? "STARTER CODE" : selectedPanel === "canvas" ? (job?.robot_class === "differential-drive-rover" ? "2D PREVIEW" : "3D MODEL") : activeBody?.schema_version || "WAITING"}</span></div>
          <div style={{ display: selectedPanel === "assembly" ? "block" : "none" }}><ComponentAssemblyEditor freecadExport={cadExportDocument} designSpec={isImageDesign ? designSpec : null} onInspectComponent={isImageDesign ? inspectDesignComponent : undefined} onProposeSketchEdit={isImageDesign ? proposeCadSketchEdit : undefined} onApplySketchEdit={isImageDesign ? editCadSketches : undefined} onProposeDesignRevision={isImageDesign ? proposeDesignRevision : undefined} onApplyDesignRevision={isImageDesign ? applyDesignRevision : undefined} busy={busy} /></div>
          {selectedPanel !== "assembly" && (selectedPanel === "canvas" ? <div className="canvas-stage job-canvas">{job?.robot_class === "tabletop_robot_arm" ? <ArmModelViewer urdf={urdfDocument?.body} initialPickTarget={visionPickTarget} /> : isHumanoid || isImageDesign ? <UrdfModelViewer urdf={urdfDocument?.body} geometry={isHumanoid ? designSpec?.geometry : null} autoFrame={isImageDesign} componentCount={isImageDesign ? designSpec?.components?.length : null} /> : <RoverView sim={simulationDoc} />}</div> : selectedPanel === "simulation" && job?.robot_class === "tabletop_robot_arm" ? <div className="artifact-workspace arm-simulation-workspace"><div className="simulation-controls"><p className="eyebrow">REPRODUCIBLE PHYSICS TASK</p><h3>Tabletop pick and place</h3><p>Run the reviewed arm specification in MuJoCo with contact physics, gravity, joint effort limits, and a parallel gripper.</p>{simulationDoc?.status === "stale" && <p className="simulation-stale-note">{simulationDoc.reason}</p>}{[{ label: "Pick target", values: simulationPick, set: setSimulationPick }, { label: "Place target", values: simulationPlace, set: setSimulationPlace }].map((target) => <fieldset className="target-fields" key={target.label}><legend>{target.label} · metres</legend>{["X", "Y", "Z"].map((axis, index) => <label key={axis}>{axis}<input type="number" min="-0.75" max="0.75" step="0.01" value={target.values[index]} disabled={busy} onChange={(event) => target.set((current) => current.map((value, i) => i === index ? event.target.value : value))} /></label>)}</fieldset>)}<label className="simulation-training-toggle"><input type="checkbox" checked={trainArmController} disabled={busy} onChange={(event) => setTrainArmController(event.target.checked)} /> Tune controller gains against this task before the final run</label>{perceivedTarget && <><label className="simulation-training-toggle"><input type="checkbox" checked={seedFromVision} disabled={busy} onChange={(event) => { setSeedFromVision(event.target.checked); if (event.target.checked) setSimulationPick([perceivedTarget.x, perceivedTarget.y, perceivedTarget.z]); }} /> Seed pick from calibrated vision target</label><small>Vision seed σ {(perceivedTarget.uncertainty_m * 1000).toFixed(1)} mm · depth {perceivedTarget.depth_source.replace(/_/g, " ")}. The backend records the seed and refuses runs above the 10 mm gate.</small></>}<button className="primary-action" type="button" disabled={busy || job?.state !== "succeeded"} onClick={runArmPhysics}>{busy ? "Running MuJoCo…" : trainArmController ? "Tune and run simulation →" : "Run physics simulation →"}</button><small>Controller tuning is deterministic servo-gain search, not reinforcement learning. Generated mass and geometry remain estimates.</small></div>{simulationDoc?.engine === "MuJoCo" && <><div className="simulation-metrics"><div><span>Task</span><strong>{simulationDoc.passed ? "Passed" : "Needs review"}</strong></div><div><span>Payload placement error</span><strong>{(simulationDoc.metrics.goal_error_m * 1000).toFixed(1)} mm</strong></div><div><span>Contact steps observed</span><strong>{simulationDoc.metrics.contacts_seen}</strong></div><div><span>Physics engine</span><strong>MuJoCo {simulationDoc.engine_version}</strong></div>{simulationDoc.task?.vision_seed && <div><span>Vision seed</span><strong>σ {(simulationDoc.task.vision_seed.uncertainty_m * 1000).toFixed(1)} mm</strong></div>}</div>{!simulationDoc.passed && <div className="simulation-repair"><strong>Simulation found a design issue</strong><p>Ask Meta to use the local task metrics to suggest one bounded parameter change. You will review it before any files are rebuilt.</p><button className="secondary-button" type="button" disabled={busy} onClick={requestArmRepair}>{busy ? "Reviewing simulation…" : "Ask AI to suggest a repair"}</button>{armRepairProposal && <div className="cad-sketch-proposal"><strong>{armRepairProposal.parameter.replace(/_/g, " ")}{armRepairProposal.joint_name !== "none" ? ` · ${armRepairProposal.joint_name}` : ""}: {armRepairProposal.value}</strong><p>{armRepairProposal.rationale}</p><small>{armRepairProposal.review_notes}</small><button className="artifact-download" type="button" disabled={busy} onClick={applyArmRepair}>Accept change, rebuild files, and rerun</button><small>Applying the edit marks the previous physics result stale. A new simulation is required before the change counts as improved.</small></div>}</div>}{simulationDoc.controller?.training?.status === "completed" && <p className="simulation-training-result">Controller tuning completed · {simulationDoc.controller.training.iterations} configurations · selected gains {simulationDoc.controller.training.selected_gains ? simulationDoc.controller.training.selected_gains.map((gain) => gain.toFixed(0)).join(" / ") : simulationDoc.controller.gains.map((gain) => gain.toFixed(0)).join(" / ")}</p>}<div className="canvas-stage job-canvas"><ArmModelViewer urdf={urdfDocument?.body} simulationTrajectory={simulationDoc.trajectory} /></div><details className="simulation-record"><summary>Open full simulation record and assumptions</summary><pre>{JSON.stringify(simulationDoc, null, 2)}</pre></details><p className="panel-footnote">The animation replays sampled joint states from the physics run using the visual URDF. The simulated payload path and contact results are in the run record.</p></>}</div> : activeBody ? <div className="artifact-workspace"><ArtifactViewer artifact={activeBody} cadExport={cadExportBody} jobId={jobId} onRebuildCad={isImageDesign && selectedPanel === "cad" ? rebuildCad : undefined} rebuildingCad={busy} onApplyCoDesignEdits={isImageDesign ? editCadSketches : undefined} onAskPcbComponent={askPcbComponent} /></div> : <div className="empty-state">This file appears after its workflow stage completes.</div>)}
          {selectedPanel === "simulation" && job?.robot_class === "tabletop_robot_arm" && <section className="simulation-benchmark"><div><strong>Compare generated arm designs</strong><p>Run the same default MuJoCo pick-and-place acceptance task on up to eight recent arm designs in this project.</p></div><button className="secondary-button" type="button" disabled={busy || job?.state !== "succeeded"} onClick={benchmarkArmDesigns}>{busy ? "Benchmarking designs…" : "Run project benchmark"}</button>{simulationDoc?.engine === "MuJoCo" && simulationDoc.failure_reasons?.length > 0 && <div className="simulation-failures"><strong>Why this run failed</strong><ul>{simulationDoc.failure_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></div>}{benchmarkDoc?.results && <div className="simulation-benchmark-results"><div><strong>Benchmark result</strong><span>{benchmarkDoc.passed_count}/{benchmarkDoc.design_count} designs passed</span></div>{benchmarkDoc.results.map((result) => <article key={`${result.job_id}-${result.spec_artifact_id || "invalid"}`}><div><strong>{result.design_name}</strong><span className={result.passed ? "benchmark-pass" : "benchmark-fail"}>{result.passed ? "PASS" : "FAIL"}</span></div>{result.metrics && <small>Placement error {(result.metrics.goal_error_m * 1000).toFixed(1)} mm · grasp {result.metrics.grasp_contact_seen ? "contacted" : "missed"} · lift {result.metrics.payload_lifted ? "held" : "failed"} · release {result.metrics.payload_released ? "yes" : "no"}</small>}{result.failure_reasons?.length > 0 && <ul>{result.failure_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}</article>)}</div>}</section>}
          {selectedPanel === "canvas" && (job?.robot_class === "tabletop_robot_arm" ? <div className="canvas-specs"><div><span>Robot platform</span><strong>4-DOF tabletop arm</strong></div><div><span>Maximum reach</span><strong>{designSpec ? `${(designSpec.geometry.upper_arm_length_m + designSpec.geometry.forearm_length_m + designSpec.geometry.wrist_length_m).toFixed(2)} m` : "Generating spec"}</strong></div><div><span>Payload proposal</span><strong>{designSpec ? `${designSpec.payload_kg} kg` : "Awaiting design"}</strong></div></div> : isHumanoid ? <div className="canvas-specs"><div><span>Robot platform</span><strong>{designSpec ? `${designSpec.height_m.toFixed(2)} m humanoid` : "Research humanoid"}</strong></div><div><span>Actuated joints</span><strong>31 body · 20 per hand</strong></div><div><span>Payload proposal</span><strong>{designSpec ? `${designSpec.payload_per_arm_kg.toFixed(1)} kg per arm` : "Awaiting design"}</strong></div></div> : isImageDesign ? <div className="canvas-specs"><div><span>Robot concept</span><strong>{designSpec?.title || designSpec?.robot_type || "Image-derived concept"}</strong></div><div><span>Estimated envelope</span><strong>{designSpec?.estimated_dimensions_m ? `${designSpec.estimated_dimensions_m.width.toFixed(2)} × ${designSpec.estimated_dimensions_m.depth.toFixed(2)} × ${designSpec.estimated_dimensions_m.height.toFixed(2)} m` : "Awaiting proposal"}</strong></div><div><span>Concept components</span><strong>{designSpec?.components?.length ? `${designSpec.components.length} parametric parts · revolute kinematics` : "Awaiting proposal"}</strong></div></div> : <div className="canvas-specs"><div><span>Robot platform</span><strong>Differential-drive rover</strong></div><div><span>Route distance</span><strong>{simulationDoc?.distance_m != null ? `${Number(simulationDoc.distance_m).toFixed(1)} m` : "Awaiting simulation"}</strong></div><div><span>Evidence</span><strong>{simulationDoc?.passed ? "Route passed" : simulationDoc ? "Review findings" : "Preview only"}</strong></div></div>)}
        </section>

        <aside className="studio-panel activity-panel">
          <div className="panel-heading"><span className="agent-orb">✦</span><div><p className="eyebrow">FORGEBOT WORKFLOW</p><h3>Build activity</h3></div></div>
          <div className="activity-state">{job ? <><StateChip state={job.state} /><span>{job.state === "awaiting_review" ? "Waiting for your approval" : job.state === "succeeded" ? (job.robot_class === "differential-drive-rover" ? "Prototype package ready" : "Design artifacts ready") : isFailed ? "Run stopped; inspect findings" : "Working through the design"}</span></> : <span className="muted">Loading session…</span>}</div>
          {job?.provider === "meta-model-api" && <p className="model-attribution">Generated with {job.provider} · {job.model}</p>}
          <ol className="workflow-steps">{workflowSteps.map((step, i) => {
            const complete = job?.state === "succeeded" || progressIndex > i;
            const active = job?.state === step.state && job.state !== "succeeded";
            return <li key={step.state} className={`${complete ? "complete" : ""} ${active ? "current" : ""}`}><span className="workflow-marker">{complete ? "✓" : active ? "•" : String(i + 1).padStart(2, "0")}</span><div><strong>{step.label}</strong>{active && <small>{job.state === "awaiting_review" ? "Your review is needed" : "In progress"}</small>}</div></li>;
          })}</ol>
          <div className="activity-log"><h4>Run events</h4>{events.length === 0 ? <p className="muted">Waiting for workflow events…</p> : events.slice(-5).reverse().map((event) => <div className="activity-event" key={event.id}><span className="event-mark" /><div><strong>{event.state.replace(/_/g, " ")}</strong><small>{new Date(event.created_at).toLocaleTimeString()}</small></div></div>)}</div>
          <p className="panel-footnote">{isImageDesign ? "Full-stack mechatronics package: FreeCAD 1.0 OpenCASCADE solids (.FCStd/.STEP/.STL), KiCad 8.0 2-layer PCB, 3D harness routing, MuJoCo 3.17.0 physics, and ROS 2 / C++ / Python firmware." : isHumanoid ? "Humanoid assembly is a neutral-pose kinematic preview. Balance, gait physics, full collision checks, and hardware safety approval are not implemented." : job?.robot_class === "tabletop_robot_arm" ? "The canvas preview is kinematic. The Simulation panel runs a separate MuJoCo task using estimated primitive geometry; neither output authorizes hardware motion." : "Simulation results are estimates for review, not hardware safety approval."}</p>
        </aside>
      </div>
    </section>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const [project, setProject] = useState(null);
  const [jobId, setJobId] = useState(null);
  const [visionPickTarget, setVisionPickTarget] = useState(null);
  useEffect(() => {
    api
      .init()
      .then(() => api.me())
      .then((data) => setUser(data.username))
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }, []);
  const logout = async () => {
    await api.logout();
    setUser(null);
    setProject(null);
    setJobId(null);
    setVisionPickTarget(null);
  };
  if (!ready) return <p className="muted">Loading ForgeBot…</p>;
  return (
    <div className="shell">
      <header className="topbar">
        <div style={{ display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap" }}>
          <div>
            <h1>ForgeBot</h1>
            <p className="muted">Multimodal AI-to-Hardware Mechatronics Studio · FreeCAD 1.0 · MuJoCo 3.17 · KiCad 8.0 · ROS 2</p>
          </div>
        </div>
        {user && (
          <div className="userbox">
            <span>{user}</span>
            <button className="link" onClick={logout}>
              Sign out
            </button>
          </div>
        )}
      </header>
      <main>
        {!user && <LoginScreen onLogin={setUser} />}
        {user && !project && <ProjectsScreen onOpen={setProject} />}
        {user && project && jobId === null && (
          <ProjectScreen project={project} onBack={() => setProject(null)} onOpenJob={setJobId} onUseVisionTarget={setVisionPickTarget} />
        )}
        {user && project && jobId !== null && (
          <JobScreen jobId={jobId} onBack={() => setJobId(null)} visionPickTarget={visionPickTarget} />
        )}
      </main>
    </div>
  );
}
