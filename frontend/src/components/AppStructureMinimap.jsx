/** Small corner widget — app filters → widgets (Hex-style structure preview). */
export default function AppStructureMinimap({
  filters = [],
  widgets = [],
  onExpand,
  mode = "builder",
}) {
  if (!filters.length && !widgets.length) return null;

  return (
    <div className="app-structure-minimap">
      <div className="app-structure-minimap-head">
        <span>App</span>
        {onExpand && (
          <button type="button" className="link-btn" onClick={onExpand}>
            {mode === "preview" ? "Builder" : "Preview"}
          </button>
        )}
      </div>
      <div className="app-structure-minimap-body">
        {filters.length > 0 && (
          <div className="app-structure-row">
            <span className="app-structure-label">Filters</span>
            <div className="app-structure-chips">
              {filters.map((f) => (
                <span key={f.id} className="app-structure-chip filter">
                  {f.label || f.name}
                </span>
              ))}
            </div>
          </div>
        )}
        {filters.length > 0 && widgets.length > 0 && (
          <div className="app-structure-arrow">↓</div>
        )}
        {widgets.length > 0 && (
          <div className="app-structure-row">
            <span className="app-structure-label">Widgets</span>
            <div className="app-structure-chips">
              {widgets.slice(0, 4).map((w) => (
                <span
                  key={w.id}
                  className={`app-structure-chip widget${w.kind === "code" ? " code" : ""}`}
                >
                  {w.title || w.name}
                </span>
              ))}
              {widgets.length > 4 && (
                <span className="app-structure-chip more">+{widgets.length - 4}</span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
