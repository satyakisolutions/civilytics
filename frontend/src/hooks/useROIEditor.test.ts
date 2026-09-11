import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useROIEditor } from "./useROIEditor";

describe("useROIEditor", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn();
  });

  it("initializes with empty state", () => {
    const { result } = renderHook(() => useROIEditor({ cameraId: "cam-01" }));
    expect(result.current.polygons).toEqual([]);
    expect(result.current.isEditing).toBe(false);
    expect(result.current.currentPoints).toEqual([]);
  });

  it("addPoint adds to currentPoints", () => {
    const { result } = renderHook(() => useROIEditor({ cameraId: "cam-01" }));
    act(() => {
      result.current.addPoint({ x: 0.1, y: 0.2 });
    });
    expect(result.current.currentPoints).toHaveLength(1);
    act(() => {
      result.current.addPoint({ x: 0.3, y: 0.4 });
    });
    expect(result.current.currentPoints).toHaveLength(2);
  });

  it("finishPolygon requires 3+ points", () => {
    const { result } = renderHook(() => useROIEditor({ cameraId: "cam-01" }));
    act(() => {
      result.current.addPoint({ x: 0.1, y: 0.2 });
      result.current.addPoint({ x: 0.3, y: 0.4 });
    });
    act(() => {
      result.current.finishPolygon("test-roi");
    });
    // Should NOT create polygon with only 2 points
    expect(result.current.polygons).toHaveLength(0);
  });

  it("finishPolygon creates polygon with 3+ points", () => {
    (global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ polygons: [{ name: "roi", points: [{ x: 0.1, y: 0.2 }, { x: 0.3, y: 0.4 }, { x: 0.5, y: 0.6 }] }] }) });
    const { result } = renderHook(() => useROIEditor({ cameraId: "cam-01" }));
    act(() => {
      result.current.addPoint({ x: 0.1, y: 0.2 });
      result.current.addPoint({ x: 0.3, y: 0.4 });
      result.current.addPoint({ x: 0.5, y: 0.6 });
    });
    act(() => {
      result.current.finishPolygon("roi");
    });
    expect(result.current.polygons).toHaveLength(1);
    expect(result.current.currentPoints).toHaveLength(0);
  });

  it("cancelEditing clears points", () => {
    const { result } = renderHook(() => useROIEditor({ cameraId: "cam-01" }));
    act(() => {
      result.current.addPoint({ x: 0.1, y: 0.2 });
      result.current.setIsEditing(true);
    });
    act(() => {
      result.current.cancelEditing();
    });
    expect(result.current.currentPoints).toHaveLength(0);
    expect(result.current.isEditing).toBe(false);
  });

  it("fetchPolygons calls API", async () => {
    const mockPolygons = [{ name: "roi1", points: [{ x: 0.1, y: 0.2 }] }];
    (global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ polygons: mockPolygons }) });
    const { result } = renderHook(() => useROIEditor({ cameraId: "cam-01" }));
    await act(async () => {
      await result.current.fetchPolygons();
    });
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining("/api/cameras/cam-01/roi"));
  });

  it("removePolygon removes by index", () => {
    (global.fetch as any).mockResolvedValue({ ok: true, json: async () => ({ polygons: [] }) });
    const { result } = renderHook(() => useROIEditor({ cameraId: "cam-01" }));

    // Add 3 points and finish a polygon first
    act(() => {
      result.current.addPoint({ x: 0.1, y: 0.1 });
      result.current.addPoint({ x: 0.5, y: 0.1 });
      result.current.addPoint({ x: 0.5, y: 0.5 });
    });
    act(() => {
      result.current.finishPolygon("roi1");
    });
    expect(result.current.polygons.length).toBeGreaterThanOrEqual(1);

    act(() => {
      result.current.removePolygon(0);
    });
    expect(result.current.polygons).toHaveLength(0);
  });
});
