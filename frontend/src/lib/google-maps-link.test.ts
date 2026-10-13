import { describe, it, expect } from "vitest";
import { parseGoogleMapsLatLng, isGoogleMapsShortLink } from "./google-maps-link";

describe("parseGoogleMapsLatLng", () => {
  it("parses @lat,lng,zoomz form", () => {
    expect(parseGoogleMapsLatLng("https://www.google.com/maps/@12.9716,77.5946,15z"))
      .toEqual({ lat: 12.9716, lng: 77.5946 });
  });

  it("parses !3d..!4d.. place form", () => {
    expect(parseGoogleMapsLatLng("https://www.google.com/maps/place/X/data=!3d40.7128!4d-74.006"))
      .toEqual({ lat: 40.7128, lng: -74.006 });
  });

  it("parses q= query param", () => {
    expect(parseGoogleMapsLatLng("https://maps.google.com/?q=51.5074,-0.1278"))
      .toEqual({ lat: 51.5074, lng: -0.1278 });
  });

  it("parses query= query param", () => {
    expect(parseGoogleMapsLatLng("https://www.google.com/maps/search/?api=1&query=-33.8688,151.2093"))
      .toEqual({ lat: -33.8688, lng: 151.2093 });
  });

  it("parses ll= query param", () => {
    expect(parseGoogleMapsLatLng("https://maps.google.com/?ll=35.6895,139.6917&z=12"))
      .toEqual({ lat: 35.6895, lng: 139.6917 });
  });

  it("parses a bare lat,lng string", () => {
    expect(parseGoogleMapsLatLng("12.9716, 77.5946")).toEqual({ lat: 12.9716, lng: 77.5946 });
  });

  it("returns null for a link with no coordinates", () => {
    expect(parseGoogleMapsLatLng("https://www.google.com/maps/place/Eiffel+Tower")).toBeNull();
  });

  it("returns null for empty input", () => {
    expect(parseGoogleMapsLatLng("")).toBeNull();
  });

  it("returns null for out-of-range coordinates", () => {
    expect(parseGoogleMapsLatLng("@91.0,200.0,15z")).toBeNull();
    expect(parseGoogleMapsLatLng("99.0,-999.0")).toBeNull();
  });

  it("does not resolve a short link to coordinates", () => {
    expect(parseGoogleMapsLatLng("https://maps.app.goo.gl/abc123")).toBeNull();
  });
});

describe("isGoogleMapsShortLink", () => {
  it("detects maps.app.goo.gl", () => {
    expect(isGoogleMapsShortLink("https://maps.app.goo.gl/abc123")).toBe(true);
  });

  it("detects goo.gl/maps", () => {
    expect(isGoogleMapsShortLink("https://goo.gl/maps/xyz")).toBe(true);
  });

  it("is false for a full maps URL", () => {
    expect(isGoogleMapsShortLink("https://www.google.com/maps/@12.9716,77.5946,15z")).toBe(false);
  });
});
