export default function Badge({ fg, bg, children, dot = false }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: "1px 8px",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 500,
        color: fg,
        background: bg,
        whiteSpace: "nowrap",
      }}
    >
      {dot && (
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: fg,
            flexShrink: 0,
          }}
        />
      )}
      {children}
    </span>
  );
}