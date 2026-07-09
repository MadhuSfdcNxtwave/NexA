/** Hex-style @table mentions in Ask composer. */

const MENTION_RE = /@([a-zA-Z0-9][a-zA-Z0-9_\-.]{2,140})/g;

export function shortName(fullTableId) {
  return (fullTableId || "").split(".").pop() || fullTableId;
}

export function aiTables(tables) {
  return (tables || []).filter((t) => t.included_for_ai !== false);
}

export function resolveTableToken(token, tables) {
  const allowed = aiTables(tables);
  if (!token || !allowed.length) return null;
  const raw = token.trim().replace(/^`|`$/g, "").toLowerCase();
  const short = raw.includes(".") ? raw.split(".").pop() : raw;
  for (const t of allowed) {
    const fq = t.full_table_id;
    const s = shortName(fq).toLowerCase();
    if (fq.toLowerCase() === raw || s === short) return fq;
    if (s.includes(short) || short.includes(s)) return fq;
  }
  return null;
}

export function parseMentionTokens(text) {
  const out = [];
  const re = new RegExp(MENTION_RE.source, "g");
  let m;
  while ((m = re.exec(text || "")) !== null) {
    out.push(m[1].trim());
  }
  return out;
}

export function extractPinnedTableIds(text, tables) {
  const ids = [];
  for (const token of parseMentionTokens(text)) {
    const fq = resolveTableToken(token, tables);
    if (fq && !ids.includes(fq)) ids.push(fq);
  }
  return ids;
}

export function stripMentions(text) {
  return (text || "").replace(MENTION_RE, " ").replace(/\s+/g, " ").trim();
}

/** Active @query at caret for autocomplete (text before caret). */
export function mentionQueryAtCaret(text, caret) {
  const head = (text || "").slice(0, caret ?? text.length);
  // Allow bare "@" — show full table list before user types a filter char.
  const m = head.match(/@([a-zA-Z0-9][a-zA-Z0-9_\-.]{0,140})?$/);
  if (!m) return null;
  return { query: (m[1] || "").toLowerCase(), start: head.length - m[0].length };
}

export function filterTablesForMention(tables, query) {
  const q = (query || "").toLowerCase();
  return aiTables(tables)
    .map((t) => ({
      ...t,
      short: shortName(t.full_table_id),
    }))
    .filter((t) => {
      if (!q) return true;
      const s = t.short.toLowerCase();
      return s.includes(q) || t.full_table_id.toLowerCase().includes(q);
    })
    .slice(0, 12);
}

export function insertMention(text, caret, short) {
  const pos = caret ?? text.length;
  const ctx = mentionQueryAtCaret(text, pos);
  if (!ctx) return { text, caret: pos };
  const before = text.slice(0, ctx.start);
  const after = text.slice(pos);
  const mention = `@${short} `;
  const next = `${before}${mention}${after}`;
  const nextCaret = before.length + mention.length;
  return { text: next, caret: nextCaret };
}
