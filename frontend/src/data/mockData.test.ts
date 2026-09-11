import { describe, it, expect } from "vitest";
import { mockCameras, mockAlerts, mockViolationEvents, mockAnalytics } from "./mockData";

describe("mockData validation", () => {
  it("mockCameras has entries with required fields", () => {
    expect(mockCameras.length).toBeGreaterThan(0);
    for (const cam of mockCameras) {
      expect(cam.id).toBeTruthy();
      expect(cam.name).toBeTruthy();
      expect(cam.zone).toBeTruthy();
      expect(["online", "offline", "warning", "alert"]).toContain(cam.status);
    }
  });

  it("mockAlerts has entries with required fields", () => {
    expect(mockAlerts.length).toBeGreaterThan(0);
    for (const alert of mockAlerts) {
      expect(alert.id).toBeTruthy();
      expect(alert.severity).toBeTruthy();
      expect(alert.status).toBeTruthy();
    }
  });

  it("mockViolationEvents has entries", () => {
    expect(mockViolationEvents.length).toBeGreaterThan(0);
    for (const event of mockViolationEvents) {
      expect(event.id).toBeTruthy();
      expect(event.cameraId).toBeTruthy();
    }
  });

  it("mockAnalytics has required structure", () => {
    expect(mockAnalytics).toBeDefined();
    expect(mockAnalytics.totalEvents).toBeGreaterThanOrEqual(0);
  });

  it("camera statuses are valid enum values", () => {
    const validStatuses = new Set(["online", "offline", "warning", "alert"]);
    for (const cam of mockCameras) {
      expect(validStatuses.has(cam.status)).toBe(true);
    }
  });

  it("alert severities are valid", () => {
    const validSeverities = new Set(["critical", "high", "medium", "low", "info", "warning"]);
    for (const alert of mockAlerts) {
      expect(validSeverities.has(alert.severity)).toBe(true);
    }
  });
});
