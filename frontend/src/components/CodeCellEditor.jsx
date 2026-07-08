import { useMemo, useState } from "react";
import { LiveProvider, LiveEditor, LiveError, LivePreview } from "react-live";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

export const DEFAULT_CODE_CELL = `function Widget({ rows = [] }) {
  if (!rows.length) {
    return (
      <p style={{ color: "#64748b", padding: 12 }}>
        Link a SQL cell and click <strong>Restart &amp; run all</strong> to load data.
      </p>
    );
  }
  const keys = Object.keys(rows[0] || {});
  const xKey = keys[0];
  const yKey = keys.find((k) => !Number.isNaN(Number(rows[0][k]))) || keys[1];
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={rows}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey={xKey} />
        <YAxis />
        <Tooltip />
        <Bar dataKey={yKey} fill="#4f46e5" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

render(<Widget rows={rows} />);`;

/** Hex-style React code cell — live preview with recharts + SQL row data. */
export default function CodeCellEditor({ code, onChange, rows = [], columns = [], previewOnly = false }) {
  const [mode, setMode] = useState(previewOnly ? "preview" : "split"); // code | preview | split

  const scope = useMemo(
    () => ({
      rows,
      columns,
      BarChart,
      Bar,
      LineChart,
      Line,
      XAxis,
      YAxis,
      CartesianGrid,
      Tooltip,
      ResponsiveContainer,
      Legend,
    }),
    [rows, columns],
  );

  const sourceCode = code || DEFAULT_CODE_CELL;
  const rowHint = rows.length ? `${rows.length} rows linked` : "No data — run SQL first";

  return (
    <div className="notebook-code-cell">
      {!previewOnly && (
        <div className="notebook-code-toolbar">
          <span className="muted small">{rowHint}</span>
          <div className="notebook-code-mode-tabs">
            <button type="button" className={mode === "code" ? "active" : ""} onClick={() => setMode("code")}>
              Code
            </button>
            <button type="button" className={mode === "split" ? "active" : ""} onClick={() => setMode("split")}>
              Split
            </button>
            <button type="button" className={mode === "preview" ? "active" : ""} onClick={() => setMode("preview")}>
              Preview
            </button>
          </div>
        </div>
      )}
      <div className={`notebook-code-editor notebook-code-mode-${mode}${previewOnly ? " preview-only" : ""}`}>
        <LiveProvider code={sourceCode} scope={scope} noInline onChange={onChange}>
          {(mode === "code" || mode === "split") && (
            <LiveEditor className="notebook-code-live-editor" />
          )}
          {(mode === "preview" || mode === "split") && (
            <div className="notebook-code-preview">
              <LiveError className="notebook-code-error" />
              <LivePreview />
            </div>
          )}
        </LiveProvider>
      </div>
    </div>
  );
}
