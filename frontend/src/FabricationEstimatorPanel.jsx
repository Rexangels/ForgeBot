import React, { useState, useMemo } from "react";

const MATERIALS = [
  { id: "pla", name: "PLA (Standard / Tough)", density: 1.24, costPerKg: 20.0, temp: 210, bedTemp: 60, tag: "Rigid & High-Detail" },
  { id: "petg", name: "PETG (Impact & Chemical)", density: 1.27, costPerKg: 22.0, temp: 240, bedTemp: 80, tag: "Tough & UV-Resistant" },
  { id: "abs", name: "ABS / ASA (Thermal Resistant)", density: 1.04, costPerKg: 24.0, temp: 255, bedTemp: 100, tag: "Heat Resistant (95°C)" },
  { id: "pa_cf", name: "PA-CF (Carbon-Fiber Nylon)", density: 1.15, costPerKg: 48.0, temp: 280, bedTemp: 90, tag: "Structural Ultra-Stiff" },
  { id: "tpu", name: "TPU 95A (Flexible Elastomer)", density: 1.21, costPerKg: 28.0, temp: 225, bedTemp: 50, tag: "Shock-Absorbing Tires/Pads" },
];

const BED_SIZES = [
  { id: "std_220", label: "220 × 220 mm (Ender 3 / Bambu A1-Mini / Prusa)", size: 220 },
  { id: "bambu_256", label: "256 × 256 mm (Bambu Lab X1 / P1 / A1)", size: 256 },
  { id: "large_300", label: "300 × 300 mm (Voron 2.4 / CR-10 / Large Format)", size: 300 },
];

export function FabricationEstimatorPanel({ bom, onDownloadText }) {
  const fab = bom?.fabrication_estimate;
  const rawParts = fab?.printed_parts || [];
  const cotsItems = fab?.cots_bom || [];

  const [selectedMaterialId, setSelectedMaterialId] = useState("pla");
  const [infillPercent, setInfillPercent] = useState(25);
  const [selectedBedSize, setSelectedBedSize] = useState(220);
  const [activeTab, setActiveTab] = useState("parts"); // 'parts' | 'cots'

  const activeMaterial = MATERIALS.find((m) => m.id === selectedMaterialId) || MATERIALS[0];

  // Dynamic live recalculation when user moves infill slider or switches material
  const calculated = useMemo(() => {
    const density = activeMaterial.density;
    const costPerG = activeMaterial.costPerKg / 1000.0;
    const infillRatio = Math.max(0.1, Math.min(1.0, infillPercent / 100.0));
    const flowRate = 14.0; // mm3/s
    const shellThicknessMm = 1.2;
    const layerHeightMm = 0.2;
    const bedDiag = Math.hypot(selectedBedSize, selectedBedSize);

    let totalSolidVolCm3 = 0;
    let totalPrintVolCm3 = 0;
    let totalMassG = 0;
    let totalPrintTimeHours = 0;
    let oversizedCount = 0;
    let diagonalCount = 0;

    const parts = rawParts.map((p) => {
      const [lx, ly, lz] = p.bounding_box_mm || [50, 50, 20];
      const sorted = [lx, ly, lz].sort((a, b) => b - a);
      const maxDim = sorted[0];
      const midDim = sorted[1];
      const heightDim = sorted[2];

      const solidVolMm3 = (p.solid_volume_cm3 || 10) * 1000.0;
      const surfAreaMm2 = 2.0 * (lx * ly + lx * lz + ly * lz);
      const shellVolMm3 = Math.min(solidVolMm3 * 0.9, surfAreaMm2 * shellThicknessMm);
      const coreVolMm3 = Math.max(0, solidVolMm3 - shellVolMm3);
      const printVolMm3 = shellVolMm3 + coreVolMm3 * infillRatio;

      const massG = (printVolMm3 / 1000.0) * density;
      const filamentM = printVolMm3 / (Math.PI * Math.pow(1.75 / 2.0, 2) * 1000.0);
      const materialCost = massG * costPerG;

      const layerCount = Math.ceil(heightDim / layerHeightMm);
      const extrusionTimeS = printVolMm3 / flowRate;
      const layerOverheadS = layerCount * 1.4;
      const timeHours = (extrusionTimeS + layerOverheadS) / 3600.0;

      const fitsNormal = maxDim <= selectedBedSize && midDim <= selectedBedSize;
      const fitsDiagonal = Math.hypot(maxDim, midDim) <= bedDiag;
      let fitStatus = "fits";
      let fitText = `✓ Fits ${selectedBedSize}mm`;

      if (!fitsNormal) {
        if (fitsDiagonal) {
          fitStatus = "diagonal";
          fitText = `⚠️ 45° Angle (${Math.hypot(maxDim, midDim).toFixed(0)}mm)`;
          diagonalCount += 1;
        } else {
          fitStatus = "exceeds";
          fitText = `❌ Exceeds (${maxDim.toFixed(0)}mm)`;
          oversizedCount += 1;
        }
      }

      totalSolidVolCm3 += solidVolMm3 / 1000.0;
      totalPrintVolCm3 += printVolMm3 / 1000.0;
      totalMassG += massG;
      totalPrintTimeHours += timeHours;

      return {
        ...p,
        recalc_mass_g: massG,
        recalc_filament_m: filamentM,
        recalc_cost_usd: materialCost,
        recalc_time_hours: timeHours,
        recalc_time_str: `${Math.floor(timeHours)}h ${Math.round((timeHours % 1) * 60)}m`,
        fit_status: fitStatus,
        fit_text: fitText,
      };
    });

    const filamentCost = totalMassG * costPerG;
    const energyKwh = totalPrintTimeHours * 0.12;
    const energyCost = energyKwh * 0.15;
    const spools = Math.ceil(totalMassG / 1000.0);

    const fastenersCost = fab?.summary?.fasteners_and_inserts_cost_usd || 14.5;
    const cotsCost = fab?.summary?.cots_electronics_cost_usd || 98.0;
    const grandTotal = filamentCost + energyCost + fastenersCost + cotsCost;

    return {
      parts,
      totalSolidVolCm3,
      totalPrintVolCm3,
      totalMassG,
      spools,
      totalPrintTimeHours,
      totalPrintTimeStr: `${Math.floor(totalPrintTimeHours)}h ${Math.round((totalPrintTimeHours % 1) * 60)}m`,
      filamentCost,
      energyCost,
      fastenersCost,
      cotsCost,
      grandTotal,
      oversizedCount,
      diagonalCount,
    };
  }, [rawParts, activeMaterial, infillPercent, selectedBedSize, fab]);

  const handleExportCsv = () => {
    if (!onDownloadText) return;
    const rows = [
      ["Component ID", "Name", "Dimensions (mm)", "Solid Vol (cm3)", "Infill %", "Filament Mass (g)", "Print Time", "Bed Fit", "Material Cost (USD)"],
      ...calculated.parts.map((p) => [
        p.component_id,
        p.name,
        p.bounding_box_mm.join("x"),
        p.solid_volume_cm3,
        infillPercent,
        p.recalc_mass_g.toFixed(1),
        p.recalc_time_str,
        p.fit_text,
        p.recalc_cost_usd.toFixed(2),
      ]),
      [],
      ["COTS Items & Electronics"],
      ["Category", "Name", "Part Number", "Supplier", "Qty", "Unit (USD)", "Total (USD)"],
      ...cotsItems.map((c) => [c.category, c.name, c.part_number, c.supplier, c.quantity, c.unit_price_usd.toFixed(2), c.total_price_usd.toFixed(2)]),
      [],
      ["Grand Total Build Cost (USD)", calculated.grandTotal.toFixed(2)],
    ];
    const csvContent = rows.map((r) => r.map((cell) => `"${String(cell || "").replace(/"/g, '""')}"`).join(",")).join("\n");
    onDownloadText("forgebot_fabrication_bom.csv", csvContent, "text/csv;charset=utf-8");
  };

  return (
    <div className="fabrication-estimator-panel" style={{ color: "#e2e8f0" }}>
      {/* Top Banner & Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12, marginBottom: 18 }}>
        <div>
          <div className="fabrication-engine-badge">
            <svg aria-hidden="true" viewBox="0 0 16 16"><path d="M9.2 1.5 3.8 8h3.5l-.5 6.5L12.2 7H8.7z" /></svg>
            <span>OPEN CASCADE SLICING ENGINE</span>
          </div>
          <h3 style={{ margin: 0, fontSize: 20 }}>3D Printability, Slicing Physics &amp; Fabrication Cost</h3>
          <p className="muted" style={{ margin: "4px 0 0 0", fontSize: 13 }}>
            Exact solid volumes integrated from FreeCAD OpenCASCADE solids. Dynamic live slicing model with real market spool &amp; COTS pricing.
          </p>
        </div>
        <button
          className="artifact-download fabrication-download-button"
          type="button"
          onClick={handleExportCsv}
        >
          <svg aria-hidden="true" viewBox="0 0 16 16"><path d="M8 1.8v8.1m-3-3 3 3 3-3M2.5 11.5v2h11v-2" /></svg>
          Download Fabrication BOM (.csv)
        </button>
      </div>

      {/* Live Interactive Slicer Sliders & Controls */}
      <div className="fabrication-controls" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 14, background: "rgba(15, 23, 42, 0.75)", border: "1px solid rgba(148, 163, 184, 0.2)", borderRadius: 10, padding: 16, marginBottom: 18 }}>
        <div className="fabrication-control">
          <label style={{ display: "block", fontSize: 11.5, fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 }}>
            Filament Material ({activeMaterial.tag})
          </label>
          <select
            value={selectedMaterialId}
            onChange={(e) => setSelectedMaterialId(e.target.value)}
            style={{ width: "100%", background: "#1e293b", color: "#f8fafc", border: "1px solid #475569", borderRadius: 6, padding: "7px 10px", fontSize: 13 }}
          >
            {MATERIALS.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name} · ${m.costPerKg.toFixed(0)}/kg ({m.density} g/cm³)
              </option>
            ))}
          </select>
        </div>

        <div className="fabrication-control infill-control">
          <div className="fabrication-control-label-row" style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
            <label style={{ fontSize: 11.5, fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 0.5 }}>
              Infill Density: <strong style={{ color: "#38bdf8" }}>{infillPercent}%</strong>
            </label>
            <span style={{ fontSize: 11, color: "#cbd5e1" }}>Gyroid / Grid</span>
          </div>
            <input
            type="range"
            min="10"
            max="100"
            step="5"
            value={infillPercent}
            onChange={(e) => setInfillPercent(Number(e.target.value))}
            style={{ width: "100%", accentColor: "#0b7a6f", cursor: "pointer" }}
          />
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "#64748b" }}>
            <span>10% (Draft)</span>
            <span>25% (Robotics Standard)</span>
            <span>100% (Solid)</span>
          </div>
        </div>

        <div className="fabrication-control">
          <label style={{ display: "block", fontSize: 11.5, fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 6 }}>
            Target 3D Print Bed Volume
          </label>
          <select
            value={selectedBedSize}
            onChange={(e) => setSelectedBedSize(Number(e.target.value))}
            style={{ width: "100%", background: "#1e293b", color: "#f8fafc", border: "1px solid #475569", borderRadius: 6, padding: "7px 10px", fontSize: 13 }}
          >
            {BED_SIZES.map((b) => (
              <option key={b.id} value={b.size}>
                {b.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* KPI Cards Banner */}
      <div className="fabrication-kpis" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12, marginBottom: 20 }}>
        <div style={{ background: "linear-gradient(135deg, rgba(30, 41, 59, 0.9), rgba(15, 23, 42, 0.95))", border: "1px solid rgba(56, 189, 248, 0.4)", borderRadius: 10, padding: "14px 16px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#38bdf8", textTransform: "uppercase", letterSpacing: 0.5 }}>Grand Total Build Cost</div>
          <div style={{ fontSize: 26, fontWeight: 800, color: "#ffffff", marginTop: 4 }}>${calculated.grandTotal.toFixed(2)}</div>
          <small style={{ color: "#94a3b8", fontSize: 11 }}>3D Print + COTS + Fasteners</small>
        </div>

        <div style={{ background: "rgba(30, 41, 59, 0.8)", border: "1px solid rgba(148, 163, 184, 0.2)", borderRadius: 10, padding: "14px 16px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 0.5 }}>Total Filament Mass</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: "#f8fafc", marginTop: 4 }}>
            {calculated.totalMassG.toFixed(0)} g <span style={{ fontSize: 13, fontWeight: 500, color: "#cbd5e1" }}>({(calculated.totalMassG / 1000).toFixed(2)} kg)</span>
          </div>
          <small style={{ color: "#38bdf8", fontSize: 11 }}>{calculated.spools} × 1 kg spool required (${calculated.filamentCost.toFixed(2)})</small>
        </div>

        <div style={{ background: "rgba(30, 41, 59, 0.8)", border: "1px solid rgba(148, 163, 184, 0.2)", borderRadius: 10, padding: "14px 16px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 0.5 }}>Total Print Duration</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: "#f8fafc", marginTop: 4 }}>{calculated.totalPrintTimeStr}</div>
          <small style={{ color: "#94a3b8", fontSize: 11 }}>@ 14 mm³/s flow (${calculated.energyCost.toFixed(2)} power)</small>
        </div>

        <div style={{ background: "rgba(30, 41, 59, 0.8)", border: "1px solid rgba(148, 163, 184, 0.2)", borderRadius: 10, padding: "14px 16px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 0.5 }}>Bed Fit Status ({selectedBedSize}mm)</div>
          <div style={{ fontSize: 17, fontWeight: 700, color: calculated.oversizedCount === 0 ? "#10b981" : "#f59e0b", marginTop: 4 }}>
            {calculated.oversizedCount === 0 ? "✓ 100% Fit Standard Bed" : `⚠️ ${calculated.oversizedCount} Parts Oversized`}
          </div>
          <small style={{ color: "#94a3b8", fontSize: 11 }}>
            {calculated.oversizedCount === 0 ? "Ready to slice on Bambu / Ender 3" : "Requires splitting with dowels"}
          </small>
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="fabrication-tabs">
        <button
          className={`fabrication-tab ${activeTab === "parts" ? "active" : ""}`}
          aria-pressed={activeTab === "parts"}
          type="button"
          onClick={() => setActiveTab("parts")}
        >
          <svg aria-hidden="true" viewBox="0 0 16 16"><path d="m8 1.5 5.5 3.2v6.6L8 14.5l-5.5-3.2V4.7zM2.5 4.7 8 8l5.5-3.3M8 8v6.5" /></svg>
          3D Printed Parts &amp; Slicing Table ({calculated.parts.length} solids)
        </button>
        <button
          className={`fabrication-tab ${activeTab === "cots" ? "active" : ""}`}
          aria-pressed={activeTab === "cots"}
          type="button"
          onClick={() => setActiveTab("cots")}
        >
          <svg aria-hidden="true" viewBox="0 0 16 16"><path d="M9.2 1.5 3.8 8h3.5l-.5 6.5L12.2 7H8.7z" /></svg>
          COTS Electronics, PCB &amp; Fasteners (${(calculated.cotsCost + calculated.fastenersCost).toFixed(2)})
        </button>
      </div>

      {/* Tab 1: 3D Printed Parts Table */}
      {activeTab === "parts" && (
        <div style={{ overflowX: "auto", background: "rgba(15, 23, 42, 0.6)", borderRadius: 8, border: "1px solid rgba(148, 163, 184, 0.2)" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5, textAlign: "left" }}>
            <thead>
              <tr style={{ background: "rgba(30, 41, 59, 0.9)", borderBottom: "1px solid rgba(148, 163, 184, 0.2)" }}>
                <th style={{ padding: "10px 12px" }}>Component / Role</th>
                <th style={{ padding: "10px 12px" }}>Dimensions (mm)</th>
                <th style={{ padding: "10px 12px" }}>Solid Vol (cm³)</th>
                <th style={{ padding: "10px 12px" }}>Printed Mass</th>
                <th style={{ padding: "10px 12px" }}>Print Time</th>
                <th style={{ padding: "10px 12px" }}>Bed Fit ({selectedBedSize}mm)</th>
                <th style={{ padding: "10px 12px", textAlign: "right" }}>Cost ({activeMaterial.name.split(" ")[0]})</th>
              </tr>
            </thead>
            <tbody>
              {calculated.parts.map((p, idx) => (
                <tr
                  key={p.component_id}
                  style={{
                    borderBottom: "1px solid rgba(148, 163, 184, 0.1)",
                    background: idx % 2 === 0 ? "transparent" : "rgba(30, 41, 59, 0.35)",
                  }}
                >
                  <td style={{ padding: "9px 12px" }}>
                    <strong>{p.name}</strong>
                    <div style={{ fontSize: 11, color: "#94a3b8" }}>
                      <code>{p.component_id}</code> · {p.role}
                    </div>
                  </td>
                  <td style={{ padding: "9px 12px", color: "#cbd5e1" }}>
                    {p.bounding_box_mm.map((v) => v.toFixed(0)).join(" × ")} mm
                  </td>
                  <td style={{ padding: "9px 12px" }}>
                    <span>{p.solid_volume_cm3.toFixed(1)} cm³</span>
                    {p.is_opencascade_exact && (
                      <span style={{ display: "block", fontSize: 9.5, color: "#10b981", fontWeight: 700 }}>
                        ✓ OpenCASCADE
                      </span>
                    )}
                  </td>
                  <td style={{ padding: "9px 12px", color: "#f8fafc", fontWeight: 600 }}>
                    {p.recalc_mass_g.toFixed(1)} g
                    <div style={{ fontSize: 10, color: "#64748b" }}>{p.recalc_filament_m.toFixed(1)}m length</div>
                  </td>
                  <td style={{ padding: "9px 12px", color: "#cbd5e1" }}>{p.recalc_time_str}</td>
                  <td style={{ padding: "9px 12px" }}>
                    <span
                      style={{
                        display: "inline-block",
                        padding: "2px 8px",
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 600,
                        background:
                          p.fit_status === "fits"
                            ? "rgba(16, 185, 129, 0.15)"
                            : p.fit_status === "diagonal"
                            ? "rgba(245, 158, 11, 0.15)"
                            : "rgba(239, 68, 68, 0.15)",
                        color:
                          p.fit_status === "fits"
                            ? "#10b981"
                            : p.fit_status === "diagonal"
                            ? "#f59e0b"
                            : "#ef4444",
                        border:
                          p.fit_status === "fits"
                            ? "1px solid rgba(16, 185, 129, 0.4)"
                            : p.fit_status === "diagonal"
                            ? "1px solid rgba(245, 158, 11, 0.4)"
                            : "1px solid rgba(239, 68, 68, 0.4)",
                      }}
                    >
                      {p.fit_text}
                    </span>
                  </td>
                  <td style={{ padding: "9px 12px", textAlign: "right", fontWeight: 700, color: "#38bdf8" }}>
                    ${p.recalc_cost_usd.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Tab 2: Commercial Electronics & Fasteners BOM */}
      {activeTab === "cots" && (
        <div style={{ overflowX: "auto", background: "rgba(15, 23, 42, 0.6)", borderRadius: 8, border: "1px solid rgba(148, 163, 184, 0.2)" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5, textAlign: "left" }}>
            <thead>
              <tr style={{ background: "rgba(30, 41, 59, 0.9)", borderBottom: "1px solid rgba(148, 163, 184, 0.2)" }}>
                <th style={{ padding: "10px 12px" }}>Category</th>
                <th style={{ padding: "10px 12px" }}>Part Name / Specification</th>
                <th style={{ padding: "10px 12px" }}>Part Number</th>
                <th style={{ padding: "10px 12px" }}>Distributor / Retailer</th>
                <th style={{ padding: "10px 12px", textAlign: "center" }}>Qty</th>
                <th style={{ padding: "10px 12px", textAlign: "right" }}>Unit Price</th>
                <th style={{ padding: "10px 12px", textAlign: "right" }}>Total</th>
              </tr>
            </thead>
            <tbody>
              {/* Fasteners row */}
              <tr style={{ borderBottom: "1px solid rgba(148, 163, 184, 0.15)", background: "rgba(56, 189, 248, 0.05)" }}>
                <td style={{ padding: "9px 12px", fontWeight: 700, color: "#38bdf8" }}>Hardware Fasteners</td>
                <td style={{ padding: "9px 12px" }}>
                  <strong>M3 Stainless Socket Cap Screws &amp; Brass Heat-Set Threaded Inserts</strong>
                  <div style={{ fontSize: 11, color: "#94a3b8" }}>M3×10mm DIN 912 fasteners + M3×4×5mm Ruthex knurled inserts</div>
                </td>
                <td style={{ padding: "9px 12px", color: "#cbd5e1" }}>M3-RUTHEX-KIT</td>
                <td style={{ padding: "9px 12px", color: "#94a3b8" }}>McMaster-Carr / Amazon</td>
                <td style={{ padding: "9px 12px", textAlign: "center", fontWeight: 700 }}>
                  {fab?.summary?.fastener_screw_count || 16} pairs
                </td>
                <td style={{ padding: "9px 12px", textAlign: "right" }}>$0.20 / pair</td>
                <td style={{ padding: "9px 12px", textAlign: "right", fontWeight: 700, color: "#f8fafc" }}>
                  ${calculated.fastenersCost.toFixed(2)}
                </td>
              </tr>

              {/* COTS items rows */}
              {cotsItems.map((item, idx) => (
                <tr
                  key={item.part_number + idx}
                  style={{
                    borderBottom: "1px solid rgba(148, 163, 184, 0.1)",
                    background: idx % 2 === 0 ? "transparent" : "rgba(30, 41, 59, 0.35)",
                  }}
                >
                  <td style={{ padding: "9px 12px", color: "#94a3b8", fontWeight: 600 }}>{item.category}</td>
                  <td style={{ padding: "9px 12px" }}>
                    <strong>{item.name}</strong>
                  </td>
                  <td style={{ padding: "9px 12px", color: "#38bdf8", fontFamily: "monospace" }}>{item.part_number}</td>
                  <td style={{ padding: "9px 12px", color: "#94a3b8" }}>{item.supplier}</td>
                  <td style={{ padding: "9px 12px", textAlign: "center", fontWeight: 700 }}>{item.quantity}</td>
                  <td style={{ padding: "9px 12px", textAlign: "right" }}>${item.unit_price_usd.toFixed(2)}</td>
                  <td style={{ padding: "9px 12px", textAlign: "right", fontWeight: 700, color: "#f8fafc" }}>
                    ${item.total_price_usd.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
