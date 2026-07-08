import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api.js";
import BackgroundGraphics from "../components/BackgroundGraphics.jsx";
import DashboardBoard from "../components/DashboardBoard.jsx";
import AppInputBar, { defaultValuesFromInputs } from "../components/AppInputBar.jsx";

export default function SharedDashboardPage() {
  const { token } = useParams();
  const [data, setData] = useState(null);
  const [inputValues, setInputValues] = useState({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = async (overrides = inputValues) => {
    setRefreshing(true);
    if (!data) setLoading(true);
    try {
      const res = await api.getSharedDashboard(token, overrides);
      setData(res);
      if (!Object.keys(inputValues).length && res.inputs?.length) {
        setInputValues(defaultValuesFromInputs(res.inputs));
      }
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    load({});
  }, [token]);

  const applyInputs = () => load(inputValues);

  if (loading) {
    return (
      <div className="shared-dashboard">
        <div className="loading-state"><div className="spinner" /><span>Loading live data…</span></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="shared-dashboard">
        <div className="shared-header">
          <span className="shared-brand">NexA</span>
        </div>
        <div className="error">{error}</div>
      </div>
    );
  }

  const appTitle = data.config?.title || data.project_name;

  const toolbar = (
    <div className="db-toolbar-actions">
      <span className="shared-brand">NexA</span>
      <button type="button" className="secondary small" onClick={() => load(inputValues)} disabled={refreshing}>
        {refreshing ? "Refreshing…" : "Refresh"}
      </button>
    </div>
  );

  return (
    <div className="shared-dashboard">
      <BackgroundGraphics />
      <div className="page-content dashboard-page shared-dashboard-inner">
        {data.inputs?.length > 0 && (
          <AppInputBar
            inputs={data.inputs}
            values={inputValues}
            onChange={setInputValues}
            onApply={applyInputs}
            applying={refreshing}
          />
        )}
        <DashboardBoard
          title={appTitle}
          subtitle={data.config?.description}
          items={data.items}
          toolbar={toolbar}
        />
        <footer className="shared-footer muted small">
          Live app — data refreshes from BigQuery when you apply filters or open this page.
        </footer>
      </div>
    </div>
  );
}
