import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api.js";
import AppShell from "../components/AppShell.jsx";
import LoadingScreen from "../components/LoadingScreen.jsx";
import SetupBanner from "../components/SetupBanner.jsx";
import ProjectNav from "../components/ProjectNav.jsx";
import DashboardBoard from "../components/DashboardBoard.jsx";
import AppInputBar, { defaultValuesFromInputs } from "../components/AppInputBar.jsx";
import ThreadAgentPanel from "../components/ThreadAgentPanel.jsx";
import AppStructureMinimap from "../components/AppStructureMinimap.jsx";

export default function DashboardPage() {
  const { id } = useParams();
  const [project, setProject] = useState(null);
  const [projects, setProjects] = useState([]);
  const [app, setApp] = useState(null);
  const [notebookInputs, setNotebookInputs] = useState([]);
  const [notebookCells, setNotebookCells] = useState([]);
  const [inputValues, setInputValues] = useState({});
  const [mode, setMode] = useState("builder");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [shareToken, setShareToken] = useState(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const [titleDraft, setTitleDraft] = useState("");
  const [descDraft, setDescDraft] = useState("");
  const [savingConfig, setSavingConfig] = useState(false);

  const reloadProjects = () => api.listProjects().then(setProjects);

  const load = async (refresh = false, overrides = inputValues) => {
    try {
      const [p, list, appData, cells] = await Promise.all([
        api.getProject(id),
        api.listProjects(),
        api.getApp(id, refresh, overrides),
        api.listNotebookCells(id).catch(() => []),
      ]);
      setProject(p);
      setProjects(list);
      setApp(appData);
      setShareToken(appData.share_token || p.share_token || null);
      setTitleDraft(appData.config?.title || p.name || "");
      setDescDraft(appData.config?.description || "");
      const inputs = (cells || []).filter((c) => c.cell_type === "input");
      setNotebookInputs(inputs);
      setNotebookCells((cells || []).filter((c) => ["sql", "code"].includes(c.cell_type)));
      if (!Object.keys(inputValues).length && appData.inputs?.length) {
        setInputValues(defaultValuesFromInputs(appData.inputs));
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
    setLoading(true);
    load(true);
  }, [id]);

  const refreshAll = async () => {
    setRefreshing(true);
    await load(true, inputValues);
  };

  const applyInputs = async () => {
    setRefreshing(true);
    await load(true, inputValues);
  };

  const saveAppConfig = async () => {
    setSavingConfig(true);
    try {
      const picked = app?.config?.input_cell_ids || [];
      await api.updateApp(id, {
        title: titleDraft,
        description: descDraft,
        input_cell_ids: picked,
      });
      await load(false, inputValues);
    } catch (e) {
      setError(e.message);
    } finally {
      setSavingConfig(false);
    }
  };

  const toggleInputExposure = async (cellId) => {
    const current = new Set(app?.config?.input_cell_ids || []);
    if (current.has(cellId)) current.delete(cellId);
    else current.add(cellId);
    try {
      await api.updateApp(id, { input_cell_ids: [...current] });
      await load(false, inputValues);
    } catch (e) {
      setError(e.message);
    }
  };

  const publish = async () => {
    try {
      const { share_token } = await api.publishDashboard(id);
      setShareToken(share_token);
      copyLink(share_token);
    } catch (e) {
      setError(e.message);
    }
  };

  const copyLink = (token = shareToken) => {
    if (!token) return;
    const url = `${window.location.origin}/shared/${token}`;
    navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const removeItem = async (itemId) => {
    await api.removeDashboardItem(id, itemId);
    setApp((prev) => ({
      ...prev,
      items: (prev?.items || []).filter((i) => i.id !== itemId),
    }));
  };

  const pinNotebookCell = async (cellId) => {
    try {
      await api.addDashboardFromNotebook(id, cellId);
      await load(true, inputValues);
    } catch (e) {
      setError(e.message);
    }
  };

  if (!project && loading) {
    return (
      <AppShell projects={[]} onNewProject={() => {}}>
        <LoadingScreen message="Loading dashboard…" fullScreen />
      </AppShell>
    );
  }

  const items = app?.items || [];
  const appTitle = app?.config?.title || project?.name || "App";
  const exposedIds = new Set(app?.config?.input_cell_ids || []);
  const exposedFilters = notebookInputs
    .filter((c) => exposedIds.has(c.id))
    .map((c) => ({ id: c.id, name: c.name, label: c.config?.label || c.name }));
  const widgetSummaries = items.map((it) => ({
    id: it.id,
    title: it.question,
    kind: it.chart_spec?.chart === "code" ? "code" : "sql",
  }));
  const pinableCells = notebookCells.filter(
    (c) => !items.some((it) => it.chart_spec?.notebook_cell_id === c.id),
  );

  const toolbar = (
    <div className="db-toolbar-actions app-toolbar">
      <div className="app-mode-tabs">
        <button type="button" className={mode === "builder" ? "active" : ""} onClick={() => setMode("builder")}>
          App builder
        </button>
        <button type="button" className={mode === "preview" ? "active" : ""} onClick={() => setMode("preview")}>
          Preview
        </button>
      </div>
      <button type="button" className="secondary" onClick={refreshAll} disabled={refreshing}>
        {refreshing ? "Refreshing…" : "Refresh all"}
      </button>
      {shareToken ? (
        <button type="button" className="primary" onClick={() => copyLink()}>
          {copied ? "Link copied!" : "Copy app link"}
        </button>
      ) : (
        <button type="button" className="primary" onClick={publish} disabled={!items.length}>
          Publish app
        </button>
      )}
    </div>
  );

  return (
    <AppShell projects={projects} activeProjectId={Number(id)} onProjectsChange={reloadProjects} fullBleed>
      <SetupBanner />
      <ProjectNav
        projectId={id}
        projectName={project?.name}
        active="dashboard"
        notebookEnabled={project?.notebook_enabled}
      />

      <div className="dashboard-page app-builder-page app-builder-hex">
        {error && <div className="error">{error}</div>}
        {loading ? (
          <div className="loading-state"><div className="spinner" /><span>Loading app…</span></div>
        ) : (
          <div className="app-builder-layout">
            <div className="app-builder-main">
            {mode === "builder" && (
              <section className="app-builder-panel">
                <h2 className="app-builder-heading">App builder</h2>
                <p className="muted small">
                  Hex-style app: expose notebook filters, pin SQL + Code widgets, publish a share link.
                </p>
                <div className="app-builder-form">
                  <label>
                    App title
                    <input value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)} />
                  </label>
                  <label>
                    Description
                    <textarea
                      rows={2}
                      value={descDraft}
                      onChange={(e) => setDescDraft(e.target.value)}
                      placeholder="Shown under the app title for viewers"
                    />
                  </label>
                  <button type="button" className="secondary small" onClick={saveAppConfig} disabled={savingConfig}>
                    {savingConfig ? "Saving…" : "Save settings"}
                  </button>
                </div>

                <h3 className="app-builder-sub">Consumer filters</h3>
                <p className="muted small">
                  Choose notebook input cells that appear on the published app. Widget SQL with{" "}
                  <code>{`{{ variables }}`}</code> re-queries BigQuery when viewers change filters.
                </p>
                {!notebookInputs.length ? (
                  <p className="muted">
                    No input cells yet.{" "}
                    <Link to={`/projects/${id}/notebook`}>Add inputs in the Notebook</Link>.
                  </p>
                ) : (
                  <ul className="app-input-picker">
                    {notebookInputs.map((c) => (
                      <li key={c.id}>
                        <label>
                          <input
                            type="checkbox"
                            checked={exposedIds.has(c.id)}
                            onChange={() => toggleInputExposure(c.id)}
                          />
                          <span>{c.config?.label || c.name}</span>
                          <span className="muted small">({c.config?.input_type || "input"})</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                )}

                <h3 className="app-builder-sub">Pin from Notebook</h3>
                <p className="muted small">
                  SQL and Code cells from your notebook. Pin both — code widgets use SQL data on refresh.
                </p>
                {!pinableCells.length ? (
                  <p className="muted">
                    {items.length ? "All notebook widgets pinned." : "No cells yet."}{" "}
                    <Link to={`/projects/${id}/notebook`}>Open Notebook →</Link>
                  </p>
                ) : (
                  <ul className="app-pin-list">
                    {pinableCells.map((c) => (
                      <li key={c.id}>
                        <span className={`app-pin-type ${c.cell_type}`}>{c.cell_type}</span>
                        <span>{c.name}</span>
                        <button type="button" className="secondary small" onClick={() => pinNotebookCell(c.id)}>
                          Pin to app
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                <h3 className="app-builder-sub">Pinned widgets</h3>
                <p className="muted small">
                  {items.length
                    ? `${items.length} widget${items.length === 1 ? "" : "s"} in this app.`
                    : "None yet — pin from Notebook or Thread."}
                </p>
              </section>
            )}

            {(mode === "preview" || app?.inputs?.length > 0) && app?.inputs?.length > 0 && (
              <AppInputBar
                inputs={app.inputs}
                values={inputValues}
                onChange={setInputValues}
                onApply={applyInputs}
                applying={refreshing}
              />
            )}

            <DashboardBoard
              title={appTitle}
              subtitle={app?.config?.description}
              items={items}
              editable={mode === "builder"}
              onRemoveItem={removeItem}
              toolbar={toolbar}
            />
            <AppStructureMinimap
              filters={exposedFilters}
              widgets={widgetSummaries}
              mode={mode}
              onExpand={() => setMode((m) => (m === "preview" ? "builder" : "preview"))}
            />
            </div>
            <ThreadAgentPanel
              projectId={id}
              projectName={project?.name}
              onTurnComplete={() => load(true, inputValues)}
            />
          </div>
        )}
      </div>
    </AppShell>
  );
}
