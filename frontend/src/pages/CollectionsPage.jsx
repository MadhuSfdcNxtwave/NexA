import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import AppShell from "../components/AppShell.jsx";
import LoadingScreen from "../components/LoadingScreen.jsx";
import { IconFolder, IconPlus, IconTrash } from "../components/Icons.jsx";
import { timeAgo } from "../utils/timeago.js";

/** Hex-style Collections — named groups of projects, shown as a card grid. */
export default function CollectionsPage() {
  const [collections, setCollections] = useState(null);
  const [projects, setProjects] = useState([]);
  const [error, setError] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [openId, setOpenId] = useState(null);
  const [addingProject, setAddingProject] = useState("");
  const [showNewProject, setShowNewProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");

  const projectById = useMemo(() => {
    const map = {};
    for (const p of projects) map[p.id] = p;
    return map;
  }, [projects]);

  const load = async () => {
    try {
      const [c, p] = await Promise.all([api.listCollections(), api.listProjects()]);
      setCollections(c);
      setProjects(p);
    } catch (e) {
      setError(e.message);
      setCollections([]);
    }
  };

  useEffect(() => { load(); }, []);

  const createCollection = async () => {
    if (!newName.trim()) return;
    try {
      await api.createCollection(newName.trim(), newDesc.trim());
      setNewName("");
      setNewDesc("");
      setShowNew(false);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const removeCollection = async (e, c) => {
    e.stopPropagation();
    if (!window.confirm(`Delete collection "${c.name}"?\n\nProjects inside it are kept.`)) return;
    try {
      await api.deleteCollection(c.id);
      if (openId === c.id) setOpenId(null);
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const addProject = async (collectionId) => {
    if (!addingProject) return;
    try {
      await api.addProjectToCollection(collectionId, Number(addingProject));
      setAddingProject("");
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const removeProject = async (collectionId, projectId) => {
    try {
      await api.removeProjectFromCollection(collectionId, projectId);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const createProject = async () => {
    if (!newProjectName.trim()) return;
    try {
      await api.createProject(newProjectName.trim());
      setNewProjectName("");
      setShowNewProject(false);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const open = collections?.find((c) => c.id === openId) || null;

  return (
    <AppShell projects={projects} onNewProject={() => setShowNewProject(true)} onProjectsChange={load} fullBleed>
      <div className="hex-list-page">
        <header className="hex-list-header">
          <h1 className="hex-list-title"><IconFolder /> Collections</h1>
          <button type="button" className="primary hex-list-new" onClick={() => setShowNew(true)}>
            <IconPlus /> New collection
          </button>
        </header>

        {error && <div className="error banner-error">{error}</div>}

        {collections === null ? (
          <LoadingScreen message="Loading collections…" />
        ) : collections.length === 0 ? (
          <div className="hex-list-empty">
            <p>No collections yet — group related projects so your team can find them.</p>
            <button type="button" className="primary hex-list-new" onClick={() => setShowNew(true)}>
              <IconPlus /> New collection
            </button>
          </div>
        ) : (
          <div className="collection-grid">
            {collections.map((c, i) => (
              <button
                key={c.id}
                type="button"
                className={`collection-card ${openId === c.id ? "active" : ""}`}
                style={{ "--row-i": i }}
                onClick={() => setOpenId(openId === c.id ? null : c.id)}
              >
                <div className="collection-card-head">
                  <span className="collection-card-name">{c.name}</span>
                  <span
                    role="button"
                    tabIndex={0}
                    className="recent-delete"
                    title={`Delete ${c.name}`}
                    onClick={(e) => removeCollection(e, c)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") removeCollection(e, c);
                    }}
                  >
                    <IconTrash />
                  </span>
                </div>
                {c.description && <p className="collection-card-desc">{c.description}</p>}
                <div className="collection-card-meta">
                  <span>{c.project_count} project{c.project_count === 1 ? "" : "s"}</span>
                  {c.owner_name && (
                    <span className="hex-creator">
                      <span className="hex-avatar" aria-hidden>{c.owner_name.charAt(0).toUpperCase()}</span>
                      {c.owner_name}
                    </span>
                  )}
                  <span className="muted">{timeAgo(c.created_at)}</span>
                </div>
              </button>
            ))}
          </div>
        )}

        {open && (
          <section className="collection-detail" key={open.id}>
            <header className="collection-detail-head">
              <h2>{open.name}</h2>
              <div className="collection-detail-add">
                <select
                  className="project-select"
                  value={addingProject}
                  onChange={(e) => setAddingProject(e.target.value)}
                >
                  <option value="">Add a project…</option>
                  {projects
                    .filter((p) => !open.project_ids.includes(p.id))
                    .map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                </select>
                <button
                  type="button"
                  className="primary hex-list-new"
                  disabled={!addingProject}
                  onClick={() => addProject(open.id)}
                >
                  Add
                </button>
              </div>
            </header>

            {open.project_ids.length === 0 ? (
              <div className="hex-list-empty"><p>No projects in this collection yet.</p></div>
            ) : (
              <table className="hex-list-table stagger-rows">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Owner</th>
                    <th>Last activity</th>
                    <th aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {open.project_ids.map((pid, i) => {
                    const p = projectById[pid];
                    if (!p) return null;
                    return (
                      <tr key={pid} style={{ "--row-i": i }}>
                        <td>
                          <Link to={`/projects/${pid}`} className="hex-list-link">
                            <span className="hex-list-icon" aria-hidden>◆</span>
                            <span className="hex-list-name">{p.name}</span>
                          </Link>
                        </td>
                        <td>
                          <span className="hex-creator">
                            <span className="hex-avatar" aria-hidden>{(p.owner_name || "N").charAt(0).toUpperCase()}</span>
                            {p.owner_name || "—"}
                          </span>
                        </td>
                        <td className="muted">{timeAgo(p.last_activity_at || p.created_at)}</td>
                        <td className="hex-row-actions">
                          <button
                            type="button"
                            className="recent-delete"
                            title="Remove from collection"
                            onClick={() => removeProject(open.id, pid)}
                          >
                            ×
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>
        )}

        {showNew && (
          <div className="modal-overlay" onClick={() => setShowNew(false)}>
            <div className="modal-card" onClick={(e) => e.stopPropagation()}>
              <h3>New collection</h3>
              <p className="muted">Group related projects — e.g. a team, an initiative, or a report series.</p>
              <input
                autoFocus
                placeholder="Collection name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && createCollection()}
              />
              <input
                placeholder="Description (optional)"
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && createCollection()}
              />
              <div className="modal-actions">
                <button type="button" onClick={() => setShowNew(false)}>Cancel</button>
                <button type="button" className="primary" onClick={createCollection}>Create</button>
              </div>
            </div>
          </div>
        )}

        {showNewProject && (
          <div className="modal-overlay" onClick={() => setShowNewProject(false)}>
            <div className="modal-card" onClick={(e) => e.stopPropagation()}>
              <h3>New project</h3>
              <input
                autoFocus
                placeholder="Project name"
                value={newProjectName}
                onChange={(e) => setNewProjectName(e.target.value)}
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
