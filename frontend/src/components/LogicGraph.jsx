import { useMemo, useRef, useState } from "react";

const TYPE_COLORS = {
  input: { bg: "#eef6ff", border: "#3b82f6", badge: "Input" },
  sql: { bg: "#f0fdf9", border: "#14b8a6", badge: "SQL" },
  code: { bg: "#faf5ff", border: "#a855f7", badge: "Code" },
  text: { bg: "#fafafa", border: "#a1a1aa", badge: "Text" },
};

function nodeCenter(node) {
  return { x: node.x + node.width / 2, y: node.y + node.height / 2 };
}

function edgePath(from, to) {
  const sx = from.x + from.width / 2;
  const sy = from.y + from.height;
  const tx = to.x + to.width / 2;
  const ty = to.y;
  const midY = (sy + ty) / 2;
  return `M ${sx} ${sy} C ${sx} ${midY}, ${tx} ${midY}, ${tx} ${ty}`;
}

function GraphCanvas({
  graph,
  zoom,
  highlightId,
  onNodeClick,
  className,
  interactive = true,
}) {
  const byName = useMemo(
    () => Object.fromEntries((graph?.nodes || []).map((n) => [n.name, n])),
    [graph],
  );

  if (!graph?.nodes?.length) {
    return (
      <div className={`logic-graph-empty ${className || ""}`}>
        <p className="muted">No cells — add inputs and SQL to see the DAG.</p>
      </div>
    );
  }

  const w = graph.width * zoom;
  const h = graph.height * zoom;

  return (
    <svg
      className={`logic-graph-svg ${className || ""}`}
      width={w}
      height={h}
      viewBox={`0 0 ${graph.width} ${graph.height}`}
      role="img"
      aria-label="Notebook logic graph"
    >
      <defs>
        <marker
          id="logic-arrow"
          markerWidth="8"
          markerHeight="8"
          refX="7"
          refY="4"
          orient="auto"
        >
          <path d="M0,0 L8,4 L0,8 Z" fill="#94a3b8" />
        </marker>
        <marker
          id="logic-arrow-var"
          markerWidth="8"
          markerHeight="8"
          refX="7"
          refY="4"
          orient="auto"
        >
          <path d="M0,0 L8,4 L0,8 Z" fill="#8b5cf6" />
        </marker>
      </defs>

      {(graph.edges || []).map((e, i) => {
        const from = byName[e.from];
        const to = byName[e.to];
        if (!from || !to) return null;
        const isVar = e.kind === "variable";
        return (
          <g key={`${e.from}-${e.to}-${i}`}>
            <path
              d={edgePath(from, to)}
              fill="none"
              stroke={isVar ? "#c4b5fd" : "#cbd5e1"}
              strokeWidth={isVar ? 1.5 : 2}
              strokeDasharray={isVar ? "5 4" : undefined}
              markerEnd={isVar ? "url(#logic-arrow-var)" : "url(#logic-arrow)"}
            />
            {e.label && isVar && (
              <text
                x={(from.x + to.x) / 2 + from.width / 4}
                y={(from.y + to.y) / 2 + from.height / 2}
                className="logic-edge-label"
                fontSize="10"
              >
                {`{{ ${e.label} }}`}
              </text>
            )}
          </g>
        );
      })}

      {graph.nodes.map((node) => {
        const style = TYPE_COLORS[node.cell_type] || TYPE_COLORS.text;
        const active = highlightId === node.id;
        return (
          <g
            key={node.id}
            className={`logic-node ${active ? "active" : ""} ${interactive ? "clickable" : ""}`}
            onClick={interactive && onNodeClick ? () => onNodeClick(node.id) : undefined}
            style={{ cursor: interactive && onNodeClick ? "pointer" : "default" }}
          >
            <rect
              x={node.x}
              y={node.y}
              width={node.width}
              height={node.height}
              rx={8}
              fill={style.bg}
              stroke={active ? "var(--brand)" : style.border}
              strokeWidth={active ? 2.5 : 1.5}
            />
            <text x={node.x + 12} y={node.y + 22} className="logic-node-type" fontSize="10">
              {style.badge}
            </text>
            <text x={node.x + 12} y={node.y + 42} className="logic-node-name" fontSize="13" fontWeight="600">
              {node.name.length > 26 ? `${node.name.slice(0, 24)}…` : node.name}
            </text>
            {node.cell_type === "sql" && node.row_count != null && (
              <text x={node.x + 12} y={node.y + 60} className="logic-node-meta" fontSize="10">
                {node.row_count} rows · cached
              </text>
            )}
            {node.cell_type === "input" && node.variables?.length > 0 && (
              <text x={node.x + 12} y={node.y + 60} className="logic-node-meta" fontSize="10">
                {node.variables.map((v) => `{{ ${v} }}`).join(", ")}
              </text>
            )}
            <circle
              cx={node.x + node.width - 14}
              cy={node.y + 14}
              r={5}
              fill={node.status === "ok" ? "#22c55e" : "#d4d4d8"}
            />
          </g>
        );
      })}
    </svg>
  );
}

/** Full logic graph with pan/zoom (Hex-style logic view). */
export function LogicGraphPanel({ graph, highlightId, onNodeClick }) {
  const [zoom, setZoom] = useState(1);
  const scrollRef = useRef(null);

  const zoomIn = () => setZoom((z) => Math.min(2, Math.round((z + 0.15) * 100) / 100));
  const zoomOut = () => setZoom((z) => Math.max(0.35, Math.round((z - 0.15) * 100) / 100));
  const zoomFit = () => setZoom(1);

  return (
    <div className="logic-graph-panel">
      <div className="logic-graph-toolbar">
        <span className="logic-graph-title">Logic graph</span>
        <span className="muted logic-graph-sub">
          {(graph?.nodes || []).length} nodes · {(graph?.edges || []).length} edges
        </span>
        <div className="logic-graph-zoom">
          <button type="button" className="secondary small" onClick={zoomOut} aria-label="Zoom out">−</button>
          <span className="logic-graph-zoom-label">{Math.round(zoom * 100)}%</span>
          <button type="button" className="secondary small" onClick={zoomIn} aria-label="Zoom in">+</button>
          <button type="button" className="secondary small" onClick={zoomFit}>Fit</button>
        </div>
      </div>
      <div className="logic-graph-legend">
        <span><i className="legend-line solid" /> FROM chain</span>
        <span><i className="legend-line dashed" /> {"{{ variable }}"}</span>
      </div>
      <div className="logic-graph-scroll" ref={scrollRef}>
        <GraphCanvas
          graph={graph}
          zoom={zoom}
          highlightId={highlightId}
          onNodeClick={onNodeClick}
          interactive
        />
      </div>
    </div>
  );
}

/** Corner minimap (bird's-eye view). */
export function LogicGraphMinimap({ graph, onExpand, highlightId }) {
  const miniZoom = useMemo(() => {
    if (!graph?.width || !graph?.height) return 0.2;
    return Math.min(180 / graph.width, 120 / graph.height, 0.35);
  }, [graph]);

  if (!graph?.nodes?.length) return null;

  return (
    <div className="logic-minimap">
      <div className="logic-minimap-head">
        <span>Logic</span>
        {onExpand && (
          <button type="button" className="link-btn" onClick={onExpand}>
            Expand
          </button>
        )}
      </div>
      <div className="logic-minimap-canvas">
        <GraphCanvas
          graph={graph}
          zoom={miniZoom}
          highlightId={highlightId}
          interactive={false}
          className="minimap"
        />
      </div>
    </div>
  );
}

export default LogicGraphPanel;
