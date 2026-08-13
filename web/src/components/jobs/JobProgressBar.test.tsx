import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  resetJobPollRegistry,
  useJobPolling,
  useCollectTrigger,
} from "@/api/hooks/useJobPolling";
import { clearCredentials } from "@/lib/auth";

import { JobProgressBar } from "./JobProgressBar";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderWithQuery(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

/**
 * Drain microtasks AND the 0ms notify timers React Query uses to re-render.
 * A single advanceTimersByTimeAsync(0) only yields once, which is not enough
 * to flush the full fetch -> dispatch -> notify chain — that made these
 * tests flaky (sometimes the done/progress update never reached the DOM).
 */
async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
    for (let i = 0; i < 25; i += 1) {
      await vi.advanceTimersByTimeAsync(0);
      await Promise.resolve();
    }
  });
}

beforeEach(() => {
  localStorage.clear();
  clearCredentials();
  vi.unstubAllGlobals();
  vi.useFakeTimers();
  resetJobPollRegistry();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("JobProgressBar", () => {
  it("polls the job every 2s and stops once the job is done", async () => {
    let jobFetches = 0;
    const fetchMock = vi.fn((url: string) => {
      if (String(url).endsWith("/api/v1/jobs/7")) {
        jobFetches += 1;
        const data =
          jobFetches === 1
            ? {
                id: 7,
                status: "running",
                progress: 40,
                error: null,
                result_ref: null,
              }
            : {
                id: 7,
                status: "done",
                progress: 100,
                error: null,
                result_ref: "hotspot_count=3",
              };
        return Promise.resolve(jsonResponse({ data }));
      }
      return Promise.resolve(jsonResponse({ data: {} }));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithQuery(<JobProgressBar jobId={7} />);
    await flush();

    // First fetch resolves: running at 40%.
    expect(screen.getByText(/进行中 40%/)).toBeInTheDocument();
    expect(jobFetches).toBe(1);

    // One interval later the job is done.
    await flush(2000);
    expect(screen.getByText(/已完成/)).toBeInTheDocument();
    expect(jobFetches).toBe(2);

    // Polling must stop: no further fetches even after several intervals.
    const callsAtDone = jobFetches;
    await flush(6000);
    expect(jobFetches).toBe(callsAtDone);
  });

  it("reuses the existing polling when collect returns reused:true (single subscription)", async () => {
    let jobFetches = 0;
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const u = String(url);
      const method = init?.method ?? "GET";
      if (method === "POST" && u.endsWith("/api/v1/collect")) {
        return Promise.resolve(
          jsonResponse({ data: { job_id: 7, reused: jobFetches > 0 } }),
        );
      }
      if (method === "GET" && u.endsWith("/api/v1/jobs/7")) {
        jobFetches += 1;
        return Promise.resolve(
          jsonResponse({
            data: {
              id: 7,
              status: "running",
              progress: 60,
              error: null,
              result_ref: null,
            },
          }),
        );
      }
      return Promise.resolve(jsonResponse({ data: {} }));
    });
    vi.stubGlobal("fetch", fetchMock);

    function Harness() {
      const { trigger, job, reusedNotice } = useCollectTrigger();
      return (
        <div>
          <button type="button" onClick={() => trigger()}>
            触发收集
          </button>
          {job ? (
            <JobProgressBar jobId={job.jobId} notice={reusedNotice} />
          ) : null}
        </div>
      );
    }

    renderWithQuery(<Harness />);
    await flush();

    // First trigger: POST /collect -> reused:false, start polling job 7.
    fireEvent.click(screen.getByRole("button", { name: /触发收集/ }));
    await flush();
    expect(screen.getByText(/进行中 60%/)).toBeInTheDocument();
    expect(jobFetches).toBe(1);

    // Second trigger: POST /collect -> reused:true while job 7 is already
    // being polled. Show the hint and keep the SAME single subscription.
    fireEvent.click(screen.getByRole("button", { name: /触发收集/ }));
    await flush();
    expect(screen.getByText(/已有进行中的任务/)).toBeInTheDocument();
    expect(jobFetches).toBe(1);

    // Exactly one poll per interval — no duplicate subscription was added.
    await flush(2000);
    expect(jobFetches).toBe(2);
    await flush(2000);
    expect(jobFetches).toBe(3);
  });

  it("shows the error detail and a retry button when the job fails", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        jsonResponse({
          data: {
            id: 7,
            status: "failed",
            progress: 30,
            error: "source fetch timeout",
            result_ref: null,
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onRetry = vi.fn();
    renderWithQuery(<JobProgressBar jobId={7} onRetry={onRetry} />);
    await flush();

    expect(screen.getByText(/失败/)).toBeInTheDocument();
    expect(screen.getByText(/source fetch timeout/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /重试/ }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("stops polling and surfaces the error when the job request itself fails", async () => {
    let jobFetches = 0;
    const fetchMock = vi.fn((url: string) => {
      if (String(url).endsWith("/api/v1/jobs/7")) {
        jobFetches += 1;
        return Promise.resolve(
          jsonResponse(
            { error: { code: "INTERNAL", message: "backend unreachable" } },
            500,
          ),
        );
      }
      return Promise.resolve(jsonResponse({ data: {} }));
    });
    vi.stubGlobal("fetch", fetchMock);

    // Probe component renders the hook return value into the DOM so the test
    // can assert the surfaced error without depending on renderHook internals
    // (result.current is set in a passive effect that fake timers + react-query
    // never flush in this environment).
    function ErrorProbe() {
      const state = useJobPolling(7);
      return <p data-testid="poll-error">{state.error ?? "no-error"}</p>;
    }

    renderWithQuery(<ErrorProbe />);
    await flush();

    // Request error surfaced through the hook return value (not dropped).
    expect(screen.getByTestId("poll-error")).toHaveTextContent(
      "backend unreachable",
    );
    expect(jobFetches).toBe(1);

    // Polling must stop on request error: no further fetches even after
    // several 2s intervals.
    await flush(6000);
    expect(jobFetches).toBe(1);
    expect(screen.getByTestId("poll-error")).toHaveTextContent(
      "backend unreachable",
    );
  });
});
