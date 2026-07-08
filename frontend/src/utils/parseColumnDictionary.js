/** Parse CSV/TSV column dictionaries and map to { column_name: description }. */

function unquote(s) {
  let t = String(s ?? "").trim();
  if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) {
    return t.slice(1, -1).replace(/""/g, '"');
  }
  return t;
}

function normalizeHeader(h) {
  return String(h ?? "")
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, "");
}

const NAME_HEADERS = new Set([
  "fieldname",
  "columnname",
  "column",
  "name",
  "field",
  "colname",
  "attributename",
]);

const DESC_HEADERS = new Set([
  "description",
  "desc",
  "columndescription",
  "details",
  "definition",
  "comment",
]);

/** RFC 4180-style CSV/TSV row parser. */
export function parseDelimitedRows(text) {
  const delimiter = detectDelimiter(text);
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const next = text[i + 1];

    if (inQuotes) {
      if (ch === '"' && next === '"') {
        field += '"';
        i++;
      } else if (ch === '"') {
        inQuotes = false;
      } else {
        field += ch;
      }
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = "";
    } else if (ch === "\n" || (ch === "\r" && next === "\n")) {
      row.push(field);
      field = "";
      if (row.some((c) => String(c).trim())) rows.push(row);
      row = [];
      if (ch === "\r") i++;
    } else if (ch !== "\r") {
      field += ch;
    }
  }

  if (field.length || row.length) {
    row.push(field);
    if (row.some((c) => String(c).trim())) rows.push(row);
  }

  return rows;
}

function detectDelimiter(text) {
  const firstLine = text.split(/\r?\n/).find((l) => l.trim()) || "";
  const tabs = (firstLine.match(/\t/g) || []).length;
  const commas = (firstLine.match(/,/g) || []).length;
  return tabs > commas ? "\t" : ",";
}

function findHeaderIndex(headers, allowed) {
  for (let i = 0; i < headers.length; i++) {
    const key = normalizeHeader(headers[i]);
    if (allowed.has(key)) return i;
  }
  return -1;
}

function mapToTableColumns(parsed, tableColumnNames) {
  if (!tableColumnNames?.length) return { mapped: parsed, skipped: [] };

  const byLower = new Map(tableColumnNames.map((c) => [c.toLowerCase(), c]));
  const mapped = {};
  const skipped = [];

  for (const [rawName, desc] of Object.entries(parsed)) {
    const col = byLower.get(rawName.toLowerCase()) || rawName;
    if (byLower.has(rawName.toLowerCase())) {
      mapped[col] = desc;
    } else if (tableColumnNames.includes(rawName)) {
      mapped[rawName] = desc;
    } else {
      skipped.push(rawName);
    }
  }

  return { mapped, skipped };
}

function parseFromRows(rows, tableColumnNames) {
  if (rows.length < 2) return { mapped: {}, skipped: [], unmatchedInTable: [] };

  const headers = rows[0].map((h) => String(h).trim());
  const nameIdx = findHeaderIndex(headers, NAME_HEADERS);
  const descIdx = findHeaderIndex(headers, DESC_HEADERS);

  if (nameIdx < 0 || descIdx < 0) {
    throw new Error(
      'CSV must include headers like "field name" (or "column") and "description".'
    );
  }

  const parsed = {};
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    const name = String(row[nameIdx] ?? "").trim();
    const desc = String(row[descIdx] ?? "").trim();
    if (name && desc) parsed[name] = desc;
  }

  const { mapped, skipped } = mapToTableColumns(parsed, tableColumnNames);
  const matched = new Set(Object.keys(mapped).map((k) => k.toLowerCase()));
  const unmatchedInTable = tableColumnNames.filter((c) => !matched.has(c.toLowerCase()));

  return { mapped, skipped, unmatchedInTable, totalInFile: Object.keys(parsed).length };
}

export function parseColumnDictionary(text, tableColumnNames = []) {
  const trimmed = (text || "").trim();
  if (!trimmed) return { mapped: {}, skipped: [], unmatchedInTable: [], totalInFile: 0 };

  if (trimmed.startsWith("{")) {
    const data = JSON.parse(trimmed);
    if (typeof data !== "object" || data === null || Array.isArray(data)) {
      throw new Error('JSON must be an object: { "column_name": "description" }');
    }
    const parsed = {};
    for (const [k, v] of Object.entries(data)) {
      const desc = String(v ?? "").trim();
      if (k.trim() && desc) parsed[k.trim()] = desc;
    }
    const { mapped, skipped } = mapToTableColumns(parsed, tableColumnNames);
    const matched = new Set(Object.keys(mapped).map((k) => k.toLowerCase()));
    const unmatchedInTable = (tableColumnNames || []).filter(
      (c) => !matched.has(c.toLowerCase())
    );
    return { mapped, skipped, unmatchedInTable, totalInFile: Object.keys(parsed).length };
  }

  const rows = parseDelimitedRows(trimmed);
  if (rows.length >= 2 && findHeaderIndex(rows[0], NAME_HEADERS) >= 0) {
    return parseFromRows(rows, tableColumnNames);
  }

  // Legacy tab-separated paste (no CSV header row)
  const out = {};
  let currentCol = null;
  const lines = trimmed.split(/\r?\n/);

  for (const rawLine of lines) {
    const line = rawLine.replace(/\r$/, "");
    if (!line.trim()) continue;
    if (/^field\s*name\b/i.test(line) && /description/i.test(line)) continue;

    const tabRow = line.match(/^([a-zA-Z_][\w]*)\t([^\t]+)\t(.*)$/);
    if (tabRow) {
      currentCol = tabRow[1];
      out[currentCol] = unquote(tabRow[3]);
      continue;
    }

    const twoCol = line.match(/^([a-zA-Z_][\w]*)\t(.+)$/);
    if (twoCol) {
      currentCol = twoCol[1];
      out[currentCol] = unquote(twoCol[2]);
      continue;
    }

    if (currentCol && !/^[a-zA-Z_][\w]*\t/.test(line)) {
      out[currentCol] = `${out[currentCol]}\n${line}`.trim();
    }
  }

  const { mapped, skipped } = mapToTableColumns(out, tableColumnNames);
  const matched = new Set(Object.keys(mapped).map((k) => k.toLowerCase()));
  const unmatchedInTable = (tableColumnNames || []).filter(
    (c) => !matched.has(c.toLowerCase())
  );
  return { mapped, skipped, unmatchedInTable, totalInFile: Object.keys(out).length };
}

export async function readColumnDictionaryFile(file, tableColumnNames = []) {
  const text = await file.text();
  const result = parseColumnDictionary(text, tableColumnNames);
  return result;
}
