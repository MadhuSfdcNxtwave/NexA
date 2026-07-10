import { useState } from "react";

const CONFIDENCE = {
  drill_down: { label: "High", className: "confidence-high" },
  template: { label: "High", className: "confidence-high" },
  domain: { label: "High", className: "confidence-high" },
  join_template: { label: "High", className: "confidence-high" },
  planner: { label: "Medium", className: "confidence-medium" },
  semantic: { label: "Medium", className: "confidence-medium" },
  llm: { label: "Low", className: "confidence-low" },
};

/** Light markdown: **bold**, newlines, `code`. */
function renderInsight(text) {
  if (!text) return null;
  const paragraphs = String(text).split(/\n\n+/);
  return paragraphs.map((para, pi) => {
    const parts = [];
    const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let last = 0;
    let m;
    let key = 0;
    while ((m = re.exec(para)) !== null) {
      if (m.index > last) {
        parts.push(<span key={`t${key++}`}>{para.slice(last, m.index)}</span>);
      }
      const token = m[0];
      if (token.startsWith("**")) {
        parts.push(<strong key={`b${key++}`}>{token.slice(2, -2)}</strong>);
      } else {
        parts.push(<code key={`c${key++}`}>{token.slice(1, -1)}</code>);
      }
      last = m.index + token.length;
    }
    if (last < para.length) {
      parts.push(<span key={`t${key++}`}>{para.slice(last)}</span>);
    }
    return (
      <p key={pi} className="insight-text">
        {parts}
      </p>
    );
  });
}

export default function InsightCard({
  analysis = "",
  sqlSource = "",
  modelUsed = "",
  suggestions = [],
  onFollowUp,
  disabled = false,
  showDetails = false,
}) {
  const [feedback, setFeedback] = useState(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const conf = CONFIDENCE[sqlSource] || CONFIDENCE.llm;

  return (
    <div className="insight-card">
      <div className="insight-body">{renderInsight(analysis)}</div>
      {(showDetails || detailsOpen) && (
        <div className="insight-meta">
          <span className={`insight-confidence ${conf.className}`}>{conf.label} confidence</span>
          {sqlSource && <span className="insight-badge">{sqlSource}</span>}
          {modelUsed && <span className="insight-badge muted">{modelUsed}</span>}
        </div>
      )}
      {!showDetails && (
        <button
          type="button"
          className="link-btn insight-details-toggle"
          onClick={() => setDetailsOpen((v) => !v)}
        >
          {detailsOpen ? "Hide details" : "Details"}
        </button>
      )}
      {suggestions?.length > 0 && (
        <div className="insight-followups">
          {suggestions.slice(0, 4).map((s) => (
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
