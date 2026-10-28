import React from "react";
import RoverView from "./RoverView.jsx";
import MechatronicsPanel from "./MechatronicsPanel.jsx";
import { FabricationEstimatorPanel } from "./FabricationEstimatorPanel.jsx";

export function StateChip({ state }) {
  return <span className={`chip chip-${state}`}>{state.replace(/_/g, " ")}</span>;
}

function tryParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function zipStore(files) {
  const encoder = new TextEncoder();
  const crc32 = (bytes) => {
    let crc = 0xffffffff;
    for (const byte of bytes) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
    return (crc ^ 0xffffffff) >>> 0;
  };
  const local = [];
  const central = [];
  let offset = 0;
  const header = (length, writes) => {
    const buffer = new Uint8Array(length);
    writes(new DataView(buffer.buffer));
    return buffer;
  };
  for (const [name, content] of Object.entries(files)) {
    const nameBytes = encoder.encode(name);
    const data = encoder.encode(content);
    const crc = crc32(data);
    const localHeader = header(30, (view) => {
      view.setUint32(0, 0x04034b50, true); view.setUint16(4, 20, true); view.setUint16(6, 0x0800, true);
      view.setUint16(8, 0, true); view.setUint16(12, 0x21, true); view.setUint32(14, crc, true); view.setUint32(18, data.length, true);
      view.setUint32(22, data.length, true); view.setUint16(26, nameBytes.length, true);
    });
    local.push(localHeader, nameBytes, data);
    const centralHeader = header(46, (view) => {
      view.setUint32(0, 0x02014b50, true); view.setUint16(4, 20, true); view.setUint16(6, 20, true);
      view.setUint16(8, 0x0800, true); view.setUint16(10, 0, true); view.setUint16(14, 0x21, true); view.setUint32(16, crc, true);
      view.setUint32(20, data.length, true); view.setUint32(24, data.length, true);
      view.setUint16(28, nameBytes.length, true); view.setUint32(42, offset, true);
    });
    central.push(centralHeader, nameBytes);
    offset += localHeader.length + nameBytes.length + data.length;
  }
  const centralSize = central.reduce((size, part) => size + part.length, 0);
  const end = header(22, (view) => {
    view.setUint32(0, 0x06054b50, true); view.setUint16(8, Object.keys(files).length, true);
    view.setUint16(10, Object.keys(files).length, true); view.setUint32(12, centralSize, true);
    view.setUint32(16, offset, true);
  });
  return new Blob([...local, ...central, end], { type: "application/zip" });
}

function downloadText(name, body, mime = "text/plain;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([body], { type: mime }));
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = name; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadBase64(file) {
  const binary = atob(file.base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  const url = URL.createObjectURL(new Blob([bytes], { type: file.media_type || "application/octet-stream" }));
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = file.name; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ArtifactViewer({ artifact, cadExport, jobId, onRebuildCad, rebuildingCad = false, onApplyCoDesignEdits, onAskPcbComponent }) {
  if (!artifact) {
    return <p className="muted">Select an artifact to inspect it.</p>;
  }
  const doc = tryParse(artifact.body);
  if (artifact.kind === "cad" && ["forgebot-cad-package/v1", "forgebot-cad-package/v2", "forgebot-cad-package/v3", "forgebot-cad-package/v4"].includes(doc?.schema_version) && doc.files) {
    const filenames = Object.keys(doc.files);
    const validation = tryParse(doc.files["assembly_validation.json"]);
    const manifest = tryParse(doc.files["manifest.json"]);
    const exportDoc = tryParse(cadExport?.body);
    const findings = validation?.findings || [];
    const actionableFindings = findings.filter((finding) => finding.severity !== "info");
    const infoCount = findings.length - actionableFindings.length;
    return (
      <div>
        <h3>Editable CAD assembly package</h3>
        <p className="muted">Separate OpenSCAD part modules, positioned assembly, URDF and mount manifest. Concept dimensions are estimates.</p>
        <section className="cad-validation-panel">
          <h4>Assembly checks <span className={`assembly-status assembly-status-${validation?.status || "unknown"}`}>{(validation?.status || "not run").replace(/_/g, " ")}</span></h4>
          <p className="muted">{validation?.component_count ?? 0} parts · {validation?.connection_count ?? 0} inferred connections · {validation?.method || "No check details available."}</p>
          {actionableFindings.length ? <ul className="findings">{actionableFindings.map((finding, index) => <li key={`${finding.code}-${index}`} className={finding.severity === "blocker" ? "blocker" : "warning"}><strong>{finding.severity}:</strong> {finding.message}</li>)}</ul> : <p>No blocking or warning findings.</p>}
          {infoCount > 0 && <details className="cad-details"><summary>{infoCount} informational notes</summary><ul className="findings">{findings.filter((finding) => finding.severity === "info").map((finding, index) => <li key={`${finding.code}-${index}`}><strong>Info:</strong> {finding.message}</li>)}</ul></details>}
        </section>
        {!!validation?.connections?.length && <details className="cad-details"><summary>Inferred mating interfaces</summary><ul className="findings">{validation.connections.map((connection) => <li key={`${connection.parent_id}-${connection.child_id}`}><strong>{connection.parent_id} → {connection.child_id}</strong> · {connection.type} · {connection.parent_interface} ↔ {connection.child_interface}{connection.type === "fixed_surface" ? ` · ${Math.abs(connection.surface_gap_m * 1000).toFixed(1)} mm ${connection.surface_gap_m >= 0 ? "gap" : "overlap"}` : " · axle center inferred from wheel placement"}</li>)}</ul></details>}
        {!!manifest?.components?.length && <details className="cad-details"><summary>Named part interfaces</summary>{manifest.components.map((part) => <details key={part.id} className="cad-part-interfaces"><summary>{part.name} · {part.interfaces?.length || 0} interfaces</summary><ul className="findings">{(part.interfaces || []).map((item) => <li key={item.name}><strong>{item.name}</strong> · {item.type}{item.size_m ? ` · ${item.size_m.map((value) => `${(value * 1000).toFixed(0)} mm`).join(" × ")}` : ""}{item.hole_pattern?.status === "proposed_not_cut" ? ` · 4-hole layout proposed (${(item.hole_pattern.hole_diameter_m * 1000).toFixed(1)} mm); not cut` : ""}</li>)}</ul></details>)}</details>}
        <section className="cad-validation-panel cad-export-guide"><h4>FreeCAD workflow</h4><ol><li>For newly approved image designs, ForgeBot runs the local FreeCAD CAD engine automatically.</li><li>The native <code>.FCStd</code>, STEP assembly, per-part meshes, and placement manifest are saved to the job export folder. The CAD assembly view loads its preview meshes automatically.</li><li>For an older job, use <strong>Rebuild CAD package in FreeCAD</strong> below to run the same local export. Download the ZIP or macro if you want to open the source workflow yourself.</li></ol><p className="muted">FreeCAD must run on the same computer as Django. The STEP file is the CAD exchange model; STL meshes are used for the in-app preview. Use <strong>Import FreeCAD export</strong> for files created manually outside ForgeBot.</p></section>
        <p><button className="artifact-download" type="button" onClick={() => downloadText("forgebot-cad-package.zip", zipStore(doc.files), "application/zip")}>Download complete CAD package (.zip)</button></p>
        {doc.files["freecad/build_step.FCMacro"] && <><p><button className="artifact-download" type="button" onClick={() => downloadText("build_step.FCMacro", doc.files["freecad/build_step.FCMacro"])}>Download FreeCAD macro (.FCMacro)</button></p><p className="muted">The ZIP includes this same macro plus its README. Use the ZIP if you want the full source package.</p></>}
        {onRebuildCad && <p><button className="secondary-button" type="button" disabled={rebuildingCad} onClick={onRebuildCad}>{rebuildingCad ? "Building in FreeCAD…" : "Rebuild CAD package in FreeCAD"}</button></p>}
        {exportDoc?.status === "completed" ? <section className="cad-validation-panel"><h4>FreeCAD export ready <span className="assembly-status assembly-status-passed">FreeCAD {exportDoc.freecad_version}</span></h4><p className="muted">ForgeBot built the CAD files locally and loaded their part meshes and placements into CAD assembly. Open the downloaded FCStd or STEP file in FreeCAD to inspect or edit the model.</p><p><button className="artifact-download" type="button" onClick={() => downloadBase64({ ...exportDoc.step_file, media_type: "model/step" })}>Download STEP assembly</button></p><p><button className="artifact-download" type="button" onClick={() => downloadBase64({ ...exportDoc.native_document, media_type: "application/x-freecad-document" })}>Download native FreeCAD document (.FCStd)</button></p><small className="muted">Saved in <code>{exportDoc.output_directory}</code></small></section> : exportDoc && <section className="cad-validation-panel"><h4>FreeCAD export {exportDoc.status === "unavailable" ? "not available" : "failed"}</h4><p className="muted">{exportDoc.message || "Run the FreeCAD macro manually from the complete package."}</p></section>}
        <p><button className="artifact-download" type="button" onClick={() => downloadText("assembly.scad", doc.files["assembly.scad"])}>Download assembly source</button></p>
        <h4>Package files</h4>
        <ul className="findings">{filenames.map((name) => <li key={name}><button className="text-button" type="button" onClick={() => downloadText(name.split("/").at(-1), doc.files[name], name.endsWith(".json") ? "application/json" : "text/plain;charset=utf-8")}>{name}</button></li>)}</ul>
        <details><summary>Inspect assembly manifest</summary><pre className="artifact-body">{doc.files["manifest.json"]}</pre></details>
        <p className="muted">Open <code>assembly.scad</code> in OpenSCAD and keep the <code>parts/</code> folder beside it. The FreeCAD macro uses the local solid kernel to create STEP and FCStd files; manufacturing strength and tolerance are still unverified.</p>
      </div>
    );
  }
  if (artifact.kind === "report") {
    const items = doc?.findings || doc?.errors || [];
    return (
      <div>
        <h3>Validation report</h3>
        {items.length === 0 && <p>No findings.</p>}
        <ul className="findings">
          {items.map((f, i) => (
            <li key={i} className={f.severity === "blocker" ? "blocker" : "warning"}>
              <strong>{typeof f === "string" ? "error" : f.severity}:</strong>{" "}
              {typeof f === "string" ? f : `${f.message} (${f.path})`}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (artifact.kind === "sim" && doc) {
    if (doc.status === "not_run") {
      return <div><h3>Simulation not run</h3><p>{doc.reason || "A physics simulation is not available for this concept."}</p></div>;
    }
    const mp = doc.mass_properties;
    const stab = doc.stability;
    const dt = doc.drivetrain;
    const ot = doc.obstacle_training;
    const codesign = ot?.codesign_refinements;
    return (
      <div>
        <h3>Simulation &amp; Obstacle World Co-Design Training</h3>
        <p>
          {doc.passed ? "Route passed." : "Route failed."} Distance{" "}
          {Number(doc.distance_m).toFixed(2)} m
          {doc.collisions ? `, ${doc.collisions} collision(s)` : ", no collisions"}.
          {ot ? ` · Trained across ${ot.episodes_run} episodes in a ${ot.obstacles?.length || 0}-obstacle world (best policy selected from Episode ${ot.converged_episode}).` : ""}
        </p>
        {!doc.passed && doc.route_status && (
          <p className="route-failure-reason" role="status">
            {doc.route_status.goal_reached === false
              ? `The selected episode stopped ${Number(doc.route_status.goal_error_m).toFixed(2)} m from the final waypoint (it must finish within 0.22 m).`
              : !doc.route_status.simulator_checks_passed
              ? "The route failed the speed or motor limit checks. See the validation findings below."
              : !doc.route_status.static_stability_passed
              ? "The center of mass falls outside the support footprint. See the stability findings below."
              : "The route did not pass all simulation checks. See the validation findings below."}
          </p>
        )}
        {mp && stab && dt && (
          <p className="muted">
            Mass: <strong>{Number(mp.total_mass_kg).toFixed(3)} kg</strong> · <span className="term-help" title="Center of mass: the balance point where the robot's mass can be treated as concentrated." tabIndex={0}>CoM</span>{" "}
            <code>[{mp.center_of_mass_m.map((v) => Number(v).toFixed(3)).join(", ")}] m</code> · Stability margin:{" "}
            <strong title="Distance from the projected center of mass to the edge of the support area.">{(Number(stab.stability_margin_m) * 1000).toFixed(1)} mm</strong> (tip-over{" "}
            <strong>{Number(stab.tip_over_angle_deg).toFixed(1)}°</strong>) · Drive:{" "}
            <strong>{dt.wheel_count} wheels</strong> ({Number(dt.cruise_speed_m_s).toFixed(2)} m/s,{" "}
            {Number(dt.estimated_motor_effort_nm).toFixed(3)} N·m)
          </p>
        )}
        {doc.violations?.length > 0 && (
          <ul className="findings">
            {doc.violations.map((v, i) => (
              <li key={i} className="blocker">
                {v}
              </li>
            ))}
          </ul>
        )}
        <RoverView sim={doc} />

        {ot && (
          <section className="cad-validation-panel" style={{ marginTop: 18 }}>
            <h4>
              Multi-Episode Obstacle World Training Progression{" "}
              <span className="assembly-status assembly-status-passed">
                Converged Ep {ot.converged_episode}/{ot.episodes_run}
              </span>
            </h4>
            <p className="muted">
              Episode 1 starts with an untuned reactive controller that clips obstacles. The policy optimizer iteratively tunes repulsive potential gain, lookahead horizon, and yaw-rate speed damping across {ot.episodes_run} episodes while recording structural stress &amp; clearance telemetry.
            </p>
            <div style={{ overflowX: "auto", marginTop: 10 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid rgba(148,163,184,0.25)", color: "#94a3b8" }}>
                    <th style={{ padding: "6px 8px" }}>Episode</th>
                    <th style={{ padding: "6px 8px" }}>Collisions</th>
                    <th style={{ padding: "6px 8px" }}>Min Clearance</th>
                    <th style={{ padding: "6px 8px" }}>Peak Dynamic Roll</th>
                    <th style={{ padding: "6px 8px" }}>Path Length</th>
                    <th style={{ padding: "6px 8px" }}>Reward</th>
                    <th style={{ padding: "6px 8px" }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {(ot.episode_history || []).map((ep) => (
                    <tr
                      key={ep.episode}
                      style={{
                        borderBottom: "1px solid rgba(148,163,184,0.12)",
                        background: ep.episode === ot.converged_episode ? "rgba(16,185,129,0.10)" : "transparent",
                      }}
                    >
                      <td style={{ padding: "6px 8px", fontWeight: 700 }}>
                        Ep {ep.episode} {ep.episode === 1 ? "(Untuned)" : ep.episode === ot.converged_episode ? "★ (Selected)" : ""}
                      </td>
                      <td style={{ padding: "6px 8px", color: ep.collisions > 0 ? "#f87171" : "#34d399", fontWeight: 600 }}>
                        {ep.collisions} {ep.collided_obstacles?.length ? `(${ep.collided_obstacles.join(", ")})` : ""}
                      </td>
                      <td style={{ padding: "6px 8px" }}>{(Number(ep.min_clearance_m) * 1000).toFixed(1)} mm</td>
                      <td style={{ padding: "6px 8px", color: ep.peak_roll_ratio > 0.7 ? "#fbbf24" : "#cbd5e1" }}>
                        {(Number(ep.peak_roll_ratio) * 100).toFixed(1)}% of tip-over
                      </td>
                      <td style={{ padding: "6px 8px" }}>{Number(ep.distance_m).toFixed(2)} m</td>
                      <td style={{ padding: "6px 8px", fontFamily: "monospace" }}>{Number(ep.reward).toFixed(1)}</td>
                      <td style={{ padding: "6px 8px" }}>
                        <span className={`assembly-status assembly-status-${ep.passed ? "passed" : "failed"}`}>
                          {ep.passed ? "PASSED" : "COLLIDED"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {doc.codesign_comparison && (
          <section
            className="cad-validation-panel"
            style={{
              marginTop: 14,
              border: "1.5px solid rgba(16, 185, 129, 0.55)",
              background: "linear-gradient(135deg, rgba(6, 78, 59, 0.45), rgba(15, 23, 42, 0.85))",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
              <h4 style={{ margin: 0, color: "#a7f3d0" }}>
                ✓ Closed-Loop Co-Design Verification Scorecard (Before vs. After CAD + MuJoCo Rebuild)
              </h4>
              <span className="assembly-status assembly-status-passed">
                {doc.codesign_comparison.edited_components?.length || 0} CAD Parts Updated &amp; Re-Verified
              </span>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(175px, 1fr))",
                gap: 10,
                marginTop: 12,
              }}
            >
              {[
                {
                  label: "Static Tip-Over Angle",
                  b: doc.codesign_comparison.tip_over_deg?.before,
                  a: doc.codesign_comparison.tip_over_deg?.after,
                  unit: "°",
                  higherBetter: true,
                },
                {
                  label: "Support Polygon Margin",
                  b: doc.codesign_comparison.stability_margin_mm?.before,
                  a: doc.codesign_comparison.stability_margin_mm?.after,
                  unit: " mm",
                  higherBetter: true,
                },
                {
                  label: "3D Center of Mass Height",
                  b: doc.codesign_comparison.com_height_mm?.before,
                  a: doc.codesign_comparison.com_height_mm?.after,
                  unit: " mm",
                  higherBetter: false,
                },
                {
                  label: "Peak Dynamic Roll",
                  b: doc.codesign_comparison.peak_roll_deg?.before,
                  a: doc.codesign_comparison.peak_roll_deg?.after,
                  unit: "°",
                  higherBetter: false,
                },
              ].map((m) => {
                const delta = Number(m.a || 0) - Number(m.b || 0);
                const improved = m.higherBetter ? delta >= 0 : delta <= 0;
                return (
                  <div
                    key={m.label}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 6,
                      background: "rgba(15, 23, 42, 0.7)",
                      border: "1px solid rgba(148, 163, 184, 0.2)",
                    }}
                  >
                    <div style={{ fontSize: 11, color: "#94a3b8" }}>{m.label}</div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: "#f8fafc", marginTop: 3, fontFamily: "monospace" }}>
                      {m.b}
                      {m.unit} → <span style={{ color: "#34d399" }}>{m.a}{m.unit}</span>
                    </div>
                    <div style={{ fontSize: 11, color: improved ? "#34d399" : "#fbbf24", marginTop: 2 }}>
                      {delta > 0 ? `+${delta.toFixed(1)}` : delta.toFixed(1)}
                      {m.unit} {improved ? "(Improved)" : ""}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {codesign && (
          <>
            {onApplyCoDesignEdits && (codesign.structural_refinements || []).length > 0 && (
              <div
                style={{
                  marginTop: 16,
                  padding: "12px 16px",
                  borderRadius: 8,
                  background: "#f0f7f4",
                  border: "1px solid #cfe3da",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: 12,
                }}
              >
                <div>
                  <strong style={{ color: "#24483f", fontSize: 14 }}>
                    ⚡ Closed-Loop Hardware + Software Co-Design Optimizer
                  </strong>
                  <p style={{ margin: "3px 0 0", fontSize: 12.5, color: "#53645e" }}>
                    Automatically apply the primary structural CAD refinement, rebuild FreeCAD <code>.FCStd</code>/<code>.STEP</code>/<code>.STL</code> solids, and re-run MuJoCo 3.17.0 obstacle training to verify stability gains.
                  </p>
                </div>
                <button
                  className="primary-action"
                  type="button"
                  disabled={rebuildingCad}
                  style={{ cursor: "pointer", whiteSpace: "nowrap" }}
                  onClick={() => onApplyCoDesignEdits(codesign.structural_refinements[0].component_edits)}
                >
                  {rebuildingCad
                    ? "Rebuilding FreeCAD & MuJoCo…"
                    : `⚡ Auto-Refine Structure & Software (${codesign.structural_refinements[0].component_edits.length} parts) →`}
                </button>
              </div>
            )}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 16, marginTop: 16 }}>
              <section className="cad-validation-panel" style={{ margin: 0 }}>
                <h4>
                  1. Structural &amp; Mechanical CAD Refinements{" "}
                  <span className="assembly-status assembly-status-warning">Hardware Co-Design</span>
                </h4>
                <p className="muted">
                  Empirical findings from obstacle avoidance maneuvers translated into concrete parametric CAD changes. Apply any refinement below to automatically rebuild the FreeCAD <code>.FCStd</code>/<code>.STEP</code> solids and re-run the obstacle world simulation.
                </p>
                <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 10 }}>
                {(codesign.structural_refinements || []).map((item) => (
                  <div
                    key={item.id}
                    style={{
                      padding: 12,
                      borderRadius: 8,
                      background: "rgba(15, 23, 42, 0.55)",
                      border: "1px solid rgba(148, 163, 184, 0.22)",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                      <strong style={{ color: "#f8fafc", fontSize: 13.5 }}>{item.title}</strong>
                      <span
                        style={{
                          fontSize: 11,
                          padding: "2px 8px",
                          borderRadius: 999,
                          background: item.severity === "high" ? "#fff3dc" : "#e6f4ef",
                          color: item.severity === "high" ? "#8a5a13" : "#176b5d",
                          fontWeight: 600,
                        }}
                      >
                        {item.expected_gain}
                      </span>
                    </div>
                    <p style={{ fontSize: 12.5, margin: "6px 0", color: "#cbd5e1" }}>
                      <strong>Telemetry finding:</strong> {item.finding}
                    </p>
                    <p style={{ fontSize: 12.5, margin: "6px 0", color: "#94a3b8" }}>
                      <strong>CAD change:</strong> {item.recommendation}
                    </p>
                    {onApplyCoDesignEdits && item.component_edits?.length > 0 && (
                      <button
                        className="artifact-download"
                        type="button"
                        disabled={rebuildingCad}
                        style={{ marginTop: 6, cursor: "pointer" }}
                        onClick={() => onApplyCoDesignEdits(item.component_edits)}
                      >
                        {rebuildingCad
                          ? "Rebuilding CAD & Re-Simulating…"
                          : `Apply Structural Refinement (${item.component_edits.length} parts) & Re-Simulate →`}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </section>

            <section className="cad-validation-panel" style={{ margin: 0 }}>
              <h4>
                2. Control Software &amp; Policy Refinements{" "}
                <span className="assembly-status assembly-status-passed">Learned Gains</span>
              </h4>
              <p className="muted">
                Control policy parameters discovered during multi-episode obstacle world training (Episode 1 untuned baseline vs. Episode {ot.converged_episode} converged policy).
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
                {(codesign.software_refinements || []).map((sw) => (
                  <div
                    key={sw.id}
                    style={{
                      padding: 10,
                      borderRadius: 8,
                      background: "rgba(15, 23, 42, 0.55)",
                      border: "1px solid rgba(148, 163, 184, 0.22)",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <code style={{ color: "#38bdf8", fontWeight: 700, fontSize: 12.5 }}>{sw.parameter}</code>
                      <span style={{ fontSize: 12, fontFamily: "monospace", color: "#34d399", fontWeight: 700 }}>
                        {sw.before} → {sw.after}
                      </span>
                    </div>
                    <p style={{ fontSize: 12, margin: "5px 0 0", color: "#cbd5e1" }}>{sw.rationale}</p>
                  </div>
                ))}
              </div>
              {codesign.learned_controller_code && (
                <div style={{ marginTop: 12 }}>
                  <p>
                    <button
                      className="artifact-download"
                      type="button"
                      onClick={() =>
                        downloadText(
                          "obstacle_avoidance_controller.py",
                          codesign.learned_controller_code,
                          "text/x-python;charset=utf-8"
                        )
                      }
                    >
                      Download trained controller (obstacle_avoidance_controller.py)
                    </button>
                  </p>
                  <details className="cad-details">
                    <summary>Inspect learned Python control law</summary>
                    <pre className="artifact-body" style={{ maxHeight: 240, overflowY: "auto", fontSize: 11.5 }}>
                      {codesign.learned_controller_code}
                    </pre>
                  </details>
                </div>
              )}
            </section>
          </div>
          </>
        )}

        {doc.mechatronics && (
          <MechatronicsPanel
            jobId={jobId}
            mechatronics={doc.mechatronics}
            drivetrain={dt}
            onApplyCoDesignEdits={onApplyCoDesignEdits}
            rebuildingCad={rebuildingCad}
            onDownloadText={downloadText}
            onDownloadZip={(zipName, filesObj) => downloadText(zipName, zipStore(filesObj), "application/zip")}
            onAskPcbComponent={onAskPcbComponent}
          />
        )}

        <h4 style={{ marginTop: 18 }}>Assumptions</h4>
        <ul>
          {(doc.assumptions || []).map((a, i) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      </div>
    );
  }
  if (artifact.kind === "code" && doc?.schema_version === "forgebot-mechatronics-firmware/v1") {
    return (
      <div>
        <h3>Mechatronics, Custom Carrier PCB (.kicad_pcb), Wiring &amp; Vision Firmware</h3>
        <p className="muted">
          Complete hardware-to-software deployment package combining physics-coupled 3-hour battery sizing, inspectable custom carrier PCB (aligned with your FreeCAD standoffs), pin-to-pin wiring harness, and onboard camera + ultrasonic obstacle avoidance firmware.
        </p>
        <MechatronicsPanel
          jobId={jobId}
          mechatronics={{
            power_sizing: doc.power_report,
            pcb_wiring: doc.pcb_wiring,
            firmware_files: doc.files,
          }}
          drivetrain={{
            wheel_count: 2,
            wheel_diameter_m: 0.07,
          }}
          onApplyCoDesignEdits={onApplyCoDesignEdits}
          rebuildingCad={rebuildingCad}
          onDownloadText={downloadText}
          onDownloadZip={(zipName, filesObj) => downloadText(zipName, zipStore(filesObj), "application/zip")}
          onAskPcbComponent={onAskPcbComponent}
        />
      </div>
    );
  }
  if (artifact.kind === "bom") {
    if (doc?.fabrication_estimate) {
      return (
        <div>
          <FabricationEstimatorPanel bom={doc} onDownloadText={downloadText} />
        </div>
      );
    }
    const items = doc?.items || [];
    return (
      <div>
        <h3>Bill of materials <span className="muted">{artifact.schema_version}</span></h3>
        <p className="muted">Initial components list proposed by Meta model. Rebuild or approve files to run full OpenCASCADE slicing analysis.</p>
        <ul className="findings">
          {items.map((item, index) => (
            <li key={index}>
              <strong>{item.name || item.id}</strong> {item.role ? `· ${item.role}` : ""} · Qty {item.quantity || 1}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  return (
    <div>
      <h3>
        {artifact.kind} <span className="muted">{artifact.schema_version}</span>
      </h3>
      {artifact.kind === "cad" && <p><a className="artifact-download" href={`data:text/plain;charset=utf-8,${encodeURIComponent(artifact.body)}`} download="forgebot-image-concept.scad">Download OpenSCAD source</a><span className="muted"> · Open this concept file in OpenSCAD to preview or export a mesh.</span></p>}
      <pre className="artifact-body">{artifact.body}</pre>
    </div>
  );
}
