import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function SettingsModal({ open, onClose }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    api
      .getSetupStatus()
      .then(setStatus)
      .catch((e) => setStatus({ gcp_ok: false, issues: [e.message] }))
      .finally(() => setLoading(false));
  }, [open]);

  if (!open) return null;

  const llm = status?.llm || {};

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-modal-head">
          <h3>NexA · Settings</h3>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {loading && <p className="muted">Loading…</p>}

        {!loading && status && (
          <div className="settings-sections">
            <section>
              <h4>BigQuery</h4>
              <dl className="settings-dl">
                <dt>Status</dt>
                <dd className={status.gcp_ok ? "ok" : "err"}>
                  {status.gcp_ok ? "Connected" : "Not connected"}
                </dd>
                <dt>Project</dt>
                <dd>{status.gcp_project || "—"}</dd>
                <dt>Dataset</dt>
                <dd><code>{status.default_dataset_full_id || "—"}</code></dd>
                <dt>Region</dt>
                <dd>{status.bq_location || "—"}</dd>
                <dt>Service account</dt>
                <dd className="small">{status.service_account_email || "—"}</dd>
              </dl>
              {!status.gcp_ok && status.issues?.length > 0 && (
                <p className="settings-issue">{status.issues.join(" ")}</p>
              )}
            </section>

            <section>
              <h4>SQL accuracy</h4>
              <dl className="settings-dl">
                <dt>Validation retries</dt>
                <dd>{status.accuracy?.sql_max_attempts ?? "—"}</dd>
                <dt>LLM SQL review</dt>
                <dd>{status.accuracy?.sql_verify_with_llm ? "On" : "Off"}</dd>
                <dt>Confirm before run</dt>
                <dd>{status.accuracy?.require_sql_approval ? "On" : "Off (auto-run after validation)"}</dd>
              </dl>
            </section>

            <section>
              <h4>AI models</h4>
              <dl className="settings-dl">
                <dt>SQL (Fetch)</dt>
                <dd><code>{llm.fetch_model || "—"}</code></dd>
                <dt>Charts &amp; analysis (Viz)</dt>
                <dd><code>{llm.viz_model || "—"}</code></dd>
              </dl>
            </section>
          </div>
        )}

        <div className="modal-actions">
          <button type="button" className="primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
