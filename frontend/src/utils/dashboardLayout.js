const KPI_THEMES = ["kpi-green", "kpi-blue", "kpi-yellow", "kpi-red", "kpi-neutral"];

export function formatKpiValue(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return String(v ?? "—");
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

/** Pull up to 5 KPI metrics from single-row / scalar answers. */
export function extractKpis(items, max = 5) {
  const kpis = [];
  for (const item of items) {
    const rows = item.rows || [];
    if (!rows.length) continue;
    const cols = item.columns?.length ? item.columns : Object.keys(rows[0] || {});
    const targets = rows.length === 1 ? [rows[0]] : rows.slice(0, 1);

    for (const row of targets) {
      for (const c of cols) {
        const raw = row[c];
        const n = Number(raw);
        if (raw == null || raw === "" || Number.isNaN(n)) continue;
        kpis.push({
          id: `${item.id}-${c}`,
          label: item.chart_spec?.title || c.replace(/_/g, " "),
          sublabel: rows.length === 1 ? item.question : c.replace(/_/g, " "),
          value: formatKpiValue(raw),
          theme: KPI_THEMES[kpis.length % KPI_THEMES.length],
        });
        if (kpis.length >= max) return kpis;
      }
    }
  }
  return kpis;
}

function chartType(item) {
  return item.chart_spec?.chart || "none";
}

/** Assign pinned widgets to action-board slots. */
export function layoutDashboard(items) {
  if (!items?.length) {
    return { kpis: [], hero: null, donut: null, bar: null, table: null, extras: [] };
  }

  const kpis = extractKpis(items);
  const withChart = items.filter((i) => {
    const t = chartType(i);
    return t && t !== "none";
  });

  const tables = items.filter(
    (i) => chartType(i) === "table" || ((i.rows?.length || 0) >= 6 && (i.columns?.length || 0) >= 3)
  );
  const lines = withChart.filter((i) => chartType(i) === "line");
  const pies = withChart.filter((i) => chartType(i) === "pie");
  const bars = withChart.filter((i) => chartType(i) === "bar");

  const used = new Set();
  const pick = (list) => {
    const item = list.find((i) => !used.has(i.id));
    if (item) used.add(item.id);
    return item || null;
  };

  const hero = pick(lines) || pick(bars) || pick(withChart);
  const donut = pick(pies);
  const bar = pick(bars.filter((i) => i.id !== hero?.id));
  const table = pick(tables) || items.find((i) => (i.rows?.length || 0) >= 4) || null;
  const extras = items.filter((i) => !used.has(i.id) && i.id !== table?.id);

  return { kpis, hero, donut, bar, table, extras };
}

export function totalRowCount(items) {
  return items.reduce((sum, i) => sum + (i.rows?.length || 0), 0);
}

/** Guess filter dimensions from column names in widget data. */
export function guessFilterColumns(items) {
  const seen = new Set();
  const out = [];
  const hints = ["segment", "month", "course", "state", "team", "cohort", "program", "region"];

  for (const item of items) {
    const cols = item.columns?.length ? item.columns : Object.keys(item.rows?.[0] || {});
    for (const c of cols) {
      const low = c.toLowerCase();
      if (hints.some((h) => low.includes(h)) && !seen.has(c)) {
        seen.add(c);
        out.push(c);
      }
    }
  }
  return out.slice(0, 10);
}

export function filterOptions(items, column) {
  const vals = new Set();
  for (const item of items) {
    for (const row of item.rows || []) {
      if (row[column] != null && row[column] !== "") vals.add(String(row[column]));
    }
  }
  return ["All", ...Array.from(vals).sort().slice(0, 24)];
}
