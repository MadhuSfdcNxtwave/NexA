import {
  ResponsiveContainer, BarChart, Bar, LineChart, Line, ScatterChart, Scatter,
  PieChart, Pie, Cell, XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";

const COLORS = ["#378ADD", "#1D9E75", "#D85A30", "#D4537E", "#BA7517", "#7F77DD"];

// spec = { chart, x, y, color, title }; rows = array of objects from the query
export default function Chart({ spec, rows }) {
  if (!spec || spec.chart === "none" || !rows || rows.length === 0) return null;
  const { chart, x, y, title } = spec;

  let inner = null;
  if (chart === "bar") {
    inner = (
      <BarChart data={rows}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
        <XAxis dataKey={x} tick={{ fontSize: 12 }} />
        <YAxis tick={{ fontSize: 12 }} />
        <Tooltip />
        <Bar dataKey={y} fill={COLORS[0]} radius={[4, 4, 0, 0]} />
      </BarChart>
    );
  } else if (chart === "line") {
    inner = (
      <LineChart data={rows}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
        <XAxis dataKey={x} tick={{ fontSize: 12 }} />
        <YAxis tick={{ fontSize: 12 }} />
        <Tooltip />
        <Line type="monotone" dataKey={y} stroke={COLORS[0]} dot={false} />
      </LineChart>
    );
  } else if (chart === "scatter") {
    inner = (
      <ScatterChart>
        <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
        <XAxis dataKey={x} name={x} tick={{ fontSize: 12 }} />
        <YAxis dataKey={y} name={y} tick={{ fontSize: 12 }} />
        <Tooltip cursor={{ strokeDasharray: "3 3" }} />
        <Scatter data={rows} fill={COLORS[0]} />
      </ScatterChart>
    );
  } else if (chart === "pie") {
    inner = (
      <PieChart>
        <Tooltip />
        <Pie data={rows} dataKey={y} nameKey={x} outerRadius={90} label>
          {rows.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Pie>
      </PieChart>
    );
  } else {
    return null;
  }

  return (
    <div className="chart">
      {title && <div className="chart-title">{title}</div>}
      <ResponsiveContainer width="100%" height={260}>
        {inner}
      </ResponsiveContainer>
    </div>
  );
}
