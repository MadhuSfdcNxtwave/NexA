/** Client-side chart prep (mirrors backend chart_prepare for loaded memory rows). */

const TOP_N = 12;
const TEXT_COL = /reason|feedback|comment|description|title|name|label|text|verbatim/i;

function isNumeric(val) {
  if (val == null || val === "") return false;
  return !Number.isNaN(Number(val));
}

function looksTextColumn(rows, col) {
  const sample = rows.slice(0, 40).map((r) => r[col]).filter((v) => v != null && v !== "");
  if (!sample.length) return false;
  if (sample.slice(0, 8).some(isNumeric)) return false;
  const avgLen = sample.reduce((n, v) => n + String(v).length, 0) / sample.length;
  if (avgLen > 18) return true;
  if (TEXT_COL.test(col)) return true;
  const unique = new Set(sample.map((v) => String(v).trim().toLowerCase())).size;
  return unique >= Math.min(8, sample.length * 0.6);
}

function truncateLabel(text, maxLen = 72) {
  const s = String(text).replace(/\s+/g, " ").trim();
  return s.length <= maxLen ? s : `${s.slice(0, maxLen - 1)}…`;
}

function aggregateValueCounts(rows, col, topN = TOP_N) {
  const counts = new Map();
  for (const row of rows) {
    const raw = row[col];
    const key = raw == null || String(raw).trim() === "" ? "(blank)" : truncateLabel(raw);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, topN);
  const shown = ranked.reduce((n, [, c]) => n + c, 0);
  const other = rows.length - shown;
  if (other > 0) ranked.push(["Other", other]);
  const countCol = "count";
  return {
    rows: ranked.map(([label, n]) => ({ [col]: label, [countCol]: n })),
    x: col,
    y: countCol,
  };
}

export function prepareChartData(rows, spec) {
  if (!rows?.length || !spec || spec.chart === "none") {
    return { rows: rows || [], spec: spec || { chart: "none" } };
  }

  const columns = Object.keys(rows[0] || {});
  const next = { ...spec };

  if (columns.length === 1 && rows.length > 6 && looksTextColumn(rows, columns[0])) {
    const { rows: agg, x, y } = aggregateValueCounts(rows, columns[0]);
    return {
      rows: agg,
      spec: {
        chart: "bar",
        x,
        y,
        color: null,
        title: next.title || `Top ${columns[0].replace(/_/g, " ")} (by count)`,
        horizontal: true,
        variant: "ranked",
      },
    };
  }

  const { x, y } = next;
  if (
    next.chart === "bar"
    && x
    && y
    && columns.includes(x)
    && columns.includes(y)
    && looksTextColumn(rows, x)
    && rows.length > 8
  ) {
    const { rows: agg, x: x2, y: y2 } = aggregateValueCounts(rows, x);
    return {
      rows: agg,
      spec: { ...next, x: x2, y: y2, horizontal: true, variant: "ranked" },
    };
  }

  if (next.horizontal == null && next.chart === "bar" && x && looksTextColumn(rows, x)) {
    next.horizontal = true;
  }

  return { rows, spec: next };
}
