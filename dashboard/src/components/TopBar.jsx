export default function TopBar({ repo, repoInput, onRepoInputChange, onRepoSubmit, autoRefresh, onToggleAutoRefresh, wsConnected }) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 16px",
        borderBottom: "1px solid var(--border)",
        background: "var(--canvas)",
      }}
    >
      <div style={{ fontWeight: 700, fontSize: 14, whiteSpace: "nowrap" }}>AI Code Review</div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          onRepoSubmit();
        }}
        style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, maxWidth: 420 }}
      >
        <input
          value={repoInput}
          onChange={(e) => onRepoInputChange(e.target.value)}
          placeholder="owner/repo"
          className="mono"
          style={{
            flex: 1,
            padding: "6px 10px",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            fontSize: 13,
          }}
        />
        <button
          type="submit"
          style={{
            border: "1px solid var(--border)",
            background: "var(--canvas-subtle)",
            borderRadius: "var(--radius)",
            padding: "6px 12px",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Load
        </button>
      </form>

      <span
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12,
          color: "var(--fg-muted)",
          marginLeft: "auto",
        }}
      >
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: wsConnected ? "var(--success)" : "var(--fg-subtle)",
          }}
        />
        {wsConnected ? "Live" : "Reconnecting…"}
      </span>

      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--fg-muted)" }}>
        <input type="checkbox" checked={autoRefresh} onChange={onToggleAutoRefresh} />
        Auto-refresh
      </label>

      <span className="mono" style={{ fontSize: 12, color: "var(--fg-subtle)" }}>{repo}</span>
    </header>
  );
}