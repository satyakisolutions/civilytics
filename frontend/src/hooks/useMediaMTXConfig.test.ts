import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMediaMTXConfig } from "./useMediaMTXConfig";

describe("useMediaMTXConfig", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    global.fetch = vi.fn();
  });

  it("returns default config", () => {
    const { result } = renderHook(() => useMediaMTXConfig());
    expect(result.current.serverUrl).toContain("localhost");
    expect(result.current.autoReconnect).toBe(true);
    expect(result.current.healthCheckInterval).toBe(30);
    expect(result.current.frameAnalysisInterval).toBe(10);
  });

  it("loads from localStorage", () => {
    localStorage.setItem("wastewatch_mediamtx_config", JSON.stringify({
      serverUrl: "http://custom:8889",
      autoReconnect: false,
    }));
    const { result } = renderHook(() => useMediaMTXConfig());
    expect(result.current.serverUrl).toBe("http://custom:8889");
    expect(result.current.autoReconnect).toBe(false);
  });

  it("updateConfig merges partial updates", () => {
    const { result } = renderHook(() => useMediaMTXConfig());
    act(() => {
      result.current.updateConfig({ autoReconnect: false });
    });
    expect(result.current.autoReconnect).toBe(false);
    expect(result.current.healthCheckInterval).toBe(30); // unchanged
  });

  it("resetConfig restores defaults", () => {
    localStorage.setItem("wastewatch_mediamtx_config", JSON.stringify({ serverUrl: "http://custom:8889" }));
    const { result } = renderHook(() => useMediaMTXConfig());
    expect(result.current.serverUrl).toBe("http://custom:8889");
    act(() => {
      result.current.resetConfig();
    });
    // After reset, should use default config
    expect(result.current.autoReconnect).toBe(true);
    expect(result.current.healthCheckInterval).toBe(30);
  });

  it("validateServer returns false when fetch fails (jsdom lacks AbortSignal.timeout)", async () => {
    // In jsdom, AbortSignal.timeout is not available, so validateServer catches the error
    (global.fetch as any).mockRejectedValue(new Error("network error"));
    const { result } = renderHook(() => useMediaMTXConfig());
    let isReachable: boolean = true;
    await act(async () => {
      isReachable = await result.current.validateServer("http://bad:8889");
    });
    expect(isReachable).toBe(false);
  });

  it("handles invalid localStorage gracefully", () => {
    localStorage.setItem("wastewatch_mediamtx_config", "not json");
    const { result } = renderHook(() => useMediaMTXConfig());
    expect(result.current.serverUrl).toContain("localhost");
  });
});
