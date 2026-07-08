/** Normalize ask stream / memory payloads for UI turns. */

export function normalizeAskResult(res) {
  if (!res || res.type === "error") return null;
  const analysis = (res.analysis ?? res.summary ?? "").trim();
  return {
    question: res.question || "",
    analysis: analysis || "See the chart, table, or SQL below for your answer.",
    sql: res.sql || "",
    chart_spec: res.chart_spec || { chart: "none" },
    rows: res.rows || [],
    viz_rows: res.viz_rows || res.rows || [],
    columns: res.columns || [],
    bytes_estimate: res.bytes_estimate,
    credits_used: res.credits_used,
    credits_remaining: res.credits_remaining,
    sql_steps: res.sql_steps,
    from_cache: !!res.from_cache,
    suggestions: res.suggestions || [],
    response_mode: res.response_mode,
    worked_seconds: res.worked_seconds,
    routing_reason: res.routing_reason,
    selected_tables: res.selected_tables,
    probe_stats: res.probe_stats,
    sql_source: res.sql_source,
    thread_id: res.thread_id,
  };
}

export function memoryToTurn(m) {
  return {
    question: m.question,
    analysis: m.summary,
    sql: m.sql,
    chart_spec: m.chart_spec,
    rows: m.rows,
    columns: m.columns,
    bytes_estimate: m.bytes_estimate,
    credits_used: m.credits_used,
    from_cache: !!m.from_cache,
    fromMemory: true,
  };
}
