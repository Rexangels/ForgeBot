/* Same-origin JSON client for the Django Phase 1 API (via Vite /api proxy). */

function getCookie(name) {
  const m = document.cookie.match(new RegExp("(^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[2]) : "";
}

async function req(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (method !== "GET" && method !== "HEAD") {
    headers["X-CSRFToken"] = getCookie("csrftoken");
  }
  const res = await fetch(path, {
    method,
    headers,
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    throw new Error((data && data.error) || `request failed (${res.status})`);
  }
  return data;
}

export const api = {
  init: () => req("GET", "/api/v1/auth/csrf"),
  me: () => req("GET", "/api/v1/auth/me"),
  login: (username, password) =>
    req("POST", "/api/v1/auth/login", { username, password }),
  logout: () => req("POST", "/api/v1/auth/logout", {}),
  projects: () => req("GET", "/api/v1/projects/list"),
  createProject: (name) => req("POST", "/api/v1/projects/", { name }),
  jobs: (projectId) => req("GET", `/api/v1/projects/${projectId}/jobs/list`),
  createDemoJob: (projectId, robotClass, mission) =>
    req("POST", `/api/v1/projects/${projectId}/demo-jobs`, {
      idempotency_key: crypto.randomUUID(),
      input_revision: "rev-1",
      robot_class: robotClass,
      mission,
    }),
  job: (jobId) => req("GET", `/api/v1/jobs/${jobId}`),
  events: (jobId, afterId) =>
    req("GET", `/api/v1/jobs/${jobId}/events?after_id=${afterId}`),
  approve: (jobId) => req("POST", `/api/v1/jobs/${jobId}/approve`, {}),
  rebuildCad: (jobId) => req("POST", `/api/v1/jobs/${jobId}/rebuild-cad`, {}),
  simulateArm: (jobId, { trainController = false, pickTargetM, placeTargetM } = {}) => req("POST", `/api/v1/jobs/${jobId}/simulate-arm`, { train_controller: trainController, ...(pickTargetM ? { pick_target_m: pickTargetM } : {}), ...(placeTargetM ? { place_target_m: placeTargetM } : {}) }),
  benchmarkArmDesigns: (jobId) => req("POST", `/api/v1/jobs/${jobId}/benchmark-arm-designs`, {}),
  armSimulationDefaults: (jobId) => req("GET", `/api/v1/jobs/${jobId}/arm-simulation-defaults`),
  proposeArmRepair: (jobId) => req("POST", `/api/v1/jobs/${jobId}/propose-arm-repair`, {}),
  applyArmRepair: (jobId, proposal) => req("POST", `/api/v1/jobs/${jobId}/apply-arm-repair`, proposal),
  editCadSketches: (jobId, components) => req("POST", `/api/v1/jobs/${jobId}/edit-cad-sketches`, { components }),
  proposeCadSketchEdit: (jobId, componentId, instruction) => req("POST", `/api/v1/jobs/${jobId}/propose-cad-sketch-edit`, { component_id: componentId, instruction }),
  inspectComponent: (jobId, componentId, question = "") => req("POST", `/api/v1/jobs/${jobId}/inspect-component`, { component_id: componentId, question }),
  proposeDesignRevision: (jobId, instruction = "") => req("POST", `/api/v1/jobs/${jobId}/propose-design-revision`, { instruction }),
  applyDesignRevision: (jobId, proposal) => req("POST", `/api/v1/jobs/${jobId}/apply-design-revision`, { proposal }),
  artifact: (artifactId) => req("GET", `/api/v1/artifacts/${artifactId}`),
  analyzeScene: async (imageFile) => {
    const headers = { "X-CSRFToken": getCookie("csrftoken") };
    const form = new FormData();
    form.append("image", imageFile);
    const res = await fetch("/api/v1/vision/analyze", { method: "POST", headers, credentials: "include", body: form });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error(data?.error || `request failed (${res.status})`);
    return data;
  },
  saveCalibration: (projectId, { label, image_points, world_points }) =>
    req("POST", `/api/v1/projects/${projectId}/calibrations`, { label, image_points, world_points }),
  latestCalibration: (projectId) => req("GET", `/api/v1/projects/${projectId}/calibrations/latest`),
  trackDetections: (previous, current) => req("POST", "/api/v1/vision/track", { previous, current }),
  createImageDesign: async (projectId, views, { brief, targetHeightM, constraints, localOnly }) => {
    const headers = { "X-CSRFToken": getCookie("csrftoken") };
    const form = new FormData();
    views.slice(0, 3).forEach((item, index) => {
      const slot = index === 0 ? "" : `_${index + 1}`;
      form.append(index === 0 ? "image" : `image${slot}`, item.file);
      form.append(index === 0 ? "view" : `view${slot}`, item.view || "other");
    });
    form.append("brief", brief);
    form.append("target_height_m", String(targetHeightM));
    form.append("constraints", constraints);
    if (localOnly) form.append("local_only", "1");
    form.append("idempotency_key", crypto.randomUUID());
    const res = await fetch(`/api/v1/projects/${projectId}/image-design`, { method: "POST", headers, credentials: "include", body: form });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error(data?.error || `request failed (${res.status})`);
    return data;
  },
};
