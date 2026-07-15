import Badge from "./Badge";
import { SEVERITY_META } from "../constants";

const CATEGORY_LABEL = {
  SECURITY: "Security",
  ARCHITECTURE: "Architecture",
  TEST_COVERAGE: "Test coverage",
  DOCUMENTATION: "Documentation",
};

export default function FindingCard({ finding }) {
  const sev = SEVERITY_META[finding.severity] ?? SEVERITY_META.INFO;
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        background: "var(--canvas)",
        padding: 12,
        marginBottom: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        <Badge fg={sev.fg} bg={sev.bg}>{sev.label}</Badge>
        <span style={{ fontSize: 12, color: "var(--fg-subtle)" }}>
          {CATEGORY_LABEL[finding.category] ?? finding.category}
        </span>
        <span
          className="mono"
          style={{ fontSize: 12, color: "var(--fg-subtle)", marginLeft: "auto" }}
        >
          {finding.location.file}:{finding.location.line_start}
          {finding.location.line_end !== finding.location.line_start ? `-${finding.location.line_end}` : ""}
        </span>
      </div>

      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>{finding.title}</div>
      <div style={{ fontSize: 13, color: "var(--fg-muted)", marginBottom: finding.suggestion ? 8 : 0 }}>
        {finding.description}
      </div>

      {finding.location.snippet && (
        <pre
          className="mono"
          style={{
            margin: "8px 0",
            padding: 8,
            background: "var(--canvas-inset)",
            borderRadius: 4,
            fontSize: 12,
            overflowX: "auto",
            color: "var(--fg-default)",
          }}
        >
          {finding.location.snippet}
        </pre>
      )}

      {finding.suggestion && (
        <div
          style={{
            fontSize: 13,
            padding: "6px 10px",
            background: "var(--accent-subtle)",
            borderRadius: 4,
            color: "var(--fg-default)",
          }}
        >
          <strong style={{ color: "var(--accent-emphasis)" }}>Suggestion:</strong> {finding.suggestion}
        </div>
      )}
    </div>
  );
}