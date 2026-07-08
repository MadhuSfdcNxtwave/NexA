import { useEffect, useState } from "react";
import { useParams, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import AppShell from "../components/AppShell.jsx";
import SetupBanner from "../components/SetupBanner.jsx";
import ProjectNav from "../components/ProjectNav.jsx";
import LoadingScreen from "../components/LoadingScreen.jsx";
import AskSection from "../components/AskSection.jsx";

export default function ProjectPage() {
  const { id } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [project, setProject] = useState(null);
  const [projects, setProjects] = useState([]);
  const [showNewProject, setShowNewProject] = useState(false);
  const [newName, setNewName] = useState("");

  const reloadProjects = () => api.listProjects().then(setProjects);

  useEffect(() => {
    api.getProject(id).then(setProject);
    reloadProjects();
    api.trackProjectView(id).catch(() => {});
  }, [id]);

  const createProject = async () => {
    if (!newName.trim()) return;
    const p = await api.createProject(newName.trim());
    setShowNewProject(false);
    setNewName("");
    navigate(`/projects/${p.id}`);
  };

  if (!project) {
    return (
      <AppShell projects={projects} activeProjectId={Number(id)} onNewProject={() => setShowNewProject(true)} onProjectsChange={reloadProjects}>
        <LoadingScreen message="Loading project…" fullScreen />
      </AppShell>
    );
  }

  return (
    <AppShell
      projects={projects}
      activeProjectId={project.id}
      onNewProject={() => setShowNewProject(true)}
      onProjectsChange={reloadProjects}
      fullBleed
    >
      <SetupBanner />
      <ProjectNav projectId={id} projectName={project?.name} active="thread" />

      <AskSection
        id={id}
        project={project}
        onProjectChange={setProject}
        projectName={project.name}
        initialQuestion={location.state?.question}
      />

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
    </AppShell>
  );
}
