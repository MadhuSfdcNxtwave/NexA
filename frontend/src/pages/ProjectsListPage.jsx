import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import AppShell from "../components/AppShell.jsx";
import LoadingScreen from "../components/LoadingScreen.jsx";
import { IconFolder, IconPlus, IconTrash } from "../components/Icons.jsx";
import { timeAgo } from "../utils/timeago.js";

const STATUS_OPTIONS = ["", "In progress", "In monitoring", "Completed", "Archived"];

const statusClass = (s) => {
  const v = (s || "").toLowerCase();
  if (v === "active" || v === "completed") return "active";
  if (v === "archived") return "muted-status";
  return "progress";
};

/** Hex-style Projects list — status, owner, views, last activity. */
export default function ProjectsListPage() {
  const [projects, setProjects] = useState(null);
  const [showNewProject, setShowNewProject] = useState(false);
  const [newName, setNewName] = useState("");
  const [deletingId, setDeletingId] = useState(null);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const load = async () => {
    try {
      setProjects(await api.listProjects());
    } catch (e) {
      setError(e.message);
      setProjects([]);
    }
  };

  useEffect(() => { load(); }, []);

  const createProject = async () => {
    if (!newName.trim()) return;
    const p = await api.createProject(newName.trim());
    setShowNewProject(false);
    setNewName("");
    navigate(`/projects/${p.id}`);
  };

  const setStatus = async (p, status) => {
    try {
      await api.updateProjectSettings(p.id, { status });
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const deleteProject = async (e, p) => {
    e.stopPropagation();
    if (!window.confirm(`Delete "${p.name}"?\n\nThreads, notebook cells, and dashboard widgets will be removed.`)) return;
    setDeletingId(p.id);
    try {
      await api.deleteProject(p.id);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <AppShell projects={projects || []} onNewProject={() => setShowNewProject(true)} onProjectsChange={load} fullBleed>
      <div className="hex-list-page">
        <header className="hex-list-header">
          <h1 className="hex-list-title"><IconFolder /> Projects</h1>
          <button type="button" className="primary hex-list-new" onClick={() => setShowNewProject(true)}>
            <IconPlus /> New project
          </button>
        </header>

        {error && <div className="error banner-error">{error}</div>}

        {projects === null ? (
          <LoadingScreen message="Loading projects…" />
        ) : projects.length === 0 ? (
          <div className="hex-list-empty">
            <p>No projects yet — create one to get started.</p>
          </div>
        ) : (
          <table className="hex-list-table stagger-rows">
            <thead>
              <tr>
                <th>Title</th>
                <th>Status</th>
                <th>Owner</th>
                <th>Last activity</th>
                <th>Last viewed</th>
                <th className="num">Views</th>
                <th className="num">Turns</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {projects.map((p, i) => (
                <tr key={p.id} style={{ "--row-i": i }} onClick={() => navigate(`/projects/${p.id}`)}>
                  <td>
                    <Link to={`/projects/${p.id}`} className="hex-list-link" onClick={(e) => e.stopPropagation()}>
                      <span className="hex-list-icon" aria-hidden>◆</span>
                      <span className="hex-list-name">{p.name}</span>
                    </Link>
                    {p.categories?.length > 0 && (
                      <span className="hex-list-sub">
                        {p.categories.map((c) => (
                          <span key={c} className="hex-category-chip">{c}</span>
                        ))}
                      </span>
                    )}
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <select
                      className={`hex-status-select ${statusClass(p.status)}`}
                      value={STATUS_OPTIONS.includes(p.status) ? p.status : ""}
                      onChange={(e) => setStatus(p, e.target.value)}
                      title="Set project status"
                    >
                      <option value="">{p.status && !STATUS_OPTIONS.includes(p.status) ? p.status : "No status"}</option>
                      {STATUS_OPTIONS.filter(Boolean).map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <span className="hex-creator">
                      <span className="hex-avatar" aria-hidden>{(p.owner_name || "N").charAt(0).toUpperCase()}</span>
                      {p.owner_name || "—"}
                    </span>
                  </td>
                  <td className="muted">{timeAgo(p.last_activity_at || p.created_at)}</td>
                  <td className="muted">{timeAgo(p.last_viewed_at)}</td>
                  <td className="num muted">{p.view_count > 0 ? `↗ ${p.view_count}` : "—"}</td>
                  <td className="num muted">{p.thread_count}</td>
                  <td className="hex-row-actions">
                    <button
                      type="button"
                      className="recent-delete"
                      title={`Delete ${p.name}`}
                      disabled={deletingId === p.id}
                      onClick={(e) => deleteProject(e, p)}
                    >
                      <IconTrash />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {showNewProject && (
          <div className="modal-overlay" onClick={() => setShowNewProject(false)}>
            <div className="modal-card" onClick={(e) => e.stopPropagation()}>
              <h3>New project</h3>
              <input
                autoFocus
                placeholder="Project name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && createProject()}
              />
              <div className="modal-actions">
                <button type="button" onClick={() => setShowNewProject(false)}>Cancel</button>
                <button type="button" className="primary" onClick={createProject}>Create</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
