import { useEffect, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const WS_BASE = API_BASE.replace(/^http/, "ws");
const RECONNECT_DELAY_MS = 3000;

/**
 * Subscribes to /ws/reviews?repo=... and forwards every parsed event to
 * onEvent. One connection per repo, matching how the backend's
 * ConnectionManager is scoped. Reconnects automatically on drop.
 */
export function useReviewSocket(repo, onEvent) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    let cancelled = false;

    function connect() {
      if (cancelled || !repo) return;
      const url = `${WS_BASE}/ws/reviews?repo=${encodeURIComponent(repo)}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) {
          reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY_MS);
        }
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
    };
  }, [repo]);

  return { connected };
}