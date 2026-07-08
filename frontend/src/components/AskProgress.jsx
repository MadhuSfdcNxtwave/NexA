import { useMemo } from "react";

/** User-facing pipeline stages — never show raw table/join debug text. */
const STAGES = [
  { key: "understand", label: "Understanding your question" },
  { key: "tables", label: "Finding the right tables" },
  { key: "columns", label: "Matching columns & joins" },
  { key: "sql", label: "Writing SQL" },
  { key: "run", label: "Running on BigQuery" },
  { key: "answer", label: "Preparing your answer" },
];

function stageIndex(progress) {
  if (!progress) return 0;
  const { phase, tables = [], viewedTables = [], matchedColumns = [] } = progress;

  if (phase === "analyze" || phase === "cache") return 5;
  if (phase === "query" || phase === "chain") return 4;
  if (phase === "sql" || phase === "validate") return 3;
  if (phase === "columns" || phase === "joins") return 2;
  if (viewedTables.length || matchedColumns.length) return 2;
  if (tables.length) return 1;
  if (phase === "plan") return 0;
  return 0;
}

function stageDetail(progress, key) {
  if (!progress) return null;
  const { tables = [], viewedTables = [], matchedColumns = [], joinRelations = [] } = progress;

  if (key === "tables") {
    const n = viewedTables.length || tables.length;
    if (n > 0) return `${n} table${n === 1 ? "" : "s"} in scope`;
  }
  if (key === "columns") {
    if (matchedColumns.length) return `${matchedColumns.length} table${matchedColumns.length === 1 ? "" : "s"} mapped`;
    if (joinRelations.length) return `${joinRelations.length} join${joinRelations.length === 1 ? "" : "s"} planned`;
  }
  if (key === "run" && progress.phase === "chain" && progress.chainStep) {
    return `Step ${progress.chainStep} of ${progress.chainTotal || "?"}`;
  }
  return null;
}

/** Friendly one-liner for banners and compact views. */
export function getFriendlyStageLabel(progress) {
  if (!progress) return "Processing your question…";
  const idx = stageIndex(progress);
  const stage = STAGES[idx] || STAGES[0];
  const detail = stageDetail(progress, stage.key);
  return detail ? `${stage.label} · ${detail}` : stage.label;
}

function StageRow({ stage, state, detail, active }) {
  return (
    <div className={`ask-stage ask-stage-${state}${active ? " ask-stage-active" : ""}`}>
      <span className="ask-stage-icon" aria-hidden>
        {state === "done" && "✓"}
        {state === "active" && <span className="ask-stage-spinner" />}
        {state === "pending" && "○"}
      </span>
      <span className="ask-stage-body">
        <span className="ask-stage-label">{stage.label}</span>
        {detail && state !== "pending" && (
          <span className="ask-stage-detail">{detail}</span>
        )}
      </span>
    </div>
  );
}

/** Engaging staged progress — no raw routing / join-hint dumps. */
export default function AskProgress({ progress, active, compact = false }) {
  const currentIdx = useMemo(() => stageIndex(progress), [progress]);
  const headline = getFriendlyStageLabel(progress);

  if (!progress) return null;

  if (compact) {
    return (
      <div className={`ask-progress-compact ${active ? "active" : ""}`} role="status" aria-live="polite">
        {active && <span className="ask-stage-spinner" aria-hidden />}
        <span className="ask-progress-compact-label">{headline}</span>
        <div className="ask-progress-dots" aria-hidden>
          {STAGES.map((s, i) => (
            <span
              key={s.key}
              className={`ask-progress-dot${i <= currentIdx ? " filled" : ""}${i === currentIdx && active ? " pulse" : ""}`}
            />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className={`ask-progress ${active ? "active" : ""}`} role="status" aria-live="polite">
      <div className="ask-progress-head">
        {active && <span className="ask-stage-spinner" aria-hidden />}
        <span className="ask-progress-title">{active ? "Working on it…" : "Done"}</span>
      </div>

      <div className="ask-progress-stages">
        {STAGES.map((stage, i) => {
          let state = "pending";
          if (i < currentIdx) state = "done";
          else if (i === currentIdx) state = active ? "active" : "done";
          return (
            <StageRow
              key={stage.key}
              stage={stage}
              state={state}
              detail={stageDetail(progress, stage.key)}
              active={active && i === currentIdx}
            />
          );
        })}
      </div>
    </div>
  );
}
