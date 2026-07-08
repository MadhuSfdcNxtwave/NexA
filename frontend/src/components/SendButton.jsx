import { IconArrowUp } from "./Icons.jsx";

/** Primary ask / send control — pill on hero, compact circle in thread composer. */
export default function SendButton({ onClick, disabled, label = "Ask", compact = false }) {
  if (compact) {
    return (
      <button
        type="button"
        className="send-btn send-btn-compact"
        onClick={onClick}
        disabled={disabled}
        title={label}
        aria-label={label}
      >
        <IconArrowUp />
      </button>
    );
  }

  return (
    <button
      type="button"
      className="send-btn"
      onClick={onClick}
      disabled={disabled}
      title={label}
    >
      <span className="send-btn-label">{label}</span>
      <span className="send-btn-icon">
        <IconArrowUp />
      </span>
    </button>
  );
}
