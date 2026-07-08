import { useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../api.js";
import { isAdmin } from "../auth.js";
import AppShell from "../components/AppShell.jsx";
import OrgSchemaGraph from "../components/OrgSchemaGraph.jsx";

function TableCard({ table, selected, onSelect }) {
  const [open, setOpen] = useState(false);
  const isSelected = selected && selected.toLowerCase() === table.short_name.toLowerCase();
  return (
    <div className={`org-schema-card ${isSelected ? "selected" : ""}`} id={`table-${table.short_name}`}>
      <button
        type="button"
        className="org-schema-card-head"
        onClick={() => {
          setOpen(!open);
          onSelect?.(table.short_name);
        }}
        aria-expanded={open}
      >
        <span className="org-schema-caret">{open ? "▾" : "▸"}</span>
        <span className="org-schema-name">{table.short_name}</span>
        <span className="org-schema-badges">
          {table.endorsed && <span className="badge endorsed">Endorsed</span>}
          {!table.included_for_ai && <span className="badge excluded">Excluded from AI</span>}
          {table.has_ai_overview && <span className="badge overview">AI overview</span>}
          <span className="badge cols">{table.column_count} columns</span>
        </span>
      </button>
      <div className="org-schema-fqid">{table.full_table_id}</div>
      {table.description && <p className="org-schema-desc">{table.description}</p>}
      {open && (
        <table className="admin-table org-schema-columns">
          <thead>
            <tr><th>Column</th><th>Description</th></tr>
          </thead>
          <tbody>
            {(table.columns || []).map((c) => (
              <tr key={c.name}>
                <td><code>{c.name}</code></td>
                <td>{c.description || <span className="muted">—</span>}</td>
              </tr>
            ))}
            {(table.columns || []).length === 0 && (
              <tr><td colSpan={2} className="muted">No column metadata saved for this table.</td></tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function OrgSchemaPage() {
  const [schema, setSchema] = useState(null);
  const [projects, setProjects] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [toast, setToast] = useState("");
  const [selectedTable, setSelectedTable] = useState(null);
  const [view, setView] = useState("both");

  if (!isAdmin()) return <Navigate to="/" replace />;

  const load = async () => {
    try {
      const [doc, p] = await Promise.all([api.getOrgSchema(), api.listProjects()]);
      setSchema(doc);
      setProjects(p);
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2500);
  };

  const rebuild = async () => {
    setBusy(true);
    setError("");
    try {
      const doc = await api.rebuildOrgSchema();
      setSchema(doc);
      showToast(`Schema rebuilt — ${doc.table_count} tables saved`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const copyMarkdown = async () => {
    setError("");
    try {
      const { markdown } = await api.getOrgSchemaMarkdown();
      await navigator.clipboard.writeText(markdown);
      showToast("Schema copied to clipboard as markdown");
    } catch (e) {
      setError(e.message);
    }
  };

  const q = search.trim().toLowerCase();
  const filtered = useMemo(() => {
    const tables = schema?.tables || [];
    if (!q) return tables;
    return tables.filter(
      (t) =>
        t.short_name.toLowerCase().includes(q) ||
        t.full_table_id.toLowerCase().includes(q) ||
        (t.description || "").toLowerCase().includes(q) ||
        (t.columns || []).some((c) => c.name.toLowerCase().includes(q))
    );
  }, [schema, q]);

  const generatedAt = schema?.generated_at
    ? new Date(schema.generated_at).toLocaleString()
    : "";

  const selectTable = (name) => {
    setSelectedTable(name || null);
    if (name) {
      requestAnimationFrame(() => {
        document.getElementById(`table-${name}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
  };

  return (
    <AppShell projects={projects} onProjectsChange={load}>
      <div className="admin-page org-schema-page">
        <h1>Org Schema</h1>
        <p className="muted">
          The saved schema memory of every workspace table — descriptions, columns, and join
          hints — exactly what the AI uses to route questions. Admin only.
        </p>
        {error && <div className="error">{error}</div>}
        {toast && <div className="sidebar-toast">{toast}</div>}

        <div className="org-schema-toolbar">
          <input
            className="org-schema-search"
            placeholder="Search tables or columns…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <button type="button" className="secondary" onClick={copyMarkdown} disabled={!schema}>
            Copy as markdown
          </button>
          <button type="button" className="primary" onClick={rebuild} disabled={busy}>
            {busy ? "Rebuilding…" : "Rebuild schema"}
          </button>
        </div>

        {schema && (
          <p className="muted org-schema-meta">
            {schema.table_count} tables in catalog
            {schema.relation_count != null && ` · ${schema.relation_count} join connections`}
            {q && ` · ${filtered.length} matching "${search}"`}
            {generatedAt && ` · snapshot saved ${generatedAt}`}
          </p>
        )}

        {schema && (
          <div className="org-schema-view-tabs">
            <button
              type="button"
              className={view === "graph" ? "active" : ""}
              onClick={() => setView("graph")}
            >
              Connections
            </button>
            <button
              type="button"
              className={view === "tables" ? "active" : ""}
              onClick={() => setView("tables")}
            >
              Table catalog
            </button>
            <button
              type="button"
              className={view === "both" ? "active" : ""}
              onClick={() => setView("both")}
            >
              Both
            </button>
          </div>
        )}

        {!schema && !error && <p className="muted">Loading schema…</p>}

        {schema && (view === "graph" || view === "both") && (
          <section className="admin-section org-schema-graph-section">
            <OrgSchemaGraph
              tables={schema.tables}
              relations={schema.relations || []}
              search={search}
              selectedTable={selectedTable}
              onSelectTable={selectTable}
            />
          </section>
        )}

        {(view === "tables" || view === "both") && (
        <div className="org-schema-list">
          {filtered.map((t) => (
            <TableCard
              key={t.full_table_id}
              table={t}
              selected={selectedTable}
              onSelect={selectTable}
            />
          ))}
          {schema && filtered.length === 0 && (
            <p className="muted">No tables match "{search}".</p>
          )}
        </div>
        )}

        {schema?.join_hints?.trim() && (
          <section className="admin-section">
            <h2>Workspace join hints</h2>
            <pre className="org-schema-hints">{schema.join_hints}</pre>
          </section>
        )}
      </div>
    </AppShell>
  );
}
