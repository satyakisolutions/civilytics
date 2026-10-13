import { describe, it, expect, afterEach } from "vitest";
import { getApiBase, getWsBase, getMediaMtxBase, LSG_PORTS } from "./api-base";

/**
 * Same-origin / dev-port resolution tests for the API + WS base resolver.
 *
 * IMPORTANT constraint: Vite inlines `import.meta.env.VITE_*` as frozen
 * literals at transform time, so the VALUES the module reads are fixed by the
 * build env and cannot be re-stubbed per test (vi.stubEnv does not reach the
 * already-transformed source module). In this repo's committed env:
 *   - VITE_API_URL is UNSET  -> getApiBase always falls through to the
 *     window.location logic, which is exactly what we exercise below.
 *   - VITE_WS_URL  is SET    -> getWsBase always returns that absolute value
 *     (the "env wins" branch), which we assert dominates over location.
 * We therefore drive every reachable branch by stubbing window.location only.
 */
function setLocation(opts: {
  protocol?: string;
  hostname?: string;
  port?: string;
  host?: string;
}) {
  const protocol = opts.protocol ?? "http:";
  const hostname = opts.hostname ?? "localhost";
  const port = opts.port ?? "";
  const host = opts.host ?? (port ? `${hostname}:${port}` : hostname);
  Object.defineProperty(window, "location", {
    value: { protocol, hostname, port, host },
    writable: true,
    configurable: true,
  });
}

const originalLocation = window.location;
afterEach(() => {
  Object.defineProperty(window, "location", {
    value: originalLocation,
    writable: true,
    configurable: true,
  });
});

describe("getApiBase (VITE_API_URL unset -> location-based resolution)", () => {
  it("served behind proxy on port 443 -> same-origin empty base", () => {
    setLocation({ protocol: "https:", hostname: "demo.example.com", port: "443" });
    expect(getApiBase()).toBe("");
  });

  it("served behind proxy on port 80 -> same-origin empty base", () => {
    setLocation({ protocol: "http:", hostname: "192.168.1.8", port: "80" });
    expect(getApiBase()).toBe("");
  });

  it("served behind proxy on empty port (e.g. tunnel) -> same-origin empty base", () => {
    setLocation({ protocol: "https:", hostname: "tunnel.ngrok.io", port: "" });
    expect(getApiBase()).toBe("");
  });

  it("dev server on port 5174 -> API on the well-known port 8082, same host", () => {
    setLocation({ protocol: "http:", hostname: "localhost", port: "5174" });
    expect(getApiBase()).toBe(`http://localhost:${LSG_PORTS.api}`);
  });

  it("dev server on a LAN IP -> API on same host, well-known port", () => {
    setLocation({ protocol: "http:", hostname: "192.168.1.8", port: "5174" });
    expect(getApiBase()).toBe(`http://192.168.1.8:${LSG_PORTS.api}`);
  });

  it("LSG_PORTS.api is the documented remapped backend port (8082)", () => {
    expect(LSG_PORTS.api).toBe(8082);
  });
});

describe("getWsBase (VITE_WS_URL set in env -> absolute env value wins)", () => {
  it("absolute env value wins over a dev-port location", () => {
    setLocation({ protocol: "http:", hostname: "localhost", port: "5174" });
    // From the committed .env: ws://localhost:8080/ws, trailing slashes stripped.
    expect(getWsBase()).toBe("ws://localhost:8080/ws");
  });

  it("absolute env value wins over a behind-proxy location too (no same-origin override)", () => {
    setLocation({ protocol: "https:", hostname: "demo.example.com", port: "443" });
    expect(getWsBase()).toBe("ws://localhost:8080/ws");
  });

  it("returns a value with no trailing slash", () => {
    setLocation({ protocol: "http:", hostname: "localhost", port: "5174" });
    expect(getWsBase().endsWith("/")).toBe(false);
  });
});

describe("getMediaMtxBase (VITE_MEDIAMTX_URL unset)", () => {
  it("uses nginx's same-origin TLS route behind the production proxy", () => {
    setLocation({ protocol: "https:", hostname: "demo.example.com", port: "443", host: "demo.example.com" });
    expect(getMediaMtxBase()).toBe("https://demo.example.com/webrtc");
  });

  it("resolves to the same host on the MediaMTX WebRTC port", () => {
    setLocation({ protocol: "http:", hostname: "192.168.1.8", port: "5174" });
    expect(getMediaMtxBase()).toBe(`http://192.168.1.8:${LSG_PORTS.mediamtxWeb}`);
  });
});
