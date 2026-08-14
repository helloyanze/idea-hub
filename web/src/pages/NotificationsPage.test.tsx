import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { NotificationsPage } from "./NotificationsPage";
import { clearCredentials } from "@/lib/auth";

interface Notification {
  id: number;
  type: string;
  title: string;
  body: string;
  level: "info" | "warn" | "error";
  entity_type: string | null;
  entity_id: number | null;
  is_read: boolean;
  created_at: string;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const API = "http://localhost:8000";

function notification(overrides: Partial<Notification>): Notification {
  return {
    id: 1,
    type: "execute_done",
    title: "任务执行完成",
    body: "任务 #12 已执行完成",
    level: "info",
    entity_type: "task",
    entity_id: 12,
    is_read: false,
    created_at: "2026-08-14 09:00:00",
    ...overrides,
  };
}

function notificationsPage(items: Notification[], total = items.length) {
  return { items, total, page: 1, size: 20 };
}

function stubFetch(
  routes: Array<
    [RegExp, (url: string, init?: RequestInit) => Response | Promise<Response>]
  >,
) {
  const fetchMock = vi.fn(
    (input: string | URL | Request, init?: RequestInit) => {
      const u = String(input);
      for (const [pattern, respond] of routes) {
        if (pattern.test(u)) {
          return Promise.resolve(respond(u, init));
        }
      }
      return Promise.resolve(jsonResponse({ data: null }));
    },
  );
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
      <MemoryRouter>
        <NotificationsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  clearCredentials();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("NotificationsPage", () => {
  it("renders notifications with level badge colors, type, title, body and time", async () => {
    const all: Notification[] = [
      notification({
        id: 1,
        level: "info",
        title: "任务执行完成",
        body: "任务 #12 已执行完成",
        created_at: "2026-08-14 09:00:00",
      }),
      notification({
        id: 2,
        level: "warn",
        title: "任务即将过期",
        body: "任务 #3 将在 2 小时内过期",
        created_at: "2026-08-14 08:00:00",
      }),
      notification({
        id: 3,
        level: "error",
        type: "job_failed",
        title: "执行失败",
        body: "任务 #7 执行失败",
        created_at: "2026-08-14 07:00:00",
        is_read: true,
      }),
    ];
    stubFetch([
      [
        /\/api\/v1\/notifications\?/,
        () => jsonResponse({ data: notificationsPage(all) }),
      ],
    ]);

    renderPage();

    expect(await screen.findByText("任务执行完成")).toBeInTheDocument();
    expect(screen.getByText("任务 #12 已执行完成")).toBeInTheDocument();
    expect(screen.getByText("2026-08-14 09:00:00")).toBeInTheDocument();
    expect(screen.getByText("任务即将过期")).toBeInTheDocument();
    expect(screen.getByText("执行失败")).toBeInTheDocument();

    const badges = screen.getAllByTestId("level-badge");
    expect(badges).toHaveLength(3);
    expect(badges[0]).toHaveTextContent("info");
    expect(badges[0]).toHaveClass(/blue/);
    expect(badges[1]).toHaveTextContent("warn");
    expect(badges[1]).toHaveClass(/yellow/);
    expect(badges[2]).toHaveTextContent("error");
    expect(badges[2]).toHaveClass(/red/);
  });

  it("links task notifications to the task detail page", async () => {
    stubFetch([
      [
        /\/api\/v1\/notifications\?/,
        () =>
          jsonResponse({
            data: notificationsPage([
              notification({ id: 1, entity_type: "task", entity_id: 12 }),
            ]),
          }),
      ],
    ]);

    renderPage();

    const link = await screen.findByRole("link", { name: /任务执行完成/ });
    expect(link).toHaveAttribute("href", "/tasks/12");
  });

  it("shows the unread count from the unread-count endpoint", async () => {
    stubFetch([
      [
        /\/api\/v1\/notifications\/unread-count/,
        () => jsonResponse({ data: { count: 3 } }),
      ],
      [
        /\/api\/v1\/notifications\?/,
        () => jsonResponse({ data: notificationsPage([]) }),
      ],
    ]);

    renderPage();

    expect(await screen.findByText("未读 3 条")).toBeInTheDocument();
  });

  it("shows an empty state when there are no notifications", async () => {
    stubFetch([
      [
        /\/api\/v1\/notifications\?/,
        () => jsonResponse({ data: notificationsPage([]) }),
      ],
    ]);

    renderPage();

    expect(await screen.findByText("暂无通知")).toBeInTheDocument();
  });

  it("filters by level and type when the dropdowns change", async () => {
    const fetchMock = stubFetch([
      [
        /\/api\/v1\/notifications\?/,
        () => jsonResponse({ data: notificationsPage([notification({})]) }),
      ],
    ]);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("任务执行完成");

    await user.selectOptions(
      screen.getByRole("combobox", { name: /级别/ }),
      "error",
    );
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).includes("level=error"),
        ),
      ).toBe(true);
    });

    await user.selectOptions(
      screen.getByRole("combobox", { name: /类型/ }),
      "job_failed",
    );
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).includes("type=job_failed"),
        ),
      ).toBe(true);
    });
  });

  it("marks a single notification as read", async () => {
    const fetchMock = stubFetch([
      [
        /\/api\/v1\/notifications\/unread-count/,
        () => jsonResponse({ data: { count: 2 } }),
      ],
      [
        /\/api\/v1\/notifications\?/,
        () =>
          jsonResponse({
            data: notificationsPage([
              notification({ id: 7, is_read: false }),
            ]),
          }),
      ],
      [
        /\/api\/v1\/notifications\/7\/read/,
        () => jsonResponse({ data: { id: 7, is_read: true } }),
      ],
    ]);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("任务执行完成");

    await user.click(screen.getByRole("button", { name: /标已读/ }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) =>
        String(url).includes("/api/v1/notifications/7/read"),
      );
      expect(call).toBeTruthy();
      expect(call?.[1]?.method).toBe("POST");
    });
  });

  it("marks all notifications as read", async () => {
    const fetchMock = stubFetch([
      [
        /\/api\/v1\/notifications\/read-all/,
        () => jsonResponse({ data: { updated: 2 } }),
      ],
      [
        /\/api\/v1\/notifications\?/,
        () =>
          jsonResponse({
            data: notificationsPage([
              notification({ id: 1, title: "通知一" }),
              notification({ id: 2, title: "通知二" }),
            ]),
          }),
      ],
    ]);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("通知一");

    await user.click(screen.getByRole("button", { name: /全部已读/ }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) =>
        String(url).includes("/api/v1/notifications/read-all"),
      );
      expect(call).toBeTruthy();
      expect(call?.[1]?.method).toBe("POST");
    });
  });

  it("loads more notifications when the load more button is clicked", async () => {
    const all: Notification[] = Array.from({ length: 25 }, (_, i) =>
      notification({ id: i + 1, title: `通知${i + 1}` }),
    );
    const fetchMock = stubFetch([
      [
        /\/api\/v1\/notifications\?/,
        (url) => {
          const params = new URL(url, API).searchParams;
          const page = Number(params.get("page") ?? 1);
          const size = Number(params.get("size") ?? 20);
          const items = all.slice((page - 1) * size, page * size);
          return jsonResponse({ data: notificationsPage(items, all.length) });
        },
      ],
    ]);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("通知1");

    await user.click(screen.getByRole("button", { name: /加载更多/ }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("page=2")),
      ).toBe(true);
    });
    expect(await screen.findByText("通知21")).toBeInTheDocument();
    expect(screen.getByText("通知1")).toBeInTheDocument();
  });
});
