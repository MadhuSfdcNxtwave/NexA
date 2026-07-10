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

function selectedTableNames(progress) {
  if (!progress) return [];
  const viewed = progress.viewedTables || [];
  if (viewed.length) {
    return viewed.map((t) => t.short_name || t.full_table_id?.split(".").pop() || String(t));
  }
  const selected = (progress.tables || []).filter((t) => t.selected);
  if (selected.length) {
    return selected.map((t) => t.short_name || t.full_table_id?.split(".").pop() || String(t));
  }
  return [];
}

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
  const { matchedColumns = [], joinRelations = [] } = progress;
  const names = selectedTableNames(progress);

  if (key === "tables") {
    if (names.length === 1) return names[0];
    if (names.length > 1) return `${names.length} tables locked`;
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
  const names = selectedTableNames(progress);
  if (names.length) {
    const tableBit = names.length === 1 ? names[0] : names.slice(0, 2).join(", ");
    const idx = stageIndex(progress);
    if (idx <= 1) return `Using table: ${tableBit}`;
    const stage = STAGES[idx] || STAGES[0];
    return `${stage.label} · ${tableBit}`;
  }
  const idx = stageIndex(progress);
  const stage = STAGES[idx] || STAGES[0];
  const detail = stageDetail(progress, stage.key);
  return detail ? `${stage.label} · ${detail}` : stage.label;
}

function TableChips({ names, reason }) {
  if (!names?.length) return null;
  return (
    <div className="ask-selected-tables">
      <span className="ask-selected-tables-label">Using table{names.length === 1 ? "" : "s"}</span>
      <div className="ask-selected-table-chips">
        {names.map((name) => (
          <code key={name} className="ask-selected-table-chip" title={name}>
            {name}
          </code>
        ))}
      </div>
      {reason ? <p className="ask-selected-tables-reason muted">{reason}</p> : null}
    </div>
  );
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

/** Engaging staged progress — shows locked table name(s) clearly. */
export default function AskProgress({ progress, active, compact = false }) {
  const currentIdx = useMemo(() => stageIndex(progress), [progress]);
  const headline = getFriendlyStageLabel(progress);
  const tableNames = useMemo(() => selectedTableNames(progress), [progress]);
  const routingReason = progress?.routingReason || "";

  if (!progress) return null;

  if (compact) {
    return (
      <div className={`ask-progress-compact ${active ? "active" : ""}`} role="status" aria-live="polite">
        {active && <span className="ask-stage-spinner" aria-hidden />}
        <div className="ask-progress-compact-body">
          <span className="ask-progress-compact-label">{headline}</span>
          <TableChips names={tableNames} reason={routingReason} />
        </div>
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

      <TableChips names={tableNames} reason={routingReason} />

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
