import React, { useEffect, useMemo, useState } from "react";
import PCB3DView from "./PCB3DView.jsx";
import { api } from "./api.js";

const NET_COLORS = {
  BAT_11V1_RAW: "#f59e0b",
  VMOT_11V1_FUSED: "#ef4444",
  VCC_5V_BUCK: "#f97316",
  VCC_3V3_LOGIC: "#22c55e",
  GND: "#64748b",
  GPIO12_PWM_L: "#a855f7",
  GPIO13_PWM_R: "#d946ef",
  GPIO23_SONAR_TRIG_L: "#06b6d4",
  GPIO24_SONAR_ECHO_L_3V3: "#10b981",
  GPIO17_SONAR_TRIG_R: "#3b82f6",
  GPIO27_SONAR_ECHO_R_3V3: "#14b8a6",
};

export default function MechatronicsPanel({
  jobId,
  mechatronics,
  drivetrain,
  onApplyCoDesignEdits,
  rebuildingCad = false,
  onDownloadText,
  onDownloadZip,
  onAskPcbComponent,
}) {
  const powerSizing = mechatronics?.power_sizing;
  const pcbWiring = mechatronics?.pcb_wiring;
  const firmwareFiles = mechatronics?.firmware_files || {};

  const [targetHours, setTargetHours] = useState(powerSizing?.target_runtime_hours || 3.0);
  const [extraPayloadKg, setExtraPayloadKg] = useState(powerSizing?.extra_payload_kg || 0.0);
  const [rollingCoeff, setRollingCoeff] = useState(powerSizing?.rolling_resistance_coeff || 0.028);
  const [selectedNet, setSelectedNet] = useState("ALL");
  const [showTopCu, setShowTopCu] = useState(true);
  const [showBotCu, setShowBotCu] = useState(true);
  const [showWiring3d, setShowWiring3d] = useState(false);
  const [activeCodeFile, setActiveCodeFile] = useState("run_robot.py");
  const [boardView, setBoardView] = useState("3d");
  const [selectedFootprint, setSelectedFootprint] = useState(null);
  const [componentQuestion, setComponentQuestion] = useState("");
  const [componentAnswer, setComponentAnswer] = useState(null);
  const [questionBusy, setQuestionBusy] = useState(false);
  const [questionError, setQuestionError] = useState("");
  const [nativeModelUrl, setNativeModelUrl] = useState("");
  const [nativeModelBusy, setNativeModelBusy] = useState(false);
  const [nativeModelError, setNativeModelError] = useState("");
  const [nativeModelStatus, setNativeModelStatus] = useState(null);

  useEffect(() => () => {
    if (nativeModelUrl) URL.revokeObjectURL(nativeModelUrl);
  }, [nativeModelUrl]);

  const loadNativeModels = async () => {
    if (!jobId || nativeModelBusy) return;
    setNativeModelBusy(true);
    setNativeModelError("");
    setNativeModelStatus({ state: "loading", count: 0 });
    try {
      const glb = await api.exportPcb3d(jobId);
      const nextUrl = URL.createObjectURL(glb);
      setNativeModelUrl((current) => {
        if (current) URL.revokeObjectURL(current);
        return nextUrl;
      });
    } catch (error) {
      setNativeModelError(error.message || "KiCad could not export the native footprint models.");
      setNativeModelStatus({ state: "error", count: 0 });
    } finally {
      setNativeModelBusy(false);
    }
  };

  if (!powerSizing || !pcbWiring) {
    return null;
  }

  // Live physics-coupled battery & tire-load solver matching backend/core/mechatronics.py
  const liveSizing = useMemo(() => {
    const dryMass = Number(powerSizing.mass_budget?.dry_mass_without_battery_kg || 1.15);
    const baseBatDims = powerSizing.battery_recommendation?.current_cad_dimensions_m || [0.095, 0.055, 0.024];
    const baseVolM3 = Math.max(1e-5, baseBatDims[0] * baseBatDims[1] * baseBatDims[2]);
    const wheelCount = Math.max(2, Number(drivetrain?.wheel_count || 2));
    const wheelRadius = Math.max(0.025, Number(drivetrain?.wheel_diameter_m || 0.07) / 2.0);
    const meanSpeed = Number(powerSizing.drivetrain_energetics?.mean_obstacle_course_speed_m_s || 0.42);
    const avionicsW = Number(powerSizing.avionics_load?.total_avionics_power_w || 7.5);
    const eff = Number(powerSizing.drivetrain_energetics?.drivetrain_efficiency_pct || 66.2) / 100.0;
    const busV = 11.1;
    const g = 9.81;

    let batMassKg = Number(powerSizing.mass_budget?.sized_battery_mass_kg || 0.28);
    let totalMassKg = dryMass + extraPayloadKg + batMassKg;
    let tireLoadN = 0;
    let fRollN = 0;
    let fScrubN = 0;
    let wheelTorqueNm = 0;
    let peakTorqueNm = 0;
    let elecDriveW = 0;
    let totalPowerW = 0;
    let nominalWh = 0;

    for (let i = 0; i < 4; i += 1) {
      totalMassKg = dryMass + extraPayloadKg + batMassKg;
      const normalN = totalMassKg * g;
      tireLoadN = normalN / wheelCount;
      fRollN = normalN * (rollingCoeff + Math.sin((1.2 * Math.PI) / 180.0));
      fScrubN = totalMassKg * (0.18 * 0.42 * meanSpeed + 0.08 * 0.45);
      const fTot = fRollN + fScrubN;
      wheelTorqueNm = (fTot * wheelRadius) / wheelCount;
      peakTorqueNm = Math.max(wheelTorqueNm * 2.8, (totalMassKg * g * 0.22 * wheelRadius) / wheelCount);
      const mechW = fTot * meanSpeed;
      elecDriveW = mechW / eff + wheelCount * 0.14 * busV;
      totalPowerW = elecDriveW + avionicsW;
      const usableWh = totalPowerW * targetHours;
      nominalWh = usableWh / (0.8 * 0.92);
      batMassKg = Math.max(0.095, nominalWh / 148.0);
    }

    const reqMah = Math.ceil(((nominalWh / busV) * 1000.0) / 100.0) * 100;
    const finalWh = (reqMah / 1000.0) * busV;
    const contA = totalPowerW / busV;
    const peakA = (peakTorqueNm * wheelCount * (meanSpeed / wheelRadius) * 1.9) / (busV * eff) + 1.8;
    const reqVolM3 = finalWh / 295.0 / 1000.0;
    const scale = Math.cbrt(reqVolM3 / baseVolM3);
    const sizedDimsM = [
      Number(Math.max(0.065, Math.min(0.165, baseBatDims[0] * scale)).toFixed(4)),
      Number(Math.max(0.038, Math.min(0.11, baseBatDims[1] * scale)).toFixed(4)),
      Number(Math.max(0.018, Math.min(0.055, baseBatDims[2] * scale)).toFixed(4)),
    ];
    const fitsBay = sizedDimsM.every((d, idx) => d <= baseBatDims[idx] * 1.08);

    const baseEdit = powerSizing.battery_recommendation?.battery_component_edits?.[0];
    const componentEdits = baseEdit
      ? [
          {
            id: baseEdit.id,
            dimensions_m: sizedDimsM,
            position_m: baseEdit.position_m,
          },
        ]
      : [];

    return {
      totalMassKg,
      batMassKg,
      tireLoadN,
      fRollN,
      wheelTorqueNm,
      peakTorqueNm,
      elecDriveW,
      avionicsW,
      totalPowerW,
      reqMah,
      finalWh,
      contA,
      peakA,
      sizedDimsM,
      baseBatDims,
      fitsBay,
      componentEdits,
    };
  }, [powerSizing, drivetrain, targetHours, extraPayloadKg, rollingCoeff]);

  const board = pcbWiring.pcb_board;
  const halfW = board.width_mm / 2;
  const halfH = board.height_mm / 2;
  const padMargin = 10;
  const viewBox = `${-halfW - padMargin} ${-halfH - padMargin} ${board.width_mm + padMargin * 2} ${board.height_mm + padMargin * 2}`;

  const allNets = useMemo(() => {
    const s = new Set();
    (board.tracks || []).forEach((t) => s.add(t.net_name));
    return ["ALL", ...Array.from(s)];
  }, [board]);

  const codeFilenames = Object.keys(firmwareFiles);
  const relatedConnections = selectedFootprint
    ? (pcbWiring.wiring_connections || []).filter((wire) => (selectedFootprint.pads || []).some((pad) => wire.net_name?.includes(pad.net_name)))
    : [];
  const askAboutComponent = async (event) => {
    event.preventDefault();
    if (!selectedFootprint || !onAskPcbComponent || questionBusy) return;
    setQuestionBusy(true);
    setQuestionError("");
    setComponentAnswer(null);
    try {
      const answer = await onAskPcbComponent(selectedFootprint, relatedConnections, componentQuestion);
      setComponentAnswer(answer);
    } catch (error) {
      setQuestionError(error.message || "Could not answer this component question.");
    } finally {
      setQuestionBusy(false);
    }
  };

  return (
    <div className="mechatronics-panel" style={{ marginTop: 18, display: "flex", flexDirection: "column", gap: 18 }}>
      {/* 1. Interactive 3-Hour Battery & Tire-Weight Sizing Calculator */}
      <section className="cad-validation-panel" style={{ margin: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <h4 style={{ margin: 0 }}>
            3. Physics-Coupled Tire Load &amp; Battery Endurance Sizer{" "}
            <span className="assembly-status assembly-status-passed">
              {liveSizing.reqMah} mAh ({liveSizing.finalWh.toFixed(1)} Wh) for {targetHours.toFixed(1)}h
            </span>
          </h4>
          <span style={{ fontSize: 12, color: "#94a3b8" }}>
            Recursive Mass-Feedback Solver (Tire Friction + Obstacle Turn Scrub + 7.5W Vision/Avionics)
          </span>
        </div>

        {/* Interactive Sliders */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: 14,
            marginTop: 12,
            padding: 12,
            borderRadius: 8,
            background: "#f5f8f6",
            border: "1px solid #dce7e2",
          }}
        >
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12.5 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Target Mission Runtime:</span>
              <strong style={{ color: "var(--accent)" }}>{targetHours.toFixed(1)} hours</strong>
            </div>
            <input
              type="range"
              min="0.5"
              max="8.0"
              step="0.5"
              value={targetHours}
              onChange={(e) => setTargetHours(Number(e.target.value))}
            />
          </label>

          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12.5 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Extra Payload / Cargo Weight:</span>
              <strong style={{ color: "#fbbf24" }}>+{extraPayloadKg.toFixed(1)} kg</strong>
            </div>
            <input
              type="range"
              min="0.0"
              max="3.0"
              step="0.1"
              value={extraPayloadKg}
              onChange={(e) => setExtraPayloadKg(Number(e.target.value))}
            />
          </label>

          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12.5 }}>
            <span>Surface / Tire Rolling Resistance (μr):</span>
            <select
              value={rollingCoeff}
              onChange={(e) => setRollingCoeff(Number(e.target.value))}
              style={{
                background: "#fff",
                color: "#26333c",
                border: "1px solid #dce2e6",
                borderRadius: 6,
                padding: "5px 8px",
                fontSize: 12,
              }}
            >
              <option value={0.02}>Smooth Lab Floor (μr = 0.020)</option>
              <option value={0.028}>Standard Tile / Wood (μr = 0.028)</option>
              <option value={0.055}>Indoor Carpet (μr = 0.055)</option>
              <option value={0.075}>Rough Outdoor Asphalt (μr = 0.075)</option>
            </select>
          </label>
        </div>

        {/* Live Telemetry Cards */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(175px, 1fr))",
            gap: 10,
            marginTop: 12,
          }}
        >
          <div className="power-metric-card">
            <div className="power-metric-label">CONVERGED MASS &amp; TIRE LOAD</div>
            <div className="power-metric-value">
              {liveSizing.totalMassKg.toFixed(2)} kg · {liveSizing.tireLoadN.toFixed(1)} N/tire
            </div>
            <div className="power-metric-note">
              Includes {(liveSizing.batMassKg * 1000).toFixed(0)} g battery pack
            </div>
          </div>

          <div className="power-metric-card">
            <div className="power-metric-label">WHEEL TORQUE &amp; DRIVE POWER</div>
            <div className="power-metric-value">
              {liveSizing.wheelTorqueNm.toFixed(3)} N·m ({liveSizing.elecDriveW.toFixed(1)} W)
            </div>
            <div className="power-metric-note">
              Peak obstacle turn torque: {liveSizing.peakTorqueNm.toFixed(2)} N·m
            </div>
          </div>

          <div className="power-metric-card">
            <div className="power-metric-label">TOTAL POWER &amp; CURRENT</div>
            <div className="power-metric-value">
              {liveSizing.totalPowerW.toFixed(1)} W ({liveSizing.contA.toFixed(2)} A @ 11.1V)
            </div>
            <div className="power-metric-note">
              Drive {liveSizing.elecDriveW.toFixed(1)}W + Vision/SBC {liveSizing.avionicsW.toFixed(1)}W
            </div>
          </div>

          <div className="power-metric-card">
            <div className="power-metric-label">REQUIRED 3S BATTERY PACK</div>
            <div className="power-metric-value">
              {liveSizing.reqMah} mAh · {liveSizing.finalWh.toFixed(1)} Wh
            </div>
            <div className="power-metric-note">
              Peak burst: {liveSizing.peakA.toFixed(1)} A (80% safe DoD)
            </div>
          </div>
        </div>

        {/* CAD Battery Compartment Fit Check & One-Click Resize */}
        <div
          style={{
            marginTop: 12,
            padding: "10px 12px",
            borderRadius: 8,
            background: liveSizing.fitsBay ? "rgba(16, 185, 129, 0.1)" : "rgba(245, 158, 11, 0.14)",
            border: `1px solid ${liveSizing.fitsBay ? "rgba(16, 185, 129, 0.35)" : "rgba(245, 158, 11, 0.4)"}`,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 10,
          }}
        >
          <div style={{ fontSize: 12.5 }}>
            <strong>CAD Battery Bay Fit Check:</strong> Sized {liveSizing.reqMah} mAh 3S pack requires{" "}
            <code>{liveSizing.sizedDimsM.map((v) => (v * 1000).toFixed(0)).join(" × ")} mm</code> (current CAD{" "}
            <code>battery_pack</code> is{" "}
            <code>{liveSizing.baseBatDims.map((v) => (v * 1000).toFixed(0)).join(" × ")} mm</code>).{" "}
            {liveSizing.fitsBay
              ? "Fits inside current lower-deck battery bay."
              : "Larger pack required for this runtime/payload — update CAD battery envelope below."}
          </div>
          {onApplyCoDesignEdits && liveSizing.componentEdits.length > 0 && (
            <button
              className="artifact-download"
              type="button"
              disabled={rebuildingCad}
              onClick={() => onApplyCoDesignEdits(liveSizing.componentEdits)}
              style={{ cursor: "pointer" }}
            >
              {rebuildingCad
                ? "Updating CAD Battery & Re-Simulating…"
                : `Sync ${liveSizing.reqMah} mAh Battery Envelope to FreeCAD →`}
            </button>
          )}
        </div>
      </section>

      {/* 2. Inspectable Custom Carrier PCB (.kicad_pcb) & System Wiring Harness */}
      <div className="pcb-wiring-layout">
        <section className="cad-validation-panel" style={{ margin: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
            <h4 style={{ margin: 0 }}>
              4. Custom Carrier PCB Inspector{" "}
              <span className="assembly-status assembly-status-passed">
                {board.width_mm} × {board.height_mm} mm · 2-Layer
              </span>
            </h4>
            <div style={{ display: "flex", gap: 8, fontSize: 11.5 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                <input type="checkbox" checked={showTopCu} onChange={(e) => setShowTopCu(e.target.checked)} />
                <span style={{ color: "#f87171", fontWeight: 600 }}>F.Cu (Top)</span>
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                <input type="checkbox" checked={showBotCu} onChange={(e) => setShowBotCu(e.target.checked)} />
                <span style={{ color: "#38bdf8", fontWeight: 600 }}>B.Cu (Bottom)</span>
              </label>
            </div>
          </div>
          <p className="muted" style={{ marginTop: 6 }}>
            Generated in native KiCad format (<code>.kicad_pcb</code>). Load installed STEP models for realistic connectors and fuse; the buck module remains an approximation because no exact model is installed.
          </p>

          <div className="pcb-view-toolbar" role="group" aria-label="PCB view options">
            <div className="pcb-view-switch">
              <button type="button" className={boardView === "3d" ? "active" : ""} aria-pressed={boardView === "3d"} onClick={() => setBoardView("3d")}>3D assembly</button>
              <button type="button" className={boardView === "2d" ? "active" : ""} aria-pressed={boardView === "2d"} onClick={() => setBoardView("2d")}>2D traces</button>
            </div>
            <label><input type="checkbox" checked={showTopCu} onChange={(e) => setShowTopCu(e.target.checked)} /> Top copper</label>
            <label><input type="checkbox" checked={showBotCu} onChange={(e) => setShowBotCu(e.target.checked)} /> Bottom copper</label>
            <label><input type="checkbox" checked={showWiring3d} disabled={boardView !== "3d"} onChange={(e) => setShowWiring3d(e.target.checked)} /> Show wire harness</label>
            {boardView === "3d" && <button type="button" className="pcb-native-model-button" onClick={loadNativeModels} disabled={!jobId || nativeModelBusy}>{nativeModelBusy ? "Loading KiCad models…" : nativeModelUrl ? "Reload KiCad models" : "Load KiCad 3D models"}</button>}
          </div>
          {boardView === "3d" && <p className="pcb-view-note">This layout currently has {board.tracks?.length || 0} routed copper segments, not a full copper pour. Top traces are on this face; bottom traces are on the reverse and show when you orbit underneath. Real solder mask hides most copper; these overlays exaggerate it for inspection. Colored harness leads are separate cables and only run to external connectors. Drag to orbit, scroll to zoom, or use Reset view.</p>}

            {nativeModelError && <p className="error pcb-native-model-error" role="status">{nativeModelError}</p>}
            {nativeModelStatus?.state === "loading" && <p className="pcb-native-model-note" role="status">KiCad models are being loaded into the 3D view…</p>}
            {nativeModelStatus?.state === "loaded" && <p className="pcb-native-model-note" role="status">{nativeModelStatus.count} real KiCad STEP models are visible in the assembly. U1 and unmatched parts use the ForgeBot preview.</p>}
            {nativeModelStatus?.state === "partial" && <p className="pcb-native-model-error" role="status">KiCad export opened, but only {nativeModelStatus.count} of 6 footprint models matched. Unmatched references: {nativeModelStatus.missingRefs?.join(", ")}. Preview parts remain visible.</p>}
            {nativeModelStatus?.state === "no-match" && <p className="pcb-native-model-error" role="status">The 3D viewer opened the GLB but found no matching footprint references. Expected: {nativeModelStatus.missingRefs?.join(", ")}. First GLB nodes: {nativeModelStatus.nodeNames?.join(", ") || "none"}.</p>}
            {nativeModelStatus?.state === "load-error" && <p className="pcb-native-model-error" role="status">The KiCad GLB was exported, but the browser could not parse it: {nativeModelStatus.error}.</p>}

          {boardView === "3d" && (
            <PCB3DView
              board={board}
              selectedRef={selectedFootprint?.ref}
              onSelect={(footprint) => { setSelectedFootprint(footprint); setComponentAnswer(null); }}
              showTopCu={showTopCu}
              showBotCu={showBotCu}
              showWiring={showWiring3d}
              wiringConnections={pcbWiring.wiring_connections || []}
              nativeModelUrl={nativeModelUrl}
                onNativeModelStatus={setNativeModelStatus}
            />
          )}

          <div className="pcb-component-list" aria-label="Select a PCB component">
            {(board.footprints || []).map((footprint) => <button key={footprint.ref} type="button" className={selectedFootprint?.ref === footprint.ref ? "active" : ""} aria-pressed={selectedFootprint?.ref === footprint.ref} onClick={() => { setSelectedFootprint(footprint); setComponentAnswer(null); }}><strong>{footprint.ref}</strong><span>{footprint.role || footprint.label}</span></button>)}
          </div>

          {selectedFootprint && (
            <section className="pcb-component-inspector" aria-live="polite">
              <div className="pcb-component-title">
                <div><p className="eyebrow">SELECTED PCB COMPONENT</p><h5>{selectedFootprint.ref} · {selectedFootprint.role || selectedFootprint.label}</h5><small>{selectedFootprint.footprint_lib}</small></div>
                <span>{selectedFootprint.width_mm} × {selectedFootprint.height_mm} mm footprint</span>
              </div>
              <div className="pcb-pad-list"><strong>Pads and nets</strong>{(selectedFootprint.pads || []).map((pad) => <span key={`${selectedFootprint.ref}-${pad.num}`}>{pad.num} · {pad.name} <b>{pad.net_name}</b></span>)}</div>
              {onAskPcbComponent && <form className="pcb-question-form" onSubmit={askAboutComponent}>
                <label htmlFor="pcb-component-question">Ask about {selectedFootprint.ref}</label>
                <div><input id="pcb-component-question" value={componentQuestion} onChange={(event) => setComponentQuestion(event.target.value)} placeholder="What does this part do? How is it connected?" maxLength={1000} /><button className="artifact-download" disabled={questionBusy}>{questionBusy ? "Thinking…" : "Ask"}</button></div>
                {questionError && <p className="error">{questionError}</p>}
                {componentAnswer && <div className="pcb-answer"><strong>{componentAnswer.answer}</strong>{componentAnswer.known_from_design?.length > 0 && <p><b>From the design:</b> {componentAnswer.known_from_design.join(" · ")}</p>}{componentAnswer.unknown_or_unverified?.length > 0 && <p><b>Not confirmed:</b> {componentAnswer.unknown_or_unverified.join(" · ")}</p>}</div>}
              </form>}
            </section>
          )}

          {/* Net Filter Pills */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 8 }}>
            {allNets.map((net) => (
              <button
                key={net}
                className="net-filter-chip"
                type="button"
                onClick={() => setSelectedNet(net)}
                style={{
                  fontSize: 10.5,
                  padding: "3px 7px",
                  borderRadius: 6,
                  cursor: "pointer",
                  border: selectedNet === net ? "1px solid #38bdf8" : "1px solid rgba(148,163,184,0.25)",
                  background: selectedNet === net ? "rgba(56,189,248,0.22)" : "rgba(15,23,42,0.7)",
                  color: NET_COLORS[net] || "#f8fafc",
                  fontWeight: 600,
                }}
              >
                {net}
              </button>
            ))}
          </div>

          {/* Interactive 2D PCB Canvas (SVG) */}
          {boardView === "2d" && <div
            style={{
              marginTop: 10,
              background: "#071318",
              borderRadius: 10,
              border: "1px solid rgba(148,163,184,0.25)",
              padding: 8,
            }}
          >
            <svg viewBox={viewBox} style={{ width: "100%", height: 270, display: "block" }}>
              {/* Solder Mask Substrate */}
              <rect
                x={-halfW}
                y={-halfH}
                width={board.width_mm}
                height={board.height_mm}
                rx={2.5}
                fill="#0b3b24"
                stroke="#facc15"
                strokeWidth={0.45}
              />

              {/* Millimeter Grid Lines */}
              {[-30, -20, -10, 0, 10, 20, 30].map((gx) => (
                <line
                  key={`gx-${gx}`}
                  x1={gx}
                  y1={-halfH}
                  x2={gx}
                  y2={halfH}
                  stroke="rgba(255,255,255,0.05)"
                  strokeWidth={0.2}
                />
              ))}
              {[-20, -10, 0, 10, 20].map((gy) => (
                <line
                  key={`gy-${gy}`}
                  x1={-halfW}
                  y1={gy}
                  x2={halfW}
                  y2={gy}
                  stroke="rgba(255,255,255,0.05)"
                  strokeWidth={0.2}
                />
              ))}

              {/* Routed Copper Tracks */}
              {(board.tracks || []).map((tr) => {
                if (tr.layer === "F.Cu" && !showTopCu) return null;
                if (tr.layer === "B.Cu" && !showBotCu) return null;
                const isSelected = selectedNet === "ALL" || selectedNet === tr.net_name;
                const strokeColor =
                  selectedNet === tr.net_name
                    ? "#facc15"
                    : tr.layer === "F.Cu"
                    ? "#ef4444"
                    : "#0ea5e9";
                return (
                  <g key={tr.id} style={{ cursor: "pointer" }} onClick={() => setSelectedNet(tr.net_name)}>
                    <line
                      x1={tr.start_mm[0]}
                      y1={tr.start_mm[1]}
                      x2={tr.end_mm[0]}
                      y2={tr.end_mm[1]}
                      stroke={strokeColor}
                      strokeWidth={Math.max(0.55, tr.width_mm)}
                      strokeLinecap="round"
                      opacity={isSelected ? 0.95 : 0.2}
                    />
                  </g>
                );
              })}

              {/* 4x M3 Mounting Holes (Aligned with FreeCAD Standoffs) */}
              {(board.mounting_holes_mm || []).map((hole) => (
                <g key={hole.id} transform={`translate(${hole.x_mm}, ${hole.y_mm})`}>
                  <circle r={hole.diameter_mm / 2 + 1.2} fill="#d4af37" opacity={0.85} />
                  <circle r={hole.diameter_mm / 2} fill="#071318" />
                  <text y={-3.4} fontSize={2.1} fill="#e2e8f0" textAnchor="middle">
                    {hole.id}
                  </text>
                </g>
              ))}

              {/* Component Footprints & Pads */}
              {(board.footprints || []).map((fp) => (
                <g key={fp.ref} transform={`translate(${fp.x_mm}, ${fp.y_mm})`}>
                  <rect
                    x={-fp.width_mm / 2}
                    y={-fp.height_mm / 2}
                    width={fp.width_mm}
                    height={fp.height_mm}
                    rx={0.8}
                    fill="rgba(15, 23, 42, 0.72)"
                    stroke="#f8fafc"
                    strokeWidth={0.28}
                  />
                  <text y={-fp.height_mm / 2 - 1.0} fontSize={2.2} fill="#f8fafc" fontWeight="bold" textAnchor="middle">
                    {fp.ref}
                  </text>
                  {(fp.pads || []).map((pad) => {
                    const padHighlighted = selectedNet === "ALL" || selectedNet === pad.net_name;
                    return (
                      <g
                        key={`${fp.ref}-${pad.num}`}
                        transform={`translate(${pad.dx_mm}, ${pad.dy_mm})`}
                        style={{ cursor: "pointer" }}
                        onClick={() => setSelectedNet(pad.net_name)}
                      >
                        <circle
                          r={1.05}
                          fill={padHighlighted ? NET_COLORS[pad.net_name] || "#eab308" : "#475569"}
                          stroke="#0f172a"
                          strokeWidth={0.2}
                        />
                        <circle r={0.42} fill="#071318" />
                      </g>
                    );
                  })}
                </g>
              ))}
            </svg>
          </div>}

          {/* KiCad Download Buttons */}
          {onDownloadText && pcbWiring.kicad_files && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
              <button
                className="artifact-download"
                type="button"
                onClick={() =>
                  onDownloadText(
                    "forgebot_carrier.kicad_pcb",
                    pcbWiring.kicad_files["forgebot_carrier.kicad_pcb"],
                    "text/plain;charset=utf-8"
                  )
                }
              >
                Download KiCad PCB (.kicad_pcb)
              </button>
              <button
                className="artifact-download"
                type="button"
                onClick={() =>
                  onDownloadText(
                    "forgebot_carrier.kicad_sch",
                    pcbWiring.kicad_files["forgebot_carrier.kicad_sch"],
                    "text/plain;charset=utf-8"
                  )
                }
              >
                Download KiCad Schematic (.kicad_sch)
              </button>
            </div>
          )}
        </section>

        {/* System Wiring Harness & ERC Checks */}
        <section className="cad-validation-panel" style={{ margin: 0 }}>
          <h4 style={{ margin: 0 }}>
            5. System Wiring Harness &amp; Electrical Rule Checks{" "}
            <span className="assembly-status assembly-status-passed">
              {pcbWiring.erc_checks?.length || 0} ERC Checks Passed
            </span>
          </h4>
          <p className="muted" style={{ marginTop: 6 }}>
            Pin-to-pin wiring harness with wire gauges (AWG) sized for peak motor current and 3D cable lengths measured from your CAD assembly.
          </p>

          <div style={{ overflowX: "auto", marginTop: 8 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5, textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(148,163,184,0.25)", color: "#94a3b8" }}>
                  <th style={{ padding: "5px 6px" }}>From → To</th>
                  <th style={{ padding: "5px 6px" }}>Pins / Net</th>
                  <th style={{ padding: "5px 6px" }}>Rail / Max I</th>
                  <th style={{ padding: "5px 6px" }}>Gauge &amp; Length</th>
                </tr>
              </thead>
              <tbody>
                {(pcbWiring.wiring_connections || []).map((w) => (
                  <tr key={w.id} style={{ borderBottom: "1px solid rgba(148,163,184,0.12)" }}>
                    <td style={{ padding: "5px 6px", fontWeight: 600 }}>
                      <code>{w.from_component}</code> → <code>{w.to_component}</code>
                    </td>
                    <td style={{ padding: "5px 6px", color: "#cbd5e1" }}>
                      <div>{w.to_pin}</div>
                      <small style={{ color: "#38bdf8" }}>{w.net_name}</small>
                    </td>
                    <td style={{ padding: "5px 6px" }}>
                      <strong>{w.voltage_v}V</strong> · {w.max_current_a}A
                    </td>
                    <td style={{ padding: "5px 6px", color: "#94a3b8" }}>
                      {w.wire_gauge_awg} ({w.length_mm} mm)
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h5 style={{ margin: "12px 0 6px", fontSize: 12.5 }}>Automated Electrical Rule Checks (ERC / DRC)</h5>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {(pcbWiring.erc_checks || []).map((chk) => (
              <div
                key={chk.code}
                className="erc-check"
                style={{
                  padding: "6px 10px",
                  borderRadius: 6,
                  background: "rgba(16, 185, 129, 0.08)",
                  border: "1px solid rgba(16, 185, 129, 0.25)",
                  fontSize: 11.5,
                }}
              >
                <strong style={{ color: "#34d399" }}>✓ {chk.title}:</strong>{" "}
                <span style={{ color: "#cbd5e1" }}>{chk.detail}</span>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* 3. Deployable Vision + Sonar + Learned Policy Firmware Bundle */}
      {codeFilenames.length > 0 && (
        <section className="cad-validation-panel" style={{ margin: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
            <h4 style={{ margin: 0 }}>
              6. Deployable Vision Obstacle Recognition &amp; Control Firmware Bundle{" "}
              <span className="assembly-status assembly-status-passed">{codeFilenames.length} Files Ready</span>
            </h4>
            {onDownloadZip && (
              <button
                className="artifact-download"
                type="button"
                onClick={() => onDownloadZip("forgebot-firmware-and-pcb.zip", firmwareFiles)}
              >
                Download Complete Firmware, Vision &amp; KiCad PCB Bundle (.zip)
              </button>
            )}
          </div>
          <p className="muted" style={{ marginTop: 6 }}>
            Pre-wired to the exact BCM GPIO pinout above (<code>hardware_pinout.py</code>), combining camera floor-homography obstacle recognition + dual ultrasonic fusion (<code>vision_obstacle_detector.py</code>) with the learned obstacle-world policy gains (<code>obstacle_avoidance_controller.py</code>).
          </p>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
            {codeFilenames.map((fname) => (
              <button
                key={fname}
                className="firmware-file-tab"
                aria-pressed={activeCodeFile === fname}
                type="button"
                onClick={() => setActiveCodeFile(fname)}
                style={{
                  fontSize: 11.5,
                  padding: "5px 10px",
                  borderRadius: 6,
                  cursor: "pointer",
                  border: activeCodeFile === fname ? "1px solid #38bdf8" : "1px solid rgba(148,163,184,0.25)",
                  background: activeCodeFile === fname ? "rgba(56,189,248,0.2)" : "rgba(15,23,42,0.65)",
                  color: activeCodeFile === fname ? "#38bdf8" : "#cbd5e1",
                  fontFamily: "monospace",
                }}
              >
                {fname}
              </button>
            ))}
          </div>

          <pre
            className="artifact-body"
            style={{
              marginTop: 10,
              maxHeight: 280,
              overflowY: "auto",
              fontSize: 11.5,
            }}
          >
            {firmwareFiles[activeCodeFile]}
          </pre>
        </section>
      )}
    </div>
  );
}
