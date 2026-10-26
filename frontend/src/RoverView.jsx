import React, { useEffect, useRef } from "react";

/* Top-down preview of the rover and its waypoint route.
   Visualization only: geometry preview, not physics. */
export default function RoverView({ sim }) {
  const ref = useRef(null);
  const waypoints = sim?.waypoints || [
    [0, 0],
    [2, 0],
    [2, 2],
  ];

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    // Fit world bounds with a margin.
    const pad = 30;
    const xs = waypoints.map((p) => p[0]);
    const ys = waypoints.map((p) => p[1]);
    const minX = Math.min(...xs) - 0.6;
    const maxX = Math.max(...xs) + 0.6;
    const minY = Math.min(...ys) - 0.6;
    const maxY = Math.max(...ys) + 0.6;
    const sx = (W - pad * 2) / Math.max(maxX - minX, 0.001);
    const sy = (H - pad * 2) / Math.max(maxY - minY, 0.001);
    const s = Math.min(sx, sy);
    const X = (x) => pad + (x - minX) * s;
    const Y = (y) => H - pad - (y - minY) * s;

    // Route polyline.
    ctx.strokeStyle = "#0e7490";
    ctx.lineWidth = 2;
    ctx.beginPath();
    waypoints.forEach(([x, y], i) => {
      if (i === 0) ctx.moveTo(X(x), Y(y));
      else ctx.lineTo(X(x), Y(y));
    });
    ctx.stroke();

    // Waypoint markers.
    waypoints.forEach(([x, y], i) => {
      ctx.beginPath();
      ctx.arc(X(x), Y(y), 5, 0, Math.PI * 2);
      ctx.fillStyle = i === 0 ? "#334155" : "#0e7490";
      ctx.fill();
      ctx.fillStyle = "#0f172a";
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillText(`W${i}`, X(x) + 8, Y(y) - 6);
    });

    // Rover body at the final waypoint, facing along the last segment.
    const [ax, ay] = waypoints[waypoints.length - 2];
    const [bx, by] = waypoints[waypoints.length - 1];
    const ang = Math.atan2(-(by - ay), bx - ax); // canvas y is flipped
    ctx.save();
    ctx.translate(X(bx), Y(by));
    ctx.rotate(ang);
    const bodyL = 0.5 * s;
    const bodyW = 0.35 * s;
    ctx.fillStyle = "#1e293b";
    ctx.fillRect(-bodyL / 2, -bodyW / 2, bodyL, bodyW);
    ctx.fillStyle = "#64748b";
    ctx.fillRect(-bodyL / 4, -bodyW / 2 - 0.06 * s, bodyL / 4, 0.06 * s);
    ctx.fillRect(-bodyL / 4, bodyW / 2, bodyL / 4, 0.06 * s);
    ctx.beginPath();
    ctx.arc(bodyL / 2 - 2, 0, 3, 0, Math.PI * 2);
    ctx.fillStyle = "#94a3b8";
    ctx.fill();
    ctx.restore();
  }, [sim]);

  return (
    <figure className="viz">
      <canvas ref={ref} width={600} height={340} aria-label="Rover route preview" />
      <figcaption>
        Visualization only — route preview, not physics. Waypoints:{" "}
        {waypoints.map((p) => `(${p[0]}, ${p[1]})`).join(" → ")}
        {sim && (
          <>
            {" "}· {sim.passed ? "route passed" : "route failed"}
            {sim.collisions ? ` · ${sim.collisions} collision(s)` : ""}
          </>
        )}
      </figcaption>
    </figure>
  );
}
