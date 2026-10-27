import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fetchWsTicket } from "../api";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const WS_BASE = API_BASE.replace(/^http/, "ws");
const RECONNECT_DELAY_MS = 3000;

/**
 * Subscribes to /ws/reviews?repo=...&ticket=... and forwards every parsed
 * event to onEvent. One connection per repo, matching how the backend's
 * ConnectionManager is scoped. Tickets are single-use, so every connect and
 * reconnect fetches a fresh one first. Reconnects automatically on drop,
 * except when the ticket request itself is refused (401/403): retrying can't
 * fix that, so it stops and reports the status via onAuthError instead.
 */
export function useReviewSocket(repo, onEvent, { enabled = true, onAuthError } = {}) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const onEventRef = useRef(onEvent);
  const onAuthErrorRef = useRef(onAuthError);

  // Keep the latest handlers without reconnecting; refs can't be written during render.
  useLayoutEffect(() => {
    onEventRef.current = onEvent;
    onAuthErrorRef.current = onAuthError;
  });

  useEffect(() => {
    if (!enabled || !repo) return;
    let cancelled = false;

    function scheduleReconnect() {
      if (!cancelled) {
        reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY_MS);
      }
    }

    async function connect() {
      if (cancelled) return;
      let ticket;
      try {
        ({ ticket } = await fetchWsTicket(repo));
      } catch (e) {
        if (cancelled) return;
        if (e.status === 401 || e.status === 403) {
          onAuthErrorRef.current?.(e.status);
        } else {
          scheduleReconnect(); // backend down or restarting — keep trying
        }
        return;
      }
      if (cancelled) return;

      const url = `${WS_BASE}/ws/reviews?repo=${encodeURIComponent(repo)}&ticket=${encodeURIComponent(ticket)}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        scheduleReconnect();
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (msg) => {
        try {
          const event = JSON.parse(msg.data);
          onEventRef.current?.(event);
        } catch {
          // ignore malformed frames
        }
      };
    }

    connect();
    return () => {
      cancelled = true;
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
      wsRef.current = null;
      setConnected(false);
    };
  }, [repo, enabled]);

  return { connected: enabled && connected };
}
