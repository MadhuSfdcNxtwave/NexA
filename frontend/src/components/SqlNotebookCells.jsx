/** Hex-style editable SQL notebook cells for multi-step ask results. */
import { useState } from "react";

export default function SqlNotebookCells({
  steps = [],
  combinedSql,
  onRunStep,
  rerunDisabled = false,
}) {
  const [drafts, setDrafts] = useState({});
  const [openStep, setOpenStep] = useState(steps.length ? steps.length - 1 : 0);

  if (!steps?.length) {
    return combinedSql ? <pre className="code-block">{combinedSql}</pre> : null;
  }

  const sqlFor = (step, i) => drafts[i] ?? step.sql ?? "";

  return (
    <div className="sql-notebook-cells">
      {steps.map((step, i) => {
        const isFinal = i === steps.length - 1;
        const hasRows = step.rows?.length > 0;
        return (
          <article
            key={`${step.label}-${i}`}
            className={`sql-notebook-cell${openStep === i ? " open" : ""}${isFinal ? " final" : ""}`}
          >
            <header
              className="sql-notebook-cell-head"
              onClick={() => setOpenStep(openStep === i ? -1 : i)}
              onKeyDown={(e) => e.key === "Enter" && setOpenStep(openStep === i ? -1 : i)}
              role="button"
              tabIndex={0}
            >
              <span className="sql-notebook-cell-num">{i + 1}</span>
              <span className="sql-notebook-cell-title">{step.label || `Step ${i + 1}`}</span>
              {hasRows && (
                <span className="sql-notebook-cell-meta">{step.rows.length} rows</span>
              )}
            </header>
            {openStep === i && (
              <div className="sql-notebook-cell-body">
                {step.question && (
                  <p className="muted small sql-notebook-cell-q">{step.question}</p>
                )}
                <textarea
                  className="sql-edit-area notebook-step-sql"
                  value={sqlFor(step, i)}
                  onChange={(e) => setDrafts((d) => ({ ...d, [i]: e.target.value }))}
                  rows={Math.min(16, Math.max(6, (sqlFor(step, i).match(/\n/g) || []).length + 2))}
                  spellCheck={false}
                />
                <div className="sql-edit-actions">
                  {onRunStep && (
                    <button
                      type="button"
                      className="primary"
                      disabled={rerunDisabled}
                      onClick={() => onRunStep(step.question || "", sqlFor(step, i), i)}
                    >
                      Run cell
                    </button>
                  )}
                </div>
                {hasRows && (
                  <div className="sql-notebook-cell-preview">
                    <table className="viz-table compact">
                      <thead>
                        <tr>
                          {(step.columns || Object.keys(step.rows[0] || {})).map((c) => (
                            <th key={c}>{c}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {step.rows.slice(0, 8).map((row, ri) => (
                          <tr key={ri}>
                            {(step.columns || Object.keys(row)).map((c) => (
                              <td key={c}>{String(row[c] ?? "")}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}
