import { useEffect, useMemo, useRef, useState } from "react";

const TYPE_COLORS = {
  many_to_one: "#3b82f6",
  one_to_many: "#14b8a6",
  one_to_one: "#8b5cf6",
  many_to_many: "#f59e0b",
};

const TYPE_LABELS = {
  many_to_one: "many → one",
  one_to_many: "one → many",
  one_to_one: "one → one",
  many_to_many: "many → many",
};

const MAX_GRAPH_NEIGHBORS = 10;
const NODE_W = 200;
const NODE_H = 52;

function buildIndex(tables, relations) {
  const nameById = {};
  for (const t of tables || []) {
    nameById[t.short_name.toLowerCase()] = t.short_name;
  }

  const degree = {};
  const edgesByTable = {};

  const addEdge = (tableId, edge) => {
    if (!edgesByTable[tableId]) edgesByTable[tableId] = [];
    edgesByTable[tableId].push(edge);
    degree[tableId] = (degree[tableId] || 0) + 1;
  };

  const edges = [];
  const seen = new Set();

  for (const r of relations || []) {
    const source = (r.source || "").toLowerCase();
    const target = (r.target || "").toLowerCase();
    if (!source || !target) continue;
    const key = `${source}|${target}|${r.rel_type}`;
    if (seen.has(key)) continue;
    seen.add(key);

    if (!nameById[source]) nameById[source] = r.source;
    if (!nameById[target]) nameById[target] = r.target;

    const edge = {
      id: key,
      source,
      target,
      sourceLabel: nameById[source],
      targetLabel: nameById[target],
      rel_type: r.rel_type || "many_to_one",
      join_sql: r.join_sql || "",
    };
    edges.push(edge);
    addEdge(source, edge);
    addEdge(target, edge);
  }

  const tableRows = Object.keys(nameById)
    .map((id) => ({
      id,
      label: nameById[id],
      degree: degree[id] || 0,
    }))
    .sort((a, b) => b.degree - a.degree || a.label.localeCompare(b.label));

  return { tableRows, edgesByTable, nameById, edges };
}

function layoutRadial(centerId, neighborIds, width = 720, height = 420) {
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.34;

  const nodes = [
    {
      id: centerId,
      x: cx - NODE_W / 2,
      y: cy - NODE_H / 2,
      width: NODE_W,
      height: NODE_H,
      role: "center",
    },
  ];

  neighborIds.forEach((id, i) => {
    const angle = (2 * Math.PI * i) / neighborIds.length - Math.PI / 2;
    nodes.push({
      id,
      x: cx + Math.cos(angle) * radius - NODE_W / 2,
      y: cy + Math.sin(angle) * radius - NODE_H / 2,
      width: NODE_W,
      height: NODE_H,
      role: "neighbor",
    });
  });

  return { nodes, width, height };
}

function edgePath(from, to) {
  const sx = from.x + from.width / 2;
  const sy = from.y + from.height / 2;
  const tx = to.x + to.width / 2;
  const ty = to.y + to.height / 2;
  return `M ${sx} ${sy} L ${tx} ${ty}`;
}

function RelationRow({ edge, focusId, onPick }) {
  const outgoing = edge.source === focusId;
  const other = outgoing ? edge.targetLabel : edge.sourceLabel;
  const otherId = outgoing ? edge.target : edge.source;
  const color = TYPE_COLORS[edge.rel_type] || "#94a3b8";

  return (
    <div className="org-join-row">
      <div className="org-join-row-head">
        <span className="org-join-direction" style={{ color }}>
          {outgoing ? "→" : "←"}
        </span>
        <button type="button" className="org-join-table" onClick={() => onPick(otherId)}>
          {other}
        </button>
        <span className="org-join-type" style={{ background: `${color}18`, color }}>
          {TYPE_LABELS[edge.rel_type] || edge.rel_type}
        </span>
      </div>
      {edge.join_sql && <code className="org-join-sql">{edge.join_sql}</code>}
    </div>
  );
}

export default function OrgSchemaGraph({
  tables = [],
  relations = [],
  search = "",
  selectedTable = null,
  onSelectTable,
}) {
  const [localFocus, setLocalFocus] = useState(null);
  const [showAllJoins, setShowAllJoins] = useState(false);
  const [listFilter, setListFilter] = useState("");
  const [nodePositions, setNodePositions] = useState({});
  const [drag, setDrag] = useState(null);
  const [hoverNode, setHoverNode] = useState(null);
  const [suppressClick, setSuppressClick] = useState(false);
  const svgRef = useRef(null);

  const index = useMemo(() => buildIndex(tables, relations), [tables, relations]);
  const q = search.trim().toLowerCase();
  const listQ = listFilter.trim().toLowerCase();

  const focusId = useMemo(() => {
    if (selectedTable) return selectedTable.toLowerCase();
    if (localFocus) return localFocus;
    if (q) {
      const hit = index.tableRows.find((t) => t.label.toLowerCase().includes(q));
      if (hit) return hit.id;
    }
    const hub = index.tableRows.find((t) => t.degree > 0);
    return hub?.id || index.tableRows[0]?.id || null;
  }, [selectedTable, localFocus, q, index.tableRows]);

  useEffect(() => {
    if (selectedTable) setLocalFocus(selectedTable.toLowerCase());
  }, [selectedTable]);

  useEffect(() => {
    setShowAllJoins(false);
  }, [focusId]);

  const pickTable = (id) => {
    const normalized = id?.toLowerCase() || null;
    setLocalFocus(normalized);
    onSelectTable?.(normalized ? index.nameById[normalized] : null);
  };

  const focusEdges = focusId ? index.edgesByTable[focusId] || [] : [];
  const focusLabel = focusId ? index.nameById[focusId] : "";

  const neighborIds = useMemo(() => {
    if (!focusId) return [];
    const ids = new Set();
    for (const e of focusEdges) {
      if (e.source === focusId) ids.add(e.target);
      if (e.target === focusId) ids.add(e.source);
    }
    return [...ids].sort((a, b) =>
      (index.nameById[a] || a).localeCompare(index.nameById[b] || b),
    );
  }, [focusId, focusEdges, index.nameById]);

  const graphNeighbors = neighborIds.slice(0, MAX_GRAPH_NEIGHBORS);
  const layout = useMemo(
    () => (focusId ? layoutRadial(focusId, graphNeighbors) : null),
    [focusId, graphNeighbors],
  );

  const graphNodes = useMemo(() => {
    if (!layout) return [];
    return layout.nodes.map((node) => ({
      ...node,
      ...(nodePositions[node.id] || {}),
    }));
  }, [layout, nodePositions]);

  const byId = useMemo(
    () => Object.fromEntries(graphNodes.map((n) => [n.id, n])),
    [graphNodes],
  );

  const visibleEdges = useMemo(() => {
    if (!focusId || !layout) return [];
    const visible = new Set(graphNodes.map((n) => n.id));
    return focusEdges.filter((e) => visible.has(e.source) && visible.has(e.target));
  }, [focusId, focusEdges, graphNodes, layout]);

  const filteredList = useMemo(() => {
    const needle = listQ || q;
    if (!needle) return index.tableRows;
    return index.tableRows.filter(
      (t) => t.label.toLowerCase().includes(needle) || t.id.includes(needle),
    );
  }, [index.tableRows, listQ, q]);

  const displayedJoins = showAllJoins ? focusEdges : focusEdges.slice(0, 12);

  const svgPoint = (event) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const pt = svg.createSVGPoint();
    pt.x = event.clientX;
    pt.y = event.clientY;
    const matrix = svg.getScreenCTM();
    if (!matrix) return { x: event.clientX, y: event.clientY };
    return pt.matrixTransform(matrix.inverse());
  };

  const nodeJoinCount = (id) => index.edgesByTable[id]?.length || 0;

  const startNodeDrag = (event, node) => {
    event.preventDefault();
    event.stopPropagation();
    const pt = svgPoint(event);
    setDrag({
      id: node.id,
      offsetX: pt.x - node.x,
      offsetY: pt.y - node.y,
      moved: false,
    });
  };

  const moveNode = (event) => {
    if (!drag) return;
    event.preventDefault();
    const pt = svgPoint(event);
    const next = {
      x: Math.max(8, Math.min((layout?.width || 720) - NODE_W - 8, pt.x - drag.offsetX)),
      y: Math.max(8, Math.min((layout?.height || 420) - NODE_H - 8, pt.y - drag.offsetY)),
    };
    setNodePositions((prev) => ({
      ...prev,
      [drag.id]: next,
    }));
    setSuppressClick(true);
    setDrag((prev) => (prev ? { ...prev, moved: true } : prev));
  };

  const stopNodeDrag = () => {
    setDrag(null);
  };

  if (!index.tableRows.length) {
    return (
      <div className="org-schema-graph-empty">
        <p className="muted">No tables to visualize. Rebuild the schema after importing models.</p>
      </div>
    );
  }

  return (
    <div className="org-schema-graph-panel">
      <div className="org-schema-graph-toolbar">
        <div>
          <span className="org-schema-graph-title">Table connections</span>
          <span className="muted org-schema-graph-sub">
            {index.tableRows.length} tables · {index.edges.length} joins
          </span>
        </div>
        <p className="org-schema-graph-hint muted">
          Pick a table to see its joins. The map shows direct neighbors only — not the full web.
        </p>
      </div>

      <div className="org-schema-graph-layout">
        <aside className="org-schema-table-list">
          <input
            className="org-schema-list-search"
            placeholder="Filter tables…"
            value={listFilter}
            onChange={(e) => setListFilter(e.target.value)}
          />
          <div className="org-schema-table-list-scroll">
            {filteredList.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`org-schema-table-item ${focusId === t.id ? "active" : ""}`}
                onClick={() => pickTable(t.id)}
              >
                <span className="org-schema-table-item-name" title={t.label}>{t.label}</span>
                <span className={`org-schema-table-item-degree ${t.degree ? "" : "zero"}`}>
                  {t.degree || "0"}
                </span>
              </button>
            ))}
            {filteredList.length === 0 && (
              <p className="muted org-schema-list-empty">No tables match.</p>
            )}
          </div>
        </aside>

        <div className="org-schema-focus-panel">
          {!focusId ? (
            <div className="org-schema-graph-empty">
              <p>Select a table from the list to explore its connections.</p>
            </div>
          ) : (
            <>
              <div className="org-schema-focus-head">
                <div>
                  <h3>{focusLabel}</h3>
                  <p className="muted">
                    {focusEdges.length} direct join{focusEdges.length === 1 ? "" : "s"}
                    {neighborIds.length > MAX_GRAPH_NEIGHBORS
                      ? ` · map shows ${MAX_GRAPH_NEIGHBORS} of ${neighborIds.length} neighbors`
                      : ""}
                  </p>
                </div>
                <div className="org-schema-graph-legend compact">
                  {Object.entries(TYPE_COLORS).map(([type, color]) => (
                    <span key={type}><i style={{ background: color }} /> {TYPE_LABELS[type]}</span>
                  ))}
                </div>
              </div>

              <div className="org-schema-radial-wrap">
                <svg
                  ref={svgRef}
                  className="org-schema-graph-svg"
                  viewBox={`0 0 ${layout.width} ${layout.height}`}
                  role="img"
                  aria-label={`Connections for ${focusLabel}`}
                  onPointerMove={moveNode}
                  onPointerUp={stopNodeDrag}
                  onPointerLeave={stopNodeDrag}
                >
                  <defs>
                    <marker id="org-schema-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                      <path d="M0,0 L8,4 L0,8 Z" fill="#64748b" />
                    </marker>
                  </defs>

                  {visibleEdges.map((e) => {
                    const from = byId[e.source];
                    const to = byId[e.target];
                    if (!from || !to) return null;
                    const color = TYPE_COLORS[e.rel_type] || "#94a3b8";
                    return (
                      <path
                        key={e.id}
                        d={edgePath(from, to)}
                        fill="none"
                        stroke={color}
                        strokeWidth={2}
                        strokeOpacity={0.7}
                        markerEnd="url(#org-schema-arrow)"
                      />
                    );
                  })}

                  {graphNodes.map((node) => {
                    const isCenter = node.id === focusId;
                    const label = index.nameById[node.id] || node.id;
                    const joins = nodeJoinCount(node.id);
                    return (
                      <g
                        key={node.id}
                        className={`org-schema-graph-node ${isCenter ? "center" : "neighbor"} ${drag?.id === node.id ? "dragging" : ""}`}
                        transform={`translate(${node.x} ${node.y})`}
                        onPointerDown={(event) => startNodeDrag(event, node)}
                        onPointerEnter={() => setHoverNode({ id: node.id, label, joins })}
                        onPointerLeave={() => setHoverNode((prev) => (prev?.id === node.id ? null : prev))}
                        onClick={() => {
                          if (suppressClick) {
                            setSuppressClick(false);
                            return;
                          }
                          if (!isCenter) pickTable(node.id);
                        }}
                      >
                        <rect
                          width={node.width}
                          height={node.height}
                          rx={10}
                          fill={isCenter ? "#eef6ff" : "#ffffff"}
                          stroke={isCenter ? "var(--brand, #3b82f6)" : "#cbd5e1"}
                          strokeWidth={isCenter ? 2.5 : 1.5}
                        />
                        <text x={12} y={20} fontSize="10" fill="#64748b">
                          {isCenter ? "selected" : `${joins} join${joins === 1 ? "" : "s"}`}
                        </text>
                        <foreignObject x={12} y={24} width={node.width - 24} height={24}>
                          <div className="org-schema-node-label" title={label}>{label}</div>
                        </foreignObject>
                      </g>
                    );
                  })}
                </svg>
                {hoverNode && (
                  <div className="org-schema-node-hover">
                    <strong>{hoverNode.label}</strong>
                    <span>{hoverNode.joins} join{hoverNode.joins === 1 ? "" : "s"}</span>
                    <em>Drag card to reposition</em>
                  </div>
                )}
              </div>

              <div className="org-schema-joins-panel">
                <div className="org-schema-joins-head">
                  <h4>Join definitions</h4>
                  <span className="muted">{focusEdges.length} total</span>
                </div>
                <div className="org-schema-joins-list">
                  {displayedJoins.map((e) => (
                    <RelationRow key={e.id} edge={e} focusId={focusId} onPick={pickTable} />
                  ))}
                  {focusEdges.length === 0 && (
                    <p className="muted">This table has no join hints yet.</p>
                  )}
                </div>
                {focusEdges.length > 12 && (
                  <button
                    type="button"
                    className="secondary small org-schema-joins-more"
                    onClick={() => setShowAllJoins((v) => !v)}
                  >
                    {showAllJoins ? "Show fewer" : `Show all ${focusEdges.length} joins`}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
