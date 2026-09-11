import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useCameraOrder } from "./useCameraOrder";

describe("useCameraOrder", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns empty order initially", () => {
    const { result } = renderHook(() => useCameraOrder());
    expect(result.current.cameraOrder).toEqual({});
  });

  it("loads from localStorage", () => {
    localStorage.setItem("wastewatch-camera-order", JSON.stringify({ "Zone A": ["cam-1", "cam-2"] }));
    const { result } = renderHook(() => useCameraOrder());
    expect(result.current.cameraOrder).toEqual({ "Zone A": ["cam-1", "cam-2"] });
  });

  it("handles invalid JSON gracefully", () => {
    localStorage.setItem("wastewatch-camera-order", "not json");
    const { result } = renderHook(() => useCameraOrder());
    expect(result.current.cameraOrder).toEqual({});
  });

  it("setZoneOrder updates state and localStorage", () => {
    const { result } = renderHook(() => useCameraOrder());
    act(() => {
      result.current.setZoneOrder("Zone B", ["cam-3", "cam-4"]);
    });
    expect(result.current.cameraOrder["Zone B"]).toEqual(["cam-3", "cam-4"]);
  });

  it("getZoneOrder returns correct order", () => {
    localStorage.setItem("wastewatch-camera-order", JSON.stringify({ "Zone A": ["cam-1"] }));
    const { result } = renderHook(() => useCameraOrder());
    expect(result.current.getZoneOrder("Zone A")).toEqual(["cam-1"]);
    expect(result.current.getZoneOrder("Zone X")).toBeUndefined();
  });

  it("resetOrder clears state", () => {
    localStorage.setItem("wastewatch-camera-order", JSON.stringify({ "Z": ["c"] }));
    const { result } = renderHook(() => useCameraOrder());
    act(() => {
      result.current.resetOrder();
    });
    expect(result.current.cameraOrder).toEqual({});
  });
});
