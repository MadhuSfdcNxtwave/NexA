/** Hex-style empty notebook — guided quick start. */
export default function NotebookQuickStart({ onStart, onAddCell, disabled }) {
  const steps = [
    { n: 1, title: "Add Input", desc: "Date range or text filter", action: () => onAddCell("input") },
    { n: 2, title: "Add SQL", desc: "Query BigQuery with {{ variables }}", action: () => onAddCell("sql") },
    { n: 3, title: "Run all", desc: "Restart & run all in toolbar", action: null },
    { n: 4, title: "Add Code", desc: "React chart linked to SQL results", action: () => onAddCell("code") },
    { n: 5, title: "Ask Agent", desc: "Questions appear on the left as Thread cells", action: null },
  ];

  return (
    <div className="notebook-quickstart">
      <div className="notebook-quickstart-hero">
        <h2>Build like Hex</h2>
        <p className="muted">
          Cells and answers on the left · Agent on the right. Chain SQL, write React widgets, publish to App.
        </p>
        <button type="button" className="primary" onClick={onStart} disabled={disabled}>
          Start with template (Input + SQL + Code)
        </button>
      </div>
      <ol className="notebook-quickstart-steps">
        {steps.map((s) => (
          <li key={s.n}>
            <span className="notebook-quickstart-n">{s.n}</span>
            <div>
              <strong>{s.title}</strong>
              <span className="muted small">{s.desc}</span>
            </div>
            {s.action && (
              <button type="button" className="secondary small" onClick={s.action} disabled={disabled}>
                + {s.title.replace("Add ", "")}
              </button>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
