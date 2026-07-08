import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  ScatterChart,
  Scatter,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
  LabelList,
} from "recharts";

import VizTable from "./VizTable.jsx";
import { prepareChartData } from "../utils/chartPrepare.js";

const COLORS_MONO = [
  "#4f46e5",
  "#2563eb",
  "#6366f1",
  "#0891b2",
  "#7c3aed",
  "#475569",
  "#64748b",
  "#334155",
  "#1e40af",
  "#94a3b8",
];

const COLORS_SEMANTIC = [
  "#22c55e",
  "#fbbf24",
  "#f87171",
  "#3b82f6",
  "#8b5cf6",
  "#14b8a6",
  "#f97316",
  "#6366f1",
  "#ec4899",
  "#64748b",
];

const LINE_COLOR = "#2563eb";

const GRID = "#e2e8f0";
const AXIS = "#64748b";

function displayVal(v) {
  if (v === null || v === undefined || v === "") return "Unknown";
  return String(v);
}

function formatNum(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** Pivot long data into wide format for grouped bars: x × color → y */
function pivotGrouped(rows, x, y, color) {
  const xKey = x;
  const wide = new Map();
  const series = new Set();

  for (const row of rows) {
    const xVal = displayVal(row[xKey]);
    const cVal = displayVal(row[color]);
    series.add(cVal);
    if (!wide.has(xVal)) wide.set(xVal, { [xKey]: xVal });
    wide.get(xVal)[cVal] = row[y];
  }

  return {
    data: Array.from(wide.values()),
    seriesKeys: Array.from(series).sort(),
  };
}

function RichTooltip({ active, payload, label, xKey, extraKeys = [] }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload || {};

  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">{displayVal(label ?? row[xKey])}</div>
      {payload.map((p) => (
        <div key={p.dataKey} className="chart-tooltip-row">
          <span
            className="chart-tooltip-swatch"
            style={{ background: p.color || p.fill }}
          />
          <span className="chart-tooltip-label">{p.name}</span>
          <span className="chart-tooltip-value">{formatNum(p.value)}</span>
        </div>
      ))}
      {extraKeys.map((k) =>
        row[k] !== undefined && !payload.some((p) => p.dataKey === k) ? (
          <div key={k} className="chart-tooltip-extra">
            <span>{k}</span>
            <span>{formatNum(row[k])}</span>
          </div>
        ) : null
      )}
    </div>
  );
}

function tickFormatter(val, maxLen = 14) {
  const s = displayVal(val);
  return s.length > maxLen ? `${s.slice(0, maxLen - 1)}…` : s;
}

function yAxisWidthForLabels(data, xKey, ranked = false) {
  if (!data?.length) return ranked ? 180 : 88;
  const maxLen = Math.max(...data.map((r) => String(r[xKey] ?? "").length), 0);
  const cap = ranked ? 56 : 20;
  return Math.min(280, Math.max(ranked ? 140 : 88, Math.min(maxLen, cap) * 7));
}

function chartHeightForRows(rowCount, horizontal, ranked, base = 320) {
  if (!horizontal) return base;
  const perBar = ranked ? 36 : 28;
  const h = Math.max(base, rowCount * perBar + 48);
  return Math.min(560, h);
}

export default function Chart({
  spec,
  rows,
  vizRows,
  horizontal: horizontalProp,
  palette = "mono",
  height: heightProp,
  dashboard = false,
}) {
  const prepared = prepareChartData(vizRows || rows, spec);
  const chartRows = prepared.rows;
  const chartSpec = prepared.spec;

  if (!chartSpec || chartSpec.chart === "none" || !chartRows || chartRows.length === 0) return null;

  const { chart, x, y, color, title, variant } = chartSpec;
  const horizontal = horizontalProp ?? chartSpec.horizontal ?? false;
  const ranked = variant === "ranked" || (horizontal && chart === "bar");
  const height = heightProp ?? chartHeightForRows(chartRows.length, horizontal, ranked);
  const COLORS = palette === "semantic" ? COLORS_SEMANTIC : COLORS_MONO;

  if (chart === "table") {
    const columns = Object.keys(chartRows[0] || {});
    return <VizTable rows={chartRows} columns={columns} title={dashboard ? null : title} limit={dashboard ? 50 : 50} />;
  }

  if (!x || !y) return null;

  const columns = Object.keys(chartRows[0] || {});
  const colorKey =
    color && color !== "null" && columns.includes(color) ? color : null;
  const extraTooltipKeys = columns.filter(
    (c) => c !== x && c !== y && c !== colorKey
  );

  const tooltipStyle = {
    background: "transparent",
    border: "none",
    padding: 0,
  };

  let inner = null;

  if (chart === "bar") {
    const useGrouped = colorKey && columns.includes(colorKey);
    const { data, seriesKeys } = useGrouped
      ? pivotGrouped(chartRows, x, y, colorKey)
      : { data: chartRows, seriesKeys: [] };
    const yWidth = yAxisWidthForLabels(data, x, ranked);
    const barFill = ranked ? COLORS[0] : null;

    if (horizontal) {
      inner = (
        <BarChart
          layout="vertical"
          data={data}
          margin={{ top: 8, right: ranked ? 56 : 16, left: 4, bottom: 8 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke={GRID} horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 11, fill: AXIS }} stroke={GRID} tickFormatter={formatNum} />
          <YAxis
            type="category"
            dataKey={x}
            tick={{ fontSize: 11, fill: AXIS }}
            stroke={GRID}
            width={yWidth}
            tickFormatter={(v) => tickFormatter(v, ranked ? 48 : 14)}
          />
          <Tooltip
            content={<RichTooltip xKey={x} extraKeys={useGrouped ? [] : extraTooltipKeys} />}
            contentStyle={tooltipStyle}
            cursor={{ fill: "rgba(79, 70, 229, 0.08)" }}
          />
          {useGrouped && (
            <Legend wrapperStyle={{ fontSize: 12, paddingTop: 8 }} formatter={displayVal} />
          )}
          {useGrouped ? (
            seriesKeys.map((key, i) => (
              <Bar
                key={key}
                dataKey={key}
                name={key}
                stackId="stack"
                fill={COLORS[i % COLORS.length]}
                radius={[0, 3, 3, 0]}
                maxBarSize={28}
              />
            ))
          ) : (
            <Bar
              dataKey={y}
              name={y}
              fill={barFill || COLORS[0]}
              radius={[0, 4, 4, 0]}
              maxBarSize={ranked ? 24 : 32}
            >
              {!barFill && data.map((_, i) => (
                <Cell key={i} fill={COLORS[i % COLORS.length]} />
              ))}
              {ranked && (
                <LabelList
                  dataKey={y}
                  position="right"
                  formatter={formatNum}
                  style={{ fontSize: 11, fill: AXIS, fontWeight: 600 }}
                />
              )}
            </Bar>
          )}
        </BarChart>
      );
    } else {
    inner = (
      <BarChart data={data} margin={{ top: 8, right: 12, left: 4, bottom: 48 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
        <XAxis
          dataKey={x}
          tick={{ fontSize: 11, fill: AXIS }}
          stroke={GRID}
          interval={0}
          angle={-28}
          textAnchor="end"
          height={56}
          tickFormatter={tickFormatter}
        />
        <YAxis
          tick={{ fontSize: 11, fill: AXIS }}
          stroke={GRID}
          tickFormatter={formatNum}
          width={56}
        />
        <Tooltip
          content={
            <RichTooltip xKey={x} extraKeys={useGrouped ? [] : extraTooltipKeys} />
          }
          contentStyle={tooltipStyle}
          cursor={{ fill: "rgba(79, 70, 229, 0.08)" }}
        />
        {useGrouped && (
          <Legend
            wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
            formatter={displayVal}
          />
        )}
        {useGrouped ? (
          seriesKeys.map((key, i) => (
            <Bar
              key={key}
              dataKey={key}
              name={key}
              fill={COLORS[i % COLORS.length]}
              radius={[4, 4, 0, 0]}
              maxBarSize={48}
            />
          ))
        ) : (
          <Bar dataKey={y} name={y} radius={[4, 4, 0, 0]} maxBarSize={56}>
            {data.map((_, i) => (
              <Cell key={i} fill={COLORS[i % COLORS.length]} />
            ))}
          </Bar>
        )}
      </BarChart>
    );
    }
  } else if (chart === "line") {
    inner = (
      <LineChart data={chartRows} margin={{ top: 8, right: 12, left: 4, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
        <XAxis dataKey={x} tick={{ fontSize: 11, fill: AXIS }} stroke={GRID} />
        <YAxis tick={{ fontSize: 11, fill: AXIS }} stroke={GRID} tickFormatter={formatNum} />
        <Tooltip
          content={<RichTooltip xKey={x} extraKeys={extraTooltipKeys} />}
          contentStyle={tooltipStyle}
        />
        <Line
          type="monotone"
          dataKey={y}
          stroke={dashboard ? LINE_COLOR : COLORS[0]}
          dot={{ r: 3, fill: dashboard ? LINE_COLOR : COLORS[0] }}
          strokeWidth={2.5}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    );
  } else if (chart === "scatter") {
    inner = (
      <ScatterChart margin={{ top: 8, right: 12, left: 4, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
        <XAxis dataKey={x} name={x} tick={{ fontSize: 11, fill: AXIS }} stroke={GRID} />
        <YAxis dataKey={y} name={y} tick={{ fontSize: 11, fill: AXIS }} stroke={GRID} />
        <Tooltip
          cursor={{ strokeDasharray: "3 3" }}
          content={<RichTooltip xKey={x} extraKeys={extraTooltipKeys} />}
          contentStyle={tooltipStyle}
        />
        <Scatter data={chartRows} fill={COLORS[0]}>
          {chartRows.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Scatter>
      </ScatterChart>
    );
  } else if (chart === "pie") {
    inner = (
      <PieChart>
        <Tooltip
          content={<RichTooltip xKey={x} extraKeys={extraTooltipKeys} />}
          contentStyle={tooltipStyle}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} formatter={displayVal} />
        <Pie
          data={chartRows}
          dataKey={y}
          nameKey={x}
          outerRadius={100}
          innerRadius={40}
          paddingAngle={2}
          label={({ name, percent }) =>
            percent > 0.04 ? `${tickFormatter(name)} ${(percent * 100).toFixed(0)}%` : ""
          }
          labelLine={false}
        >
          {chartRows.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} stroke="#fff" strokeWidth={2} />
          ))}
        </Pie>
      </PieChart>
    );
  } else {
    return null;
  }

  return (
    <div className={`chart chart-modern ${dashboard ? "chart-dashboard" : ""} ${ranked ? "chart-ranked" : ""}`}>
      {!dashboard && title && <div className="chart-title">{title}</div>}
      <ResponsiveContainer width="100%" height={height}>
        {inner}
      </ResponsiveContainer>
    </div>
  );
}
