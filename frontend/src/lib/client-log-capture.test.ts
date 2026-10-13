import { describe, it, expect } from "vitest";
import {
  buildEnvelope,
  redact,
  mapConsoleLevel,
  ClientLogCapture,
  MAX_RECORDS,
  MAX_MESSAGE,
  type LogRecord,
} from "./client-log-capture";

const rec = (over: Partial<LogRecord> = {}): LogRecord => ({
  level: "error",
  message: "m",
  ts: 1,
  ...over,
});

describe("mapConsoleLevel", () => {
  it("passes known levels through, defaults the rest to log", () => {
    expect(mapConsoleLevel("error")).toBe("error");
    expect(mapConsoleLevel("warn")).toBe("warn");
    expect(mapConsoleLevel("info")).toBe("info");
    expect(mapConsoleLevel("debug")).toBe("debug");
    expect(mapConsoleLevel("trace")).toBe("log");
  });
});

describe("redact", () => {
  it("strips bearer tokens, password assignments and JWT-looking strings", () => {
    expect(redact("Authorization: Bearer abc.def-123")).toContain("Bearer [REDACTED]");
    expect(redact("login password=hunter2 ok")).toContain("password=[REDACTED]");
    expect(redact('{"api_key":"xyz"}')).toContain("[REDACTED]");
    const jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF";
    expect(redact(`t=${jwt}`)).toContain("[REDACTED_JWT]");
  });
  it("leaves ordinary text untouched", () => {
    expect(redact("just a normal error message")).toBe("just a normal error message");
  });
});

describe("buildEnvelope", () => {
  it("sets shape + transport + dropped count", () => {
    const env = buildEnvelope({
      sessionId: "sid",
      records: [rec({ level: "warn", message: "hi" })],
      dropped: 7,
      transport: "ws",
      url: "https://x/y",
      userAgent: "UA",
    });
    expect(env).toMatchObject({
      session_id: "sid",
      url: "https://x/y",
      user_agent: "UA",
      dropped: 7,
      transport: "ws",
    });
    expect(env.records).toEqual([{ level: "warn", message: "hi", ts: 1 }]);
  });

  it("caps records at MAX_RECORDS", () => {
    const records = Array.from({ length: MAX_RECORDS + 50 }, () => rec());
    const env = buildEnvelope({ sessionId: "s", records, dropped: 0, transport: "post" });
    expect(env.records.length).toBe(MAX_RECORDS);
  });

  it("truncates and redacts each message", () => {
    const env = buildEnvelope({
      sessionId: "s",
      records: [rec({ message: "Bearer secrettoken " + "a".repeat(MAX_MESSAGE) })],
      dropped: 0,
      transport: "ws",
    });
    expect(env.records[0].message.length).toBe(MAX_MESSAGE);
    expect(env.records[0].message).toContain("[REDACTED]");
  });
});

describe("dormancy (capture disabled by default)", () => {
  it("discards records and sends nothing until enabled", () => {
    const cap = new ClientLogCapture();
    expect(cap.isEnabled()).toBe(false);
    cap.push("error", "boom");
    cap.push("warn", "careful");
    expect(cap.bufferLength()).toBe(0); // discarded while dormant
  });

  it("buffers once the server enables capture, drops again when disabled", () => {
    const cap = new ClientLogCapture();
    cap.handleControl({ type: "capture", enabled: true, remaining_s: 60 });
    expect(cap.isEnabled()).toBe(true);
    cap.push("error", "boom");
    expect(cap.bufferLength()).toBe(1);
    cap.handleControl({ type: "capture", enabled: false, remaining_s: 0 });
    expect(cap.isEnabled()).toBe(false);
    expect(cap.bufferLength()).toBe(0); // dropped on going dormant
  });

  it("default captured levels are errors + warnings only", () => {
    expect(new ClientLogCapture().captured).toEqual(["warn", "error"]);
    expect(new ClientLogCapture({ verbose: true }).captured).toEqual([
      "debug",
      "info",
      "log",
      "warn",
      "error",
    ]);
  });

  it("flush is a no-op while dormant (no WebSocket/fetch needed)", async () => {
    const cap = new ClientLogCapture();
    await expect(cap.flush()).resolves.toBeUndefined();
  });
});
