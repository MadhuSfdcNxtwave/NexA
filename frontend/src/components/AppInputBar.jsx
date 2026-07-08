/** Consumer-facing filters for a published NexA app (mirrors notebook input cells). */
export function defaultValuesFromInputs(inputs = []) {
  const vals = {};
  for (const inp of inputs) {
    if (inp.input_type === "date_range") {
      const sk = inp.start_var || "range_start";
      const ek = inp.end_var || "range_end";
      vals[sk] = inp.default_start || "2025-04-01";
      vals[ek] =
        inp.default_end === "CURRENT_MONTH_END"
          ? new Date().toISOString().slice(0, 10)
          : inp.default_end || new Date().toISOString().slice(0, 10);
    } else {
      vals[inp.var || "value"] = inp.default || "";
    }
  }
  return vals;
}

export function queryStringFromValues(values) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(values || {})) {
    if (v != null && String(v).trim()) params.set(k, String(v).trim());
  }
  return params.toString();
}

export default function AppInputBar({ inputs = [], values, onChange, onApply, applying }) {
  if (!inputs.length) return null;

  return (
    <div className="app-input-bar">
      {inputs.map((inp) => {
        if (inp.input_type === "date_range") {
          const sk = inp.start_var || "range_start";
          const ek = inp.end_var || "range_end";
          return (
            <label key={inp.cell_id} className="app-input-field">
              <span className="app-input-label">{inp.label || inp.name}</span>
              <div className="app-input-dates">
                <input
                  type="date"
                  value={values[sk] || ""}
                  onChange={(e) => onChange({ ...values, [sk]: e.target.value })}
                />
                <span className="muted">to</span>
                <input
                  type="date"
                  value={values[ek] || ""}
                  onChange={(e) => onChange({ ...values, [ek]: e.target.value })}
                />
              </div>
            </label>
          );
        }
        const vk = inp.var || "value";
        return (
          <label key={inp.cell_id} className="app-input-field">
            <span className="app-input-label">{inp.label || inp.name}</span>
            <input
              type="text"
              value={values[vk] || ""}
              onChange={(e) => onChange({ ...values, [vk]: e.target.value })}
              placeholder={inp.default || ""}
            />
          </label>
        );
      })}
      {onApply && (
        <button type="button" className="primary small app-input-apply" onClick={onApply} disabled={applying}>
          {applying ? "Updating…" : "Apply"}
        </button>
      )}
    </div>
  );
}
