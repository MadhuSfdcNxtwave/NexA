import { useEffect, useRef, useState } from "react";
import SmartChart from "./visualizations/SmartChart.jsx";
import InsightCard from "./visualizations/InsightCard.jsx";
import VizTable from "./VizTable.jsx";
import SqlNotebookCells from "./SqlNotebookCells.jsx";
import UsageMeta from "./UsageMeta.jsx";
import AskProgress from "./AskProgress.jsx";

function ResultsEmpty() {
  return (
    <div className="results-empty">
      <div className="results-empty-icon" aria-hidden>✦</div>
      <h3>Ask a question to see results</h3>
      <p className="muted">
        NexA picks the right tables, writes SQL, and shows charts here — like Cursor picks the right files.
      </p>
      <ul className="results-demo-tips">
        <li>Users by gender</li>
        <li>NPS average by gender</li>
        <li>Job applications count</li>
      </ul>
    </div>
  );
}

function ResultsSkeleton() {
  return (
    <div className="results-skeleton" aria-busy="true" aria-label="Loading results">
      <div className="skeleton-line w-40" />
      <div className="skeleton-block chart" />
      <div className="skeleton-line w-70" />
      <div className="skeleton-line w-90" />
      <div className="skeleton-block table" />
    </div>
  );
}

function defaultTab(turn) {
  const hasChart = turn.chart_spec?.chart && turn.chart_spec.chart !== "none";
  const hasTable = turn.rows?.length > 0 && turn.columns?.length > 0;
  if (hasChart) return "chart";
  if (hasTable) return "table";
  return "sql";
}

function TurnResultCard({
  turn,
  turnIndex,
  isActive,
  onSelect,
  loading,
  askProgress,
  onPin,
  pinDisabled,
  onRerunSql,
  rerunDisabled,
  cardRef,
}) {
  const [activeTab, setActiveTab] = useState(null);
  const [editSql, setEditSql] = useState(false);
  const [sqlDraft, setSqlDraft] = useState("");

  const routingLine = [
    turn.routing_reason,
    turn.selected_tables?.length
      ? `Tables: ${turn.selected_tables.map((t) => `\`${t}\``).join(", ")}`
      : "",
    turn.probe_stats,
    turn.sql_source ? `SQL: ${turn.sql_source}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const tab = activeTab || defaultTab(turn);
  const hasChart = turn.chart_spec?.chart && turn.chart_spec.chart !== "none";
  const hasTable = turn.rows?.length > 0 && turn.columns?.length > 0;
  const showSqlEditor = editSql || tab === "sql";
  const showProgress = loading && isActive && askProgress;

  const openSqlEdit = () => {
    setSqlDraft(turn.sql || "");
    setEditSql(true);
    setActiveTab("sql");
  };

  return (
    <article
      ref={cardRef}
      className={`analysis-turn-block ${isActive ? "active" : ""}`}
      onClick={() => onSelect?.(turnIndex)}
    >
      <header className="results-header">
        <span className="results-header-label">Turn {turnIndex + 1}</span>
        <h2 className="results-question">{turn.question}</h2>
        {typeof turn.worked_seconds === "number" && turn.worked_seconds > 0 && (
          <p className="results-worked muted">
            Worked for {turn.worked_seconds < 60
              ? `${Math.round(turn.worked_seconds)} seconds`
              : `${Math.floor(turn.worked_seconds / 60)} min ${Math.round(turn.worked_seconds % 60)} sec`}
          </p>
        )}
        <UsageMeta
          bytes_estimate={turn.bytes_estimate}
          credits_used={turn.credits_used}
          credits_remaining={turn.credits_remaining}
          from_cache={turn.from_cache}
        />
      </header>

      {routingLine && <p className="results-routing muted">{routingLine}</p>}

      <InsightCard
        analysis={turn.analysis}
        sqlSource={turn.sql_source}
        modelUsed={turn.model_used}
        suggestions={turn.suggestions}
      />

      {showProgress && (
        <div className="results-loading-overlay">
          <AskProgress progress={askProgress} active />
        </div>
      )}

      <div className="results-tabs" role="tablist" aria-label="Result views">
        {hasChart && (
          <button
            type="button"
            role="tab"
            className={tab === "chart" ? "active" : ""}
            aria-selected={tab === "chart"}
            onClick={(e) => { e.stopPropagation(); setActiveTab("chart"); setEditSql(false); }}
          >
            Chart
          </button>
        )}
        {hasTable && (
          <button
            type="button"
            role="tab"
            className={tab === "table" ? "active" : ""}
            aria-selected={tab === "table"}
            onClick={(e) => { e.stopPropagation(); setActiveTab("table"); setEditSql(false); }}
          >
            Table
          </button>
        )}
        {turn.sql && (
          <button
            type="button"
            role="tab"
            className={tab === "sql" ? "active" : ""}
            aria-selected={tab === "sql"}
            onClick={(e) => { e.stopPropagation(); setActiveTab("sql"); setEditSql(false); }}
          >
            SQL
          </button>
        )}
      </div>

      {tab === "chart" && hasChart && (
        <section className="results-section glass-card">
          <SmartChart
            columns={turn.columns}
            rows={turn.viz_rows?.length ? turn.viz_rows : turn.rows}
            question={turn.question}
            chartSpec={turn.chart_spec}
          />
        </section>
      )}

      {tab === "table" && hasTable && (
        <section className="results-section glass-card">
          <VizTable rows={turn.rows} columns={turn.columns} limit={100} />
        </section>
      )}

      {tab === "sql" && turn.sql && (
        <section className="results-section glass-card">
          {showSqlEditor && onRerunSql ? (
            <>
              <textarea
                className="sql-edit-area"
                value={sqlDraft || turn.sql}
                onChange={(e) => setSqlDraft(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                rows={12}
                spellCheck={false}
              />
              <div className="sql-edit-actions">
                <button
                  type="button"
                  className="primary"
                  disabled={rerunDisabled}
                  onClick={(e) => {
                    e.stopPropagation();
                    onRerunSql(turn.question, sqlDraft || turn.sql);
                  }}
                >
                  Run edited SQL
                </button>
                <button type="button" className="ghost" onClick={(e) => { e.stopPropagation(); setEditSql(false); }}>
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <>
              {turn.sql_steps?.length ? (
                <SqlNotebookCells
                  steps={turn.sql_steps}
                  combinedSql={turn.sql}
                  onRunStep={onRerunSql}
                  rerunDisabled={rerunDisabled}
                />
              ) : (
                <pre className="code-block">{turn.sql}</pre>
              )}
              {onRerunSql && (
                <button type="button" className="ghost sql-edit-btn" onClick={(e) => { e.stopPropagation(); openSqlEdit(); }}>
                  Edit SQL &amp; re-run
                </button>
              )}
            </>
          )}
        </section>
      )}

      {turn.sql && onPin && (
        <button
          type="button"
          className="primary results-pin-btn"
          onClick={(e) => { e.stopPropagation(); onPin(turn); }}
          disabled={pinDisabled}
        >
          Add to dashboard
        </button>
      )}
    </article>
  );
}

/** Analysis pane — scrollable feed of all thread answers. */
export default function ThreadResultsPanel({
  turns = [],
  activeTurnIdx = -1,
  onSelectTurn,
  turn,
  loading,
  askProgress,
  threadOverview = "",
  onPin,
  pinDisabled,
  onRerunSql,
  rerunDisabled,
}) {
  const feedRef = useRef(null);
  const turnRefs = useRef([]);
  const list = turns?.length ? turns : turn ? [turn] : [];
  const activeIdx = turns?.length
    ? (activeTurnIdx >= 0 && activeTurnIdx < turns.length ? activeTurnIdx : turns.length - 1)
    : 0;

  useEffect(() => {
    const el = turnRefs.current[activeIdx];
    if (el?.scrollIntoView) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [activeIdx, list.length, loading]);

  if (loading && !list.length) {
    return (
      <div className="results-panel-inner">
        {askProgress ? <AskProgress progress={askProgress} active /> : <ResultsSkeleton />}
      </div>
    );
  }

  if (!list.length) {
    return (
      <div className="results-panel-inner">
        <ResultsEmpty />
      </div>
    );
  }

  return (
    <div className="results-panel-inner analysis-feed" ref={feedRef}>
      {threadOverview && (
        <details className="thread-overview-card glass-card">
          <summary>Thread memory</summary>
          <pre className="thread-overview-body">{threadOverview}</pre>
        </details>
      )}
      {list.map((t, i) => (
        <TurnResultCard
          key={`${i}-${t.question?.slice(0, 32)}`}
          turn={t}
          turnIndex={i}
          isActive={i === activeIdx}
          onSelect={onSelectTurn}
          loading={loading}
          askProgress={i === activeIdx ? askProgress : null}
          onPin={onPin}
          pinDisabled={pinDisabled}
          onRerunSql={onRerunSql}
          rerunDisabled={rerunDisabled}
          cardRef={(el) => { turnRefs.current[i] = el; }}
        />
      ))}
    </div>
  );
}

export { ResultsSkeleton, ResultsEmpty };
