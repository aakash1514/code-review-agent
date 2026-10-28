// Where the dashboard finds the backend.
//
// Default (VITE_API_BASE_URL unset): same origin. In production CloudFront
// serves the dashboard and routes /api/*, /auth/*, /ws/* to the backend on
// the same domain; in local dev the Vite dev server proxies those same paths
// to http://localhost:8000 (vite.config.js). Either way: no CORS, and one
// URL to configure.
//
// Set VITE_API_BASE_URL (e.g. http://localhost:8000) only to call a backend
// on another origin directly.
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "");

// WebSocket URL base: ws(s):// on the API's host, or on this page's host.
export const WS_BASE = API_BASE
  ? API_BASE.replace(/^http/, "ws")
  : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;