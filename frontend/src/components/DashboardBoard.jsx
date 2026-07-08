import { useMemo, useState } from "react";
import Chart from "./Chart.jsx";
import CodeCellEditor from "./CodeCellEditor.jsx";
import {
  layoutDashboard,
  totalRowCount,
  guessFilterColumns,
  filterOptions,
} from "../utils/dashboardLayout.js";

function Panel({ title, subtitle, children, className = "", actions }) {
  return (
    <section className={`db-panel ${className}`}>
      {(title || actions) && (
        <header className="db-panel-head">
          <div>
            {title && <h3 className="db-panel-title">{title}</h3>}
            {subtitle && <p className="db-panel-sub muted small">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="db-panel-body">{children}</div>
    </section>
  );
}

function KpiStrip({ kpis }) {
  if (!kpis.length) return null;
  return (
    <div className="db-kpi-row">
      {kpis.map((k) => (
        <div key={k.id} className={`db-kpi-card ${k.theme}`}>
          <div className="db-kpi-value">{k.value}</div>
          <div className="db-kpi-label">{k.label}</div>
        </div>
      ))}
    </div>
  );
}

function DashboardTable({ item, filters }) {
  if (!item?.rows?.length) return null;
  let rows = item.rows;
  const cols = item.columns?.length ? item.columns : Object.keys(rows[0] || {});

  for (const [col, val] of Object.entries(filters)) {
    if (!val || val === "All") continue;
    rows = rows.filter((r) => String(r[col]) === val);
  }

  const numericCols = cols.filter((c) => rows.some((r) => !Number.isNaN(Number(r[c]))));

  return (
    <div className="db-table-wrap">
      <table className="db-table">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c}>{c.replace(/_/g, " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 50).map((row, i) => (
            <tr key={i}>
              {cols.map((c) => {
                const v = row[c];
                const n = Number(v);
                const isNum = !Number.isNaN(n) && numericCols.includes(c);
                const intensity = isNum
                  ? Math.abs(n) / (Math.max(...rows.map((r) => Math.abs(Number(r[c]) || 0)), 1) || 1)
                  : 0;
                return (
                  <td
                    key={c}
                    className={isNum ? "db-table-num" : ""}
                    style={
                      isNum
                        ? { background: `rgba(79, 70, 229, ${0.05 + intensity * 0.12})` }
                        : undefined
                    }
                  >
                    {v == null ? "—" : isNum ? formatNum(n) : String(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 50 && (
        <div className="db-table-foot muted small">Showing 50 of {rows.length} rows</div>
      )}
    </div>
  );
}

function formatNum(n) {
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function WidgetSlot({ item, chartProps = {}, editable, onRemove }) {
  if (!item) return null;
  const title = item.chart_spec?.title || item.question;

  if (item.chart_spec?.chart === "code") {
    return (
      <div className="db-widget db-widget-code">
        {editable && (
          <button
            type="button"
            className="db-widget-remove ghost danger small"
            onClick={() => onRemove?.(item.id)}
          >
            Remove
          </button>
        )}
        <CodeCellEditor
          code={item.chart_spec.code}
          rows={item.rows || []}
          columns={item.columns || []}
          previewOnly
        />
      </div>
    );
  }

  return (
    <div className="db-widget">
      {editable && (
        <button
          type="button"
          className="db-widget-remove ghost danger small"
          onClick={() => onRemove?.(item.id)}
        >
          Remove
        </button>
      )}
      {item.analysis && <p className="db-widget-insight muted small">{item.analysis}</p>}
      {item.chart_spec?.chart && item.chart_spec.chart !== "none" ? (
        <Chart spec={item.chart_spec} rows={item.rows} vizRows={item.viz_rows} {...chartProps} />
      ) : item.rows?.length > 1 ? (
        <Chart
          spec={{ chart: "table", title, x: null, y: null }}
          rows={item.rows}
        />
      ) : null}
    </div>
  );
}

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "charts", label: "Charts" },
  { id: "data", label: "Data table" },
];

export default function DashboardBoard({
  title,
  subtitle,
  items = [],
  editable = false,
  onRemoveItem,
  toolbar,
}) {
  const [activeTab, setActiveTab] = useState("overview");
  const [filters, setFilters] = useState({});

  const layout = useMemo(() => layoutDashboard(items), [items]);
  const filterCols = useMemo(() => guessFilterColumns(items), [items]);
  const rowTotal = useMemo(() => totalRowCount(items), [items]);

  const showOverview = activeTab === "overview";
  const showCharts = activeTab === "overview" || activeTab === "charts";
  const showTable = activeTab === "overview" || activeTab === "data";

  if (!items.length) {
    return (
      <div className="db-board db-board-empty">
        <div className="db-empty-state muted">
          <p>No widgets yet. Pin SQL from <strong>Notebook</strong> or answers from <strong>Thread</strong>.</p>
          <p className="small">Open <strong>App builder</strong> to expose filters and publish a share link.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="db-board">
      <header className="db-header">
        <div className="db-header-text">
          <h1 className="db-title">{title}</h1>
          <p className="db-subtitle muted">
            {subtitle ||
              "Widgets are saved in this project. Data refreshes from BigQuery when you open or share this dashboard."}
          </p>
        </div>
        {toolbar}
      </header>

      {filterCols.length > 0 && (
        <div className="db-filters">
          {filterCols.map((col) => (
            <label key={col} className="db-filter">
              <span className="db-filter-label">{col.replace(/_/g, " ")}</span>
              <select
                value={filters[col] || "All"}
                onChange={(e) => setFilters((f) => ({ ...f, [col]: e.target.value }))}
              >
                {filterOptions(items, col).map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
      )}

      <nav className="db-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={activeTab === t.id ? "active" : ""}
            onClick={() => setActiveTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {showOverview && <KpiStrip kpis={layout.kpis} />}

      {showCharts && layout.hero && (
        <Panel
          title={layout.hero.chart_spec?.title || layout.hero.question}
          className="db-panel-hero"
        >
          <WidgetSlot
            item={layout.hero}
            chartProps={{ height: 280, dashboard: true }}
            editable={editable}
            onRemove={onRemoveItem}
          />
        </Panel>
      )}

      {showCharts && (layout.donut || layout.bar) && (
        <div className="db-mid-row">
          {layout.donut && (
            <Panel
              title={layout.donut.chart_spec?.title || "Breakdown"}
              className="db-panel-half"
            >
              <WidgetSlot
                item={layout.donut}
                chartProps={{ height: 260, palette: "semantic", dashboard: true }}
                editable={editable}
                onRemove={onRemoveItem}
              />
            </Panel>
          )}
          {layout.bar && (
            <Panel
              title={layout.bar.chart_spec?.title || "Comparison"}
              className="db-panel-half"
            >
              <WidgetSlot
                item={layout.bar}
                chartProps={{ height: 260, horizontal: true, palette: "semantic", dashboard: true }}
                editable={editable}
                onRemove={onRemoveItem}
              />
            </Panel>
          )}
        </div>
      )}

      {showCharts && layout.extras.length > 0 && (
        <div className="db-extras-row">
          {layout.extras.map((item) => (
            <Panel
              key={item.id}
              title={item.chart_spec?.title || item.question}
              className="db-panel-third"
            >
              <WidgetSlot
                item={item}
                chartProps={{ height: 220, dashboard: true }}
                editable={editable}
                onRemove={onRemoveItem}
              />
            </Panel>
          ))}
        </div>
      )}

      {showTable && layout.table && (
        <Panel title={layout.table.chart_spec?.title || layout.table.question || "Detail table"}>
          {layout.table.analysis && (
            <p className="db-widget-insight muted small">{layout.table.analysis}</p>
          )}
          {layout.table.chart_spec?.chart === "table" ? (
            <Chart spec={layout.table.chart_spec} rows={layout.table.rows} />
          ) : (
            <DashboardTable item={layout.table} filters={filters} />
          )}
          {editable && (
            <button
              type="button"
              className="ghost danger small db-table-remove"
              onClick={() => onRemoveItem?.(layout.table.id)}
            >
              Remove table
            </button>
          )}
        </Panel>
      )}
    </div>
  );
}
