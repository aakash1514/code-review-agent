export const SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];

export const SEVERITY_META = {
  CRITICAL: { fg: "var(--sev-critical)", bg: "var(--sev-critical-bg)", label: "Critical" },
  HIGH: { fg: "var(--sev-high)", bg: "var(--sev-high-bg)", label: "High" },
  MEDIUM: { fg: "var(--sev-medium)", bg: "var(--sev-medium-bg)", label: "Medium" },
  LOW: { fg: "var(--sev-low)", bg: "var(--sev-low-bg)", label: "Low" },
  INFO: { fg: "var(--sev-info)", bg: "var(--sev-info-bg)", label: "Info" },
};

export const AGENT_META = {
  security_agent: { label: "Security", short: "SEC" },
  architecture_agent: { label: "Architecture", short: "ARCH" },
  test_coverage_agent: { label: "Test coverage", short: "TEST" },
  documentation_agent: { label: "Documentation", short: "DOCS" },
};

export const JOB_STATUS_META = {
  pending: { fg: "var(--attention)", bg: "var(--attention-subtle)", label: "Pending" },
  running: { fg: "var(--accent)", bg: "var(--accent-subtle)", label: "Running" },
  complete: { fg: "var(--success)", bg: "var(--success-subtle)", label: "Complete" },
  failed: { fg: "var(--danger)", bg: "var(--danger-subtle)", label: "Failed" },
};

export function shortSha(sha) {
  return sha ? sha.slice(0, 7) : "";
}

export function formatTimestamp(unixSeconds) {
  if (!unixSeconds) return "";
  const d = new Date(unixSeconds * 1000);
  const now = new Date();
  const diffMs = now - d;
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}