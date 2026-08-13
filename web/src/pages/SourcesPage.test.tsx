import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SourcesPage } from "./SourcesPage";
import { clearCredentials } from "@/lib/auth";

interface Source {
  id: number;
  type: string;
  name: string;
  url: string;
  enabled: boolean;
  items_path: string;
  title_field: string;
  keywords: string;
  ttl_hours: number | null;
  channel_config: Record<string, unknown>;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const sources: Source[] = [
  {
    id: 1,
    type: "hotlist",
    name: "Tophub 热榜",
    url: "https://tophub.today/",
    enabled: true,
    items_path: "data",
    title_field: "title",
    keywords: "",
    ttl_hours: 24,
    channel_config: {},
  },
  {
    id: 2,
    type: "rss",
    name: "RSS 源",
    url: "https://example.com/feed.xml",
    enabled: false,
    items_path: "data",
    title_field: "title",
    keywords: "",
    ttl_hours: 24,
    channel_config: {},
  },
];

const API = "http://localhost:8000";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SourcesPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  clearCredentials();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("SourcesPage", () => {
  it("renders the source list with name, type and enable/disable switches", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(jsonResponse({ data: sources })),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByText("Tophub 热榜")).toBeInTheDocument();
    expect(screen.getByText("RSS 源")).toBeInTheDocument();
    expect(screen.getByText("hotlist")).toBeInTheDocument();
    expect(screen.getByText("rss")).toBeInTheDocument();

    const switches = screen.getAllByRole("switch");
    expect(switches).toHaveLength(2);
    expect(switches[0]).toHaveAttribute("aria-checked", "true");
    expect(switches[1]).toHaveAttribute("aria-checked", "false");
  });

  it("fills channel preset defaults when a type is selected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ data: sources }))),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Tophub 热榜");

    await user.click(screen.getByRole("button", { name: /新建来源/ }));
    await user.selectOptions(screen.getByLabelText(/类型/), "github-trending");

    expect(screen.getByLabelText(/地址/)).toHaveValue(
      "https://github.com/trending",
    );
    expect(screen.getByLabelText(/数据路径/)).toHaveValue("data");
    expect(screen.getByLabelText(/标题字段/)).toHaveValue("title");
  });

  it("submits the correct payload when creating a new source", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const u = String(url);
      const method = init?.method ?? "GET";
      if (method === "GET" && u.endsWith("/api/v1/sources")) {
        return Promise.resolve(jsonResponse({ data: sources }));
      }
      return Promise.resolve(jsonResponse({ data: {} }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Tophub 热榜");

    await user.click(screen.getByRole("button", { name: /新建来源/ }));
    await user.selectOptions(screen.getByLabelText(/类型/), "rss");
    await user.type(screen.getByLabelText(/名称/), "My Feed");
    await user.type(
      screen.getByLabelText(/地址/),
      "https://example.com/feed.xml",
    );
    await user.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/api/v1/sources") &&
          (init?.method ?? "GET") === "POST",
      );
      expect(createCall).toBeDefined();
      const [, init] = createCall!;
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body).toEqual(
        expect.objectContaining({
          type: "rss",
          name: "My Feed",
          url: "https://example.com/feed.xml",
          enabled: true,
        }),
      );
    });
  });

  it("calls the toggle endpoint when the switch is clicked", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const u = String(url);
      const method = init?.method ?? "GET";
      if (method === "GET" && u.endsWith("/api/v1/sources")) {
        return Promise.resolve(jsonResponse({ data: sources }));
      }
      if (method === "POST" && u.endsWith("/api/v1/sources/1/toggle")) {
        return Promise.resolve(
          jsonResponse({ data: { ...sources[0], enabled: false } }),
        );
      }
      return Promise.resolve(jsonResponse({ data: {} }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Tophub 热榜");

    await user.click(screen.getByRole("switch", { name: /Tophub 热榜/ }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `${API}/api/v1/sources/1/toggle`,
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("shows the fetched item count and sample items when a test succeeds", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const u = String(url);
      const method = init?.method ?? "GET";
      if (method === "GET" && u.endsWith("/api/v1/sources")) {
        return Promise.resolve(jsonResponse({ data: sources }));
      }
      if (method === "POST" && u.endsWith("/api/v1/sources/1/test")) {
        return Promise.resolve(
          jsonResponse({
            data: {
              ok: true,
              item_count: 3,
              sample_items: [
                {
                  title: "Sample A",
                  url: "https://example.com/a",
                  content_snapshot: "",
                },
                {
                  title: "Sample B",
                  url: "https://example.com/b",
                  content_snapshot: "",
                },
              ],
            },
          }),
        );
      }
      return Promise.resolve(jsonResponse({ data: {} }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Tophub 热榜");

    await user.click(
      screen.getByRole("button", { name: /测试抓取.*Tophub 热榜/ }),
    );

    expect(await screen.findByText(/3 条/)).toBeInTheDocument();
    expect(screen.getByText("Sample A")).toBeInTheDocument();
    expect(screen.getByText("Sample B")).toBeInTheDocument();
  });

  it("shows the error message when a test fails", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const u = String(url);
      const method = init?.method ?? "GET";
      if (method === "GET" && u.endsWith("/api/v1/sources")) {
        return Promise.resolve(jsonResponse({ data: sources }));
      }
      if (method === "POST" && u.endsWith("/api/v1/sources/2/test")) {
        return Promise.resolve(
          jsonResponse({
            data: {
              ok: false,
              item_count: 0,
              sample_items: [],
              error: "connection refused",
            },
          }),
        );
      }
      return Promise.resolve(jsonResponse({ data: {} }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("RSS 源");

    await user.click(
      screen.getByRole("button", { name: /测试抓取.*RSS 源/ }),
    );

    expect(await screen.findByText(/connection refused/)).toBeInTheDocument();
  });

  it("shows a hint about linked hot items when deleting a source fails with 409", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const u = String(url);
      const method = init?.method ?? "GET";
      if (method === "GET" && u.endsWith("/api/v1/sources")) {
        return Promise.resolve(jsonResponse({ data: sources }));
      }
      if (method === "DELETE" && u.endsWith("/api/v1/sources/2")) {
        return Promise.resolve(
          jsonResponse(
            {
              error: {
                code: "SOURCE_HAS_ITEMS",
                message: "Source has 3 hot items",
              },
            },
            409,
          ),
        );
      }
      return Promise.resolve(jsonResponse({ data: {} }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("RSS 源");

    await user.click(screen.getByRole("button", { name: /删除.*RSS 源/ }));

    expect(await screen.findByText(/有关联热点/)).toBeInTheDocument();
  });
});
