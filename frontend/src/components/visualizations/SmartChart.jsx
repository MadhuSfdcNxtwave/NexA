import Chart from "../Chart.jsx";
import VizTable from "../VizTable.jsx";

/** Auto-detect chart type from data shape + question wording. */
export function detectChartType(columns, rows, question = "") {
  const q = (question || "").toLowerCase();
  const ncol = columns?.length || 0;
  const nrow = rows?.length || 0;

  if (ncol === 1 && nrow === 1) return "stat";
  if (!ncol || !nrow) return "table";

  const types = columns.map((c) => {
    const sample = rows.find((r) => r[c] != null)?.[c];
    if (sample == null) return "string";
    if (typeof sample === "number") return "number";
    const d = Date.parse(String(sample));
    if (!Number.isNaN(d) && /date|month|week|day|time/i.test(c)) return "date";
    return "string";
  });

  const numCols = columns.filter((_, i) => types[i] === "number");
  const strCols = columns.filter((_, i) => types[i] === "string");
  const dateCols = columns.filter((_, i) => types[i] === "date");

  if (/trend|over time|by month|by week|monthly|weekly/i.test(q) && (dateCols.length || strCols.length) && numCols.length) {
    return "line";
  }
  if (/breakdown|by state|by gender|by company|distribution/i.test(q) && strCols.length && numCols.length) {
    return "bar";
  }
  if (/compare|versus|vs\b/i.test(q) && numCols.length >= 2) {
    return "grouped_bar";
  }
  if (strCols.length === 1 && numCols.length === 1 && nrow <= 15) return "bar";
  if (strCols.length === 1 && numCols.length === 1) return "bar";
  if (dateCols.length === 1 && numCols.length === 1) return "line";
  if (ncol > 5) return "table";
  if (numCols.length && strCols.length) return "bar";
  return "table";
}

function StatCard({ rows, columns }) {
  const col = columns?.[0];
  const val = rows?.[0]?.[col];
  const n = Number(val);
  const display = Number.isFinite(n) ? n.toLocaleString() : String(val ?? "—");
  return (
    <div className="smart-stat-card">
      <div className="smart-stat-value">{display}</div>
      <div className="smart-stat-label muted">{col}</div>
    </div>
  );
}

export default function SmartChart({
  columns = [],
  rows = [],
  question = "",
  chartSpec = null,
  chartType: chartTypeOverride = null,
}) {
  const detected = chartTypeOverride || detectChartType(columns, rows, question);
  const baseSpec = chartSpec && chartSpec.chart ? chartSpec : { chart: detected };

  if (detected === "stat" || (baseSpec.chart === "none" && columns.length === 1 && rows.length === 1)) {
    return <StatCard rows={rows} columns={columns} />;
  }

  if (detected === "table" || baseSpec.chart === "table") {
    if (rows?.length && columns?.length) {
      return <VizTable columns={columns} rows={rows} />;
    }
    return null;
  }

  const spec = { ...baseSpec, chart: baseSpec.chart === "none" ? detected : baseSpec.chart };
  return <Chart spec={spec} rows={rows} vizRows={rows} />;
}
