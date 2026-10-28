// Shown in place of the review/graph views when the API won't serve this
// repo: either nobody is signed in (401) or the signed-in GitHub user has
// no read access to it (403).
export default function SignInPrompt({ reason, repo, login, onLogin }) {
  const forbidden = reason === "forbidden";
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background: "var(--canvas-subtle)",
      }}
    >
      <div
        style={{
          maxWidth: 420,
          textAlign: "center",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          background: "var(--canvas)",
          padding: "28px 24px",
        }}
      >
        <div style={{ fontSize: 16, fontWeight: 600, color: "var(--fg-default)", marginBottom: 8 }}>
          {forbidden ? "No access to this repository" : "Sign in to view reviews"}
        </div>
        <div style={{ fontSize: 13, color: "var(--fg-muted)", marginBottom: 20, lineHeight: 1.5 }}>
          {forbidden ? (
            <>
              <strong>@{login}</strong> doesn't have read access to <code>{repo}</code> on GitHub. Pick another
              repository above. To use a different GitHub account, sign out here and on github.com first.
            </>
          ) : (
            <>Reviews, diffs and the call graph are only visible to GitHub users who can read the repository.</>
          )}
        </div>
        <button
          onClick={onLogin}
          style={{
            border: "1px solid var(--border)",
            background: forbidden ? "var(--canvas-subtle)" : "var(--accent)",
            color: forbidden ? "var(--fg-default)" : "var(--fg-on-emphasis)",
            borderRadius: "var(--radius)",
            padding: "7px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          {forbidden ? "Sign in again" : "Sign in with GitHub"}
        </button>
      </div>
    </div>
  );
}
