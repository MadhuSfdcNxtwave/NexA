import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import AppShell from "../components/AppShell.jsx";

export default function DataRedirect() {
  const navigate = useNavigate();
  const [message, setMessage] = useState("");

  useEffect(() => {
    api.listProjects().then((list) => {
      if (list.length) {
        navigate(`/projects/${list[0].id}/data`, { replace: true });
      } else {
        setMessage("Create a project first, then open the Data tab to add tables.");
      }
    }).catch(() => setMessage("Cannot reach the backend — start the API server on port 8000."));
  }, [navigate]);

  return (
    <AppShell projects={[]} onNewProject={() => navigate("/")}>
      <div className="loading-state" style={{ padding: 48, textAlign: "center" }}>
        {message ? (
          <>
            <p className="error" style={{ marginBottom: 16 }}>{message}</p>
            <button type="button" className="primary" onClick={() => navigate("/")}>
              Go to home
            </button>
          </>
        ) : (
          <>
            <div className="spinner" />
            <span>Opening data browser…</span>
          </>
        )}
      </div>
    </AppShell>
  );
}
