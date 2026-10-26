import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { ArtifactViewer, StateChip } from "./components.jsx";
import RoverView from "./RoverView.jsx";
import ArmModelViewer from "./ArmModelViewer.jsx";
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

function VisionPanel({ onUseTarget }) {
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
        setImage(new File([blob], `workcell-${Date.now()}.jpg`, { type: "image/jpeg" }));
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
      setCalibration(null);
      setCalibrationPoints([]);
      setSelectedObject(0);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const captureCalibrationPoint = (event) => {
    if (!calibrating || calibrationPoints.length >= 4 || !imageRef.current) return;
    const rect = imageRef.current.getBoundingClientRect();
    const point = [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height];
    if (point.some((value) => value < 0 || value > 1)) return;
    setCalibrationPoints((previous) => [...previous, point]);
  };

  const finishCalibration = () => {
    try {
      setCalibration(solvePlanarHomography(calibrationPoints, WORK_AREA_CORNERS));
      setCalibrating(false);
      setError("");
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
            {cameraActive ? <><button className="primary-action" onClick={capture}>Capture frame</button><button className="secondary-action" onClick={stopCamera}>Close camera</button></> : <><button className="secondary-action" onClick={startCamera}>Open camera</button><label className="secondary-action vision-file">Choose image<input type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => { const next = event.target.files?.[0]; if (next) { setImage(next); setObservation(null); setError(""); setCalibration(null); setCalibrationPoints([]); onUseTarget(null); } }} /></label></>}
            <button className="primary-action" disabled={!image || busy || cameraActive} onClick={analyze}>{busy ? "Analyzing image…" : "Ask Meta to inspect →"}</button>
          </div>
          {error && <p className="error">{error}</p>}
          <p className="vision-privacy">Images are sent to Meta only when you select <strong>Ask Meta</strong>. ForgeBot does not store the image or analysis. Use a non-sensitive workcell image.</p>
        </div>
        <div className="vision-results">
          {!observation ? <div className="vision-empty result-empty"><strong>Scene understanding will appear here</strong><small>Includes object appearance, condition, image regions, relationships, and uncertainty.</small></div> : <>
            <div className="scene-summary"><span className="eyebrow">SCENE SUMMARY</span><p>{observation.scene_summary}</p><small>{observation.work_surface?.description || "Work surface not confidently identified"}</small></div>
            <div className="object-list"><div className="object-list-heading"><strong>Visible objects</strong><span>{observation.objects.length}</span></div>{observation.objects.map((object, index) => <article className="vision-object" key={`${object.label}-${index}`}><div><strong>{object.label}</strong><span className={`confidence-tag ${object.graspability}`}>{Math.round(object.confidence * 100)}% · {object.graspability.replace(/_/g, " ")}</span></div><p>{object.appearance} {object.state}</p>{object.uncertainty && <small>Uncertainty: {object.uncertainty}</small>}</article>)}</div>
            <div className="calibration-card"><div><strong>Map the image to the arm workspace</strong><small>Click the four table corners in this order: X−/Y−, X+/Y−, X+/Y+, X−/Y+. Bounds are −0.1..0.7 m by −0.3..0.3 m in the arm-base frame. This assumes the arm base is at (0,0), 0.1 m from the table’s X− edge and centered across its width.</small></div><div className="calibration-actions"><button className="secondary-action" onClick={() => { setCalibration(null); setCalibrationPoints([]); setCalibrating(true); setError(""); }}>Calibrate from table corners</button>{calibrating && <><span>{calibrationPoints.length}/4 corners</span><button className="secondary-action" disabled={calibrationPoints.length !== 4} onClick={finishCalibration}>Apply calibration</button><button className="link" onClick={() => { setCalibrating(false); setCalibrationPoints([]); }}>Cancel</button></>}</div>{calibration && <p className="calibration-ok">Planar calibration ready · positions are estimates at the table surface.</p>}</div>
            {observation.objects.length > 0 && <div className="object-target"><label>Object to target<select value={selectedObject} onChange={(event) => setSelectedObject(Number(event.target.value))}>{observation.objects.map((object, index) => <option key={`${object.label}-${index}`} value={index}>{object.label}</option>)}</select></label>{mappedObject ? <><p>Estimated from box lower-center: X {mappedObject.x.toFixed(3)} m · Y {mappedObject.y.toFixed(3)} m</p><div className="target-edit-grid">{["x", "y", "z"].map((axis) => <label key={axis}>{axis.toUpperCase()} m<input type="number" step="0.005" value={targetEdit[axis]} onChange={(event) => setTargetEdit((old) => ({ ...old, [axis]: Number(event.target.value) }))} /></label>)}</div><button className="secondary-action" onClick={() => onUseTarget({ ...targetEdit, source: "Meta vision estimate · manually review before motion", label: observation.objects[selectedObject].label })}>Use reviewed estimate as pick target</button><small>Metric location is inferred through your four-corner calibration. Review and adjust the coordinates before using it.</small></> : <small>Calibrate the tabletop before converting this image observation into a robot-frame target.</small>}</div>}
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
  return (
    <section className="workbench">
      <div className="page-heading">
        <button className="link" onClick={onBack}>← All projects</button>
        <div>
          <p className="eyebrow">ROBOTICS WORKSPACE · PROJECT #{project.id}</p>
          <h2>{project.name}</h2>
          <p className="muted">Shape the mission, inspect the robot, then build and review its software.</p>
        </div>
          <span className="robot-class">{robotClass === "tabletop_robot_arm" ? "AI design · tabletop arm" : "Prototype · rover"}</span>
      </div>

      <div className="studio-grid project-studio">
        <aside className="studio-panel mission-panel">
          <div className="panel-heading"><span className="step-number">01</span><div><h3>Mission brief</h3><p className="muted">Describe what the robot should do.</p></div></div>
          <form onSubmit={run}>
            <label>Robot platform<select value={robotClass} onChange={(e) => setRobotClass(e.target.value)}><option value="tabletop_robot_arm">Tabletop robot arm · Meta generated</option><option value="differential-drive-rover">Differential-drive rover · demo</option></select></label>
            <label>Mission task<textarea value={task} onChange={(e) => setTask(e.target.value)} rows={4} /></label>
            <label>Operating environment<input value={environment} onChange={(e) => setEnvironment(e.target.value)} /></label>
            <label>Units<select value={units} onChange={(e) => setUnits(e.target.value)}><option value="si">SI units</option><option value="metric">Metric</option><option value="imperial">Imperial</option></select></label>
            {error && <p className="error">{error}</p>}
            <button className="primary-action" type="submit" disabled={running}>{running ? "Preparing workspace…" : "Start design run"}<span aria-hidden="true">→</span></button>
          </form>
          <p className="panel-footnote">Arm proposals come from Meta Model API, then pass local checks. They are conceptual designs, not manufacturing or safety approvals.</p>
        </aside>

        <section className="studio-panel canvas-panel">
          <div className="panel-heading canvas-heading"><div><p className="eyebrow">DESIGN CANVAS</p><h3>{robotClass === "tabletop_robot_arm" ? "Tabletop arm assembly" : "Rover route concept"}</h3></div><span className="canvas-mode">{robotClass === "tabletop_robot_arm" ? "3D EDITOR" : "2D PREVIEW"}</span></div>
          <div className="canvas-stage">{robotClass === "tabletop_robot_arm" ? <ArmModelViewer /> : <RoverView />}</div>
          <div className="canvas-caption"><span className="legend-dot" /> {robotClass === "tabletop_robot_arm" ? "Arm design preview" : "Rover route preview"} <span>·</span> <span className="muted">{robotClass === "tabletop_robot_arm" ? "URDF assembly and target planning open in a design run" : "Preview only, no physics simulation"}</span></div>
          <div className="canvas-specs"><div><span>Platform</span><strong>{robotClass === "tabletop_robot_arm" ? "4-axis tabletop arm" : "Differential drive"}</strong></div><div><span>Editor</span><strong>{robotClass === "tabletop_robot_arm" ? "Orbit + joint controls" : "Route preview"}</strong></div><div><span>Work area</span><strong>{robotClass === "tabletop_robot_arm" ? "0.8 × 0.6 m table" : "5 × 5 m indoor"}</strong></div></div>
        </section>

        <aside className="studio-panel agent-panel">
          <div className="panel-heading"><span className="agent-orb">✦</span><div><p className="eyebrow">FORGEBOT</p><h3>Build workflow</h3></div></div>
          <p className="agent-note">Meta drafts the arm specification and parts list. ForgeBot validates the proposal, then generates a URDF and starter controls for review.</p>
          <ol className="pipeline-list">
            {["Send mission to Meta", "Review generated arm design", "Generate URDF & controls", "Validate design package", "3D pose editor"].map((label, i) => <li key={label}><span className="pipeline-dot">{String(i + 1).padStart(2, "0")}</span><span>{label}</span><span className="pipeline-pending">WAITING</span></li>)}
          </ol>
          <div className="agent-status"><span className="status-light" /> Ready when you are</div>
        </aside>
      </div>

      <VisionPanel onUseTarget={onUseVisionTarget} />

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
    const kind = ({ design: "spec", urdf: "urdf", parts: "bom", code: "code", validation: "report", simulation: "sim" })[panel];
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
  const activeKind = ({ design: "spec", urdf: "urdf", parts: "bom", code: "code", validation: "report", simulation: "sim" })[selectedPanel];
  const activeArtifact = job?.artifacts.find((item) => item.kind === activeKind);
  const activeBody = activeArtifact && bodies[activeArtifact.id];
  const designArtifact = job?.artifacts.find((item) => item.kind === "spec");
  const urdfArtifact = job?.artifacts.find((item) => item.kind === "urdf");
  const simulationArtifact = job?.artifacts.find((item) => item.kind === "sim");
  const simulationDoc = simulationArtifact ? parseArtifactDocument(bodies[simulationArtifact.id]) : null;
  const designSpec = designArtifact ? parseArtifactDocument(bodies[designArtifact.id]) : null;
  const urdfDocument = urdfArtifact ? bodies[urdfArtifact.id] : null;
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
  const workflowSteps = job?.robot_class === "tabletop_robot_arm"
    ? [
        { state: "specifying", label: "Generate design proposal" },
        { state: "awaiting_review", label: "Review assumptions & BOM" },
        { state: "generating", label: "Build URDF & controls" },
        { state: "validating", label: "Validate robot package" },
        { state: "succeeded", label: "Prepare design artifacts" },
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
      <div className="workbench-titlebar"><button className="link" onClick={onBack}>← Project workspace</button><div className="job-title"><div><p className="eyebrow">BUILD SESSION · #{jobId}</p><h2>{job?.robot_class === "tabletop_robot_arm" ? "Tabletop arm design" : "Robot prototype"}</h2></div>{job && <StateChip state={job.state} />}</div></div>
      {error && <p className="error">{error}</p>}
      {job?.state === "awaiting_review" && <div className="review-banner"><div><strong>Design proposal is ready for your review</strong><span>Check the specification and parts list before generating robot files.</span></div><button onClick={approve} disabled={busy}>{busy ? "Building files…" : "Approve & continue →"}</button></div>}
      <div className="job-studio-grid">
        <aside className="studio-panel asset-panel">
          <div className="panel-heading"><div><p className="eyebrow">PROJECT FILES</p><h3>Robot package</h3></div></div>
          <button className={`asset-button ${selectedPanel === "canvas" ? "active" : ""}`} onClick={() => openPanel("canvas")}><span>▦</span><div><strong>Robot canvas</strong><small>{job?.robot_class === "tabletop_robot_arm" ? "3D assembly & joint pose" : "Route & prototype view"}</small></div></button>
          {[{ key: "design", title: "Robot design", sub: "Specification", icon: "◇", kind: "spec" }, { key: "urdf", title: "URDF model", sub: "Robot description", icon: "⌘", kind: "urdf" }, { key: "parts", title: "Parts list", sub: "Bill of materials", icon: "▤", kind: "bom" }, { key: "code", title: "Control software", sub: "Starter code", icon: "‹/›", kind: "code" }, { key: "validation", title: "Validation", sub: "Checks & findings", icon: "✓", kind: "report" }, { key: "simulation", title: "Simulation", sub: "Physics run", icon: "↗", kind: "sim" }].map((item) => {
            const found = job?.artifacts.find((a) => a.kind === item.kind);
            const unavailableLabel = item.key === "simulation" && job?.robot_class === "tabletop_robot_arm" ? "Arm physics not built yet" : "Created during build";
            return <button key={item.key} className={`asset-button ${selectedPanel === item.key ? "active" : ""} ${!found ? "unavailable" : ""}`} onClick={() => openPanel(item.key)} disabled={!found}><span>{item.icon}</span><div><strong>{item.title}</strong><small>{found ? item.sub : unavailableLabel}</small></div>{found && <i />}</button>;
          })}
          <div className="agent-disclaimer"><span className="agent-orb small">✦</span><p><strong>Design generation</strong><br />{job?.robot_class === "tabletop_robot_arm" ? "Meta proposes the arm spec; local code builds and validates files." : "Rover artifacts are generated by the local demo pipeline."}</p></div>
        </aside>

        <section className="studio-panel editor-panel">
          <div className="editor-toolbar"><div><p className="eyebrow">{selectedPanel === "canvas" ? "VISUAL WORKSPACE" : "ARTIFACT INSPECTOR"}</p><h3>{({ canvas: "Robot canvas", design: "Robot design", urdf: "URDF model", parts: "Bill of materials", code: "Control software", validation: "Validation report", simulation: "Simulation results" })[selectedPanel]}</h3></div><span className="canvas-mode">{selectedPanel === "code" ? "STARTER CODE" : selectedPanel === "canvas" ? (job?.robot_class === "tabletop_robot_arm" ? "3D EDITOR" : "2D PREVIEW") : activeBody?.schema_version || "WAITING"}</span></div>
          {selectedPanel === "canvas" ? <div className="canvas-stage job-canvas">{job?.robot_class === "tabletop_robot_arm" ? <ArmModelViewer urdf={urdfDocument?.body} initialPickTarget={visionPickTarget} /> : <RoverView sim={simulationDoc} />}</div> : activeBody ? <div className="artifact-workspace"><ArtifactViewer artifact={activeBody} /></div> : <div className="empty-state">This file appears after its workflow stage completes.</div>}
          {selectedPanel === "canvas" && (job?.robot_class === "tabletop_robot_arm" ? <div className="canvas-specs"><div><span>Robot platform</span><strong>4-DOF tabletop arm</strong></div><div><span>Maximum reach</span><strong>{designSpec ? `${(designSpec.geometry.upper_arm_length_m + designSpec.geometry.forearm_length_m + designSpec.geometry.wrist_length_m).toFixed(2)} m` : "Generating spec"}</strong></div><div><span>Payload proposal</span><strong>{designSpec ? `${designSpec.payload_kg} kg` : "Awaiting design"}</strong></div></div> : <div className="canvas-specs"><div><span>Robot platform</span><strong>Differential-drive rover</strong></div><div><span>Route distance</span><strong>{simulationDoc ? `${Number(simulationDoc.distance_m).toFixed(1)} m` : "Awaiting simulation"}</strong></div><div><span>Evidence</span><strong>{simulationDoc?.passed ? "Route passed" : simulationDoc ? "Review findings" : "Preview only"}</strong></div></div>)}
        </section>

        <aside className="studio-panel activity-panel">
          <div className="panel-heading"><span className="agent-orb">✦</span><div><p className="eyebrow">FORGEBOT WORKFLOW</p><h3>Build activity</h3></div></div>
          <div className="activity-state">{job ? <><StateChip state={job.state} /><span>{job.state === "awaiting_review" ? "Waiting for your approval" : job.state === "succeeded" ? (job.robot_class === "tabletop_robot_arm" ? "Design artifacts ready" : "Prototype package ready") : isFailed ? "Run stopped; inspect findings" : "Working through the design"}</span></> : <span className="muted">Loading session…</span>}</div>
          {job?.robot_class === "tabletop_robot_arm" && <p className="model-attribution">Generated with {job.provider} · {job.model}</p>}
          <ol className="workflow-steps">{workflowSteps.map((step, i) => {
            const complete = job?.state === "succeeded" || progressIndex > i;
            const active = job?.state === step.state;
            return <li key={step.state} className={`${complete ? "complete" : ""} ${active ? "current" : ""}`}><span className="workflow-marker">{complete ? "✓" : active ? "•" : String(i + 1).padStart(2, "0")}</span><div><strong>{step.label}</strong>{active && <small>{job.state === "awaiting_review" ? "Your review is needed" : "In progress"}</small>}</div></li>;
          })}</ol>
          <div className="activity-log"><h4>Run events</h4>{events.length === 0 ? <p className="muted">Waiting for workflow events…</p> : events.slice(-5).reverse().map((event) => <div className="activity-event" key={event.id}><span className="event-mark" /><div><strong>{event.state.replace(/_/g, " ")}</strong><small>{new Date(event.created_at).toLocaleTimeString()}</small></div></div>)}</div>
          <p className="panel-footnote">{job?.robot_class === "tabletop_robot_arm" ? "Arm motion is a kinematic preview with a simplified tabletop clearance gate. No physics, complete collision checks, or hardware safety approval." : "Simulation results are estimates for review, not hardware safety approval."}</p>
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
        <div>
          <h1>ForgeBot</h1>
          <p className="muted">Robotics design workspace · prototype outputs need engineering review</p>
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
