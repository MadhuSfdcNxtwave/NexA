import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import AppShell from "../components/AppShell.jsx";
import SetupBanner from "../components/SetupBanner.jsx";
import SendButton from "../components/SendButton.jsx";
import { HeroBackgroundGraphics } from "../components/BackgroundGraphics.jsx";
import { IconPlus, IconTrash } from "../components/Icons.jsx";
import { timeAgo } from "../utils/timeago.js";

const SUGGESTIONS = [
  {
    title: "Academy activity and retention states",
    desc: "Analyze student engagement patterns and identify at-risk learners across cohorts.",
    question: "Show weekly active students and retention rate by cohort for the last 3 months",
  },
  {
    title: "Course completion funnel",
    desc: "Track how students progress through course modules and where drop-offs occur.",
    question: "What is the course completion funnel by module for IRP 2.0?",
  },
  {
    title: "NPS sentiment breakdown",
    desc: "Understand NPS scores and sentiment trends across academy programs.",
    question: "Show NPS score distribution and trend over the last 6 months",
  },
];

export default function ProjectsPage() {
  const [projects, setProjects] = useState([]);
  const [threads, setThreads] = useState([]);
  const [question, setQuestion] = useState("");
  const [selectedProject, setSelectedProject] = useState("");
  const [jumpTab, setJumpTab] = useState("threads");
  const [error, setError] = useState("");
  const [showNewProject, setShowNewProject] = useState(false);
  const [newName, setNewName] = useState("");
  const [deletingId, setDeletingId] = useState(null);
  const navigate = useNavigate();

  const load = async () => {
    try {
      const list = await api.listProjects();
      setProjects(list);
      if (list.length && !selectedProject) setSelectedProject(String(list[0].id));

      const allThreads = [];
      for (const p of list.slice(0, 5)) {
        try {
          const mem = await api.getMemory(p.id);
          mem.slice(-3).forEach((m) => {
            allThreads.push({ ...m, projectId: p.id, projectName: p.name });
          });
        } catch (_) {}
      }
      allThreads.sort((a, b) => (b.id || 0) - (a.id || 0));
      setThreads(allThreads.slice(0, 8));
    } catch (e) { setError(e.message); }
  };

  useEffect(() => { load(); }, []);

  const createProject = async () => {
    if (!newName.trim()) return;
    try {
      const p = await api.createProject(newName.trim());
      setNewName("");
      setShowNewProject(false);
      await load();
      setSelectedProject(String(p.id));
    } catch (e) { setError(e.message); }
  };

  const submitQuestion = async () => {
    const q = question.trim();
    if (!q) return;
    setError("");
    try {
      const t = await api.createStandaloneThread();
      navigate(`/threads/${t.id}`, { state: { question: q } });
    } catch (e) {
      setError(e.message);
    }
  };

  const deleteProject = async (e, p) => {
    e.preventDefault();
    e.stopPropagation();
    if (
      !window.confirm(
        `Delete "${p.name}"?\n\nAll threads, tables, and dashboard widgets will be removed.`
      )
    ) {
      return;
    }
    setDeletingId(p.id);
    try {
      await api.deleteProject(p.id);
      if (String(selectedProject) === String(p.id)) setSelectedProject("");
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setDeletingId(null);
    }
  };

  const askSuggestion = async (q) => {
    setError("");
    try {
      const t = await api.createStandaloneThread();
      navigate(`/threads/${t.id}`, { state: { question: q } });
    } catch (e) {
      setQuestion(q);
      setError(e.message);
    }
  };

  return (
    <AppShell
      projects={projects}
      onNewProject={() => setShowNewProject(true)}
      onProjectsChange={load}
      fullBleed
    >
      <div className="hex-home">
        <SetupBanner />
        <section className="hero-section">
          <HeroBackgroundGraphics />
          <p className="hero-brand">NexA</p>
          <h1 className="hero-title">What do you want to know?</h1>

          {error && <div className="error banner-error">{error}</div>}

          <div className="hero-prompt">
            <textarea
              rows={3}
              placeholder="Ask a data question or describe a data app to build..."
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submitQuestion();
                }
              }}
            />
            <div className="hero-prompt-footer">
              <div className="hero-prompt-left">
                <button type="button" className="pill-btn" title="Add context"><IconPlus /></button>
                <button type="button" className="pill-btn text" disabled>Analyze in a notebook</button>
                <button type="button" className="pill-btn text" disabled>Generate an app</button>
              </div>
              <div className="hero-prompt-right">
                {projects.length > 0 && (
                  <select
                    className="project-select"
                    value={selectedProject}
                    onChange={(e) => setSelectedProject(e.target.value)}
                  >
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                )}
                <button type="button" className="mode-select" disabled>
                  Auto <span>▾</span>
                </button>
                <SendButton onClick={submitQuestion} disabled={!question.trim()} />
              </div>
            </div>
          </div>
        </section>

        {showNewProject && (
          <div className="modal-overlay" onClick={() => setShowNewProject(false)}>
            <div className="modal-card" onClick={(e) => e.stopPropagation()}>
              <h3>New project</h3>
              <p className="muted">Create a workspace with its own tables and memory.</p>
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

        <section className="jump-back">
          <h2>Jump Back In</h2>
          <div className="jump-grid">
            <div className="jump-col">
              <div className="jump-col-header">Projects</div>
              <div className="jump-list">
                {projects.length === 0 && (
                  <div className="jump-empty muted">No projects yet — create one to get started.</div>
                )}
                {projects.map((p) => (
                  <div key={p.id} className="jump-item-row">
                    <Link to={`/projects/${p.id}`} className="jump-item">
                      <span className="jump-item-icon mono-icon">◆</span>
                      <div className="jump-item-body">
                        <div className="jump-item-title">{p.name}</div>
                        <div className="jump-item-meta">
                          {p.last_activity_at ? `Active ${timeAgo(p.last_activity_at)}` : "Workspace"}
                          {p.view_count > 0 ? ` · ↗ ${p.view_count} views` : ""}
                        </div>
                      </div>
                    </Link>
                    <button
                      type="button"
                      className="jump-item-delete"
                      title={`Delete ${p.name}`}
                      aria-label={`Delete project ${p.name}`}
                      disabled={deletingId === p.id}
                      onClick={(e) => deleteProject(e, p)}
                    >
                      <IconTrash />
                    </button>
                  </div>
                ))}
              </div>
              {projects.length > 0 && <Link to="/projects" className="view-all">View all</Link>}
            </div>

            <div className="jump-col">
              <div className="jump-tabs">
                <button type="button" className={jumpTab === "threads" ? "active" : ""} onClick={() => setJumpTab("threads")}>
                  Threads
                </button>
                <button className={jumpTab === "explorations" ? "active" : ""} onClick={() => setJumpTab("explorations")} disabled>
                  Explorations
                </button>
                <button
                  type="button"
                  className={jumpTab === "data" ? "active" : ""}
                  onClick={() => {
                    setJumpTab("data");
                    const pid = selectedProject || String(projects[0]?.id);
                    if (pid) navigate(`/projects/${pid}/data`);
                    else setShowNewProject(true);
                  }}
                >
                  Data
                </button>
              </div>
              <div className="jump-list">
                {jumpTab === "threads" && threads.length === 0 && (
                  <div className="jump-empty muted">No threads yet — ask a question to start.</div>
                )}
                {jumpTab === "data" && projects.length === 0 && (
                  <div className="jump-empty muted">Create a project to browse and add tables.</div>
                )}
                {jumpTab === "data" && projects.map((p) => (
                  <Link key={p.id} to={`/projects/${p.id}/data`} className="jump-item">
                    <span className="jump-item-icon">⊞</span>
                    <div className="jump-item-body">
                      <div className="jump-item-title">{p.name}</div>
                      <div className="jump-item-meta">Data browser · tables &amp; YAML</div>
                    </div>
                  </Link>
                ))}
                {jumpTab === "threads" && threads.map((t, i) => (
                  <Link
                    key={`${t.projectId}-${i}`}
                    to={`/projects/${t.projectId}${t.thread_id ? `?thread=${t.thread_id}` : ""}`}
                    className="jump-item"
                  >
                    <span className="jump-item-icon">💬</span>
                    <div className="jump-item-body">
                      <div className="jump-item-title">{t.question}</div>
                      <div className="jump-item-meta">{t.projectName} · You</div>
                    </div>
                  </Link>
                ))}
              </div>
              {threads.length > 0 && jumpTab === "threads" && (
                <Link to="/threads" className="view-all">View all</Link>
              )}
            </div>
          </div>
        </section>

        <section className="suggestions-section">
          <div className="suggestions-header">
            <h2>Latest suggestions</h2>
            <span className="view-all muted">View all</span>
          </div>
          <div className="suggestion-cards">
            {SUGGESTIONS.map((s) => (
              <button key={s.title} type="button" className="suggestion-card" onClick={() => askSuggestion(s.question)}>
                <h3>{s.title}</h3>
                <p>{s.desc}</p>
                <div className="suggestion-card-footer">
                  <span className="suggestion-tag">📊 Analysis</span>
                  <span className="suggestion-tag">👤 You</span>
                </div>
              </button>
            ))}
          </div>
        </section>
      </div>
    </AppShell>
  );
}
