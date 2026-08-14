import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StatsPage } from "./StatsPage";
import { clearCredentials } from "@/lib/auth";

interface StatsData {
  queue: Record<string, number>;
  hotspots: { total: number; admit: number; discard: number };
  tokens: { execution_total: number; generation_total: number };
  today_produced: number;
  active_jobs: number;
  scheduler: { last_tick: string | null };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function statsData(overrides: Partial<StatsData> = {}): StatsData {
  return {
    queue: { todo: 3, waiting: 1, in_progress: 2, done: 5 },
    hotspots: { total: 10, admit: 4, discard: 6 },
    tokens: { execution_total: 12345, generation_total: 6789 },
    today_produced: 2,
    active_jobs: 1,
    scheduler: { last_tick: new Date().toISOString() },
    ...overrides,
  };
}

function trendsData() {
  return {
    items: [
      { date: "2026-08-08", hotspots: 1, tasks: 2, outputs: 0 },
      { date: "2026-08-09", hotspots: 0, tasks: 1, outputs: 1 },
      { date: "2026-08-10", hotspots: 2, tasks: 0, outputs: 2 },
      { date: "2026-08-11", hotspots: 1, tasks: 1, outputs: 1 },
      { date: "2026-08-12", hotspots: 3, tasks: 2, outputs: 1 },
      { date: "2026-08-13", hotspots: 0, tasks: 3, outputs: 2 },
      { date: "2026-08-14", hotspots: 2, tasks: 1, outputs: 3 },
    ],
  };
}

function stubFetch(stats: StatsData, trends = trendsData()) {
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const u = String(input);
    if (u.includes("/api/v1/stats/trends")) {
      return Promise.resolve(jsonResponse({ data: trends }));
    }
    if (u.includes("/api/v1/stats")) {
      return Promise.resolve(jsonResponse({ data: stats }));
    }
    return Promise.resolve(jsonResponse({ data: null }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <StatsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  clearCredentials();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("StatsPage", () => {
  it("renders queue count cards, hotspot verdicts, token totals and today produced", async () => {
    stubFetch(statsData());

    renderPage();

    expect(await screen.findByText("队列")).toBeInTheDocument();
    expect(screen.getByTestId("queue-todo")).toHaveTextContent("3");
    expect(screen.getByTestId("queue-waiting")).toHaveTextContent("1");
    expect(screen.getByTestId("queue-in_progress")).toHaveTextContent("2");
    expect(screen.getByTestId("queue-done")).toHaveTextContent("5");

    expect(screen.getByTestId("hotspot-admit")).toHaveTextContent("4");
    expect(screen.getByTestId("hotspot-discard")).toHaveTextContent("6");

    expect(screen.getByTestId("token-execution")).toHaveTextContent("12345");
    expect(screen.getByTestId("token-generation")).toHaveTextContent("6789");

    expect(screen.getByTestId("today-produced")).toHaveTextContent("2");
    expect(screen.getByTestId("active-jobs")).toHaveTextContent("1");
  });

  it("shows a red health bar when the scheduler never ran", async () => {
    stubFetch(statsData({ scheduler: { last_tick: null } }));

    renderPage();

    const bar = await screen.findByTestId("scheduler-health");
    expect(bar).toHaveTextContent(/未运行/);
    expect(bar).toHaveClass(/red/);
  });

  it("shows a red health bar when the last tick is older than 10 minutes", async () => {
    const stale = new Date(Date.now() - 15 * 60 * 1000).toISOString();
    stubFetch(statsData({ scheduler: { last_tick: stale } }));

    renderPage();

    const bar = await screen.findByTestId("scheduler-health");
    expect(bar).toHaveTextContent(/异常|未运行/);
    expect(bar).toHaveClass(/red/);
  });

  it("shows a green health bar when the scheduler is running normally", async () => {
    stubFetch(statsData());

    renderPage();

    const bar = await screen.findByTestId("scheduler-health");
    expect(bar).toHaveTextContent(/运行中/);
    expect(bar).toHaveClass(/green/);
  });

  it("renders a 7-day trend bar chart from the trends endpoint", async () => {
    stubFetch(statsData());

    renderPage();

    const bars = await screen.findAllByTestId("trend-bar");
    expect(bars).toHaveLength(7);
    expect(bars[0]).toHaveAttribute("data-date", "2026-08-08");
    expect(bars[0]).toHaveAttribute("data-count", "3");
    expect(bars[6]).toHaveAttribute("data-date", "2026-08-14");
  });
});
