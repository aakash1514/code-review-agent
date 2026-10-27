const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const SESSION_KEY = "session_token";

async function request(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${body ? ` — ${body}` : ""}`);
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

export async function approveReview(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  const token = getStoredSessionToken();
  const res = await fetch(`${API_BASE}/api/reviews/${commitSha}/approve?${params.toString()}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  return res.json();
}

export async function blockReview(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  const token = getStoredSessionToken();
  const res = await fetch(`${API_BASE}/api/reviews/${commitSha}/block?${params.toString()}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  return res.json();
}

export async function clearOverride(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  const token = getStoredSessionToken();
  const res = await fetch(`${API_BASE}/api/reviews/${commitSha}/clear-override?${params.toString()}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  return res.json();
}