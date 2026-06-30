import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api.js";
import Chart from "../components/Chart.jsx";

export default function ProjectPage() {
  const { id } = useParams();
  const [project, setProject] = useState(null);
  const [tab, setTab] = useState("ask");

  useEffect(() => { api.getProject(id).then(setProject); }, [id]);

  if (!project) return <div className="container"><p className="muted">Loading…</p></div>;

  return (
    <div className="container">
      <header className="topbar">
        <Link to="/" className="logo">NexA</Link>
        <span className="subtitle">/ {project.name}</span>
      </header>

      <div className="tabs">
        <button className={tab === "ask" ? "tab active" : "tab"} onClick={() => setTab("ask")}>Ask</button>
        <button className={tab === "tables" ? "tab active" : "tab"} onClick={() => setTab("tables")}>Data tables</button>
      </div>

      {tab === "tables" ? <DataTables id={id} /> : <AskSection id={id} />}
    </div>
  );
}

function DataTables({ id }) {
  const [tables, setTables] = useState([]);
  const [newTable, setNewTable] = useState("");
  const [hints, setHints] = useState("");
  const [schema, setSchema] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => {
    api.listTables(id).then(setTables);
    api.getProject(id).then((p) => setHints(p.join_hints || ""));
  };
  useEffect(() => { load(); }, [id]);

  const add = async () => {
    if (!newTable.trim()) return;
    await api.addTable(id, newTable.trim());
    setNewTable("");
    load();
  };
  const remove = async (tid) => { await api.removeTable(id, tid); load(); };
  const saveHints = async () => { await api.saveJoinHints(id, hints); setMsg("Saved."); setTimeout(() => setMsg(""), 1500); };
  const refresh = async () => {
    setSchema("Loading…");
    try { setSchema((await api.getSchema(id)).schema); }
    catch (e) { setSchema("Error: " + e.message); }
  };

  return (
    <div className="section">
      <h2>Tables in this project</h2>
      <p className="muted">Add BigQuery tables as <code>project.dataset.table</code>. Only these are visible to the model.</p>
      <div className="row">
        <input placeholder="nxtwave-analytics.analytics.users" value={newTable}
          onChange={(e) => setNewTable(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        <button className="primary" onClick={add}>Add table</button>
      </div>
      <ul className="table-list">
        {tables.map((t) => (
          <li key={t.id}><code>{t.full_table_id}</code><button className="ghost" onClick={() => remove(t.id)}>Remove</button></li>
        ))}
        {tables.length === 0 && <p className="muted">No tables yet.</p>}
      </ul>

      <h2>Join hints</h2>
      <p className="muted">Plain English: which column joins to which, table grain, gotchas. Improves multi-table answers.</p>
      <textarea rows={6} value={hints} onChange={(e) => setHints(e.target.value)}
        placeholder={"- users.id joins to events.user_id (one user, many events)\n- money columns are in paise; divide by 100"} />
      <div className="row">
        <button className="primary" onClick={saveHints}>Save hints</button>
        <button onClick={refresh}>Preview schema the model sees</button>
        <span className="muted">{msg}</span>
      </div>
      {schema && <pre className="schema">{schema}</pre>}
    </div>
  );
}

function AskSection({ id }) {
  const [turns, setTurns] = useState([]);   // {question, analysis, sql, rows, columns, chart_spec, bytes_estimate, fromMemory?}
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getMemory(id).then((mem) =>
      setTurns(mem.map((m) => ({ question: m.question, analysis: m.summary, sql: m.sql, fromMemory: true })))
    );
  }, [id]);

  const ask = async () => {
    if (!q.trim() || loading) return;
    const question = q.trim();
    setQ(""); setError(""); setLoading(true);
    try {
      const res = await api.ask(id, question);
      setTurns((t) => [...t, res]);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="section">
      {turns.map((t, i) => (
        <div key={i} className="turn">
          <div className="question"><span className="avatar user">U</span>{t.question}</div>
          <div className="answer">
            <span className="avatar bot">AI</span>
            <div className="answer-body">
              {typeof t.bytes_estimate === "number" &&
                <div className="muted small">~{(t.bytes_estimate / 1048576).toFixed(1)} MB scanned</div>}
              <p>{t.analysis}</p>
              {t.chart_spec && <Chart spec={t.chart_spec} rows={t.rows} />}
              {t.sql && (
                <details>
                  <summary>Data &amp; SQL</summary>
                  <pre className="sql">{t.sql}</pre>
                  {t.rows && t.rows.length > 0 && <DataTable rows={t.rows} columns={t.columns} />}
                </details>
              )}
            </div>
          </div>
        </div>
      ))}

      {loading && <p className="muted">Thinking…</p>}
      {error && <div className="error">{error}</div>}

      <div className="ask-bar">
        <input placeholder="Ask a question about your data…" value={q}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} />
        <button className="primary" onClick={ask} disabled={loading}>Ask</button>
      </div>
    </div>
  );
}

function DataTable({ rows, columns }) {
  const cols = columns || Object.keys(rows[0] || {});
  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.slice(0, 25).map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c}>{String(r[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {rows.length > 25 && <div className="muted small">Showing 25 of {rows.length} rows</div>}
    </div>
  );
}
