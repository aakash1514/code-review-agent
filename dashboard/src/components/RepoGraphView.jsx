import { useEffect, useState } from "react";
import CallGraph from "./CallGraph";
import { fetchGraph } from "../api";

export default function RepoGraphView({ repo }) {
  const [graph, setGraph] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setGraph(null);
    setLoading(true);
    setError(null);
    fetchGraph(repo)
      .then((data) => {
        if (!cancelled) setGraph(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [repo]);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ padding: "16px 20px 0" }}>
        <h1 style={{ fontSize: 16, margin: "0 0 4px" }}>Call graph</h1>
        <div className="mono" style={{ fontSize: 12, color: "var(--fg-subtle)", marginBottom: 12 }}>
          {repo}
        </div>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        {loading && (
          <div style={{ padding: 20, fontSize: 13, color: "var(--fg-muted)" }}>Loading call graph…</div>
        )}
        {error && (
          <div style={{ padding: 20, fontSize: 13, color: "var(--danger)" }}>
            Couldn't load call graph. {error}
            {error.includes("404") && (
              <div style={{ marginTop: 6, color: "var(--fg-muted)" }}>
                No knowledge graph has been indexed for this repo yet — run{" "}
                <code className="mono">python -m scripts.index_repo --path . --repo "{repo}"</code>.
              </div>
            )}
          </div>
        )}
        {graph && <CallGraph graph={graph} mode="standalone" />}
      </div>
    </div>
  );
}