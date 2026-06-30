import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function ProjectsPage() {
  const [projects, setProjects] = useState([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const load = () => api.listProjects().then(setProjects).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!name.trim()) return;
    try {
      await api.createProject(name.trim());
      setName("");
      load();
    } catch (e) { setError(e.message); }
  };

  const remove = async (id) => {
    if (!confirm("Delete this project and all its memory?")) return;
    await api.deleteProject(id);
    load();
  };

  return (
    <div className="container">
      <header className="topbar">
        <span className="logo">NexA</span>
        <span className="subtitle">ask BigQuery in plain English</span>
      </header>

      <h1>Projects</h1>
      {error && <div className="error">{error}</div>}

      <div className="row">
        <input
          placeholder="New project name (e.g. Growth analytics)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
        />
        <button className="primary" onClick={create}>Create project</button>
      </div>

      <ul className="project-list">
        {projects.map((p) => (
          <li key={p.id}>
            <Link to={`/projects/${p.id}`}>{p.name}</Link>
            <button className="ghost" onClick={() => remove(p.id)}>Delete</button>
          </li>
        ))}
        {projects.length === 0 && <p className="muted">No projects yet. Create one above.</p>}
      </ul>
    </div>
  );
}
