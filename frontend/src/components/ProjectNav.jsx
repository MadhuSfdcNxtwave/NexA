import { Link, useSearchParams } from "react-router-dom";

/** Thread / Data / Dashboard tabs shared across project pages. */
export default function ProjectNav({ projectId, projectName, active }) {
  const id = String(projectId);
  const base = `/projects/${id}`;
  const [searchParams] = useSearchParams();
  const thread = searchParams.get("thread");
  const qs = thread ? `?thread=${thread}` : "";

  return (
    <div className="project-header">
      <div className="project-header-left">
        <Link to="/" className="breadcrumb">NexA</Link>
        <span className="breadcrumb-sep">/</span>
        <span className="breadcrumb-current">{projectName || "Project"}</span>
      </div>
      <nav className="project-tabs" aria-label="Project views">
        <Link
          to={`${base}/notebook${qs}`}
          className={`project-tab ${active === "notebook" ? "active" : ""}`}
          title="Chained SQL + logic graph"
        >
          Notebook
        </Link>
        <Link
          to={`${base}${qs}`}
          className={`project-tab ${active === "thread" ? "active" : ""}`}
        >
          Thread
        </Link>
        <Link
          to={`${base}/data${qs}`}
          className={`project-tab ${active === "data" ? "active" : ""}`}
        >
          Data
        </Link>
        <Link
          to={`${base}/dashboard${qs}`}
          className={`project-tab ${active === "dashboard" ? "active" : ""}`}
          title="Build and publish interactive apps"
        >
          App
        </Link>
      </nav>
    </div>
  );
}
