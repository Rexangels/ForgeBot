import React from "react";
import RoverView from "./RoverView.jsx";

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

export function ArtifactViewer({ artifact, cadExport, onRebuildCad, rebuildingCad = false }) {
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
    return (
      <div>
        <h3>Simulation result (simulated)</h3>
        <p>
          {doc.passed ? "Route passed." : "Route failed."} Distance{" "}
          {Number(doc.distance_m).toFixed(2)} m
          {doc.collisions ? `, ${doc.collisions} collision(s)` : ", no collisions"}.
        </p>
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
        <h4>Assumptions</h4>
        <ul>
          {(doc.assumptions || []).map((a, i) => (
            <li key={i}>{a}</li>
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
