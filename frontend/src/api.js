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
  createImageDesign: async (projectId, imageFile, { brief, targetHeightM, constraints }) => {
    const headers = { "X-CSRFToken": getCookie("csrftoken") };
    const form = new FormData();
    form.append("image", imageFile);
    form.append("brief", brief);
    form.append("target_height_m", String(targetHeightM));
    form.append("constraints", constraints);
    form.append("idempotency_key", crypto.randomUUID());
    const res = await fetch(`/api/v1/projects/${projectId}/image-design`, { method: "POST", headers, credentials: "include", body: form });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error(data?.error || `request failed (${res.status})`);
    return data;
  },
};
