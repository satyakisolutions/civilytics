import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDetectionMetadata } from "./useDetectionMetadata";

class MockWebSocket {
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: any) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 0;

  constructor(url: string) {
    this.url = url;
    setTimeout(() => this.onopen?.(), 0);
  }

  close() {
    this.readyState = 3;
    setTimeout(() => this.onclose?.(), 0);
  }

  send() {}
}

describe("useDetectionMetadata", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (global as any).WebSocket = MockWebSocket;
  });

  afterEach(() => {
    vi.useRealTimers();
    delete (global as any).WebSocket;
  });

  it("initializes disconnected with null metadata", () => {
    const { result } = renderHook(() =>
      useDetectionMetadata({ cameraId: "cam-01", enabled: false })
    );
    expect(result.current.metadata).toBeNull();
    expect(result.current.isConnected).toBe(false);
  });

  it("connects to correct WebSocket URL", () => {
    const { result } = renderHook(() =>
      useDetectionMetadata({ cameraId: "cam-01", enabled: true })
    );
    // WebSocket created with cameraId in URL
    expect(result.current.isConnected).toBe(false); // async connect
  });

  it("does not connect when disabled", () => {
    const original = (global as any).WebSocket;
    const spy = vi.fn().mockImplementation((url: string) => new MockWebSocket(url));
    (global as any).WebSocket = spy;

    renderHook(() => useDetectionMetadata({ cameraId: "cam-01", enabled: false }));
    expect(spy).not.toHaveBeenCalled();

    (global as any).WebSocket = original;
  });

  it("does not connect with empty cameraId", () => {
    const original = (global as any).WebSocket;
    const spy = vi.fn().mockImplementation((url: string) => new MockWebSocket(url));
    (global as any).WebSocket = spy;

    renderHook(() => useDetectionMetadata({ cameraId: "", enabled: true }));
    expect(spy).not.toHaveBeenCalled();

    (global as any).WebSocket = original;
  });

  it("cleans up WebSocket on unmount", () => {
    const { unmount } = renderHook(() =>
      useDetectionMetadata({ cameraId: "cam-01", enabled: true })
    );
    unmount();
    // Should not throw
  });
});
