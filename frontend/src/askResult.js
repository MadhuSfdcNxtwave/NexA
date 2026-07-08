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
  const analysis = (m.summary ?? m.analysis ?? "").trim();
  return {
    question: m.question,
    analysis: analysis || "See the chart, table, or SQL below for your answer.",
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

/** Keep in-flight / just-finished local turns when memory reload is stale. */
export function mergeTurns(localTurns, memoryTurns) {
  const local = localTurns || [];
  const memory = memoryTurns || [];
  if (!local.length) return memory;
  if (!memory.length) return local;

  const byQuestion = new Map();
  for (const turn of memory) {
    byQuestion.set(turn.question.trim(), turn);
  }
  for (const turn of local) {
    const key = turn.question.trim();
    const existing = byQuestion.get(key);
    if (!existing) {
      byQuestion.set(key, turn);
      continue;
    }
    const localRows = turn.rows?.length || 0;
    const memRows = existing.rows?.length || 0;
    const localAnalysis = (turn.analysis || "").length;
    const memAnalysis = (existing.analysis || "").length;
    if (localRows > memRows || localAnalysis > memAnalysis) {
      byQuestion.set(key, turn);
    }
  }
  const memoryOrder = memory.map((t) => t.question.trim());
  const seen = new Set();
  const merged = [];
  for (const key of memoryOrder) {
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(byQuestion.get(key));
  }
  for (const turn of local) {
    const key = turn.question.trim();
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(turn);
  }
  return merged;
}
