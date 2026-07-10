import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { api } from "../api.js";
import { isAdmin } from "../auth.js";
import { parseColumnDictionary } from "../utils/parseColumnDictionary.js";
import AppShell from "../components/AppShell.jsx";
import LoadingScreen from "../components/LoadingScreen.jsx";
import SetupBanner from "../components/SetupBanner.jsx";
import ProjectNav from "../components/ProjectNav.jsx";

function DescriptionEditor({ value, onSave }) {
  const [text, setText] = useState(value);
  const [saved, setSaved] = useState(false);
  useEffect(() => { setText(value); }, [value]);
  const save = () => {
    if (text !== value) {
      onSave(text);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    }
  };
  return (
    <div className="description-block">
      <label>Description</label>
      <textarea
        rows={3}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={save}
        placeholder="Describe what this table contains…"
      />
      <button type="button" className="secondary small" onClick={save}>
        {saved ? "Saved" : "Save description"}
      </button>
    </div>
  );
}

function BusinessRulesEditor({ value, onSave }) {
  const [text, setText] = useState(value || "");
  const [saved, setSaved] = useState(false);
  useEffect(() => { setText(value || ""); }, [value]);
  const save = () => {
    if (text !== (value || "")) {
      onSave(text);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    }
  };
  return (
    <div className="description-block business-rules-block">
      <label>Business rules</label>
      <p className="muted small" style={{ margin: "0 0 6px" }}>
        Rules Ask must follow for this table (overrides default filters). Example:{" "}
        <em>Every row is an active portal user — do not add WHERE filters.</em>
      </p>
      <textarea
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={save}
        placeholder={"Every row is an active learning portal user.\nDo not add pause_status or onboarding WHERE filters.\nUse COUNT(DISTINCT user_id) only."}
      />
      <button type="button" className="secondary small" onClick={save}>
        {saved ? "Saved" : "Save rules"}
      </button>
    </div>
  );
}

function ColumnDescriptionEditor({ value, onSave, placeholder }) {
  const [text, setText] = useState(value || "");
  const [saved, setSaved] = useState(false);
  useEffect(() => { setText(value || ""); }, [value]);
  const save = () => {
    const next = text.trim();
    const prev = (value || "").trim();
    if (next !== prev) {
      onSave(next);
      setSaved(true);
      setTimeout(() => setSaved(false), 1200);
    }
  };
  return (
    <div className="column-desc-editor">
      <input
        type="text"
        className="column-desc-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={save}
        onKeyDown={(e) => e.key === "Enter" && save()}
        placeholder={placeholder}
      />
      {saved && <span className="column-desc-saved">Saved</span>}
    </div>
  );
}

function BulkColumnImport({ onImport, tableColumnNames = [] }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const fileRef = useRef(null);

  const applyParsed = async (result) => {
    const { mapped, skipped, unmatchedInTable, totalInFile } = result;
    const count = Object.keys(mapped).length;
    if (!count) {
      throw new Error(
        "No matching columns found. Check CSV headers (field name, description) and column names."
      );
    }
    await onImport(mapped);
    let message = `Saved ${count} description${count === 1 ? "" : "s"}`;
    if (totalInFile > count) {
      message += ` (${totalInFile - count} row${totalInFile - count === 1 ? "" : "s"} not in this table)`;
    }
    if (skipped.length) {
      message += `. Skipped unknown: ${skipped.slice(0, 3).join(", ")}${skipped.length > 3 ? "…" : ""}`;
    }
    setMsg(message);
    setText("");
    setTimeout(() => setMsg(""), 4000);
    if (unmatchedInTable?.length) {
      console.info("Table columns without descriptions:", unmatchedInTable);
    }
  };

  const apply = async () => {
    setErr("");
    setLoading(true);
    try {
      await applyParsed(parseColumnDictionary(text, tableColumnNames));
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  const onFileChange = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setErr("");
    setLoading(true);
    try {
      const text = await file.text();
      setText(text);
      await applyParsed(parseColumnDictionary(text, tableColumnNames));
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className="secondary small bulk-import-toggle" onClick={() => setOpen(true)}>
        Import CSV / descriptions
      </button>
    );
  }

  return (
    <div className="bulk-column-import">
      <div className="bulk-column-import-head">
        <strong>Import column descriptions</strong>
        <button type="button" className="ghost small" onClick={() => setOpen(false)}>Close</button>
      </div>
      <p className="muted small">
        Upload a CSV with headers <code>field name</code>, <code>type</code>, <code>description</code>.
        Columns are auto-matched to this table and saved immediately.
      </p>
      <div className="bulk-column-import-actions">
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.tsv,.txt,text/csv"
          className="bulk-file-input"
          onChange={onFileChange}
        />
        <button
          type="button"
          className="primary small"
          onClick={() => fileRef.current?.click()}
          disabled={loading}
        >
          {loading ? "Importing…" : "Choose CSV file"}
        </button>
        <span className="muted small">or paste below</span>
      </div>
      <textarea
        rows={6}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={'field name,type,description\nuser_id,STRING,"Unique identifier…"'}
      />
      <div className="row">
        <button type="button" className="secondary small" onClick={apply} disabled={loading || !text.trim()}>
          Apply paste
        </button>
        {msg && <span className="success-msg">{msg}</span>}
        {err && <span className="error small">{err}</span>}
      </div>
    </div>
  );
}

function ModelYamlImport({ onImported }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const fileRef = useRef(null);

  const summarize = (result) => {
    const added = result.tables.filter((t) => t.created).length;
    const updated = result.tables.length - added;
    const cols = result.tables.reduce((n, t) => n + t.columns_imported, 0);
    const rels = result.tables.reduce((n, t) => n + t.relations_imported, 0);
    let message = `Imported ${result.tables.length} model${result.tables.length === 1 ? "" : "s"}`;
    if (added) message += ` · ${added} table${added === 1 ? "" : "s"} added`;
    if (updated) message += ` · ${updated} updated`;
    if (cols) message += ` · ${cols} column description${cols === 1 ? "" : "s"}`;
    if (rels) message += ` · ${rels} relation${rels === 1 ? "" : "s"}`;
    if (result.join_hints_updated) message += " · join hints updated";
    if (result.errors?.length) {
      message += `. ${result.errors.length} warning${result.errors.length === 1 ? "" : "s"}`;
    }
    return message;
  };

  const runImport = async (body, clearOnSuccess = false) => {
    setErr("");
    setLoading(true);
    try {
      const result = await api.importWorkspaceModels(body);
      if (!result.tables.length && result.errors?.length) {
        throw new Error(result.errors.join("; "));
      }
      await onImported(result);
      setMsg(summarize(result));
      if (clearOnSuccess) setText("");
      setTimeout(() => setMsg(""), 5000);
      if (result.errors?.length) {
        console.warn("Model YAML import warnings:", result.errors);
      }
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  const applyPaste = () => runImport({ yaml: text }, true);

  const pruneToYaml = async () => {
    if (
      !window.confirm(
        "Remove workspace tables that are NOT in workspace_models.yaml?\n\n" +
          "This deletes extras added by full BigQuery dataset sync. " +
          "YAML models (~55 tables) are kept."
      )
    ) {
      return;
    }
    setErr("");
    setLoading(true);
    try {
      const result = await api.pruneWorkspaceToYaml();
      await onImported(result);
      setMsg(
        `Pruned catalog: kept ${result.kept} · removed ${result.removed} ` +
          `(YAML has ${result.yaml_tables} tables)`
      );
      setTimeout(() => setMsg(""), 8000);
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  const onFileChange = async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    const yamls = await Promise.all(files.map((f) => f.text()));
    if (files.length === 1) setText(yamls[0]);
    await runImport(yamls.length === 1 ? { yaml: yamls[0] } : { yamls });
  };

  if (!open) {
    return (
      <button type="button" className="secondary small bulk-import-toggle" onClick={() => setOpen(true)}>
        Import model YAML
      </button>
    );
  }

  return (
    <div className="bulk-column-import model-yaml-import">
      <div className="bulk-column-import-head">
        <strong>Import model YAML</strong>
        <button type="button" className="ghost small" onClick={() => setOpen(false)}>Close</button>
      </div>
      <p className="muted small">
        Upload or paste model YAML. Each document adds its <code>base_sql_table</code> to the workspace,
        saves <code>dimensions</code> as column descriptions, and merges <code>relations</code> into join hints.
        Use <code>---</code> to separate multiple models in one paste.
      </p>
      <div className="bulk-column-import-actions">
        <input
          ref={fileRef}
          type="file"
          accept=".yaml,.yml,text/yaml,application/x-yaml"
          className="bulk-file-input"
          multiple
          onChange={onFileChange}
        />
        <button
          type="button"
          className="primary small"
          onClick={() => fileRef.current?.click()}
          disabled={loading}
        >
          {loading ? "Importing…" : "Choose YAML file(s)"}
        </button>
        <span className="muted small">or paste below</span>
      </div>
      <textarea
        rows={8}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={`id: z_ccbp_academy_users_master_data\ntype: model\nbase_sql_table: "\`project\`.\`dataset\`.\`table\`"\ndimensions:\n  - id: user_id\n    description: Unique identifier…`}
        spellCheck={false}
      />
      <div className="row">
        <button
          type="button"
          className="secondary small"
          onClick={applyPaste}
          disabled={loading || !text.trim()}
        >
          Apply paste
        </button>
        <button
          type="button"
          className="secondary small"
          onClick={pruneToYaml}
          disabled={loading}
          title="Remove tables not listed in workspace_models.yaml"
        >
          Keep only YAML tables
        </button>
        {msg && <span className="success-msg">{msg}</span>}
        {err && <span className="error small">{err}</span>}
      </div>
    </div>
  );
}

export default function DataBrowserPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const canEdit = isAdmin();
  const [project, setProject] = useState(null);
  const [projects, setProjects] = useState([]);
  const [projectTables, setProjectTables] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [warehouseOk, setWarehouseOk] = useState(false);
  const [tablesByDataset, setTablesByDataset] = useState({});
  const [expandedDatasets, setExpandedDatasets] = useState(new Set());
  const [selectedTable, setSelectedTable] = useState(null);
  const [metadata, setMetadata] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewNote, setPreviewNote] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [detailTab, setDetailTab] = useState("preview");
  const [browserTab, setBrowserTab] = useState("models");
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [overviewError, setOverviewError] = useState("");
  const [search, setSearch] = useState("");
  const [colSearch, setColSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [warehouseError, setWarehouseError] = useState("");
  const [warehouseLoading, setWarehouseLoading] = useState(false);
  const [manualTable, setManualTable] = useState("");
  const [joinHints, setJoinHints] = useState("");
  const [hintsMsg, setHintsMsg] = useState("");
  const [endorseMsg, setEndorseMsg] = useState("");
  const [showNewProject, setShowNewProject] = useState(false);
  const [newName, setNewName] = useState("");
  const [projectLoading, setProjectLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [defaultDatasetFullId, setDefaultDatasetFullId] = useState("");
  const [serviceAccountEmail, setServiceAccountEmail] = useState("");
  const [datasetCount, setDatasetCount] = useState(0);

  const loadData = async () => {
    setProjectLoading(true);
    setLoadError("");
    try {
      const list = await api.listProjects();
      setProjects(list);
      const tables = await api.listWorkspaceTables();
      setProjectTables(tables);
      setBrowserTab("models");
      if (id) {
        try {
          const p = await api.getProject(id);
          setProject(p);
        } catch {
          setProject(null);
          navigate("/data", { replace: true });
          return;
        }
      } else {
        setProject(null);
      }
    } catch (e) {
      setLoadError(e.message || "Could not load workspace data");
      setProject(null);
    } finally {
      setProjectLoading(false);
    }
  };

  const projectTable = useMemo(
    () => projectTables.find((t) => t.full_table_id === selectedTable),
    [projectTables, selectedTable]
  );

  const loadTableJoinHints = async (tableId) => {
    if (!tableId) {
      setJoinHints("");
      return;
    }
    try {
      const hints = await api.getTableJoinHints(tableId);
      setJoinHints(hints.join_hints || "");
    } catch (e) {
      setJoinHints("");
      setError(e.message);
    }
  };

  useEffect(() => {
    loadTableJoinHints(projectTable?.id);
    setHintsMsg("");
  }, [projectTable?.id]);

  const filteredColumns = useMemo(() => {
    if (!metadata?.columns) return [];
    const notes = projectTable?.column_descriptions || {};
    const cols = metadata.columns.map((c) => {
      const custom = (notes[c.name] || "").trim();
      const bq = (c.description || "").trim();
      return {
        ...c,
        description: custom || bq,
        bqDescription: bq,
        hasCustomDescription: Boolean(custom),
      };
    });
    const q = colSearch.trim().toLowerCase();
    if (!q) return cols;
    return cols.filter(
      (c) => c.name.toLowerCase().includes(q) || (c.description || "").toLowerCase().includes(q)
    );
  }, [metadata, colSearch, projectTable]);

  const [warehouseLoaded, setWarehouseLoaded] = useState(false);
  const [bulkAdding, setBulkAdding] = useState(null);
  const [bulkMsg, setBulkMsg] = useState("");

  const loadSetupInfo = async () => {
    try {
      const status = await api.getSetupStatus();
      if (status.service_account_email) {
        setServiceAccountEmail(status.service_account_email);
      }
      if (status.default_dataset_full_id) {
        setDefaultDatasetFullId(status.default_dataset_full_id);
      }
      if (status.dataset_count != null) {
        setDatasetCount(status.dataset_count);
      }
      if (!status.gcp_ok) {
        setWarehouseOk(false);
        setWarehouseError(status.issues?.join(" ") || "BigQuery not configured");
        return false;
      }
      return true;
    } catch (e) {
      setWarehouseOk(false);
      setWarehouseError(e.message);
      return false;
    }
  };

  const loadDatasets = async () => {
    if (warehouseLoaded || warehouseLoading) return;
    const gcpOk = await loadSetupInfo();
    if (!gcpOk) {
      setBrowserTab("models");
      return;
    }
    setWarehouseLoading(true);
    try {
      const catalog = await api.getWarehouseCatalog();
      setDatasets(catalog.datasets);
      setTablesByDataset(catalog.tables_by_dataset || {});
      setExpandedDatasets(new Set(catalog.datasets.map((d) => d.full_id)));
      setDatasetCount(catalog.datasets.length);
      setWarehouseOk(true);
      setWarehouseError("");
      setWarehouseLoaded(true);
    } catch (e) {
      setWarehouseOk(false);
      setWarehouseError(e.message);
      setBrowserTab("models");
    } finally {
      setWarehouseLoading(false);
    }
  };

  useEffect(() => {
    setSelectedTable(null);
    setMetadata(null);
    setPreview(null);
    setPreviewNote("");
    setPreviewError("");
    setWarehouseLoaded(false);
    setDatasets([]);
    setTablesByDataset({});
    setWarehouseOk(false);
    setWarehouseError("");
    loadData();
    loadSetupInfo();
  }, [id]);

  useEffect(() => {
    if (projectLoading || !projectTables.length) return;
    const hasSelection = selectedTable
      && projectTables.some((t) => t.full_table_id === selectedTable);
    if (!hasSelection) {
      selectTable(projectTables[0].full_table_id);
    }
  }, [id, projectLoading, projectTables, selectedTable]);

  const openWarehouseTab = () => {
    setBrowserTab("warehouse");
    loadDatasets();
  };

  const toggleDataset = async (fullId) => {
    const next = new Set(expandedDatasets);
    if (next.has(fullId)) {
      next.delete(fullId);
    } else {
      next.add(fullId);
      if (!tablesByDataset[fullId]) {
        try {
          const tables = await api.listWarehouseTables(fullId);
          setTablesByDataset((m) => ({ ...m, [fullId]: tables }));
        } catch (e) {
          setWarehouseError(e.message);
        }
      }
    }
    setExpandedDatasets(next);
  };

  const selectTable = async (fullTableId) => {
    setSelectedTable(fullTableId);
    setMetadata(null);
    setPreview(null);
    setPreviewNote("");
    setPreviewError("");
    setDetailTab("preview");
    setColSearch("");
    setError("");
    setLoading(true);
    try {
      const meta = await api.getTableMetadata(fullTableId);
      setMetadata(meta);
      try {
        const prev = await api.previewTable(fullTableId);
        setPreview(prev);
        setPreviewNote(prev.note || meta.preview_note || "");
        setPreviewError("");
      } catch (e) {
        const msg = String(e.message);
        const permissionMsg =
          "NexA queries BigQuery as the service account" +
          (serviceAccountEmail ? ` ${serviceAccountEmail}` : "") +
          ", not your personal Google login. " +
          "Columns load with metadata access, but row preview and Ask need " +
          "roles/bigquery.jobUser (project) and roles/bigquery.dataViewer (on this dataset) " +
          "granted to that service account in GCP IAM.";
        if (msg.includes("403") || msg.includes("Access Denied") || msg.includes("permission")) {
          setPreviewError(permissionMsg);
          setDetailTab("columns");
        } else {
          const err = `Row preview unavailable: ${msg}`;
          setPreviewError(err);
        }
      }
    } catch (e) {
      setError(`Could not load table: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const addToProject = async (fullTableId) => {
    const fq = (fullTableId || manualTable).trim();
    if (!fq) return;
    if (fq.split(".").length !== 3) {
      setError(
        "Use the full BigQuery name: project.dataset.table — e.g. " +
        "kossip-helpers.academy_success_ai_analytics_worksapce.z_ccbp_academy_users_master_data"
      );
      return;
    }
    try {
      const t = await api.addWorkspaceTable(fq);
      setProjectTables((list) => {
        const exists = list.some((x) => x.id === t.id);
        return exists ? list.map((x) => (x.id === t.id ? t : x)) : [...list, t];
      });
      setManualTable("");
      await selectTable(fq);
    } catch (e) {
      setError(e.message);
    }
  };

  const addAllFromDataset = async (datasetFullId) => {
    if (bulkAdding) return;
    setBulkAdding(datasetFullId);
    setError("");
    try {
      const res = await api.bulkAddWorkspaceTables(datasetFullId);
      await loadData();
      setBulkMsg(
        res.added.length
          ? `Added ${res.added.length} table${res.added.length === 1 ? "" : "s"} — profiling in the background.`
          : "All tables in this dataset are already in the workspace.",
      );
      setTimeout(() => setBulkMsg(""), 5000);
    } catch (e) {
      setError(e.message);
    } finally {
      setBulkAdding(null);
    }
  };

  const updateProjectTable = async (patch) => {
    if (!projectTable) return;
    try {
      const updated = await api.updateWorkspaceTable(projectTable.id, patch);
      setProjectTables((list) => list.map((t) => (t.id === updated.id ? updated : t)));
      if (patch.endorsed != null) {
        setEndorseMsg(
          updated.endorsed
            ? "Endorsed — applies to all projects in this workspace"
            : "Endorsement removed for all projects",
        );
        setTimeout(() => setEndorseMsg(""), 2500);
      }
    } catch (e) {
      setError(e.message);
    }
  };

  const saveColumnDescription = async (columnName, description) => {
    if (!projectTable) return;
    const next = { ...(projectTable.column_descriptions || {}) };
    if (description.trim()) {
      next[columnName] = description.trim();
    } else {
      delete next[columnName];
    }
    await updateProjectTable({ column_descriptions: next });
  };

  const generateAiOverview = async () => {
    if (!projectTable) return;
    setOverviewLoading(true);
    setOverviewError("");
    try {
      const updated = await api.refreshTableAiOverview(projectTable.id);
      setProjectTables((list) => list.map((t) => (t.id === updated.id ? updated : t)));
    } catch (e) {
      setOverviewError(e.message || "Profiling failed — check BigQuery access.");
    } finally {
      setOverviewLoading(false);
    }
  };

  const importColumnDescriptions = async (parsed) => {
    if (!projectTable) return;
    const next = { ...(projectTable.column_descriptions || {}), ...parsed };
    await updateProjectTable({ column_descriptions: next });
  };

  const removeFromProject = async () => {
    if (!projectTable) return;
    if (!confirm("Remove this table from the workspace? It will disappear from all projects.")) return;
    await api.removeWorkspaceTable(projectTable.id);
    setProjectTables((list) => list.filter((t) => t.id !== projectTable.id));
    setSelectedTable(null);
    setMetadata(null);
    setPreview(null);
  };

  const fixTableId = async () => {
    if (!projectTable || projectTable.full_table_id.split(".").length === 3) return;
    const status = await api.getSetupStatus();
    const base = status.default_dataset_full_id
      || datasets[0]?.full_id
      || `${status.gcp_project || "kossip-helpers"}.DATASET_NAME`;
    const tableName = projectTable.full_table_id.split(".").pop();
    const fullId = `${base}.${tableName}`;
    try {
      await api.removeWorkspaceTable(projectTable.id);
      const t = await api.addWorkspaceTable(fullId);
      if (projectTable.description) {
        await api.updateWorkspaceTable(t.id, { description: projectTable.description });
      }
      await loadData();
      await selectTable(fullId);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  };

  const isInvalidTableId = (fq) => fq && fq.split(".").length !== 3;

  const saveJoinHints = async () => {
    if (!projectTable) return;
    try {
      const saved = await api.saveTableJoinHints(projectTable.id, joinHints);
      setJoinHints(saved.join_hints || "");
      setHintsMsg(`Join hints saved for ${projectTable.full_table_id.split(".").pop()}.`);
      setTimeout(() => setHintsMsg(""), 2500);
    } catch (e) {
      setError(e.message);
    }
  };

  const filteredProjectTables = projectTables.filter((t) =>
    t.full_table_id.toLowerCase().includes(search.toLowerCase())
  );

  const visibleDatasets = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return datasets;
    return datasets.filter((ds) =>
      ds.dataset_id.toLowerCase().includes(q)
      || (tablesByDataset[ds.full_id] || []).some((t) => t.full_table_id.toLowerCase().includes(q))
    );
  }, [datasets, tablesByDataset, search]);

  const allWarehouseTables = useMemo(() => {
    const rows = [];
    for (const ds of datasets) {
      for (const t of tablesByDataset[ds.full_id] || []) {
        if (t.full_table_id.toLowerCase().includes(search.toLowerCase())) {
          rows.push({ ...t, dataset_id: ds.dataset_id });
        }
      }
    }
    return rows;
  }, [datasets, tablesByDataset, search]);

  const breadcrumbs = selectedTable ? selectedTable.split(".") : [];

  const createProject = async () => {
    if (!newName.trim()) return;
    const p = await api.createProject(newName.trim());
    setShowNewProject(false);
    navigate(`/projects/${p.id}`);
  };

  if (projectLoading) {
    return (
      <AppShell projects={projects} onNewProject={() => setShowNewProject(true)} fullBleed>
        <LoadingScreen message="Loading data browser…" fullScreen />
      </AppShell>
    );
  }

  if (loadError) {
    return (
      <AppShell projects={projects} onNewProject={() => setShowNewProject(true)} fullBleed>
        <div className="error" style={{ margin: 24 }}>
          {loadError}
          <div style={{ marginTop: 12 }}>
            <Link to="/data">← Open data browser</Link>
            {" · "}
            <Link to="/">Home</Link>
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell
      projects={projects}
      activeProjectId={project?.id ?? (id ? Number(id) : undefined)}
      onNewProject={() => setShowNewProject(true)}
      onProjectsChange={() => api.listProjects().then(setProjects)}
      fullBleed
    >
      <SetupBanner />
      {project && (
        <ProjectNav
          projectId={project.id}
          projectName={project.name}
          active="data"
          notebookEnabled={project.notebook_enabled}
        />
      )}
      <div className="data-browser">
        <header className="data-browser-header">
          <h1>Data browser</h1>
          {!canEdit && (
            <p className="data-readonly-banner">
              View only — <strong>{projectTables.length}</strong> table{projectTables.length === 1 ? "" : "s"} connected
              {metadata?.columns?.length ? (
                <> · <strong>{metadata.columns.length}</strong> columns in selected table</>
              ) : null}
              . Contact an admin to add or change tables.
            </p>
          )}
          <div className="data-browser-tabs">
            {canEdit && (
              <>
            <button
              type="button"
              className={browserTab === "recommended" ? "active" : ""}
              onClick={() => setBrowserTab("recommended")}
            >
              Recommended
            </button>
            <button
              type="button"
              className={browserTab === "warehouse" ? "active" : ""}
              onClick={openWarehouseTab}
              title={warehouseError || (warehouseLoading ? "Loading warehouse…" : undefined)}
            >
              Warehouse {warehouseLoading ? "…" : !warehouseOk && warehouseError ? "⚠" : ""}
            </button>
              </>
            )}
            <button
              type="button"
              className={browserTab === "models" ? "active" : ""}
              onClick={() => setBrowserTab("models")}
            >
              {canEdit ? `Data models (${projectTables.length})` : `Tables (${projectTables.length})`}
            </button>
          </div>
        </header>

        <div className="data-browser-body">
          <aside className="data-object-panel">
            <div className="data-object-search">
              <input
                placeholder="Search tables…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            {/* Manual add — admin only */}
            {canEdit && (
            <div className="manual-add-panel">
              <div className="data-object-label">ADD TABLE MANUALLY</div>
              {defaultDatasetFullId ? (
                <p className="muted small dataset-hint">
                  Dataset: <code>{defaultDatasetFullId}</code>
                  {serviceAccountEmail && (
                    <> · <code>{serviceAccountEmail}</code></>
                  )}
                </p>
              ) : serviceAccountEmail && (
                <p className="muted small dataset-hint">
                  Service account: <code>{serviceAccountEmail}</code>
                </p>
              )}
              <input
                placeholder={
                  defaultDatasetFullId
                    ? `${defaultDatasetFullId}.TABLE_NAME`
                    : "kossip-helpers.dataset.TABLE_NAME"
                }
                value={manualTable}
                onChange={(e) => setManualTable(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addToProject()}
              />
              <button className="primary full-width" onClick={() => addToProject()}>
                + Add to workspace
              </button>
              <ModelYamlImport
                onImported={async () => {
                  const fq = selectedTable;
                  await loadData();
                  if (fq) {
                    const tables = await api.listWorkspaceTables();
                    const t = tables.find((x) => x.full_table_id === fq);
                    if (t) await loadTableJoinHints(t.id);
                  }
                  setBrowserTab("models");
                }}
              />
            </div>
            )}

            {(browserTab === "models" || (canEdit && browserTab === "recommended")) && (
              <div className="data-object-section">
                <div className="data-object-label">
                  WORKSPACE TABLES
                  {projectTables.length > 0 && (
                    <span className="data-object-count">
                      {filteredProjectTables.length === projectTables.length
                        ? ` (${projectTables.length})`
                        : ` (${filteredProjectTables.length}/${projectTables.length})`}
                    </span>
                  )}
                </div>
                {filteredProjectTables.length === 0 && (
                  <div className="data-object-empty muted">
                    {canEdit
                      ? "No tables in the workspace yet. Add one above or browse Warehouse."
                      : "No tables in the workspace yet. Ask an admin to add tables."}
                  </div>
                )}
                {filteredProjectTables.map((t) => (
                  <button
                    key={t.id}
                    className={`data-object-item ${selectedTable === t.full_table_id ? "active" : ""}`}
                    onClick={() => selectTable(t.full_table_id)}
                  >
                    <span className="obj-icon table">⊞</span>
                    <span className="obj-name">{t.full_table_id.split(".").pop()}</span>
                    {t.endorsed && <span className="endorse-badge" title="Endorsed for AI (all projects)">★</span>}
                    {t.included_for_ai && <span className="ai-dot" title="Included for AI" />}
                  </button>
                ))}
              </div>
            )}

            {canEdit && browserTab === "warehouse" && warehouseLoading && (
              <div className="data-object-empty muted warehouse-hint">
                <div className="loading-state"><div className="spinner" /><span>Loading all tables…</span></div>
              </div>
            )}

            {canEdit && browserTab === "warehouse" && warehouseOk && !warehouseLoading && (
              <div className="data-object-section">
                <div className="data-object-label">DATASETS ({datasets.length})</div>
                {bulkMsg && <div className="data-object-empty muted warehouse-hint">{bulkMsg}</div>}
                {visibleDatasets.map((ds) => (
                  <div key={ds.full_id}>
                    <div className="dataset-row">
                      <button className="data-object-item dataset" onClick={() => toggleDataset(ds.full_id)}>
                        <span className="obj-chevron">{expandedDatasets.has(ds.full_id) ? "▾" : "▸"}</span>
                        <span className="obj-icon schema">◫</span>
                        <span className="obj-name">{ds.dataset_id}</span>
                      </button>
                      <button
                        type="button"
                        className="mini-add dataset-add-all"
                        title="Add every table in this dataset to the workspace"
                        disabled={bulkAdding != null}
                        onClick={(e) => { e.stopPropagation(); addAllFromDataset(ds.full_id); }}
                      >
                        {bulkAdding === ds.full_id ? "Adding…" : "+ Add all"}
                      </button>
                    </div>
                    {expandedDatasets.has(ds.full_id) && (
                      <div className="data-object-nested">
                        {(tablesByDataset[ds.full_id] || [])
                          .filter((t) => !search || t.full_table_id.toLowerCase().includes(search.toLowerCase()))
                          .map((t) => (
                          <button
                            key={t.full_table_id}
                            className={`data-object-item ${selectedTable === t.full_table_id ? "active" : ""}`}
                            onClick={() => selectTable(t.full_table_id)}
                          >
                            <span className="obj-icon table">⊞</span>
                            <span className="obj-name">{t.table_id}</span>
                            {projectTables.some((p) => p.full_table_id === t.full_table_id) ? (
                              <span className="added-badge">Added</span>
                            ) : (
                              <button
                                className="mini-add"
                                onClick={(e) => { e.stopPropagation(); addToProject(t.full_table_id); }}
                                title="Add to workspace"
                              >+</button>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {canEdit && browserTab === "warehouse" && !warehouseOk && (
              <div className="data-object-empty muted warehouse-hint">{warehouseError}</div>
            )}

            {canEdit && browserTab === "warehouse" && search && allWarehouseTables.length > 0 && (
              <div className="data-object-section">
                <div className="data-object-label">SEARCH RESULTS</div>
                {allWarehouseTables.map((t) => (
                  <button
                    key={t.full_table_id}
                    className={`data-object-item ${selectedTable === t.full_table_id ? "active" : ""}`}
                    onClick={() => selectTable(t.full_table_id)}
                  >
                    <span className="obj-icon table">⊞</span>
                    <span className="obj-name">{t.table_id}</span>
                  </button>
                ))}
              </div>
            )}
          </aside>

          <section className="data-detail-panel">
            {!selectedTable && (
              <div className="data-detail-empty">
                <h2>{projectTables.length ? "Select a workspace table" : canEdit ? "Add tables to the workspace" : "No tables to view"}</h2>
                <p className="muted">
                  {projectTables.length > 0 ? (
                    <>
                      Click a table on the left to view <strong>{projectTables.length}</strong> workspace table{projectTables.length === 1 ? "" : "s"} and their columns
                      {canEdit ? " — descriptions, and preview data." : " (read-only)."}
                    </>
                  ) : canEdit ? (
                    <>
                      Use <strong>Import model YAML</strong> or <strong>ADD TABLE MANUALLY</strong> on the left,
                      or browse <strong>Warehouse</strong> once GCP is connected. Tables are shared across all projects.
                    </>
                  ) : (
                    <>Ask an admin to add tables to the workspace.</>
                  )}
                </p>
                {projectTables.length === 0 && project && (
                  <Link to={`/projects/${project.id}`} className="secondary small" style={{ display: "inline-block", marginTop: 12 }}>
                    ← Back to Thread after adding tables
                  </Link>
                )}
              </div>
            )}

            {selectedTable && (
              <>
                <div className="data-detail-top">
                  <div className="breadcrumbs">
                    <Link to="/">NexA</Link>
                    {breadcrumbs.map((part, i) => (
                      <span key={i}>
                        <span className="bc-sep">/</span>
                        <span className={i === breadcrumbs.length - 1 ? "bc-current" : ""}>{part}</span>
                      </span>
                    ))}
                  </div>
                  <div className="data-detail-actions">
                    {projectTable ? (
                      <>
                        {canEdit && (
                          <>
                        <label className="toggle-label" title="Workspace-wide — affects Ask in every project">
                          <input
                            type="checkbox"
                            checked={projectTable.included_for_ai}
                            onChange={(e) => updateProjectTable({ included_for_ai: e.target.checked })}
                          />
                          Included for AI
                        </label>
                        <button
                          type="button"
                          className={`endorse-btn ${projectTable.endorsed ? "active" : ""}`}
                          title="Mark as the preferred table for AI across all projects (+50 ranking boost in Ask)"
                          onClick={() => updateProjectTable({ endorsed: !projectTable.endorsed })}
                        >
                          {projectTable.endorsed ? "✓ Endorsed" : "Endorse"}
                        </button>
                        {endorseMsg && <span className="success-msg small">{endorseMsg}</span>}
                          </>
                        )}
                        {project && (
                        <button
                          className="primary new-thread-btn"
                          onClick={() => navigate(`/projects/${project.id}`, {
                            state: { question: `Summarize the ${selectedTable.split(".").pop()} table` },
                          })}
                        >
                          ✦ New thread
                        </button>
                        )}
                      </>
                    ) : canEdit ? (
                      <button className="primary" onClick={() => addToProject(selectedTable)}>
                        + Add to workspace
                      </button>
                    ) : null}
                  </div>
                </div>

                <h2 className="data-table-title">{selectedTable.split(".").pop()}</h2>
                <div className="data-table-subtitle muted">
                  {selectedTable}
                  {metadata?.table_type && (
                    <> · {metadata.table_type}{metadata.num_rows != null && metadata.num_rows > 0 ? ` · ${metadata.num_rows.toLocaleString()} rows` : ""}</>
                  )}
                </div>
                {metadata?.preview_note && (
                  <p className="muted preview-meta-note">{metadata.preview_note}</p>
                )}

                {projectTable && (
                  canEdit ? (
                  <>
                  <DescriptionEditor
                    value={projectTable.description}
                    onSave={(description) => updateProjectTable({ description })}
                  />
                  <BusinessRulesEditor
                    value={projectTable.business_rules || ""}
                    onSave={(business_rules) => updateProjectTable({ business_rules })}
                  />
                  </>
                  ) : (
                  <>
                    {projectTable.description ? (
                      <p className="bq-description muted">{projectTable.description}</p>
                    ) : null}
                    {projectTable.business_rules ? (
                      <div className="description-block">
                        <label>Business rules</label>
                        <pre className="bq-description muted" style={{ whiteSpace: "pre-wrap" }}>
                          {projectTable.business_rules}
                        </pre>
                      </div>
                    ) : null}
                  </>
                  )
                )}

                {!projectTable && metadata?.description && (
                  <p className="bq-description muted">{metadata.description}</p>
                )}

                {isInvalidTableId(selectedTable) && projectTable && canEdit && (
                  <div className="error warn fix-id-banner">
                    <strong>Table ID is incomplete.</strong> BigQuery needs{" "}
                    <code>project.dataset.table</code>, not just the table name.
                    <button className="primary" style={{ marginTop: 8 }} onClick={fixTableId}>
                      Fix to {(defaultDatasetFullId || datasets[0]?.full_id || "project.dataset")}.{selectedTable.split(".").pop()}
                    </button>
                  </div>
                )}

                {error && !previewError && <div className="error warn">{error}</div>}
                {previewError && <div className="error warn">{previewError}</div>}
                {loading && (
                  <div className="loading-state"><div className="spinner" /><span>Loading table…</span></div>
                )}

                {metadata && !loading && (
                  <>
                    <div className="data-detail-tabs">
                      <button className={detailTab === "preview" ? "active" : ""} onClick={() => setDetailTab("preview")}>
                        Preview
                      </button>
                      <button className={detailTab === "columns" ? "active" : ""} onClick={() => setDetailTab("columns")}>
                        Columns ({metadata.columns.length})
                      </button>
                      {projectTable && (
                        <button className={detailTab === "overview" ? "active" : ""} onClick={() => setDetailTab("overview")}>
                          AI overview{projectTable.ai_overview ? "" : " ○"}
                        </button>
                      )}
                      <input
                        className="col-search"
                        placeholder="Search columns"
                        value={colSearch}
                        onChange={(e) => setColSearch(e.target.value)}
                      />
                    </div>

                    {detailTab === "preview" && preview && (
                      <div className="preview-grid-wrap">
                        {previewNote && (
                          <p className="muted preview-meta-note">{previewNote}</p>
                        )}
                        <table className="preview-grid">
                          <thead>
                            <tr>{preview.columns.map((c) => <th key={c}>{c}</th>)}</tr>
                          </thead>
                          <tbody>
                            {preview.rows.map((row, i) => (
                              <tr key={i}>
                                {preview.columns.map((c) => (
                                  <td key={c}>{row[c] == null ? "" : String(row[c])}</td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {detailTab === "preview" && !preview && !previewError && (
                      <div className="muted preview-unavailable">No preview available.</div>
                    )}

                    {detailTab === "overview" && projectTable && (
                      <div className="ai-overview-panel">
                        <p className="muted">
                          One-time AI profile: reviews columns, sample data, and date coverage so the AI
                          writes correct SQL (including joins) and knows what periods have data.
                        </p>
                        {canEdit && (
                          <button
                            type="button"
                            className="secondary small"
                            disabled={overviewLoading}
                            onClick={generateAiOverview}
                          >
                            {overviewLoading
                              ? "Profiling table…"
                              : projectTable.ai_overview
                                ? "Regenerate overview"
                                : "Generate overview"}
                          </button>
                        )}
                        {overviewError && <p className="error">{overviewError}</p>}
                        {projectTable.ai_overview ? (
                          <pre className="ai-overview-text">{projectTable.ai_overview}</pre>
                        ) : (
                          !overviewLoading && (
                            <p className="muted">
                              No AI overview yet. It is generated automatically for new tables;
                              {canEdit ? " click Generate overview for this one." : " ask an admin to generate it."}
                            </p>
                          )
                        )}
                      </div>
                    )}

                    {detailTab === "columns" && (
                      <>
                        {!projectTable && (
                          <p className="muted columns-hint">
                            Add this table to the workspace to edit column descriptions for the AI.
                          </p>
                        )}
                        {projectTable && canEdit && (
                          <BulkColumnImport
                            onImport={importColumnDescriptions}
                            tableColumnNames={metadata?.columns?.map((c) => c.name) || []}
                          />
                        )}
                        <table className="columns-table">
                          <thead>
                            <tr><th>Name</th><th>Type</th><th>Description</th></tr>
                          </thead>
                          <tbody>
                            {filteredColumns.map((c) => (
                              <tr key={c.name} className={c.hasCustomDescription ? "col-custom-desc" : ""}>
                                <td><code>{c.name}</code></td>
                                <td className="muted">{c.type}</td>
                                <td>
                                  {projectTable && canEdit ? (
                                    <ColumnDescriptionEditor
                                      value={projectTable.column_descriptions?.[c.name] ?? c.bqDescription}
                                      onSave={(desc) => saveColumnDescription(c.name, desc)}
                                      placeholder={c.bqDescription || "Describe this column…"}
                                    />
                                  ) : (
                                    <span className="muted">{c.description || "—"}</span>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </>
                    )}
                  </>
                )}

                {!metadata && !loading && projectTable && (
                  <div className="muted meta-unavailable">
                    Table saved to workspace. Connect BigQuery to load schema preview.
                  </div>
                )}

                {projectTables.length > 1 && projectTable && (
                  <div className="joins-section">
                    <h3>Joins</h3>
                    <p className="muted">Other workspace tables — click to view.</p>
                    <div className="joins-grid">
                      {projectTables
                        .filter((t) => t.full_table_id !== selectedTable)
                        .map((t) => (
                          <button key={t.id} className="join-card" onClick={() => selectTable(t.full_table_id)}>
                            <div className="join-card-title">{t.full_table_id.split(".").pop()}</div>
                            <div className="join-card-desc muted">
                              {t.description || `Table ${t.full_table_id}`}
                            </div>
                          </button>
                        ))}
                    </div>
                  </div>
                )}

                <div className="joins-section">
                  <h3>Join hints</h3>
                  {projectTable ? (
                    canEdit ? (
                    <>
                  <p className="muted">
                    Relations involving <strong>{projectTable.full_table_id.split(".").pop()}</strong> only.
                    Other tables keep their own join hints — saving here does not overwrite them.
                  </p>
                  <textarea
                    rows={4}
                    value={joinHints}
                    onChange={(e) => setJoinHints(e.target.value)}
                    placeholder={"- z_ccbp_academy_users_jobs_details -> z_ccbp_academy_users_master_data (many_to_one): ${user_id} = ${z_ccbp_academy_users_master_data.user_id}"}
                  />
                  <div className="row">
                    <button className="primary" onClick={saveJoinHints}>Save join hints for this table</button>
                    {hintsMsg && <span className="success-msg">{hintsMsg}</span>}
                  </div>
                    </>
                  ) : (
                    <pre className="join-hints-readonly muted">{joinHints || "No join hints for this table."}</pre>
                  )
                  ) : (
                    <p className="muted">Add this table to the workspace to edit join hints for it.</p>
                  )}
                </div>

                {projectTable && canEdit && (
                  <div className="data-detail-footer">
                    <button className="ghost danger" onClick={removeFromProject}>Remove from workspace</button>
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      </div>

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
              <button onClick={() => setShowNewProject(false)}>Cancel</button>
              <button className="primary" onClick={createProject}>Create</button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
