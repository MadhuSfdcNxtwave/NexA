import { useEffect, useState } from "react";
import { Link, useParams, useLocation } from "react-router-dom";
import { api } from "../api.js";
import AppShell from "../components/AppShell.jsx";
import SetupBanner from "../components/SetupBanner.jsx";
import LoadingScreen from "../components/LoadingScreen.jsx";
import AskSection from "../components/AskSection.jsx";

/** Standalone thread — not tied to any project. */
export default function ThreadPage() {
  const { threadId } = useParams();
  const location = useLocation();
  const [thread, setThread] = useState(null);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setThread(null);
    Promise.all([
      api.getThread(threadId),
      api.listProjects(),
    ])
      .then(([t, list]) => {
        if (cancelled) return;
        setThread(t);
        setProjects(list);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e.message || "Could not load thread");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [threadId]);

  if (loading) {
    return (
      <AppShell projects={projects} onProjectsChange={() => api.listProjects().then(setProjects)}>
        <LoadingScreen message="Loading thread…" fullScreen />
      </AppShell>
    );
  }

  if (error || !thread) {
    return (
      <AppShell projects={projects} onProjectsChange={() => api.listProjects().then(setProjects)}>
        <div className="error" style={{ margin: 24 }}>
          {error || "Thread not found"}
          <div style={{ marginTop: 12 }}>
            <Link to="/threads">← Back to threads</Link>
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell
      projects={projects}
      onProjectsChange={() => api.listProjects().then(setProjects)}
      fullBleed
    >
      <SetupBanner />
      <div className="project-header">
        <div className="project-header-left">
          <Link to="/" className="breadcrumb">NexA</Link>
          <span className="breadcrumb-sep">/</span>
          <Link to="/threads" className="breadcrumb">Threads</Link>
          <span className="breadcrumb-sep">/</span>
          <span className="breadcrumb-current">{thread.title || "Thread"}</span>
        </div>
        <nav className="project-tabs" aria-label="Thread views">
          <span className="project-tab active">Thread</span>
          <Link to="/projects" className="project-tab" title="Projects hold notebooks and apps">
            Projects
          </Link>
        </nav>
      </div>

      <AskSection
        standaloneThreadId={Number(threadId)}
        threadTitle={thread.title}
        initialQuestion={location.state?.question}
      />
    </AppShell>
  );
}
