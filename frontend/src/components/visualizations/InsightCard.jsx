import { useState } from "react";

const CONFIDENCE = {
  template: { label: "High", className: "confidence-high" },
  domain: { label: "High", className: "confidence-high" },
  join_template: { label: "High", className: "confidence-high" },
  planner: { label: "Medium", className: "confidence-medium" },
  semantic: { label: "Medium", className: "confidence-medium" },
  llm: { label: "Low", className: "confidence-low" },
};

export default function InsightCard({
  analysis = "",
  sqlSource = "",
  modelUsed = "",
  suggestions = [],
  onFollowUp,
  disabled = false,
}) {
  const [feedback, setFeedback] = useState(null);
  const conf = CONFIDENCE[sqlSource] || CONFIDENCE.llm;

  return (
    <div className="insight-card">
      <p className="insight-text">{analysis}</p>
      <div className="insight-meta">
        <span className={`insight-confidence ${conf.className}`}>{conf.label} confidence</span>
        {sqlSource && <span className="insight-badge">{sqlSource}</span>}
        {modelUsed && <span className="insight-badge muted">{modelUsed}</span>}
      </div>
      {suggestions?.length > 0 && (
        <div className="insight-followups">
          {suggestions.slice(0, 3).map((s) => (
            <button
              key={s}
              type="button"
              className="thread-suggestion-chip"
              disabled={disabled}
              onClick={() => onFollowUp?.(s)}
            >
              {s}
            </button>
          ))}
        </div>
      )}
      <div className="insight-feedback">
        <button
          type="button"
          className={feedback === "up" ? "active" : ""}
          onClick={() => setFeedback("up")}
          aria-label="Helpful"
        >
          👍
        </button>
        <button
          type="button"
          className={feedback === "down" ? "active" : ""}
          onClick={() => setFeedback("down")}
          aria-label="Not helpful"
        >
          👎
        </button>
      </div>
    </div>
  );
}
