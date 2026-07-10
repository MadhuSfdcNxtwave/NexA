import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

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

function escapeCsv(val) {
  if (val == null) return "";
  const s = String(val);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function rowsToCsv(rows, cols) {
  const header = cols.map(escapeCsv).join(",");
  const body = rows.map((row) => cols.map((c) => escapeCsv(row[c])).join(",")).join("\n");
  return `${header}\n${body}`;
}

function DataTable({ rows, cols }) {
  const numericCols = new Set(cols.filter((c) => isNumericCol(rows, c)));
  const pairLayout = cols.length === 2 && numericCols.size === 1;
  const kpiLayout = cols.length === 1 && rows.length <= 1;

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
        {rows.map((row, i) => (
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

function Pager({ page, totalPages, start, end, total, onPrev, onNext }) {
  if (totalPages <= 1) return null;
  return (
    <div className="viz-table-pager">
      <button type="button" className="secondary small" disabled={page <= 1} onClick={onPrev}>
        Prev
      </button>
      <span className="muted small">
        Page {page} of {totalPages} · rows {start + 1}–{end} of {total}
      </span>
      <button type="button" className="secondary small" disabled={page >= totalPages} onClick={onNext}>
        Next
      </button>
    </div>
  );
}

function TableActions({ rows, cols, onCopied }) {
  const [busy, setBusy] = useState("");

  const copyAll = async () => {
    try {
      const text = rowsToCsv(rows, cols);
      await navigator.clipboard.writeText(text);
      setBusy("copied");
      onCopied?.("Copied all rows");
      setTimeout(() => setBusy(""), 1600);
    } catch (_) {
      setBusy("failed");
      setTimeout(() => setBusy(""), 1600);
    }
  };

  const downloadCsv = () => {
    const csv = rowsToCsv(rows, cols);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `nexa-export-${rows.length}-rows.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setBusy("downloaded");
    setTimeout(() => setBusy(""), 1600);
  };

  return (
    <div className="viz-table-actions">
      <button type="button" className="secondary small" onClick={copyAll} title="Copy all rows as CSV">
        {busy === "copied" ? "Copied" : "Copy all"}
      </button>
      <button type="button" className="secondary small" onClick={downloadCsv} title="Download CSV">
        {busy === "downloaded" ? "Downloaded" : "Download CSV"}
      </button>
    </div>
  );
}

/** Styled data table for charts, thread, and dashboard widgets. */
export default function VizTable({ rows = [], columns = [], title, limit = 25, pageSize = 50 }) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(1);
  const [modalPage, setModalPage] = useState(1);
  const [toast, setToast] = useState("");

  useEffect(() => {
    setPage(1);
    setModalPage(1);
  }, [rows, columns, pageSize]);

  useEffect(() => {
    if (!expanded) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") setExpanded(false);
    };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [expanded]);

  if (!rows?.length) return null;
  const cols = columns?.length ? columns : Object.keys(rows[0] || {});
  const cardSize = Math.max(1, pageSize || limit || 25);
  const modalSize = Math.max(cardSize, 100);

  const cardPages = Math.max(1, Math.ceil(rows.length / cardSize));
  const safePage = Math.min(Math.max(1, page), cardPages);
  const cardStart = (safePage - 1) * cardSize;
  const cardRows = rows.slice(cardStart, cardStart + cardSize);
  const cardEnd = Math.min(cardStart + cardRows.length, rows.length);

  const modalPages = Math.max(1, Math.ceil(rows.length / modalSize));
  const safeModalPage = Math.min(Math.max(1, modalPage), modalPages);
  const modalStart = (safeModalPage - 1) * modalSize;
  const modalRows = rows.slice(modalStart, modalStart + modalSize);
  const modalEnd = Math.min(modalStart + modalRows.length, rows.length);

  const flash = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 1600);
  };

  const modal = expanded
    ? createPortal(
        <div
          className="viz-table-modal-overlay"
          onClick={() => setExpanded(false)}
          role="presentation"
        >
          <div
            className="viz-table-modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={title || "Data table"}
          >
            <div className="viz-table-modal-head">
              <div>
                {title ? <h3>{title}</h3> : <h3>Data table</h3>}
                <p className="muted small">
                  {cols.length} columns · {rows.length} rows
                </p>
              </div>
              <div className="viz-table-modal-actions">
                <TableActions rows={rows} cols={cols} onCopied={flash} />
                <button type="button" className="secondary small" onClick={() => setExpanded(false)}>
                  Close
                </button>
              </div>
            </div>
            <Pager
              page={safeModalPage}
              totalPages={modalPages}
              start={modalStart}
              end={modalEnd}
              total={rows.length}
              onPrev={() => setModalPage((p) => Math.max(1, p - 1))}
              onNext={() => setModalPage((p) => Math.min(modalPages, p + 1))}
            />
            <div className="viz-table-wrap viz-table-wrap-full">
              <DataTable rows={modalRows} cols={cols} />
            </div>
            <Pager
              page={safeModalPage}
              totalPages={modalPages}
              start={modalStart}
              end={modalEnd}
              total={rows.length}
              onPrev={() => setModalPage((p) => Math.max(1, p - 1))}
              onNext={() => setModalPage((p) => Math.min(modalPages, p + 1))}
            />
          </div>
        </div>,
        document.body,
      )
    : null;

  return (
    <>
      <div className="viz-table-card">
        <div className="viz-table-head">
          {title ? <div className="chart-title">{title}</div> : <span />}
          <span className="viz-table-meta muted small">
            {cols.length} column{cols.length === 1 ? "" : "s"} · {rows.length} row
            {rows.length === 1 ? "" : "s"}
          </span>
          <TableActions rows={rows} cols={cols} onCopied={flash} />
          <button
            type="button"
            className="secondary small"
            onClick={(e) => {
              e.stopPropagation();
              setModalPage(1);
              setExpanded(true);
            }}
          >
            Expand table
          </button>
        </div>
        {toast && <div className="viz-table-toast muted small">{toast}</div>}
        <div className="viz-table-wrap">
          <DataTable rows={cardRows} cols={cols} />
        </div>
        <Pager
          page={safePage}
          totalPages={cardPages}
          start={cardStart}
          end={cardEnd}
          total={rows.length}
          onPrev={() => setPage((p) => Math.max(1, p - 1))}
          onNext={() => setPage((p) => Math.min(cardPages, p + 1))}
        />
        {rows.length > cardSize && (
          <div className="viz-table-foot muted small">
            Showing {cardStart + 1}–{cardEnd} of {rows.length} rows
            {rows.length >= 500
              ? " · ask «next page» in chat for the next BigQuery page"
              : " · Expand for a larger view"}
          </div>
        )}
      </div>
      {modal}
    </>
  );
}
