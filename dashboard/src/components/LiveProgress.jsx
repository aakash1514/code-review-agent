import { AGENT_META } from "../constants";

function stageLine(event) {
  switch (event.stage) {
    case "running":
      return { text: "Pipeline started", tone: "muted" };
    case "fetching_files":
      return { text: "Fetching changed files", tone: "muted" };
    case "file_started":
      return { text: `Reviewing ${event.file}`, tone: "muted" };
    case "agent_complete": {
      const label = AGENT_META[event.agent_name]?.label ?? event.agent_name;
      if (event.error) {
        return { text: `${label} failed — ${event.error}`, tone: "danger" };
      }
      return { text: `${label} finished — ${event.finding_count} finding(s)`, tone: "default" };
    }
    case "synthesizing":
      return {
        text: `Synthesizing report — ${event.critical_count} critical, ${event.high_count} high`,
        tone: "muted",
      };
    case "complete":
      return {
        text: event.should_block ? "Posted to GitHub — merge blocked" : "Posted to GitHub — clean",
        tone: event.should_block ? "danger" : "success",
      };
    case "failed":
      return { text: `Failed — ${event.error ?? "unknown error"}`, tone: "danger" };
    default:
      return { text: event.stage, tone: "muted" };
  }
}

const TONE_COLOR = {
  default: "var(--fg-default)",
  muted: "var(--fg-muted)",
  success: "var(--success)",
  danger: "var(--danger)",
};

export default function LiveProgress({ events, isLive }) {
  if (!events || events.length === 0) return null;

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        background: "var(--canvas)",
        padding: "10px 14px",
        marginBottom: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: isLive ? "var(--success)" : "var(--fg-subtle)",
            flexShrink: 0,
          }}
        />
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--fg-muted)", textTransform: "uppercase", letterSpacing: "0.03em" }}>
          {isLive ? "Live progress" : "Progress log"}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {events.map((event, i) => {
          const { text, tone } = stageLine(event);
          return (
            <div key={i} style={{ fontSize: 13, color: TONE_COLOR[tone] ?? TONE_COLOR.default }}>
              {text}
            </div>
          );
        })}
      </div>
    </div>
  );
}