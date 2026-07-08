import { Link, useLocation, useNavigate } from "react-router-dom";
import { useState, useMemo } from "react";
import { api } from "../api.js";
import SettingsModal from "./SettingsModal.jsx";
import { clearSession, isAdmin } from "../auth.js";
import { getFavorites, isFavorite, toggleFavorite } from "../favorites.js";
import {
  IconHome, IconSearch, IconFolder, IconThread, IconData, IconSettings, IconChevron, IconPlus, IconBell, IconTrash,
} from "./Icons.jsx";

export default function Sidebar({
  projects = [],
  activeProjectId,
  onNewProject,
  onProjectsChange,
}) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [deletingId, setDeletingId] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [favoriteIds, setFavoriteIds] = useState(() => getFavorites());

  const favoriteProjects = useMemo(
    () => projects.filter((p) => favoriteIds.includes(p.id)),
    [projects, favoriteIds]
  );

  const isHome = pathname === "/";
  const isData = pathname.includes("/data");
  const isProjectsList = pathname === "/projects";
  const isThreadsList = pathname === "/threads";
  const isCollections = pathname === "/collections";
  const isOrgSchema = pathname === "/org-schema";

  const dataHref = "/data";

  const q = search.trim().toLowerCase();
  const filteredProjects = useMemo(
    () => (q ? projects.filter((p) => p.name.toLowerCase().includes(q)) : projects),
    [projects, q]
  );

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2200);
  };

  const openThread = () => {
    navigate("/threads");
  };

  const deleteProject = async (e, p) => {
    e.preventDefault();
    e.stopPropagation();
    if (
      !window.confirm(
        `Delete "${p.name}"?\n\nThreads, notebook cells, and dashboard widgets in this project will be removed. Workspace tables, column descriptions, and join hints are kept for all projects.`
      )
    ) {
      return;
    }
    setDeletingId(p.id);
    try {
      await api.deleteProject(p.id);
      onProjectsChange?.();
      showToast(`Deleted "${p.name}"`);
      if (Number(activeProjectId) === p.id || pathname.includes(`/projects/${p.id}`)) {
        navigate(pathname.includes("/data") ? "/data" : "/");
      }
    } catch (err) {
      alert(err.message || "Could not delete project");
    } finally {
      setDeletingId(null);
    }
  };

  const ProjectRow = ({ p, showFavoriteToggle = true }) => {
    const starred = isFavorite(p.id);
    return (
    <div className={`hex-recent-row ${activeProjectId === p.id ? "active" : ""}`}>
      {showFavoriteToggle && (
        <button
          type="button"
          className={`favorite-btn ${starred ? "active" : ""}`}
          title={starred ? "Remove from favorites" : "Add to favorites"}
          aria-label={starred ? "Remove from favorites" : "Add to favorites"}
          onClick={() => {
            toggleFavorite(p.id);
            setFavoriteIds(getFavorites());
          }}
        >
          {starred ? "★" : "☆"}
        </button>
      )}
      <Link to={`/projects/${p.id}`} className="hex-recent-item">
        <span className={`recent-icon ${starred ? "star" : ""}`}>
          {starred ? "★" : p.name.charAt(0).toUpperCase()}
        </span>
        <span className="recent-text">{p.name}</span>
      </Link>
      <button
        type="button"
        className="recent-delete"
        title={`Delete ${p.name}`}
        aria-label={`Delete project ${p.name}`}
        disabled={deletingId === p.id}
        onClick={(e) => deleteProject(e, p)}
      >
        <IconTrash />
      </button>
    </div>
    );
  };

  return (
    <aside className="hex-sidebar">
      <div className="hex-sidebar-top">
        <button type="button" className="workspace-select" onClick={() => navigate("/")}>
          <span className="workspace-name workspace-brand">NexA</span>
          <IconChevron />
        </button>
        <button
          type="button"
          className="icon-btn"
          title="Notifications"
          aria-label="Notifications"
          onClick={() => showToast("Notifications coming soon")}
        >
          <IconBell />
        </button>
      </div>

      <div className="hex-actions">
        <button type="button" className="hex-action-btn" onClick={onNewProject}>
          <IconFolder /> Project
        </button>
        <button type="button" className="hex-action-btn" onClick={openThread} title="Browse and start threads">
          <IconThread /> Thread
        </button>
        <button type="button" className="hex-action-btn icon-only" onClick={onNewProject} title="New project" aria-label="New project">
          <IconPlus />
        </button>
      </div>

      <div className="hex-search">
        <IconSearch />
        <input
          placeholder="Search projects…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {search && (
          <button type="button" className="search-clear" onClick={() => setSearch("")} aria-label="Clear search">×</button>
        )}
      </div>

      <nav className="hex-nav">
        <Link to="/" className={`hex-nav-item ${isHome ? "active" : ""}`}>
          <IconHome /> Home
        </Link>
      </nav>

      <div className="hex-nav-group">
        <div className="hex-nav-group-label">Workspace</div>
        <Link to="/collections" className={`hex-nav-item ${isCollections ? "active" : ""}`}>
          <IconFolder /> Collections
        </Link>
        <Link to="/projects" className={`hex-nav-item ${isProjectsList ? "active" : ""}`}>
          <IconFolder /> Projects
        </Link>
        <Link to="/threads" className={`hex-nav-item ${isThreadsList ? "active" : ""}`}>
          <IconThread /> Threads
        </Link>
        <Link to={dataHref} className={`hex-nav-item ${isData ? "active" : ""}`}>
          <IconData /> Data
        </Link>
        {isAdmin() && (
          <Link to="/org-schema" className={`hex-nav-item ${isOrgSchema ? "active" : ""}`}>
            <IconData /> Org Schema
          </Link>
        )}
      </div>

      {filteredProjects.length > 0 && (
        <div className="hex-nav-group scrollable">
          <div className="hex-nav-group-label">Recents</div>
          {filteredProjects.slice(0, 8).map((p) => (
            <ProjectRow key={p.id} p={p} />
          ))}
        </div>
      )}

      {favoriteProjects.length > 0 && !q && (
        <div className="hex-nav-group">
          <div className="hex-nav-group-label">Favorite Projects</div>
          {favoriteProjects.map((p) => (
            <ProjectRow key={p.id} p={p} showFavoriteToggle />
          ))}
        </div>
      )}

      {q && filteredProjects.length === 0 && (
        <div className="sidebar-empty">No projects match "{search}"</div>
      )}

      {toast && <div className="sidebar-toast">{toast}</div>}

      <div className="hex-sidebar-footer">
        {isAdmin() && (
          <button type="button" className="hex-footer-link" onClick={() => navigate("/admin")}>
            Admin
          </button>
        )}
        <button type="button" className="hex-footer-link" onClick={() => navigate("/")}>
          <IconHome /> Home
        </button>
        <Link to={dataHref} className="hex-footer-link">
          <IconData /> Data
        </Link>
        <button type="button" className="hex-footer-link" onClick={() => setSettingsOpen(true)}>
          <IconSettings /> Settings
        </button>
        <button
          type="button"
          className="hex-footer-link"
          onClick={() => {
            clearSession();
            navigate("/login");
          }}
        >
          Log out
        </button>
      </div>

      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </aside>
  );
}
