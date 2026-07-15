const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${body ? ` — ${body}` : ""}`);
  }
  return res.json();
}

export function fetchReviews(repo, limit = 20) {
  const params = new URLSearchParams({ repo, limit: String(limit) });
  return request(`/api/reviews?${params.toString()}`);
}

export function fetchReview(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  return request(`/api/reviews/${commitSha}?${params.toString()}`);
}

export async function approveReview(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  const res = await fetch(`${API_BASE}/api/reviews/${commitSha}/approve?${params.toString()}`, {
    method: "POST",
  });
  return res.json();
}

export async function blockReview(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  const res = await fetch(`${API_BASE}/api/reviews/${commitSha}/block?${params.toString()}`, {
    method: "POST",
  });
  return res.json();
}

export function fetchReviewFiles(repo, commitSha) {
  const params = new URLSearchParams({ repo });
  return request(`/api/reviews/${commitSha}/files?${params.toString()}`);
}

export function fetchGraph(repo) {
  const params = new URLSearchParams({ repo });
  return request(`/api/graph?${params.toString()}`);
}