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

export function ArtifactViewer({ artifact }) {
  if (!artifact) {
    return <p className="muted">Select an artifact to inspect it.</p>;
  }
  const doc = tryParse(artifact.body);
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
