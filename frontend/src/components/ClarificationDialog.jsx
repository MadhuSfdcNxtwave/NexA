import { useState } from "react";

/** Inline clarification below the composer — not a blocking modal. */
export default function ClarificationPanel({
  prompt,
  options = [],
  allowCustom = true,
  onChoose,
  onCancel,
  loading = false,
  originalQuestion = "",
}) {
  const first = options[0];
  const [selectedId, setSelectedId] = useState(first?.id ?? "custom");
  const [editText, setEditText] = useState(first?.refined_question || first?.label || "");

  const selectOption = (opt) => {
    setSelectedId(opt.id);
    setEditText(opt.refined_question || opt.label || "");
  };

  const submit = () => {
    const text = editText.trim();
    if (!text) return;
    onChoose(selectedId, text, text);
  };

  return (
    <div className="clarification-inline" role="region" aria-label="Clarification">
      <div className="clarification-inline-head">
        <span className="clarification-inline-title">Quick clarification</span>
        <button
          type="button"
          className="clarification-inline-dismiss"
          onClick={onCancel}
          disabled={loading}
          aria-label="Dismiss"
        >
          ×
        </button>
      </div>

      {originalQuestion && (
        <p className="clarification-inline-q muted small">
          You asked: <span>{originalQuestion}</span>
        </p>
      )}
      <p className="clarification-inline-prompt">{prompt}</p>

      <div className="clarification-chips">
        {options.map((opt) => (
          <button
            key={opt.id}
            type="button"
            className={`clarification-chip${selectedId === opt.id ? " selected" : ""}`}
            disabled={loading}
            onClick={() => selectOption(opt)}
            title={opt.refined_question || opt.label}
          >
            {opt.label}
          </button>
        ))}
        {allowCustom && (
          <button
            type="button"
            className={`clarification-chip${selectedId === "custom" ? " selected" : ""}`}
            disabled={loading}
            onClick={() => setSelectedId("custom")}
          >
            Write your own
          </button>
        )}
      </div>

      <textarea
        rows={2}
        className="clarification-inline-input"
        value={editText}
        onChange={(e) => setEditText(e.target.value)}
        disabled={loading}
        placeholder="Edit the question, then run…"
      />

      <div className="clarification-inline-actions">
        <button
          type="button"
          className="btn primary compact"
          disabled={loading || !editText.trim()}
          onClick={submit}
        >
          Run query
        </button>
        <button type="button" className="link-btn" onClick={onCancel} disabled={loading}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
