const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const SESSION_KEY = "session_token";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// Any 401 means the stored session is gone or expired: drop it and tell the
// app (App.jsx subscribes) so it can swap the page for the sign-in prompt.
const authRequiredListeners = new Set();

export function onAuthRequired(listener) {
  authRequiredListeners.add(listener);
  return () => authRequiredListeners.delete(listener);
}

function handleUnauthorized(res) {
  if (res.status !== 401) return;
  setStoredSessionToken(null);
  authRequiredListeners.forEach((listener) => listener());
}

function authHeaders() {
  const token = getStoredSessionToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers: authHeaders() });
  if (!res.ok) {
    handleUnauthorized(res);
    const body = await res.text().catch(() => "");
    let detail = body;
    try {
      detail = JSON.parse(body).detail ?? body;
    } catch {
      // not JSON — keep the raw text
    }
    throw new ApiError(res.status, `${res.status} ${res.statusText}${detail ? ` — ${detail}` : ""}`);
  }
  return res.json();
}

export function getStoredSessionToken() {
  return localStorage.getItem(SESSION_KEY);
}

export function setStoredSessionToken(token) {
  if (token) localStorage.setItem(SESSION_KEY, token);
  else localStorage.removeItem(SESSION_KEY);
}

export function loginUrl() {
  return `${API_BASE}/auth/login`;
}

// The OAuth callback redirects back with a one-time ?code=; trade it for a
// session token and store that. Codes are single-use, so concurrent calls for
// the same code (React StrictMode runs mount effects twice in dev) share one
// request instead of the second one spending nothing and failing.
const pendingExchanges = new Map();

export function exchangeHandoffCode(code) {
  if (!pendingExchanges.has(code)) {
    pendingExchanges.set(code, (async () => {
      const res = await fetch(`${API_BASE}/auth/exchange`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      if (!res.ok) return false;
      const { session_token: token } = await res.json();
      setStoredSessionToken(token);
      return true;
    })());
  }
  return pendingExchanges.get(code);
}

export async function fetchMe() {
  const token = getStoredSessionToken();
  if (!token) return null;
  const res = await fetch(`${API_BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    setStoredSessionToken(null);
    return null;
  }
  return res.json();
}

export async function logout() {
  const token = getStoredSessionToken();
  if (token) {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    }).catch(() => {});
  }
  setStoredSessionToken(null);
}

export function fetchReviews(repo, limit = 20) {
  const params = new URLSearchParams({ repo, limit: String(limit) });
  return request(`/api/reviews?${params.toString()}`);
}

export function fetchReview(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  return request(`/api/reviews/${commitSha}?${params.toString()}`);
}

export function fetchReviewFiles(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  return request(`/api/reviews/${commitSha}/files?${params.toString()}`);
}

export function fetchGraph(repo) {
  const params = new URLSearchParams({ repo });
  return request(`/api/graph?${params.toString()}`);
}

export function fetchWsTicket(repo) {
  const params = new URLSearchParams({ repo });
  return request(`/api/ws-ticket?${params.toString()}`, { method: "POST" });
}

export async function approveReview(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  const res = await fetch(`${API_BASE}/api/reviews/${commitSha}/approve?${params.toString()}`, {
    method: "POST",
    headers: authHeaders(),
  });
  handleUnauthorized(res);
  return res.json();
}

export async function blockReview(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  const res = await fetch(`${API_BASE}/api/reviews/${commitSha}/block?${params.toString()}`, {
    method: "POST",
    headers: authHeaders(),
  });
  handleUnauthorized(res);
  return res.json();
}

export async function clearOverride(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  const res = await fetch(`${API_BASE}/api/reviews/${commitSha}/clear-override?${params.toString()}`, {
    method: "POST",
    headers: authHeaders(),
  });
  handleUnauthorized(res);
  return res.json();
}