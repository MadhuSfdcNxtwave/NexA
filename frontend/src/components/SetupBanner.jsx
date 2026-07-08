import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function SetupBanner() {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    api.getSetupStatus().then(setStatus).catch(() => setStatus({ gcp_ok: false, issues: ["Cannot reach backend API"] }));
  }, []);

  if (!status || status.gcp_ok) return null;

  return (
    <div className="setup-banner">
      <strong>BigQuery not connected.</strong>
      <span>
        {status.issues?.[0] || "Configure GCP to browse warehouse tables and run queries."}
      </span>
      <details>
        <summary>Setup steps</summary>
        <ol>
          <li>Edit <code>backend/.env</code> — set <code>GCP_PROJECT=your-real-project-id</code></li>
          <li>Run <code>gcloud auth application-default login</code></li>
          <li>Restart the backend server</li>
        </ol>
        <p className="muted small">You can still create projects and add tables manually by full name (<code>project.dataset.table</code>).</p>
      </details>
    </div>
  );
}
