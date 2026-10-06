/**
 * Global fetch interceptor for backend calls.
 *
 * Two problems this solves, both of which would otherwise have to be fixed
 * in all 13 files that call fetch() today and remembered in every new one:
 *
 * 1. COOKIE SESSION. The branded login (POST /api/auth/login) sets an
 *    httpOnly cookie. Cross-origin fetch drops cookies unless the request
 *    sets `credentials: "include"`, so without this every authenticated
 *    call would arrive anonymous.
 *
 * 2. SILENT 401. Hooks like useAnalytics catch every error and return a
 *    zeroed fallback, so an unauthenticated dashboard renders "0 events"
 *    instead of failing. That is strictly worse than an error: it looks
 *    like a quiet night. We intercept 401/403 before the caller's own
 *    catch block sees it and raise a session-expired signal that
 *    AuthContext listens for.
 *
 * Installed once from main.tsx, before React renders.
 */
import { API_BASE } from "./api-base";

/** Fired when the backend rejects our credentials. AuthContext listens. */
export const AUTH_FAILED_EVENT = "ww:auth-failed";

/** Endpoints that are expected to 401 and must NOT trigger a global logout. */
const AUTH_ENDPOINTS = ["/api/auth/login", "/api/auth/refresh", "/api/auth/me"];

let installed = false;
let refreshInFlight: Promise<boolean> | null = null;

function isApiRequest(url: string): boolean {
  return url.startsWith(API_BASE) || url.startsWith("/api/");
}

function isAuthEndpoint(url: string): boolean {
  return AUTH_ENDPOINTS.some((p) => url.includes(p));
}

/** Try one silent refresh. Concurrent callers share the same attempt. */
async function tryRefresh(originalFetch: typeof fetch): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = originalFetch(`${API_BASE}/api/auth/refresh`, {
      method: "POST",
      credentials: "include",
    })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        // Clear on the next tick so callers awaiting this promise all see
        // the same result before a fresh attempt becomes possible.
        setTimeout(() => {
          refreshInFlight = null;
        }, 0);
      });
  }
  return refreshInFlight;
}

export function installApiClient(): void {
  if (installed) return;
  installed = true;

  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;

    if (!isApiRequest(url)) {
      return originalFetch(input, init);
    }

    const withCreds: RequestInit = { ...init, credentials: "include" };
    let response = await originalFetch(input, withCreds);

    if (response.status === 401 && !isAuthEndpoint(url)) {
      // Access token probably just aged out. Attempt one silent refresh and
      // replay the request before giving up on the session.
      const refreshed = await tryRefresh(originalFetch);
      if (refreshed) {
        response = await originalFetch(input, withCreds);
      }
      if (response.status === 401) {
        window.dispatchEvent(
          new CustomEvent(AUTH_FAILED_EVENT, { detail: { url, status: 401 } }),
        );
      }
    } else if (response.status === 403 && !isAuthEndpoint(url)) {
      // Authenticated but not authorised. Not a session problem -- let the
      // caller handle it, but make it visible rather than silently falling
      // back to empty data.
      console.warn(`[api] 403 Forbidden: ${url} -- role insufficient`);
    }

    return response;
  };
}

/** Fetch the short-lived access token for WebSocket subprotocol auth.
 *
 * Browsers cannot set headers on `new WebSocket(...)` and will not reliably
 * attach cookies cross-origin, so the token has to be passed as a
 * subprotocol. This is the only path by which a token reaches JavaScript;
 * it is used immediately and never stored.
 */
export async function getWsToken(): Promise<string | null> {
  try {
    const resp = await fetch(`${API_BASE}/api/auth/ws-token`, { method: "GET" });
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.token ?? null;
  } catch {
    return null;
  }
}

/** Open an authenticated WebSocket.
 *
 * The token is passed as the second subprotocol entry; the backend reads it
 * from `Sec-WebSocket-Protocol`, validates it BEFORE accept(), and echoes
 * "bearer" back (see backend/app/auth/ws_auth.py). A query parameter would
 * work too but would write a live bearer token into every proxy access log.
 *
 * Returns null when no session token is available, so callers can skip
 * connecting rather than opening a socket that will be closed with 1008.
 */
export async function openAuthedWebSocket(url: string): Promise<WebSocket | null> {
  const token = await getWsToken();
  if (!token) return null;
  return new WebSocket(url, ["bearer", token]);
}
