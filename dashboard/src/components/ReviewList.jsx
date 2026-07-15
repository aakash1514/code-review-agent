import Badge from "./Badge";
import { JOB_STATUS_META, shortSha, formatTimestamp } from "../constants";

export default function ReviewList({ reviews, selectedSha, onSelect, loading, error }) {
  return (
    <div
      style={{
        borderRight: "1px solid var(--border)",
        background: "var(--canvas)",
        overflowY: "auto",
        height: "100%",
      }}
    >
      <div
        style={{
          padding: "10px 16px",
          borderBottom: "1px solid var(--border-muted)",
          fontSize: 12,
          fontWeight: 600,
          color: "var(--fg-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.03em",
          position: "sticky",
          top: 0,
          background: "var(--canvas)",
        }}
      >
        Reviews
      </div>

      {error && (
        <div style={{ padding: 16, fontSize: 13, color: "var(--danger)" }}>
          Couldn't load reviews. {error}
        </div>
      )}

      {!error && loading && reviews.length === 0 && (
        <div style={{ padding: 16, fontSize: 13, color: "var(--fg-muted)" }}>Loading…</div>
      )}

      {!error && !loading && reviews.length === 0 && (
        <div style={{ padding: 16, fontSize: 13, color: "var(--fg-muted)" }}>
          No reviews yet. Open or update a pull request on this repo to trigger one.
        </div>
      )}

      {reviews.map((review) => {
        const statusMeta = JOB_STATUS_META[review.status] ?? JOB_STATUS_META.pending;
        const isSelected = review.commit_sha === selectedSha;
        return (
          <button
            key={review.commit_sha}
            onClick={() => onSelect(review.commit_sha)}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              padding: "12px 16px",
              border: "none",
              borderBottom: "1px solid var(--border-muted)",
              background: isSelected ? "var(--accent-subtle)" : "transparent",
              cursor: "pointer",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>PR #{review.pr_number}</span>
              <Badge fg={statusMeta.fg} bg={statusMeta.bg} dot>
                {statusMeta.label}
              </Badge>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--fg-muted)" }}>
              <span className="mono">{shortSha(review.commit_sha)}</span>
              <span>·</span>
              <span>{formatTimestamp(review.updated_at)}</span>
            </div>
            {typeof review.critical_count === "number" && review.critical_count > 0 && (
              <div style={{ marginTop: 6 }}>
                <Badge fg="var(--sev-critical)" bg="var(--sev-critical-bg)">
                  {review.critical_count} critical
                </Badge>
              </div>
            )}
            {review.error && (
              <div style={{ marginTop: 6, fontSize: 12, color: "var(--danger)" }}>{review.error}</div>
            )}
          </button>
        );
      })}
    </div>
  );
}