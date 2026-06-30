// All backend calls. The base URL is injected at build time by Vite from
// VITE_API_URL (set in Render's static-site env). Falls back to localhost.
const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function req(path, { method = "GET", body } = {}) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 200 ? res.json() : null;
}

export const api = {
  listProjects: () => req("/projects"),
  createProject: (name) => req("/projects", { method: "POST", body: { name } }),
  getProject: (id) => req(`/projects/${id}`),
  deleteProject: (id) => req(`/projects/${id}`, { method: "DELETE" }),

  listTables: (id) => req(`/projects/${id}/tables`),
  addTable: (id, full_table_id) =>
    req(`/projects/${id}/tables`, { method: "POST", body: { full_table_id } }),
  removeTable: (id, tableId) =>
    req(`/projects/${id}/tables/${tableId}`, { method: "DELETE" }),
  saveJoinHints: (id, join_hints) =>
    req(`/projects/${id}/join-hints`, { method: "PUT", body: { join_hints } }),
  getSchema: (id) => req(`/projects/${id}/schema`),

  getMemory: (id) => req(`/projects/${id}/memory`),
  ask: (id, question) =>
    req(`/projects/${id}/ask`, { method: "POST", body: { question } }),
};
