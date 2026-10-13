/**
 * Browser-console log sender for Civilytics.
 *
 * Patches console.error/warn (and window error handlers) and ships captured
 * records to the backend ingest at /ws/client-logs, but ONLY while an admin has
 * turned capture on. Capture is OFF by default: the shim installs, patches the
 * console (always calling originals through), and otherwise sits dormant at
 * near-zero cost until the server sends {"type":"capture","enabled":true,...}.
 *
 * Transport: one BINARY gzip frame per batch over the WebSocket; a gzip POST to
 * /api/system/logs/client is the fallback while the socket is down. Logging
 * never throws into app code -- every path is wrapped and fails silent.
 */
import { API_BASE, WS_BASE } from "./api-base";
import { openAuthedWebSocket } from "./api-client";

export type Level = "debug" | "info" | "log" | "warn" | "error";

export interface LogRecord {
  level: Level;
  message: string;
  ts: number;
  stack?: string;
  url?: string;
}

export interface Envelope {
  session_id: string;
  url: string;
  user_agent: string;
  dropped: number;
  transport: "ws" | "post";
  records: LogRecord[];
}

/** Backend caps, mirrored client-side. */
export const MAX_RECORDS = 500; // records per batch
export const MAX_MESSAGE = 4000; // chars per message
const BUFFER_CAP = 200; // client-side buffer ceiling; beyond this we count `dropped`
const FLUSH_SIZE = 50; // flush early once this many are buffered
const FLUSH_MS = 2000; // otherwise flush on this timer
const RECONNECT_MS = 2000;
const MAX_RECONNECT_MS = 20_000;

/** Map a patched console method name to a record level. */
export function mapConsoleLevel(method: string): Level {
  switch (method) {
    case "error":
    case "warn":
    case "info":
    case "debug":
      return method;
    default:
      return "log";
  }
}

/** Strip obvious secrets before sending (defense in depth; backend redacts too). */
export function redact(s: string): string {
  return s
    .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [REDACTED]")
    .replace(/(password|passwd|pwd|secret|token|api[_-]?key)(["'\s]*[:=]\s*)\S+/gi, "$1$2[REDACTED]")
    .replace(/\beyJ[A-Za-z0-9._-]{20,}/g, "[REDACTED_JWT]");
}

/**
 * Build the gzip-ready envelope: redact + truncate each message, cap record
 * count, carry the dropped tally. Pure; exported for tests.
 */
export function buildEnvelope(opts: {
  sessionId: string;
  records: LogRecord[];
  dropped: number;
  transport: "ws" | "post";
  url?: string;
  userAgent?: string;
}): Envelope {
  const records = opts.records.slice(0, MAX_RECORDS).map((r) => ({
    ...r,
    message: redact(String(r.message ?? "")).slice(0, MAX_MESSAGE),
  }));
  return {
    session_id: opts.sessionId,
    url: opts.url ?? (typeof location !== "undefined" ? location.href : ""),
    user_agent: opts.userAgent ?? (typeof navigator !== "undefined" ? navigator.userAgent : ""),
    dropped: opts.dropped,
    transport: opts.transport,
    records,
  };
}

async function gzip(text: string): Promise<Uint8Array> {
  const cs = new CompressionStream("gzip");
  const stream = new Blob([text]).stream().pipeThrough(cs);
  const buf = await new Response(stream).arrayBuffer();
  return new Uint8Array(buf);
}

export interface CaptureOptions {
  /** Also capture console.info/log/debug. Default false -- errors + warnings only. */
  verbose?: boolean;
}

export class ClientLogCapture {
  private buffer: LogRecord[] = [];
  private dropped = 0;
  private enabled = false; // server-controlled capture switch
  private ws: WebSocket | null = null;
  private flushTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private backoffUntil = 0; // rate-limit back-off (ms epoch)

  constructor(private readonly opts: CaptureOptions = {}) {}

  /** Levels captured by default: errors + warnings, plus info/log/debug when verbose. */
  get captured(): Level[] {
    return this.opts.verbose ? ["debug", "info", "log", "warn", "error"] : ["warn", "error"];
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  bufferLength(): number {
    return this.buffer.length;
  }

  /** Add a record. Discarded while dormant; counted as `dropped` past the cap. */
  push(level: Level, message: string, stack?: string, url?: string): void {
    if (!this.enabled) return; // dormant: near-zero cost
    if (this.buffer.length >= BUFFER_CAP) {
      this.dropped++; // a console.error loop can't flood the wire
      return;
    }
    this.buffer.push({ level, message, ts: Date.now(), stack, url });
    if (this.buffer.length >= FLUSH_SIZE) void this.flush();
  }

  /** Apply a server control frame: {"type":"capture","enabled":bool,...}. */
  handleControl(frame: unknown): void {
    const f = frame as { type?: string; enabled?: boolean; ok?: boolean; retry_after_s?: number };
    if (f && f.type === "capture") {
      this.enabled = !!f.enabled;
      if (!this.enabled) this.buffer = []; // go dormant, drop what we held
    } else if (f && f.ok === false) {
      const s = Number(f.retry_after_s) || 5;
      this.backoffUntil = Date.now() + s * 1000;
    }
  }

  async flush(): Promise<void> {
    try {
      if (!this.enabled || this.buffer.length === 0) return;
      if (Date.now() < this.backoffUntil) return;

      // POST fallback only while the socket is down AND capture is on.
      const open = this.ws && this.ws.readyState === WebSocket.OPEN;

      const records = this.buffer.splice(0, MAX_RECORDS);
      const dropped = this.dropped;
      this.dropped = 0;

      const envelope = buildEnvelope({
        sessionId: this.sessionId,
        records,
        dropped,
        transport: open ? "ws" : "post",
      });
      const bytes = await gzip(JSON.stringify(envelope));

      if (open) {
        this.ws!.send(bytes);
      } else {
        await fetch(`${API_BASE}/api/system/logs/client`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: bytes,
        });
      }
    } catch {
      // Never let logging break the app.
    }
  }

  private readonly sessionId =
    typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : String(Math.random());

  // --- connection lifecycle -------------------------------------------------

  async connect(): Promise<void> {
    try {
      // Mirror useAlerts: `${WS_BASE}/alerts` -> here `${WS_BASE}/client-logs`
      // (WS_BASE already ends in /ws, server route is /ws/client-logs).
      const ws = await openAuthedWebSocket(`${WS_BASE}/client-logs`);
      if (!ws) {
        this.scheduleReconnect();
        return;
      }
      ws.binaryType = "arraybuffer";
      this.ws = ws;

      ws.onopen = () => {
        this.reconnectAttempts = 0;
      };
      ws.onmessage = (ev) => {
        try {
          if (typeof ev.data === "string") this.handleControl(JSON.parse(ev.data));
        } catch {
          /* ignore malformed */
        }
      };
      ws.onerror = () => ws.close();
      ws.onclose = () => {
        this.ws = null;
        this.scheduleReconnect();
      };
    } catch {
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    this.reconnectAttempts++;
    const delay = Math.min(RECONNECT_MS * this.reconnectAttempts, MAX_RECONNECT_MS);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.connect();
    }, delay);
  }

  start(): void {
    if (this.flushTimer) return;
    this.flushTimer = setInterval(() => void this.flush(), FLUSH_MS);
    void this.connect();
  }
}

let installed: ClientLogCapture | null = null;

/** Idempotent install: patch console + window handlers, open the control socket. */
export function installClientLogCapture(opts: CaptureOptions = {}): ClientLogCapture | null {
  if (installed) return installed;
  if (typeof window === "undefined") return null; // SSR / non-browser no-op
  const cap = new ClientLogCapture(opts);
  installed = cap;

  const wrap = (method: Level) => {
    const orig = (console as unknown as Record<string, (...a: unknown[]) => void>)[method];
    if (typeof orig !== "function") return;
    (console as unknown as Record<string, (...a: unknown[]) => void>)[method] = (...args: unknown[]) => {
      try {
        orig.apply(console, args); // always call through -- never swallow
      } finally {
        try {
          const err = args.find((a) => a instanceof Error) as Error | undefined;
          const message = args
            .map((a) => (a instanceof Error ? a.message : typeof a === "string" ? a : safeStringify(a)))
            .join(" ");
          cap.push(method, message, err?.stack);
        } catch {
          /* fail silent */
        }
      }
    };
  };

  cap.captured.forEach(wrap);

  window.addEventListener("error", (e) => {
    try {
      cap.push("error", e.message || "window.onerror", e.error?.stack, e.filename);
    } catch {
      /* fail silent */
    }
  });
  window.addEventListener("unhandledrejection", (e) => {
    try {
      const r = (e as PromiseRejectionEvent).reason;
      cap.push("error", r instanceof Error ? r.message : safeStringify(r), r instanceof Error ? r.stack : undefined);
    } catch {
      /* fail silent */
    }
  });

  cap.start();
  return cap;
}

function safeStringify(v: unknown): string {
  try {
    return typeof v === "string" ? v : JSON.stringify(v);
  } catch {
    return String(v);
  }
}
