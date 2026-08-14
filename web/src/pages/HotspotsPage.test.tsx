import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HotspotsPage } from "./HotspotsPage";
import { clearCredentials } from "@/lib/auth";

interface Hotspot {
  id: number;
  title: string;
  url: string;
  final_score: number;
  score_breakdown: Record<string, number> | null;
  verdict: string | null;
  source_name: string;
  collected_date: string;
  linked_task_count: number;
}

interface HotspotPage {
  items: Hotspot[];
  total: number;
  page: number;
  size: number;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const API = "http://localhost:8000";

function hotspot(overrides: Partial<Hotspot>): Hotspot {
  return {
    id: 1,
    title: "热点一",
    url: "https://example.com/1",
    final_score: 8.2,
    score_breakdown: { facts: 8, verification: 7, timeliness: 9, value: 8 },
    verdict: "admit",
    source_name: "Tophub 热榜",
    collected_date: "2026-08-01",
    linked_task_count: 2,
    ...overrides,
  };
}

function pageResponse(page: HotspotPage): Response {
  return jsonResponse({ data: page });
}

function stubFetch(
  routes: Array<
    [RegExp, (url: string, init?: RequestInit) => Response | Promise<Response>]
  >,
) {
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const u = String(input);
    for (const [pattern, respond] of routes) {
      if (pattern.test(u)) {
        return Promise.resolve(respond(u, init));
      }
    }
    return Promise.resolve(
      pageResponse({ items: [], total: 0, page: 1, size: 20 }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function listRoute(all: Hotspot[]): [RegExp, (url: string) => Response] {
  return [
    /\/api\/v1\/hotspots\?/,
    (url) => {
      const params = new URL(url, API).searchParams;
      const page = Number(params.get("page") ?? 1);
      const size = Number(params.get("size") ?? 20);
      const items = all.slice((page - 1) * size, page * size);
      return pageResponse({
        items,
        total: all.length,
        page,
        size,
      });
    },
  ];
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
      <HotspotsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  clearCredentials();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("HotspotsPage", () => {
  it("renders hotspot cards with link, source, score, verdict badge, date and linked task count", async () => {
    const all: Hotspot[] = [
      hotspot({ id: 1, title: "热点一", url: "https://example.com/1" }),
      hotspot({
        id: 2,
        title: "热点二",
        url: "https://example.com/2",
        final_score: 5.5,
        score_breakdown: { facts: 6, verification: 5, timeliness: 4, value: 5 },
        verdict: "discard",
        source_name: "RSS 源",
        collected_date: "2026-08-02",
        linked_task_count: 0,
      }),
    ];
    const fetchMock = stubFetch([listRoute(all)]);

    renderPage();

    const link = await screen.findByRole("link", { name: "热点一" });
    expect(link).toHaveAttribute("href", "https://example.com/1");
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "热点二" })).toHaveAttribute(
      "href",
      "https://example.com/2",
    );
    expect(screen.getByText("Tophub 热榜")).toBeInTheDocument();
    expect(screen.getByText("RSS 源")).toBeInTheDocument();

    const badges = screen.getAllByTestId("score-badge");
    expect(badges).toHaveLength(2);
    expect(badges[0]).toHaveTextContent("8");

    const verdictBadges = screen.getAllByTestId("verdict-badge");
    expect(verdictBadges).toHaveLength(2);
    expect(verdictBadges[0]).toHaveTextContent("admit");
    expect(verdictBadges[0]).toHaveClass(/green/);
    expect(verdictBadges[1]).toHaveTextContent("discard");
    expect(verdictBadges[1]).toHaveClass(/gray/);

    expect(screen.getByText("已生成 2 任务")).toBeInTheDocument();
    expect(screen.queryByText("已生成 0 任务")).not.toBeInTheDocument();
    expect(screen.getByText("2026-08-01")).toBeInTheDocument();

    const firstCall = String(fetchMock.mock.calls[0][0]);
    expect(firstCall).toContain("/api/v1/hotspots?");
    expect(firstCall).toContain("page=1");
    expect(firstCall).toContain("size=20");
  });

  it("shows an empty state when there are no hotspots", async () => {
    stubFetch([listRoute([])]);

    renderPage();

    expect(await screen.findByText("暂无热点")).toBeInTheDocument();
  });

  it("filters by verdict when the dropdown changes", async () => {
    const fetchMock = stubFetch([listRoute([hotspot({})])]);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("热点一");

    const select = screen.getByRole("combobox", { name: /判定/ });
    expect(screen.getAllByRole("option")).toHaveLength(3);

    await user.selectOptions(select, "admit");

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("verdict=admit")),
      ).toBe(true);
    });
  });

  it("debounces the search box and refetches with q", async () => {
    const fetchMock = stubFetch([listRoute([hotspot({})])]);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("热点一");

    await user.type(screen.getByRole("searchbox", { name: /搜索热点/ }), "gpt");

    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("q=gpt")),
    ).toBe(false);

    await waitFor(
      () => {
        expect(
          fetchMock.mock.calls.some(([url]) => String(url).includes("q=gpt")),
        ).toBe(true);
      },
      { timeout: 2000 },
    );
  });

  it("loads more pages when the load more button is clicked", async () => {
    const all: Hotspot[] = Array.from({ length: 25 }, (_, i) =>
      hotspot({ id: i + 1, title: `热点${i + 1}` }),
    );
    const fetchMock = stubFetch([listRoute(all)]);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("热点1");

    await user.click(screen.getByRole("button", { name: /加载更多/ }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("page=2")),
      ).toBe(true);
    });
    expect(await screen.findByText("热点21")).toBeInTheDocument();
    expect(screen.getByText("热点1")).toBeInTheDocument();
  });
});
