import { useMemo, useState } from "react";
import { ReactFlow, Background, Controls, MarkerType } from "@xyflow/react";
import dagre from "dagre";
import "@xyflow/react/dist/style.css";
import { SEVERITY_ORDER, SEVERITY_META } from "../constants";

const NODE_HEIGHT = 40;
const DIM_OPACITY = 0.55; // node not part of this PR's diff (review mode only)
const FADE_OPACITY = 0.12; // node/edge outside the selected ego network

function estimateNodeWidth(label) {
  return Math.min(260, Math.max(140, label.length * 7 + 40));
}

function layout(nodes, edges) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 30, ranksep: 90 });

  nodes.forEach((n) => {
    g.setNode(n.id, { width: estimateNodeWidth(n.name), height: NODE_HEIGHT });
  });
  edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    const width = estimateNodeWidth(n.name);
    return { ...n, position: { x: pos.x - width / 2, y: pos.y - NODE_HEIGHT / 2 }, width };
  });
}

/** Highest-severity finding per file, so file-level nodes can be color-coded
 * without needing (unreliable) function-level line matching against the KG. */
function computeFileSeverity(findings) {
  const map = {};
  for (const f of findings ?? []) {
    const file = f.location.file;
    const currentRank = map[file] ? SEVERITY_ORDER.indexOf(map[file]) : Infinity;
    const rank = SEVERITY_ORDER.indexOf(f.severity);
    if (rank < currentRank) map[file] = f.severity;
  }
  return map;
}

function buildAdjacency(edges) {
  const outgoing = new Map(); // id -> Set of ids it calls
  const incoming = new Map(); // id -> Set of ids that call it
  for (const e of edges) {
    if (!outgoing.has(e.source)) outgoing.set(e.source, new Set());
    outgoing.get(e.source).add(e.target);
    if (!incoming.has(e.target)) incoming.set(e.target, new Set());
    incoming.get(e.target).add(e.source);
  }
  return { outgoing, incoming };
}

/**
 * mode="review": nodes for files not touched by the open PR are dimmed;
 * nodes with findings get severity-colored borders; clicking a diff-file
 * node also jumps to it in the Code tab via onSelectFile.
 *
 * mode="standalone": full-repo exploration, no diff/severity context. Every
 * node starts equally weighted; clicking one highlights its direct
 * callers + callees (one-hop ego network) and fades everything else, since
 * a flat unfiltered graph of a real codebase is otherwise just noise.
 */
export default function CallGraph({ graph, findings = [], diffFiles = [], onSelectFile, mode = "review" }) {
  const [selectedId, setSelectedId] = useState(null);

  const fileSeverity = useMemo(() => computeFileSeverity(findings), [findings]);
  const diffFileSet = useMemo(() => new Set(diffFiles ?? []), [diffFiles]);
  const { outgoing, incoming } = useMemo(
    () => buildAdjacency(graph?.edges ?? []),
    [graph]
  );

  const egoSet = useMemo(() => {
    if (!selectedId) return null;
    const set = new Set([selectedId]);
    for (const id of outgoing.get(selectedId) ?? []) set.add(id);
    for (const id of incoming.get(selectedId) ?? []) set.add(id);
    return set;
  }, [selectedId, outgoing, incoming]);

  const { rfNodes, rfEdges } = useMemo(() => {
    if (!graph) return { rfNodes: [], rfEdges: [] };
    const positioned = layout(graph.nodes, graph.edges);

    const rfNodes = positioned.map((n) => {
      const inDiff = diffFileSet.has(n.file);
      const severity = fileSeverity[n.file];
      const sevMeta = severity ? SEVERITY_META[severity] : null;
      const isSelected = n.id === selectedId;
      const inEgo = !egoSet || egoSet.has(n.id);

      let opacity = mode === "review" ? (inDiff ? 1 : DIM_OPACITY) : 1;
      if (egoSet && !inEgo) opacity = FADE_OPACITY;

      return {
        id: n.id,
        position: n.position,
        data: { label: n.name, file: n.file },
        style: {
          width: n.width,
          height: NODE_HEIGHT,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 12,
          fontFamily: "var(--font-mono)",
          borderRadius: 6,
          border: isSelected
            ? "1.5px solid var(--accent)"
            : `1.5px solid ${sevMeta ? sevMeta.fg : "var(--border)"}`,
          background: isSelected ? "var(--accent-subtle)" : sevMeta ? sevMeta.bg : "var(--canvas)",
          color: "var(--fg-default)",
          opacity,
          cursor: "pointer",
          transition: "opacity 120ms ease",
        },
      };
    });

    const rfEdges = graph.edges.map((e, i) => {
      const inEgo = egoSet && (e.source === selectedId || e.target === selectedId);
      const faded = egoSet && !inEgo;
      return {
        id: `e-${i}`,
        source: e.source,
        target: e.target,
        style: {
          stroke: inEgo ? "var(--accent)" : "var(--border)",
          strokeWidth: inEgo ? 2 : 1,
          opacity: faded ? FADE_OPACITY : 1,
          transition: "opacity 120ms ease",
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: inEgo ? "var(--accent)" : "var(--border)",
          width: 14,
          height: 14,
        },
      };
    });

    return { rfNodes, rfEdges };
  }, [graph, fileSeverity, diffFileSet, mode, selectedId, egoSet]);

  if (!graph) return null;

  if (graph.nodes.length === 0) {
    return (
      <div style={{ padding: 20, color: "var(--fg-muted)", fontSize: 13 }}>
        Knowledge graph is empty for this repo — run the indexer first.
      </div>
    );
  }

  function handleNodeClick(_, node) {
    setSelectedId((prev) => (prev === node.id ? null : node.id));
    if (mode === "review" && diffFileSet.has(node.data.file)) {
      onSelectFile?.(node.data.file);
    }
  }

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "8px 12px",
          borderBottom: "1px solid var(--border-muted)",
          fontSize: 12,
          color: "var(--fg-muted)",
          flexShrink: 0,
          flexWrap: "wrap",
        }}
      >
        {mode === "review" ? (
          <>
            <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, border: "1.5px solid var(--border)", background: "var(--canvas)", opacity: DIM_OPACITY }} />
              Not in this PR's diff
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, border: "1.5px solid var(--border)", background: "var(--canvas)" }} />
              Changed in this PR
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, border: "1.5px solid var(--sev-critical)", background: "var(--sev-critical-bg)" }} />
              Has findings (border = highest severity)
            </span>
          </>
        ) : (
          <span>Click a function to highlight its direct callers and callees. Click again (or the background) to clear.</span>
        )}
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          proOptions={{ hideAttribution: true }}
          onNodeClick={handleNodeClick}
          onPaneClick={() => setSelectedId(null)}
        >
          <Background color="var(--border-muted)" gap={20} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}