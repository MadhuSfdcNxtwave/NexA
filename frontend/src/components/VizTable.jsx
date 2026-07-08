import { useEffect, useState } from "react";

function formatCell(val, maxLen = 120) {
  if (val == null || val === "") return { text: "—", title: undefined };
  const s = String(val);
  if (s.length <= maxLen) return { text: s, title: undefined };
  return { text: `${s.slice(0, maxLen)}…`, title: s };
}

function isNumericCol(rows, col) {
  const sample = rows.slice(0, 12).map((r) => r[col]);
  return sample.some((v) => v != null && v !== "" && !Number.isNaN(Number(v)));
}

function DataTable({ rows, cols, rowLimit }) {
  const shown = rows.slice(0, rowLimit);
  const numericCols = new Set(cols.filter((c) => isNumericCol(shown, c)));
  const pairLayout = cols.length === 2 && numericCols.size === 1;
  const kpiLayout = cols.length === 1 && shown.length <= 1;

  return (
    <table className={`viz-table${pairLayout ? " viz-table-pair" : ""}${kpiLayout ? " viz-table-kpi" : ""}`}>
      <thead>
        <tr>
          {cols.map((c) => (
            <th key={c} title={c} className={numericCols.has(c) && !kpiLayout ? "num" : ""}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {shown.map((row, i) => (
          <tr key={i}>
            {cols.map((c) => {
              const { text, title } = formatCell(row[c]);
              return (
                <td key={c} title={title} className={numericCols.has(c) && !kpiLayout ? "num" : ""}>
                  {text}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Styled data table for charts, thread, and dashboard widgets. */
export default function VizTable({ rows = [], columns = [], title, limit = 25 }) {
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") setExpanded(false);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", onKey);
    };
  }, [expanded]);

  if (!rows?.length) return null;
  const cols = columns?.length ? columns : Object.keys(rows[0] || {});
  const showing = Math.min(limit, rows.length);

  const tableWrap = (rowLimit, className = "") => (
    <div className={`viz-table-wrap ${className}`.trim()}>
      <DataTable rows={rows} cols={cols} rowLimit={rowLimit} />
    </div>
  );

  return (
    <>
      <div className="viz-table-card">
        <div className="viz-table-head">
          {title ? <div className="chart-title">{title}</div> : <span />}
          <span className="viz-table-meta muted small">
            {cols.length} column{cols.length === 1 ? "" : "s"} · {rows.length} row{rows.length === 1 ? "" : "s"}
          </span>
          <button type="button" className="secondary small" onClick={() => setExpanded(true)}>
            Expand table
          </button>
        </div>
        {tableWrap(limit)}
        {rows.length > limit && (
          <div className="viz-table-foot muted small">
            Showing {showing} of {rows.length} rows · use Expand for full view
          </div>
        )}
      </div>

      {expanded && (
        <div className="viz-table-modal-overlay" onClick={() => setExpanded(false)} role="presentation">
          <div
            className="viz-table-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={title || "Data table"}
          >
            <div className="viz-table-modal-head">
              <div>
                {title && <h3>{title}</h3>}
                <p className="muted small">
                  {cols.length} columns · {rows.length} rows
                </p>
              </div>
              <button type="button" className="secondary small" onClick={() => setExpanded(false)}>
                Close
              </button>
            </div>
            {tableWrap(rows.length, "viz-table-wrap-full")}
          </div>
        </div>
      )}
    </>
  );
}
