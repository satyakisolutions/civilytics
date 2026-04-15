/**
 * Centralised resolver for the LSG-WasteWatch backend + WebSocket +
 * MediaMTX endpoints. We cannot hard-code the hostname because the
 * dashboard is served both at:
 *
 *   http://localhost:5174          (developer laptop)
 *   http://192.168.1.8:5174        (LAN bring-up / demo box)
 *   http://<ops-workstation>:5174  (future deployments)
 *
 * and each of those needs the API calls, WebSocket subscription and
 * the MediaMTX WebRTC URL to point back at the same host the browser
 * loaded the app from — otherwise the browser sends requests to
 * localhost on the operator's own machine and gets ECONNREFUSED.
 *
 * Resolution order, highest priority first:
 *   1. Vite build-time env (VITE_API_URL / VITE_WS_URL / VITE_MEDIAMTX_URL)
 *      — lets us override via docker-compose or a .env file.
 *   2. `window.location.hostname` + the well-known LSG port number
 *      for each service (8082 for API, 8889 for MediaMTX, etc.).
 *
 * This is safer than a bare `import.meta.env.VITE_API_URL ?? "http://localhost:8080"`
 * fallback because the hard-coded fallback is wrong on *any* non-local
 * box and isn't updated when the compose override remaps ports.
 */

/** Well-known ports for the LSG stack, matching docker-compose.yml. */
export const LSG_PORTS = {
  api: 8082,       // FastAPI backend (remapped from 8080 to avoid a host conflict)
  ws: 8082,        // WebSocket is served off the same API port
  mediamtxWeb: 8889, // MediaMTX WebRTC (WHEP) read port
  mediamtxRtsp: 8554, // MediaMTX RTSP read port
} as const;

function hostFromBrowser(): string {
  // Vite runs this module client-side; there is always a window.location.
  // Fall back to "localhost" during SSR (shouldn't happen for a pure SPA,
  // but keeps tests happy).
  if (typeof window !== "undefined" && window.location) {
    return window.location.hostname || "localhost";
  }
  return "localhost";
}

function protocolFromBrowser(secure: boolean): string {
  if (typeof window !== "undefined" && window.location) {
    if (secure) return window.location.protocol === "https:" ? "wss:" : "ws:";
    return window.location.protocol || "http:";
  }
  return secure ? "ws:" : "http:";
}

/** Backend REST API base URL, e.g. "http://localhost:8082". */
export function getApiBase(): string {
  const fromEnv = (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_API_URL;
  if (fromEnv) return fromEnv.replace(/\/+$/, "");
  return `${protocolFromBrowser(false)}//${hostFromBrowser()}:${LSG_PORTS.api}`;
}

/** Backend WebSocket base URL, e.g. "ws://localhost:8082/ws". */
export function getWsBase(): string {
  const fromEnv = (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_WS_URL;
  if (fromEnv) return fromEnv.replace(/\/+$/, "");
  return `${protocolFromBrowser(true)}//${hostFromBrowser()}:${LSG_PORTS.ws}/ws`;
}

/** MediaMTX HTTP base for WebRTC (WHEP) reads, e.g. "http://localhost:8889". */
export function getMediaMtxBase(): string {
  const fromEnv = (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_MEDIAMTX_URL;
  if (fromEnv) return fromEnv.replace(/\/+$/, "");
  return `${protocolFromBrowser(false)}//${hostFromBrowser()}:${LSG_PORTS.mediamtxWeb}`;
}

/** Evaluated once at module load so the app's first API calls don't
 * re-run the resolver on every render. */
export const API_BASE = getApiBase();
export const WS_BASE = getWsBase();
export const MEDIAMTX_BASE = getMediaMtxBase();
