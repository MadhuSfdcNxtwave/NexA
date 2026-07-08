import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import Sidebar from "./Sidebar.jsx";
import BackgroundGraphics from "./BackgroundGraphics.jsx";
import UserTopBar from "./UserTopBar.jsx";
import AskJobBanner from "./AskJobBanner.jsx";

export default function AppShell({
  projects,
  activeProjectId,
  onNewProject,
  onProjectsChange,
  children,
  fullBleed,
}) {
  const mainRef = useRef(null);
  const { pathname } = useLocation();

  useEffect(() => {
    const main = mainRef.current;
    if (main) main.scrollTop = 0;
    const content = main?.querySelector(".page-content");
    if (content) content.scrollTop = 0;
    const thread = main?.querySelector(".thread-messages");
    if (thread) thread.scrollTop = 0;
    const results = main?.querySelector(".results-panel-inner");
    if (results) results.scrollTop = 0;
    const notebook = main?.querySelector(".notebook-page");
    if (notebook) notebook.scrollTop = 0;
  }, [pathname]);

  return (
    <div className="hex-app">
      <Sidebar
        projects={projects}
        activeProjectId={activeProjectId}
        onNewProject={onNewProject}
        onProjectsChange={onProjectsChange}
      />
      <main ref={mainRef} className={`hex-main ${fullBleed ? "full-bleed" : ""}`}>
        <UserTopBar />
        <AskJobBanner />
        <BackgroundGraphics />
        <div className="page-content">{children}</div>
      </main>
    </div>
  );
}
