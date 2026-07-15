import { AGENT_META } from "../constants";

const AGENT_ORDER = [
  "security_agent",
  "architecture_agent",
  "test_coverage_agent",
  "documentation_agent",
];

function CheckIcon({ color }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill={color} aria-hidden="true">
      <path d="M8 16A8 8 0 1 1 8 0a8 8 0 0 1 0 16Zm3.78-9.72a.75.75 0 0 0-1.06-1.06L6.75 9.19 5.28 7.72a.75.75 0 0 0-1.06 1.06l2 2a.75.75 0 0 0 1.06 0l4.5-4.5Z" />
    </svg>
  );
}

function WarnIcon({ color }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill={color} aria-hidden="true">
      <path d="M6.457 1.047c.659-1.234 2.427-1.234 3.086 0l6.082 11.378A1.75 1.75 0 0 1 14.082 15H1.918a1.75 1.75 0 0 1-1.543-2.575ZM8 5a.75.75 0 0 0-.75.75v2.5a.75.75 0 0 0 1.5 0v-2.5A.75.75 0 0 0 8 5Zm0 6a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z" />
    </svg>
  );
}

function XIcon({ color }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill={color} aria-hidden="true">
      <path d="M2.343 13.657A8 8 0 1 1 13.657 2.343 8 8 0 0 1 2.343 13.657ZM6.03 4.97a.75.75 0 0 0-1.06 1.06L6.94 8l-1.97 1.97a.75.75 0 1 0 1.06 1.06L8 9.06l1.97 1.97a.75.75 0 0 0 1.06-1.06L9.06 8l1.97-1.97a.75.75 0 0 0-1.06-1.06L8 6.94Z" />
    </svg>
  );
}

function agentState(report) {
  if (!report) return { key: "unknown", color: "var(--fg-subtle)", Icon: WarnIcon };
  if (report.error) return { key: "error", color: "var(--danger)", Icon: XIcon };
  if (report.findings && report.findings.length > 0) {
    return { key: "warn", color: "var(--attention)", Icon: WarnIcon };
  }
  return { key: "clean", color: "var(--success)", Icon: CheckIcon };
}

export default function AgentStrip({ agentReports }) {
  const byName = {};
  for (const r of agentReports || []) {
    byName[r.agent_name] = r;
  }

  return (
    <div
      style={{
        display: "flex",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        overflow: "hidden",
        background: "var(--canvas)",
      }}
    >
      {AGENT_ORDER.map((key, i) => {
        const report = byName[key];
        const meta = AGENT_META[key];
        const { color, Icon } = agentState(report);
        const count = report?.findings?.length ?? null;
        return (
          <div
            key={key}
            title={report?.error ? `${meta.label}: ${report.error}` : `${meta.label}: ${count ?? "no data"} finding(s)`}
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "8px 12px",
              borderLeft: i === 0 ? "none" : "1px solid var(--border-muted)",
            }}
          >
            <Icon color={color} />
            <span style={{ fontSize: 12, fontWeight: 500, color: "var(--fg-default)" }}>
              {meta.label}
            </span>
            {count !== null && (
              <span style={{ fontSize: 12, color: "var(--fg-subtle)", marginLeft: "auto" }}>
                {count}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}