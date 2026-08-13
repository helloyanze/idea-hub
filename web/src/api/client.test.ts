import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "./client";
import { CREDENTIALS_KEY, clearCredentials, setCredentials } from "@/lib/auth";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiFetch", () => {
  beforeEach(() => {
    localStorage.clear();
    clearCredentials();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("builds URL from VITE_API_BASE and attaches Basic auth from stored credentials", async () => {
    vi.stubEnv("VITE_API_BASE", "http://test.local");
    setCredentials("alice", "s3cret");
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: { ok: true } }));
    vi.stubGlobal("fetch", fetchMock);

    const data = await apiFetch("/api/v1/health");

    expect(data).toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://test.local/api/v1/health");
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe(
      "Basic " + btoa("alice:s3cret"),
    );
  });

  it("throws ApiError on 401 and clears stored credentials", async () => {
    setCredentials("alice", "s3cret");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { error: { code: "UNAUTHORIZED", message: "bad credentials" } },
          401,
        ),
      ),
    );

    await expect(apiFetch("/api/v1/health")).rejects.toThrow(ApiError);
    expect(localStorage.getItem(CREDENTIALS_KEY)).toBeNull();
  });

  it("unwraps {data, error} envelope and throws ApiError with code and message", async () => {
    setCredentials("alice", "s3cret");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { error: { code: "NOT_FOUND", message: "no such task" } },
          404,
        ),
      ),
    );

    const err = await apiFetch("/api/v1/tasks/1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("NOT_FOUND");
    expect((err as ApiError).message).toBe("no such task");
  });
});
