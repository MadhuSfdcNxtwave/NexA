import { useEffect, useRef, useState } from "react";
import {
  filterTablesForMention,
  insertMention,
  mentionQueryAtCaret,
  shortName,
} from "../tableMentions.js";

export default function TableMentionInput({
  value,
  onChange,
  onKeyDown,
  rows = 2,
  placeholder,
  disabled,
  tables = [],
  className = "",
}) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [query, setQuery] = useState("");

  const suggestions = filterTablesForMention(tables, query);

  useEffect(() => {
    setActive(0);
  }, [query, suggestions.length]);

  const syncFromText = (text, caret) => {
    const pos = caret ?? text.length;
    const ctx = mentionQueryAtCaret(text, pos);
    if (ctx && tables.length > 0) {
      setQuery(ctx.query);
      setOpen(true);
    } else {
      setOpen(false);
      setQuery("");
    }
  };

  const syncMentionState = () => {
    const el = ref.current;
    if (!el) return;
    syncFromText(value, el.selectionStart);
  };

  useEffect(() => {
    syncMentionState();
  }, [value, tables.length]);

  const pick = (table) => {
    const el = ref.current;
    const caret = el?.selectionStart ?? value.length;
    const { text, caret: nextCaret } = insertMention(value, caret, shortName(table.full_table_id));
    onChange(text);
    setOpen(false);
    setQuery("");
    requestAnimationFrame(() => {
      if (!ref.current) return;
      ref.current.focus();
      ref.current.setSelectionRange(nextCaret, nextCaret);
    });
  };

  const handleKeyDown = (e) => {
    if (open && suggestions.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => (i + 1) % suggestions.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => (i - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        pick(suggestions[active]);
        return;
      }
      if (e.key === "Escape") {
        setOpen(false);
        return;
      }
    }
    onKeyDown?.(e);
  };

  return (
    <div className={`table-mention-wrap ${className}`.trim()}>
      <textarea
        ref={ref}
        rows={rows}
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        onChange={(e) => {
          const next = e.target.value;
          const caret = e.target.selectionStart;
          onChange(next);
          syncFromText(next, caret);
        }}
        onClick={syncMentionState}
        onKeyUp={syncMentionState}
        onKeyDown={handleKeyDown}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && suggestions.length > 0 && (
        <ul className="table-mention-menu" role="listbox">
          {suggestions.map((t, i) => (
            <li key={t.full_table_id}>
              <button
                type="button"
                role="option"
                aria-selected={i === active}
                className={i === active ? "active" : ""}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(t);
                }}
              >
                <span className="table-mention-short">{t.short}</span>
                {t.endorsed && <span className="table-mention-badge">Endorsed</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && !suggestions.length && tables.length === 0 && (
        <div className="table-mention-menu table-mention-empty">
          No tables in workspace — add tables in the Data tab first.
        </div>
      )}
    </div>
  );
}
