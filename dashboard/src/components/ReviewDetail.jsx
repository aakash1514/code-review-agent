import { useEffect, useRef, useState } from "react";
import Badge from "./Badge";
import AgentStrip from "./AgentStrip";
import FindingCard from "./FindingCard";
import LiveProgress from "./LiveProgress";
import DiffViewer from "./DiffViewer";
import CallGraph from "./CallGraph";
import { SEVERITY_ORDER, SEVERITY_META, JOB_STATUS_META, shortSha } from "../constants";
import { approveReview, blockReview, clearOverride, fetchReviewFiles, fetchGraph } from "../api";

function VerdictBanner({ review }) {
  const critical = review.critical_count ?? 0;
  const high = review.high_count ?? 0;
  let fg = "var(--success)";
  let bg = "var(--success-subtle)";
  let text = "No blocking issues found";
  if (critical > 0) {
    fg = "var(--danger)";
    bg = "var(--danger-subtle)";
    text = `${critical} critical issue${critical === 1 ? "" : "s"} — merge blocked`;
  } else if (high > 0) {
    fg = "var(--attention)";
    bg = "var(--attention-subtle)";
    text = `${high} high-severity finding${high === 1 ? "" : "s"} to review`;
  }
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "10px 14px",
        borderRadius: "var(--radius)",
        background: bg,
        color: fg,
        fontWeight: 600,
        fontSize: 14,
      }}
    >
      {text}
    </div>
  );
}

function TabButton({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      style={{
        border: "none",
        background: "none",
        padding: "10px 4px",
        marginRight: 20,
        fontSize: 13,
        fontWeight: 600,
        color: active ? "var(--fg-default)" : "var(--fg-muted)",
        borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

function persistedResolution(job) {
  return job?.override_action ? { action: job.override_action, by: job.override_by } : null;
}

export default function ReviewDetail({ repo, job, onActionComplete, liveEvents = [], currentUser, onLogin }) {
  const review = job?.review;
  // Diff files are fetched once per (repo, commit) once a review exists.
  const filesKey = job?.commit_sha && review ? `${repo}\n${job.commit_sha}` : null;

  const [actionState, setActionState] = useState({ loading: false, message: null });
  // Restore a persisted decision from the backend (survives reloads),
  // rather than always starting blank — this is the actual point of
  // persisting override_action/override_by server-side.
  const [resolution, setResolution] = useState(() => persistedResolution(job)); // { action: "approved"|"blocked", by: string } | null
  const [clearing, setClearing] = useState(false);
  const [tab, setTab] = useState("findings");
  const [files, setFiles] = useState(null);
  const [filesLoading, setFilesLoading] = useState(filesKey !== null);
  const [filesError, setFilesError] = useState(null);
  const [jumpTarget, setJumpTarget] = useState(null);

  const [graph, setGraph] = useState(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState(null);
  const graphRepoRef = useRef(null);

  // Reset per-review UI state when a different commit is opened
  // (adjusting state during render instead of in an effect).
  const [prevSha, setPrevSha] = useState(job?.commit_sha);
  if (prevSha !== job?.commit_sha) {
    setPrevSha(job?.commit_sha);
    setTab("findings");
    setFiles(null);
    setFilesError(null);
    setJumpTarget(null);
  }

  // Sync the banner when the persisted decision changes (or a new commit is
  // opened) without touching the tab or the loaded diff.
  const [prevDecision, setPrevDecision] = useState({
    sha: job?.commit_sha,
    overrideAction: job?.override_action,
    overrideBy: job?.override_by,
  });
  if (
    prevDecision.sha !== job?.commit_sha ||
    prevDecision.overrideAction !== job?.override_action ||
    prevDecision.overrideBy !== job?.override_by
  ) {
    setPrevDecision({ sha: job?.commit_sha, overrideAction: job?.override_action, overrideBy: job?.override_by });
    setResolution(persistedResolution(job));
    setActionState({ loading: false, message: null });
  }

  // Mark the diff as loading as soon as a new fetch is due.
  const [prevFilesKey, setPrevFilesKey] = useState(filesKey);
  if (filesKey !== prevFilesKey) {
    setPrevFilesKey(filesKey);
    if (filesKey !== null) setFilesLoading(true);
  }

  useEffect(() => {
    if (tab !== "callgraph") return;
    if (graphRepoRef.current === repo && graph) return; // already loaded for this repo
    let cancelled = false;
    setGraphLoading(true);
    setGraphError(null);
    fetchGraph(repo)
      .then((data) => {
        if (!cancelled) {
          setGraph(data);
          graphRepoRef.current = repo;
        }
      })
      .catch((e) => {
        if (!cancelled) setGraphError(e.message);
      })
      .finally(() => {
        if (!cancelled) setGraphLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, repo]);

  useEffect(() => {
    if (!job?.commit_sha || !review) return;
    let cancelled = false;
    fetchReviewFiles(repo, job.commit_sha)
      .then((data) => {
        if (!cancelled) setFiles(data.files ?? []);
      })
      .catch((e) => {
        if (!cancelled) setFilesError(e.message);
      })
      .finally(() => {
        if (!cancelled) setFilesLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repo, job?.commit_sha, !!review]);

  if (!job) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "var(--fg-subtle)",
          fontSize: 14,
        }}
      >
        Select a review to see its findings.
      </div>
    );
  }

  const statusMeta = JOB_STATUS_META[job.status] ?? JOB_STATUS_META.pending;

  async function handleAction(kind) {
    setActionState({ loading: true, message: null });
    const fn = kind === "approve" ? approveReview : blockReview;
    try {
      const result = await fn(repo, job.commit_sha);
      if (result.status === "approved" || result.status === "blocked") {
        setResolution({ action: result.status, by: result.by });
        setActionState({ loading: false, message: null });
      } else {
        setActionState({ loading: false, message: result.detail || result.reason || JSON.stringify(result) });
      }
      onActionComplete?.();
    } catch (e) {
      setActionState({ loading: false, message: `Action failed: ${e.message}` });
    }
  }

  function handleViewInCode(finding) {
    setTab("code");
    setJumpTarget({ file: finding.location.file, lineStart: finding.location.line_start, key: Date.now() });
  }

  function handleSelectFileFromGraph(file) {
    if (!files?.some((f) => f.filename === file)) return; // not part of this PR's diff — nothing to show
    setTab("code");
    setJumpTarget({ file, lineStart: 1, key: Date.now() });
  }

  async function handleClearOverride() {
    setClearing(true);
    try {
      await clearOverride(repo, job.commit_sha);
      setResolution(null);
      onActionComplete?.();
    } catch (e) {
      setActionState({ loading: false, message: `Couldn't clear decision: ${e.message}` });
    } finally {
      setClearing(false);
    }
  }

  const grouped = SEVERITY_ORDER.map((sev) => ({
    severity: sev,
    findings: (review?.all_findings ?? []).filter((f) => f.severity === sev),
  })).filter((g) => g.findings.length > 0);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ padding: "20px 20px 0" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4 }}>
          <h1 style={{ fontSize: 18, margin: 0 }}>PR #{job.pr_number}</h1>
          <Badge fg={statusMeta.fg} bg={statusMeta.bg} dot>{statusMeta.label}</Badge>
        </div>
        <div className="mono" style={{ fontSize: 12, color: "var(--fg-subtle)", marginBottom: 12 }}>
          {shortSha(job.commit_sha)} · {repo}
        </div>

        {review && (
          <div style={{ display: "flex", borderBottom: "1px solid var(--border-muted)" }}>
            <TabButton active={tab === "findings"} onClick={() => setTab("findings")}>
              Findings
            </TabButton>
            <TabButton active={tab === "code"} onClick={() => setTab("code")}>
              Code
            </TabButton>
            <TabButton active={tab === "callgraph"} onClick={() => setTab("callgraph")}>
              Call Graph
            </TabButton>
          </div>
        )}
      </div>

      {tab === "code" && review ? (
        <div style={{ flex: 1, minHeight: 0 }}>
          {filesLoading && !files && (
            <div style={{ padding: 20, fontSize: 13, color: "var(--fg-muted)" }}>Loading diff…</div>
          )}
          {filesError && (
            <div style={{ padding: 20, fontSize: 13, color: "var(--danger)" }}>
              Couldn't load diff. {filesError}
            </div>
          )}
          {files && (
            <DiffViewer files={files} findings={review.all_findings ?? []} jumpTarget={jumpTarget} />
          )}
        </div>
      ) : tab === "callgraph" && review ? (
        <div style={{ flex: 1, minHeight: 0 }}>
          {graphLoading && !graph && (
            <div style={{ padding: 20, fontSize: 13, color: "var(--fg-muted)" }}>Loading call graph…</div>
          )}
          {graphError && (
            <div style={{ padding: 20, fontSize: 13, color: "var(--danger)" }}>
              Couldn't load call graph. {graphError}
            </div>
          )}
          {graph && (
            <CallGraph
              graph={graph}
              findings={review.all_findings ?? []}
              diffFiles={(files ?? []).map((f) => f.filename)}
              onSelectFile={handleSelectFileFromGraph}
            />
          )}
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "16px 20px 20px" }}>
          <LiveProgress events={liveEvents} isLive={job.status === "pending" || job.status === "running"} />

          {job.error && (
            <div
              style={{
                marginBottom: 16,
                padding: "10px 14px",
                borderRadius: "var(--radius)",
                background: "var(--danger-subtle)",
                color: "var(--danger-emphasis)",
                fontSize: 13,
              }}
            >
              {job.error}
            </div>
          )}

          {!review && !job.error && (
            <div style={{ color: "var(--fg-muted)", fontSize: 13 }}>
              Review still in progress — check back shortly.
            </div>
          )}

          {review && (
            <>
              <div style={{ marginBottom: 16 }}>
                <AgentStrip agentReports={review.agent_reports} />
              </div>

              <div style={{ marginBottom: 20 }}>
                <VerdictBanner review={review} />
              </div>

              {grouped.length === 0 ? (
                <div style={{ color: "var(--fg-muted)", fontSize: 13, marginBottom: 20 }}>
                  No findings — clean review.
                </div>
              ) : (
                grouped.map((group) => {
                  const meta = SEVERITY_META[group.severity];
                  return (
                    <div key={group.severity} style={{ marginBottom: 18 }}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          marginBottom: 8,
                          fontSize: 12,
                          fontWeight: 700,
                          color: meta.fg,
                          textTransform: "uppercase",
                          letterSpacing: "0.03em",
                        }}
                      >
                        {meta.label} · {group.findings.length}
                      </div>
                      {group.findings.map((f, i) => (
                        <FindingCard key={i} finding={f} onViewInCode={handleViewInCode} />
                      ))}
                    </div>
                  );
                })
              )}

              {review.synthesized_review && (
                <details style={{ marginTop: 8, marginBottom: 20 }}>
                  <summary style={{ cursor: "pointer", fontSize: 13, color: "var(--fg-muted)", fontWeight: 600 }}>
                    AI-generated summary (as posted to GitHub)
                  </summary>
                  <pre
                    style={{
                      whiteSpace: "pre-wrap",
                      fontSize: 13,
                      marginTop: 8,
                      padding: 12,
                      background: "var(--canvas-inset)",
                      borderRadius: "var(--radius)",
                    }}
                  >
                    {review.synthesized_review}
                  </pre>
                </details>
              )}

              <div style={{ paddingTop: 8, borderTop: "1px solid var(--border-muted)" }}>
                {resolution ? (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "10px 14px",
                      borderRadius: "var(--radius)",
                      background: resolution.action === "approved" ? "var(--success-subtle)" : "var(--danger-subtle)",
                      color: resolution.action === "approved" ? "var(--success-emphasis)" : "var(--danger-emphasis)",
                      fontSize: 13,
                      fontWeight: 600,
                    }}
                  >
                    <span style={{ fontSize: 15 }}>{resolution.action === "approved" ? "✓" : "✕"}</span>
                    <span>
                      {resolution.action === "approved" ? "Approved" : "Blocked"} by @{resolution.by}
                    </span>
                    <button
                      onClick={handleClearOverride}
                      disabled={clearing}
                      style={{
                        marginLeft: "auto",
                        border: "none",
                        background: "none",
                        color: "inherit",
                        fontSize: 12,
                        fontWeight: 500,
                        textDecoration: "underline",
                        cursor: clearing ? "default" : "pointer",
                        opacity: clearing ? 0.6 : 0.85,
                      }}
                    >
                      {clearing ? "Clearing…" : "Change decision"}
                    </button>
                  </div>
                ) : (
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    {currentUser ? (
                      <>
                        <button
                          onClick={() => handleAction("approve")}
                          disabled={actionState.loading}
                          style={buttonStyle("var(--success)")}
                        >
                          {actionState.loading ? "Working…" : "Approve"}
                        </button>
                        <button
                          onClick={() => handleAction("block")}
                          disabled={actionState.loading}
                          style={buttonStyle("var(--danger)")}
                        >
                          {actionState.loading ? "Working…" : "Block"}
                        </button>
                        {actionState.message && (
                          <span style={{ fontSize: 12, color: "var(--danger)" }}>{actionState.message}</span>
                        )}
                      </>
                    ) : (
                      <span style={{ fontSize: 12, color: "var(--fg-muted)" }}>
                        <button
                          onClick={onLogin}
                          style={{ border: "none", background: "none", color: "var(--accent)", cursor: "pointer", padding: 0, fontSize: 12, textDecoration: "underline" }}
                        >
                          Sign in
                        </button>{" "}
                        to approve or block this PR.
                      </span>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function buttonStyle(color) {
  return {
    border: `1px solid ${color}`,
    color,
    background: "var(--canvas)",
    borderRadius: "var(--radius)",
    padding: "6px 14px",
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  };
}