import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import AppShell from "../components/AppShell.jsx";
import LoadingScreen from "../components/LoadingScreen.jsx";
import { IconPlus } from "../components/Icons.jsx";
import { timeAgo } from "../utils/timeago.js";

/** Global threads list — threads are independent of projects. */
export default function ThreadsPage() {
  const [threads, setThreads] = useState(null);
  const [projects, setProjects] = useState([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const load = async () => {
    try {
      const [t, p] = await Promise.all([api.listThreads(), api.listProjects()]);
      setThreads(t);
      setProjects(p);
    } catch (e) {
      setError(e.message);
      setThreads([]);
    }
  };

  useEffect(() => { load(); }, []);

  const createThread = async () => {
    setCreating(true);
    setError("");
    try {
      const t = await api.createStandaloneThread();
      navigate(`/threads/${t.id}`);
    } catch (e) {
      setError(e.message);
      setCreating(false);
    }
  };

  const openThread = (t) => {
    if (t.project_id) {
      navigate(`/projects/${t.project_id}?thread=${t.id}`);
    } else {
      navigate(`/threads/${t.id}`);
    }
  };

  return (
    <AppShell projects={projects} onProjectsChange={load} fullBleed>
      <div className="hex-list-page">
        <header className="hex-list-header">
          <h1 className="hex-list-title">Threads</h1>
          <p className="muted hex-list-subtitle">
            Ask questions here — threads are independent. Use <strong>Projects</strong> for notebooks and apps.
          </p>
          <button type="button" className="primary hex-list-new" onClick={createThread} disabled={creating}>
            <IconPlus /> {creating ? "Creating…" : "New thread"}
          </button>
        </header>

        {error && <div className="error banner-error">{error}</div>}

        {threads === null ? (
          <LoadingScreen message="Loading threads…" />
        ) : threads.length === 0 ? (
          <div className="hex-list-empty">
            <p>No threads yet — start one to ask your first question.</p>
            <button type="button" className="primary hex-list-new" onClick={createThread} disabled={creating}>
              <IconPlus /> New thread
            </button>
          </div>
        ) : (
          <table className="hex-list-table stagger-rows">
            <thead>
              <tr>
                <th>Title</th>
                <th>Last updated</th>
                <th>Creator</th>
                <th className="num">Turns</th>
              </tr>
            </thead>
            <tbody>
              {threads.map((t, i) => (
                <tr
                  key={t.id}
                  style={{ "--row-i": i }}
                  onClick={() => openThread(t)}
                >
                  <td>
                    <span className="hex-list-link">
                      <span className="hex-list-icon" aria-hidden>◍</span>
                      <span className="hex-list-name">{t.title}</span>
                    </span>
                    {t.project_name && (
                      <span className="hex-list-sub">Linked notebook: {t.project_name}</span>
                    )}
                  </td>
                  <td className="muted">{timeAgo(t.last_updated_at)}</td>
                  <td>
                    <span className="hex-creator">
                      <span className="hex-avatar" aria-hidden>{(t.creator || "N").charAt(0).toUpperCase()}</span>
                      {t.creator || "—"}
                    </span>
                  </td>
                  <td className="num muted">{t.turn_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </AppShell>
  );
}
