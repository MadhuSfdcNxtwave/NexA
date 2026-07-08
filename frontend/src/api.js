// All backend calls. The base URL is injected at build time by Vite from
// VITE_API_URL (set in Render's static-site env). Falls back to localhost.
import { getToken, clearSession } from "./auth.js";

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

function headers(json = false, { auth = true } = {}) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  if (auth) {
    const token = getToken();
    if (token) h.Authorization = `Bearer ${token}`;
  }
  return h;
}

function handleUnauthorized(res, path = "") {
  if (res.status !== 401) return false;
  // Login failures are 401 too — show the real error, not "session expired".
  if (path === "/auth/login") return false;
  clearSession();
  if (!window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
  return true;
}

async function req(path, { method = "GET", body, auth = true } = {}) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: headers(Boolean(body), { auth }),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (handleUnauthorized(res, path)) {
    throw new Error("Session expired — please sign in again");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const err = await res.json();
      detail = err.detail || detail;
    } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (_) {
    return null;
  }
}

export const api = {
  login: (email, password) =>
    req("/auth/login", { method: "POST", body: { email, password }, auth: false }),
  me: () => req("/auth/me"),
  adminListUsers: () => req("/admin/users"),
  adminCreateUser: (body) => req("/admin/users", { method: "POST", body }),
  adminUpdateUser: (id, body) => req(`/admin/users/${id}`, { method: "PATCH", body }),
  adminDeleteUser: (id) => req(`/admin/users/${id}`, { method: "DELETE" }),
  adminUsage: (limit = 100) => req(`/admin/usage?limit=${limit}`),
  getOrgSchema: () => req("/admin/org-schema"),
  rebuildOrgSchema: () => req("/admin/org-schema/rebuild", { method: "POST" }),
  getOrgSchemaMarkdown: () => req("/admin/org-schema/markdown"),

  listProjects: () => req("/projects"),
  createProject: (name) => req("/projects", { method: "POST", body: { name } }),
  getProject: (id) => req(`/projects/${id}`),
  deleteProject: (id) => req(`/projects/${id}`, { method: "DELETE" }),
  trackProjectView: (id) => req(`/projects/${id}/view`, { method: "POST" }),
  listThreads: () => req("/threads"),
  createStandaloneThread: (title = "") =>
    req("/threads", { method: "POST", body: { title } }),
  getThread: (threadId) => req(`/threads/${threadId}`),
  getThreadMemory: (threadId) => req(`/threads/${threadId}/memory`),
  clearThreadMemory: (threadId) =>
    req(`/threads/${threadId}/memory`, { method: "DELETE" }),
  deleteStandaloneThread: (threadId) =>
    req(`/threads/${threadId}`, { method: "DELETE" }),
  renameStandaloneThread: (threadId, title) =>
    req(`/threads/${threadId}`, { method: "PATCH", body: { title } }),
  askThreadStream: async (threadId, question, onEvent, opts = {}) => {
    const {
      forceFresh = false,
      clarificationChoice,
      clarificationText,
      refinedQuestion,
    } = opts;
    const res = await fetch(`${BASE}/threads/${threadId}/ask/stream`, {
      method: "POST",
      headers: headers(true),
      body: JSON.stringify({
        question,
        force_fresh: forceFresh,
        clarification_choice: clarificationChoice || null,
        clarification_text: clarificationText || null,
        refined_question: refinedQuestion || null,
      }),
    });
    if (handleUnauthorized(res)) {
      throw new Error("Session expired — please sign in again");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalResult = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        const event = JSON.parse(line.slice(6));
        onEvent?.(event);
        if (event.type === "complete") finalResult = event;
        if (event.type === "error") throw new Error(event.message || "Ask failed");
      }
    }
    return finalResult;
  },
  askThreadConfirmStream: async (threadId, question, sql, onEvent) => {
    const res = await fetch(`${BASE}/threads/${threadId}/ask/confirm/stream`, {
      method: "POST",
      headers: headers(true),
      body: JSON.stringify({ question, sql }),
    });
    if (handleUnauthorized(res)) {
      throw new Error("Session expired — please sign in again");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalResult = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        const event = JSON.parse(line.slice(6));
        onEvent?.(event);
        if (event.type === "complete") finalResult = event;
        if (event.type === "error") throw new Error(event.message || "Ask failed");
      }
    }
    return finalResult;
  },
  listCollections: () => req("/collections"),
  createCollection: (name, description = "") =>
    req("/collections", { method: "POST", body: { name, description } }),
  updateCollection: (id, patch) =>
    req(`/collections/${id}`, { method: "PATCH", body: patch }),
  deleteCollection: (id) => req(`/collections/${id}`, { method: "DELETE" }),
  addProjectToCollection: (id, projectId) =>
    req(`/collections/${id}/projects`, { method: "POST", body: { project_id: projectId } }),
  removeProjectFromCollection: (id, projectId) =>
    req(`/collections/${id}/projects/${projectId}`, { method: "DELETE" }),

  listProjectThreads: (id) => req(`/projects/${id}/threads`),
  createThread: (id, title = "") =>
    req(`/projects/${id}/threads`, { method: "POST", body: { title } }),
  renameThread: (id, threadId, title) =>
    req(`/projects/${id}/threads/${threadId}`, { method: "PATCH", body: { title } }),
  deleteThread: (id, threadId) =>
    req(`/projects/${id}/threads/${threadId}`, { method: "DELETE" }),

  listWorkspaceTables: () => req("/workspace/tables"),
  addWorkspaceTable: (full_table_id) =>
    req("/workspace/tables", { method: "POST", body: { full_table_id } }),
  updateWorkspaceTable: (tableId, patch) =>
    req(`/workspace/tables/${tableId}`, { method: "PATCH", body: patch }),
  removeWorkspaceTable: (tableId) =>
    req(`/workspace/tables/${tableId}`, { method: "DELETE" }),
  refreshTableAiOverview: (tableId) =>
    req(`/workspace/tables/${tableId}/ai-overview`, { method: "POST" }),
  getWorkspaceJoinHints: () => req("/workspace/join-hints"),
  saveWorkspaceJoinHints: (join_hints) =>
    req("/workspace/join-hints", { method: "PUT", body: { join_hints } }),
  getTableJoinHints: (tableId) => req(`/workspace/tables/${tableId}/join-hints`),
  saveTableJoinHints: (tableId, join_hints) =>
    req(`/workspace/tables/${tableId}/join-hints`, { method: "PUT", body: { join_hints } }),
  importWorkspaceModels: (body) =>
    req("/workspace/models/import", { method: "POST", body }),

  listTables: (id) => req(`/projects/${id}/tables`),
  addTable: (id, full_table_id) =>
    req(`/projects/${id}/tables`, { method: "POST", body: { full_table_id } }),
  updateTable: (id, tableId, patch) =>
    req(`/projects/${id}/tables/${tableId}`, { method: "PATCH", body: patch }),
  removeTable: (id, tableId) =>
    req(`/projects/${id}/tables/${tableId}`, { method: "DELETE" }),
  saveJoinHints: (id, join_hints) =>
    req(`/projects/${id}/join-hints`, { method: "PUT", body: { join_hints } }),
  importModels: (id, body) =>
    req(`/projects/${id}/models/import`, { method: "POST", body }),
  getSchema: (id) => req(`/projects/${id}/schema`),

  getMemory: (id, threadId = null) =>
    req(`/projects/${id}/memory${threadId ? `?thread_id=${threadId}` : ""}`),
  clearMemory: (id, threadId = null) =>
    req(`/projects/${id}/memory${threadId ? `?thread_id=${threadId}` : ""}`, { method: "DELETE" }),
  ask: (id, question) =>
    req(`/projects/${id}/ask`, { method: "POST", body: { question } }),
  askStream: async (id, question, onEvent, { forceFresh = false, clarificationChoice, clarificationText, refinedQuestion, threadId = null } = {}) => {
    const res = await fetch(`${BASE}/projects/${id}/ask/stream`, {
      method: "POST",
      headers: headers(true),
      body: JSON.stringify({
        question,
        thread_id: threadId,
        force_fresh: forceFresh,
        clarification_choice: clarificationChoice || null,
        clarification_text: clarificationText || null,
        refined_question: refinedQuestion || null,
      }),
    });
    if (handleUnauthorized(res)) {
      throw new Error("Session expired — please sign in again");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalResult = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        const event = JSON.parse(line.slice(6));
        onEvent?.(event);
        if (event.type === "complete") finalResult = event;
        if (event.type === "error") throw new Error(event.message || "Ask failed");
      }
    }
    return finalResult;
  },
  askConfirmStream: async (id, question, sql, onEvent, { threadId = null } = {}) => {
    const res = await fetch(`${BASE}/projects/${id}/ask/confirm/stream`, {
      method: "POST",
      headers: headers(true),
      body: JSON.stringify({ question, sql, thread_id: threadId }),
    });
    if (handleUnauthorized(res)) {
      throw new Error("Session expired — please sign in again");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalResult = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        const event = JSON.parse(line.slice(6));
        onEvent?.(event);
        if (event.type === "complete") finalResult = event;
        if (event.type === "error") throw new Error(event.message || "Ask failed");
      }
    }
    return finalResult;
  },

  getDashboard: (id, refresh = false, inputOverrides = null) => {
    const params = new URLSearchParams({ refresh: refresh ? "true" : "false" });
    if (inputOverrides) {
      for (const [k, v] of Object.entries(inputOverrides)) {
        if (v != null && String(v).trim()) params.set(k, String(v).trim());
      }
    }
    return req(`/projects/${id}/dashboard?${params}`);
  },
  getApp: (id, refresh = false, inputOverrides = null) => {
    const params = new URLSearchParams({ refresh: refresh ? "true" : "false" });
    if (inputOverrides) {
      for (const [k, v] of Object.entries(inputOverrides)) {
        if (v != null && String(v).trim()) params.set(k, String(v).trim());
      }
    }
    return req(`/projects/${id}/app?${params}`);
  },
  updateApp: (id, body) => req(`/projects/${id}/app`, { method: "PATCH", body }),
  addDashboardItem: (id, body) =>
    req(`/projects/${id}/dashboard`, { method: "POST", body }),
  addDashboardFromNotebook: (id, cellId) =>
    req(`/projects/${id}/dashboard/from-notebook/${cellId}`, { method: "POST" }),
  removeDashboardItem: (id, itemId) =>
    req(`/projects/${id}/dashboard/${itemId}`, { method: "DELETE" }),
  publishDashboard: (id) =>
    req(`/projects/${id}/dashboard/publish`, { method: "POST" }),

  getSharedDashboard: (token, inputOverrides = null) => {
    const params = new URLSearchParams();
    if (inputOverrides) {
      for (const [k, v] of Object.entries(inputOverrides)) {
        if (v != null && String(v).trim()) params.set(k, String(v).trim());
      }
    }
    const qs = params.toString();
    return req(`/shared/${encodeURIComponent(token)}${qs ? `?${qs}` : ""}`);
  },

  updateProjectSettings: (id, body) =>
    req(`/projects/${id}/settings`, { method: "PATCH", body }),
  enableNotebook: (id) =>
    req(`/projects/${id}/notebook/enable`, { method: "POST" }),

  listNotebookCells: (id) => req(`/projects/${id}/notebook/cells`),
  createNotebookCell: (id, body) =>
    req(`/projects/${id}/notebook/cells`, { method: "POST", body }),
  updateNotebookCell: (id, cellId, body) =>
    req(`/projects/${id}/notebook/cells/${cellId}`, { method: "PATCH", body }),
  deleteNotebookCell: (id, cellId) =>
    req(`/projects/${id}/notebook/cells/${cellId}`, { method: "DELETE" }),
  runNotebook: (id, body = {}) =>
    req(`/projects/${id}/notebook/run`, { method: "POST", body }),
  getNotebookGraph: (id) => req(`/projects/${id}/notebook/graph`),

  getSetupStatus: () => req("/setup/status"),

  // Warehouse — all datasets/tables the service account can access
  listDatasets: () => req("/warehouse/datasets"),
  getWarehouseCatalog: () => req("/warehouse/catalog"),
  listWarehouseTables: (dataset) => req(`/warehouse/tables?dataset=${encodeURIComponent(dataset)}`),
  getTableMetadata: (full_table_id) =>
    req(`/warehouse/table/metadata?full_table_id=${encodeURIComponent(full_table_id)}`),
  previewTable: (full_table_id, limit = 25) =>
    req(`/warehouse/table/preview?full_table_id=${encodeURIComponent(full_table_id)}&limit=${limit}`),
};
