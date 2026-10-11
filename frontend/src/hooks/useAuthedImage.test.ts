import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useAuthedImage } from "./useAuthedImage";

function okResponse(): Response {
  return { ok: true, blob: () => Promise.resolve(new Blob(["jpeg"])) } as unknown as Response;
}

describe("useAuthedImage", () => {
  let counter = 0;

  beforeEach(() => {
    counter = 0;
    URL.createObjectURL = vi.fn(() => `blob:face-${++counter}`);
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("exposes the fetched image as a blob URL", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(okResponse())));
    const { result } = renderHook(() => useAuthedImage("/api/subjects/faces/a"));
    await waitFor(() => expect(result.current).toBe("blob:face-1"));
  });

  it("clears the previous image when the url changes and the new fetch fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse())
      .mockResolvedValueOnce({ ok: false, status: 404 } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(({ url }) => useAuthedImage(url), {
      initialProps: { url: "/api/subjects/faces/a" },
    });
    await waitFor(() => expect(result.current).toBe("blob:face-1"));
    rerender({ url: "/api/subjects/faces/b" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current).toBeNull());
  });

  it("never shows the previous subject's face while the next one loads", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse())
      .mockReturnValueOnce(new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    const { result, rerender } = renderHook(({ url }) => useAuthedImage(url), {
      initialProps: { url: "/api/subjects/faces/a" },
    });
    await waitFor(() => expect(result.current).toBe("blob:face-1"));
    rerender({ url: "/api/subjects/faces/b" });
    expect(result.current).toBeNull();
  });
});
