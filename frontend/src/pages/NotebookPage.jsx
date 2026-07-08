import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import AppShell from "../components/AppShell.jsx";
import SetupBanner from "../components/SetupBanner.jsx";
import ProjectNav from "../components/ProjectNav.jsx";
import VizTable from "../components/VizTable.jsx";
import { LogicGraphPanel, LogicGraphMinimap } from "../components/LogicGraph.jsx";
import ThreadAgentPanel from "../components/ThreadAgentPanel.jsx";
import CodeCellEditor, { DEFAULT_CODE_CELL } from "../components/CodeCellEditor.jsx";
import NotebookQuickStart from "../components/NotebookQuickStart.jsx";

function InputCellEditor({ cell, values, onChange, onSave }) {
  const cfg = cell.config || {};
  if (cfg.input_type === "date_range") {
    const startKey = cfg.start_var || "range_start";
    const endKey = cfg.end_var || "range_end";
    return (
      <div className="notebook-input-cell">
        <label className="notebook-input-label">{cfg.label || cell.name}</label>
        <div className="notebook-date-range">
          <input
            type="date"
            value={values[startKey] || ""}
            onChange={(e) => onChange({ ...values, [startKey]: e.target.value })}
            onBlur={onSave}
          />
          <span className="muted">→</span>
          <input
            type="date"
            value={values[endKey] || ""}
            onChange={(e) => onChange({ ...values, [endKey]: e.target.value })}
            onBlur={onSave}
          />
        </div>
        <p className="muted notebook-hint">
          Variables: <code>{`{{ ${startKey} }}`}</code>, <code>{`{{ ${endKey} }}`}</code>
        </p>
      </div>
    );
  }
  return <p className="muted">Input: {cell.content}</p>;
}

function resolveCodeData(cell, allCells) {
  const sourceName = cell.config?.data_source;
  if (!sourceName) return { rows: [], columns: [] };
  const src = allCells.find((c) => c.name === sourceName && c.cell_type === "sql");
  if (!src?.last_run) return { rows: [], columns: [] };
  return { rows: src.last_run.rows || [], columns: src.last_run.columns || [] };
}

function CellCard({
  cell,
  allCells,
  inputValues,
  setInputValues,
  onUpdate,
  onDelete,
  onPinToApp,
  onRunCell,
  running,
  highlighted,
  registerFlush,
  unregisterFlush,
}) {
  const [draft, setDraft] = useState(cell.content);
  const [nameDraft, setNameDraft] = useState(cell.name);

  useEffect(() => {
    setDraft(cell.content);
    setNameDraft(cell.name);
  }, [cell.content, cell.name]);

  const saveCell = async () => {
    const patch = {};
    if (nameDraft !== cell.name) patch.name = nameDraft;
    if (draft !== (cell.content ?? "")) patch.content = draft;
    if (Object.keys(patch).length) await onUpdate(cell.id, patch);
  };

  const saveInputConfig = async () => {
    const cfg = cell.config || {};
    if (cfg.input_type === "date_range") {
      const sk = cfg.start_var || "range_start";
      const ek = cfg.end_var || "range_end";
      await onUpdate(cell.id, {
        config: {
          ...cfg,
          default_start: inputValues[sk] || cfg.default_start,
          default_end: inputValues[ek] || cfg.default_end,
        },
      });
      return;
    }
    await saveCell();
  };

  useEffect(() => {
    const flush = async () => {
      const patch = {};
      if (nameDraft !== cell.name) patch.name = nameDraft;
      if (draft !== (cell.content ?? "")) patch.content = draft;
      if (cell.cell_type === "input" && cell.config?.input_type === "date_range") {
        const cfg = cell.config || {};
        const sk = cfg.start_var || "range_start";
        const ek = cfg.end_var || "range_end";
        patch.config = {
          ...cfg,
          default_start: inputValues[sk] || cfg.default_start,
          default_end: inputValues[ek] || cfg.default_end,
        };
      }
      if (Object.keys(patch).length) await onUpdate(cell.id, patch);
    };
    registerFlush(cell.id, flush);
    return () => unregisterFlush(cell.id);
  }, [
    cell.id,
    cell.name,
    cell.content,
    cell.cell_type,
    cell.config,
    draft,
    nameDraft,
    inputValues,
    onUpdate,
    registerFlush,
    unregisterFlush,
  ]);

  const isSummary = cell.config?.role === "summary";
  const typeLabel = isSummary
    ? "Memory summary"
    : cell.config?.source === "thread"
      ? { question: "Question", answer: "Answer", sql: "SQL" }[cell.config?.role] || "Thread"
      : { input: "Input", sql: "SQL", text: "Text", code: "Code" }[cell.cell_type] || cell.cell_type;

  return (
    <div
      className={`notebook-cell notebook-cell-${cell.cell_type}${isSummary ? " notebook-cell-summary" : ""}${highlighted ? " highlighted" : ""}`}
      id={`cell-${cell.id}`}
    >
      <div className="notebook-cell-head">
        <span className="notebook-cell-type">{typeLabel}</span>
        {cell.config?.source === "thread" && !isSummary && (
          <span className="notebook-cell-thread-badge" title="Synced from Thread">Thread</span>
        )}
        {isSummary && (
          <span className="notebook-cell-thread-badge summary" title="Used as AI context for follow-ups">
            Key points
          </span>
        )}
        {!isSummary && (
        <input
          className="notebook-cell-name"
          value={nameDraft}
          onChange={(e) => setNameDraft(e.target.value)}
          onBlur={saveCell}
        />
        )}
        {!isSummary && (
        <button type="button" className="link-btn danger" onClick={() => onDelete(cell.id)} disabled={running}>
          Delete
        </button>
        )}
      </div>

      {isSummary && (
        <div className="notebook-summary-body">
          <p className="muted notebook-hint">Updated after each Thread question — powers follow-ups without extra scans.</p>
          <pre>{cell.content}</pre>
        </div>
      )}

      {!isSummary && cell.cell_type === "input" && (
        <InputCellEditor
          cell={cell}
          values={inputValues}
          onChange={setInputValues}
          onSave={saveInputConfig}
        />
      )}

      {!isSummary && cell.cell_type === "sql" && (
        <>
          <textarea
            className="notebook-sql-editor"
            rows={8}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={saveCell}
            spellCheck={false}
          />
          <div className="notebook-sql-actions">
            <p className="muted notebook-hint">
              Chain prior cells: <code>FROM {cell.name.replace(/\W/g, "_")}</code> or use{" "}
              <code>{`{{ variable }}`}</code> from inputs.
            </p>
            <div className="notebook-cell-actions">
              <button
                type="button"
                className="primary small"
                onClick={() => onRunCell(cell.id)}
                disabled={running || !draft.trim()}
              >
                Run cell
              </button>
              {onPinToApp && (
                <button type="button" className="secondary small" onClick={() => onPinToApp(cell.id)}>
                  Add to app
                </button>
              )}
            </div>
          </div>
        </>
      )}

      {!isSummary && cell.cell_type === "text" && (
        <textarea
          className="notebook-text-editor"
          rows={3}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={saveCell}
        />
      )}

      {!isSummary && cell.cell_type === "code" && (
        <>
          <div className="notebook-code-source">
            <label className="muted small">
              Data from SQL cell
              <select
                value={cell.config?.data_source || ""}
                onChange={async (e) => {
                  await onUpdate(cell.id, {
                    config: { ...(cell.config || {}), data_source: e.target.value || null },
                  });
                }}
              >
                <option value="">— none —</option>
                {allCells
                  .filter((c) => c.cell_type === "sql")
                  .map((c) => (
                    <option key={c.id} value={c.name}>
                      {c.name}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <CodeCellEditor
            code={draft}
            onChange={setDraft}
            {...resolveCodeData(cell, allCells)}
          />
          <div className="notebook-code-actions">
            <button type="button" className="secondary small" onClick={saveCell}>
              Save code
            </button>
            {onPinToApp && (
              <button type="button" className="secondary small" onClick={() => onPinToApp(cell.id)}>
                Add to app
              </button>
            )}
          </div>
        </>
      )}

      {cell.last_run && cell.cell_type === "sql" && (
        <div className="notebook-cell-result">
          <div className="notebook-result-meta">
            <span>{cell.last_run.row_count} rows</span>
            {typeof cell.last_run.bytes_estimate === "number" && (
              <span>~{(cell.last_run.bytes_estimate / 1048576).toFixed(2)} MB</span>
            )}
          </div>
          <details open>
            <summary>SQL &amp; data (cached for follow-ups)</summary>
            <pre className="code-block">{cell.last_run.sql}</pre>
            {cell.last_run.rows?.length > 0 && (
              <VizTable rows={cell.last_run.rows} columns={cell.last_run.columns} limit={25} />
            )}
          </details>
        </div>
      )}

      {running && <p className="muted notebook-running">Running…</p>}
    </div>
  );
}

function buildUserCellSections(cells) {
  return cells
    .filter((c) => c.config?.source !== "thread" && c.config?.role !== "summary")
    .map((cell) => ({ kind: "cell", cells: [cell] }));
}

export default function NotebookPage() {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const threadId = searchParams.get("thread") ? Number(searchParams.get("thread")) : null;

  useEffect(() => {
    if (threadId) return;
    let cancelled = false;
    api.listProjectThreads(id).then((list) => {
      if (cancelled || !list.length) return;
      setSearchParams({ thread: String(list[0].id) }, { replace: true });
    });
    return () => { cancelled = true; };
  }, [id, threadId, setSearchParams]);
  const [project, setProject] = useState(null);
  const [projects, setProjects] = useState([]);
  const [cells, setCells] = useState([]);
  const [inputValues, setInputValues] = useState({});
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [runMsg, setRunMsg] = useState("");
  const [graph, setGraph] = useState(null);
  const [view, setView] = useState("notebook"); // notebook | logic
  const [highlightId, setHighlightId] = useState(null);
  const flushHandlers = useRef(new Map());

  const registerFlush = useCallback((cellId, fn) => {
    flushHandlers.current.set(cellId, fn);
  }, []);

  const unregisterFlush = useCallback((cellId) => {
    flushHandlers.current.delete(cellId);
  }, []);

  const flushAllCells = async () => {
    for (const fn of flushHandlers.current.values()) {
      await fn();
    }
  };

  const reload = useCallback(async (soft = false) => {
    if (!soft) setLoading(true);
    setError("");
    try {
      const [list, dag] = await Promise.all([
        api.listNotebookCells(id),
        api.getNotebookGraph(id),
      ]);
      setCells(list);
      setGraph(dag);
      const vals = {};
      for (const c of list) {
        if (c.cell_type === "input" && c.config?.input_type === "date_range") {
          const sk = c.config.start_var || "range_start";
          const ek = c.config.end_var || "range_end";
          if (sk) {
            vals[sk] =
              c.config.default_start ||
              vals[sk] ||
              "2025-04-01";
          }
          if (ek) {
            const endDefault = c.config.default_end;
            vals[ek] =
              endDefault === "CURRENT_MONTH_END"
                ? new Date().toISOString().slice(0, 10)
                : endDefault || vals[ek] || new Date().toISOString().slice(0, 10);
          }
        }
      }
      setInputValues((prev) => ({ ...vals, ...prev }));
    } catch (e) {
      setError(e.message);
    } finally {
      if (!soft) setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    api.getProject(id).then(setProject);
    api.listProjects().then(setProjects);
    (async () => {
      try {
        await api.enableNotebook(id);
      } catch (_) {
        /* older backend: cells endpoint may still work */
      }
      reload();
    })();
  }, [id, reload]);

  const enableNotebook = async () => {
    setError("");
    try {
      let updated = null;
      try {
        updated = await api.updateProjectSettings(id, { notebook_enabled: true });
      } catch (_) {
        updated = await api.enableNotebook(id);
      }
      setProject(updated || (await api.getProject(id)));
      await reload();
    } catch (e) {
      setError(e.message || "Could not enable Notebook. Restart the backend (uvicorn) and try again.");
    }
  };

  const updateCell = async (cellId, patch) => {
    await api.updateNotebookCell(id, cellId, patch);
    await reload(true);
  };

  const deleteCell = async (cellId) => {
    if (!window.confirm("Delete this cell? This cannot be undone.")) return;
    flushHandlers.current.delete(cellId);
    setError("");
    try {
      await api.deleteNotebookCell(id, cellId);
      setCells((prev) => prev.filter((c) => c.id !== cellId));
    } catch (e) {
      setError(e.message || "Could not delete cell");
      await reload(true);
    }
  };

  const pinToApp = async (cellId) => {
    setError("");
    try {
      await api.addDashboardFromNotebook(id, cellId);
      setRunMsg("Added to app — open App tab to configure filters and publish.");
      setTimeout(() => setRunMsg(""), 4000);
    } catch (e) {
      setError(e.message || "Could not add to app");
    }
  };

  const addCell = async (cell_type) => {
    const n = cells.length + 1;
    const lastSql = [...cells].reverse().find((c) => c.cell_type === "sql");
    const name =
      cell_type === "sql"
        ? `sql_cell_${n}`
        : cell_type === "input"
          ? `filter_${n}`
          : cell_type === "code"
            ? `widget_${n}`
            : `note_${n}`;
    await api.createNotebookCell(id, {
      cell_type,
      name,
      content:
        cell_type === "sql"
          ? "-- Write SQL here. Use {{ filter_1_start }} from input cells.\nSELECT 1 AS sample_col"
          : cell_type === "code"
            ? DEFAULT_CODE_CELL
            : "",
      sort_order: n,
      config:
        cell_type === "input"
          ? {
              input_type: "date_range",
              label: "Date range",
              start_var: `filter_${n}_start`,
              end_var: `filter_${n}_end`,
              default_start: "2025-04-01",
              default_end: "CURRENT_MONTH_END",
            }
          : cell_type === "code"
            ? { data_source: lastSql?.name || "" }
            : {},
    });
    await reload(true);
  };

  const startHexTemplate = async () => {
    setRunning(true);
    setError("");
    try {
      await api.createNotebookCell(id, {
        cell_type: "input",
        name: "filter_1",
        content: "",
        sort_order: 1,
        config: {
          input_type: "date_range",
          label: "Date range",
          start_var: "filter_1_start",
          end_var: "filter_1_end",
          default_start: "2025-04-01",
          default_end: "CURRENT_MONTH_END",
        },
      });
      await api.createNotebookCell(id, {
        cell_type: "sql",
        name: "sql_cell_1",
        content:
          "-- Replace with your table. Variables from filter_1 input above.\nSELECT CURRENT_DATE() AS report_date, 1 AS sample_metric",
        sort_order: 2,
        config: {},
      });
      await api.createNotebookCell(id, {
        cell_type: "code",
        name: "widget_1",
        content: DEFAULT_CODE_CELL,
        sort_order: 3,
        config: { data_source: "sql_cell_1" },
      });
      await reload(true);
      setRunMsg("Template ready — edit SQL, click Restart & run all, then customize Code.");
      setTimeout(() => setRunMsg(""), 5000);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  const runCell = async (cellId) => {
    setRunning(true);
    setError("");
    setRunMsg("");
    try {
      await flushAllCells();
      const out = await api.runNotebook(id, { input_overrides: inputValues, cell_id: cellId });
      setRunMsg(
        `Cell ran · ~${((out.bytes_estimate || 0) / 1048576).toFixed(2)} MB scanned`,
      );
      setTimeout(() => setRunMsg(""), 4000);
      await reload(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  const runAll = async () => {
    setRunning(true);
    setError("");
    setRunMsg("");
    try {
      await flushAllCells();
      const out = await api.runNotebook(id, { input_overrides: inputValues });
      setRunMsg(
        `Ran ${out.run_log?.filter((l) => l.status === "ok").length || 0} cells · ` +
          `~${((out.bytes_estimate || 0) / 1048576).toFixed(2)} MB scanned`
      );
      await reload(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  const userCells = cells.filter((c) => c.config?.source !== "thread" && c.config?.role !== "summary");

  const scrollToCell = (cellId) => {
    setHighlightId(cellId);
    const el = document.getElementById(`cell-${cellId}`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  if (!project) {
    return (
      <AppShell projects={projects} activeProjectId={Number(id)}>
        <div className="loading-state"><div className="spinner" /><span>Loading…</span></div>
      </AppShell>
    );
  }

  return (
    <AppShell projects={projects} activeProjectId={project.id} fullBleed>
      <SetupBanner />
      <ProjectNav
        projectId={id}
        projectName={project.name}
        active="notebook"
      />

      <div className="notebook-page notebook-page-wide">
        {error && (
          <div className="error notebook-enable-error">
            {error}
            <button type="button" className="secondary small" onClick={enableNotebook}>
              Retry enable
            </button>
          </div>
        )}
        <header className="notebook-toolbar">
          <div>
            <h1 className="notebook-title">Notebook</h1>
            <p className="muted">
              Project notebook — SQL, inputs, and code cells for <strong>{project?.name}</strong>.
              Ask questions in the Thread tab; each project has its own notebook.
            </p>
          </div>
          <div className="notebook-view-toggle">
            <button
              type="button"
              className={view === "notebook" ? "active" : ""}
              onClick={() => setView("notebook")}
            >
              Notebook
            </button>
            <button
              type="button"
              className={view === "logic" ? "active" : ""}
              onClick={() => setView("logic")}
            >
              Logic
            </button>
          </div>
          <div className="notebook-toolbar-actions">
            <button type="button" className="secondary" onClick={() => addCell("input")} disabled={running}>
              + Input
            </button>
            <button type="button" className="secondary" onClick={() => addCell("sql")} disabled={running}>
              + SQL
            </button>
            <button type="button" className="secondary" onClick={() => addCell("code")} disabled={running}>
              + Code
            </button>
            <button type="button" className="secondary" onClick={() => addCell("text")} disabled={running}>
              + Text
            </button>
            <button type="button" className="primary" onClick={runAll} disabled={running || cells.length === 0}>
              {running ? "Running…" : "Restart & run all"}
            </button>
          </div>
        </header>

        {runMsg && <div className="toast-msg">{runMsg}</div>}

        {loading && cells.length === 0 ? (
          <div className="loading-state"><div className="spinner" /><span>Loading cells…</span></div>
        ) : (
          <div className={`notebook-layout notebook-layout-${view}`}>
            {view === "notebook" && (
              <>
                <div className="notebook-cells-pane">
                  {userCells.length === 0 && (
                    <NotebookQuickStart
                      onStart={startHexTemplate}
                      onAddCell={addCell}
                      disabled={running}
                    />
                  )}
                  <div className="notebook-cell-list">
                    {buildUserCellSections(cells).map((section) =>
                      section.cells.map((cell) => (
                        <CellCard
                          key={cell.id}
                          cell={cell}
                          allCells={cells}
                          inputValues={inputValues}
                          setInputValues={setInputValues}
                          onUpdate={updateCell}
                          onDelete={deleteCell}
                          onPinToApp={pinToApp}
                          onRunCell={runCell}
                          running={running}
                          highlighted={highlightId === cell.id}
                          registerFlush={registerFlush}
                          unregisterFlush={unregisterFlush}
                        />
                      )),
                    )}
                  </div>
                  {graph && (
                    <LogicGraphMinimap
                      graph={graph}
                      highlightId={highlightId}
                      onExpand={() => setView("logic")}
                    />
                  )}
                </div>
                <ThreadAgentPanel
                  projectId={id}
                  projectName={project.name}
                  threadId={threadId}
                />
              </>
            )}
            {view === "logic" && (
              <div className="notebook-graph-pane notebook-graph-pane-full">
                <LogicGraphPanel
                  graph={graph}
                  highlightId={highlightId}
                  onNodeClick={scrollToCell}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </AppShell>
  );
}
