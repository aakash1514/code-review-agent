import { useCallback, useEffect, useRef, useState } from "react";
import TopBar from "./components/TopBar";
import ReviewList from "./components/ReviewList";
import ReviewDetail from "./components/ReviewDetail";
import RepoGraphView from "./components/RepoGraphView";
import { fetchReviews, fetchReview, fetchMe, loginUrl, logout as apiLogout, setStoredSessionToken } from "./api";
import { useReviewSocket } from "./hooks/useReviewSocket";

const DEFAULT_REPO = "aakash1514/code-review-agent-test";
// WebSocket events are now the primary freshness signal — this poll is just
// a fallback in case a connection drops silently without the onclose firing.
const POLL_INTERVAL_MS = 15000;
const MAX_EVENTS_PER_COMMIT = 30;
const LIST_REFRESH_DEBOUNCE_MS = 400;

export default function App() {
  const [view, setView] = useState("reviews"); // "reviews" | "graph"
  const [repo, setRepo] = useState(DEFAULT_REPO);
  const [repoInput, setRepoInput] = useState(DEFAULT_REPO);

  const [reviews, setReviews] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState(null);

  const [selectedSha, setSelectedSha] = useState(null);
  const [selectedJob, setSelectedJob] = useState(null);

  const [autoRefresh, setAutoRefresh] = useState(true);
  const pollRef = useRef(null);

  const [eventsByCommit, setEventsByCommit] = useState({});
  const listRefreshTimer = useRef(null);

  const [user, setUser] = useState(null);

  // Pick up ?session=<id> from the OAuth callback redirect, persist it, and
  // strip it from the URL so a page refresh doesn't re-process a stale
  // query param. Runs once on mount; also handles the "already had a valid
  // session from a previous visit" case via fetchMe()'s stored-token fallback.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sessionFromUrl = params.get("session");
    if (sessionFromUrl) {
      setStoredSessionToken(sessionFromUrl);
      params.delete("session");
      const cleanUrl = window.location.pathname + (params.toString() ? `?${params}` : "");
      window.history.replaceState({}, "", cleanUrl);
    }
    fetchMe().then(setUser);
  }, []);

  function handleLogin() {
    window.location.href = loginUrl();
  }

  async function handleLogout() {
    await apiLogout();
    setUser(null);
  }

  const loadList = useCallback((currentRepo) => {
    return fetchReviews(currentRepo)
      .then((data) => {
        setReviews(data.reviews ?? []);
        setListError(null);
      })
      .catch((e) => {
        setListError(e.message);
      })
      .finally(() => {
        setListLoading(false);
      });
  }, []);

  const loadDetail = useCallback(async (currentRepo, sha) => {
    if (!sha) return;
    try {
      const data = await fetchReview(currentRepo, sha);
      setSelectedJob(data);
    } catch (e) {
      setSelectedJob({ commit_sha: sha, status: "failed", error: e.message, review: null });
    }
  }, []);

  // initial + repo-change load (per-repo state is reset in handleRepoSubmit)
  useEffect(() => {
    loadList(repo);
  }, [repo, loadList]);

  function scheduleListRefresh() {
    clearTimeout(listRefreshTimer.current);
    listRefreshTimer.current = setTimeout(() => loadList(repo), LIST_REFRESH_DEBOUNCE_MS);
  }

  function handleSocketEvent(event) {
    if (!event?.commit_sha || !event?.stage) return;

    setEventsByCommit((prev) => {
      const existing = prev[event.commit_sha] ?? [];
      const next = [...existing, event].slice(-MAX_EVENTS_PER_COMMIT);
      return { ...prev, [event.commit_sha]: next };
    });

    // Any stage change means the job_store row (or the open detail panel) is
    // now stale — refresh the list on a short debounce so several agent_complete
    // events firing close together don't trigger a refetch each.
    scheduleListRefresh();

    // Only refetch the open detail panel on stages where the persisted
    // ReviewReport actually changed (synthesizing/complete/failed write to
    // SQLite); earlier stages only affect the live log, already updated above.
    const detailAffectingStages = new Set(["synthesizing", "complete", "failed"]);
    if (event.commit_sha === selectedSha && detailAffectingStages.has(event.stage)) {
      loadDetail(repo, selectedSha);
    }
  }

  const { connected: wsConnected } = useReviewSocket(repo, handleSocketEvent);

  // polling
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (!autoRefresh) return;
    pollRef.current = setInterval(() => {
      loadList(repo);
      if (selectedSha) loadDetail(repo, selectedSha);
    }, POLL_INTERVAL_MS);
    return () => clearInterval(pollRef.current);
  }, [autoRefresh, repo, selectedSha, loadList, loadDetail]);

  function handleSelect(sha) {
    setSelectedSha(sha);
    setSelectedJob(null);
    loadDetail(repo, sha);
  }

  function handleRepoSubmit() {
    const trimmed = repoInput.trim();
    if (!trimmed || trimmed === repo) return;
    setRepo(trimmed);
    setListLoading(true);
    setSelectedSha(null);
    setSelectedJob(null);
    setEventsByCommit({});
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <TopBar
        repo={repo}
        repoInput={repoInput}
        onRepoInputChange={setRepoInput}
        onRepoSubmit={handleRepoSubmit}
        autoRefresh={autoRefresh}
        onToggleAutoRefresh={() => setAutoRefresh((v) => !v)}
        wsConnected={wsConnected}
        user={user}
        onLogin={handleLogin}
        onLogout={handleLogout}
      />
      <div
        style={{
          display: "flex",
          padding: "0 20px",
          borderBottom: "1px solid var(--border)",
          background: "var(--canvas)",
        }}
      >
        <NavTab active={view === "reviews"} onClick={() => setView("reviews")}>
          Reviews
        </NavTab>
        <NavTab active={view === "graph"} onClick={() => setView("graph")}>
          Call Graph
        </NavTab>
      </div>

      {view === "reviews" ? (
        <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
          <div style={{ width: 320, flexShrink: 0 }}>
            <ReviewList
              reviews={reviews}
              selectedSha={selectedSha}
              onSelect={handleSelect}
              loading={listLoading}
              error={listError}
            />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <ReviewDetail
              repo={repo}
              job={selectedJob}
              onActionComplete={() => loadList(repo)}
              liveEvents={selectedSha ? eventsByCommit[selectedSha] ?? [] : []}
              currentUser={user}
              onLogin={handleLogin}
            />
          </div>
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 0 }}>
          <RepoGraphView key={repo} repo={repo} />
        </div>
      )}
    </div>
  );
}

function NavTab({ active, onClick, children }) {
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