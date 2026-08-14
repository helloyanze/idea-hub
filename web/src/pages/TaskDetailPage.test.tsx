import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TaskDetailPage } from "@/pages/TaskDetailPage";
import { clearCredentials } from "@/lib/auth";

interface Tag {
  id: number;
  name: string;
}

interface TaskDetail {
  id: number;
  title: string;
  status: string;
  content_type: string;
  feasibility_score: number;
  score_breakdown: Record<string, number> | null;
  tags: Tag[];
  idea_summary: string;
  target_desc: string;
  token_used: number | null;
  fail_count: number;
  created_at: string;
  expire_at: string | null;
  notes: string;
  ai_summary: string;
  last_fail_reason: string | null;
  redo_note: string | null;
  updated_at: string;
  completed_at: string | null;
  hotspots: Array<{ id: number; title: string; url: string; collected_date: string }>;
  output: {
    has_output: boolean;
    latest_version: number | null;
    version_count: number;
    ai_summary: string;
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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
    return Promise.resolve(jsonResponse({ data: null }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function detail(overrides: Partial<TaskDetail> = {}): TaskDetail {
  return {
    id: 1,
    title: "深度解析 AI 芯片",
    status: "in_progress",
    content_type: "article",
    feasibility_score: 8,
    score_breakdown: { facts: 8, verification: 7, timeliness: 9, value: 8 },
    tags: [
      { id: 1, name: "AI" },
      { id: 2, name: "芯片" },
    ],
    idea_summary: "摘要内容",
    target_desc: "目标读者",
    token_used: 1234,
    fail_count: 2,
    created_at: "2026-08-01 10:00:00",
    expire_at: "2026-09-01",
    notes: "",
    ai_summary: "",
    last_fail_reason: "超时",
    redo_note: null,
    updated_at: "2026-08-02 10:00:00",
    completed_at: null,
    hotspots: [
      {
        id: 1,
        title: "热点标题一",
        url: "https://example.com/hot",
        collected_date: "2026-08-01",
      },
    ],
    output: { has_output: true, latest_version: 1, version_count: 1, ai_summary: "" },
    ...overrides,
  };
}

const settingsRoute: [RegExp, () => Response] = [
  /\/api\/v1\/settings/,
  () =>
    jsonResponse({
      data: {
        done_column_limit: 50,
        score_dimensions: ["facts", "verification", "timeliness", "value"],
      },
    }),
];

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/tasks/1"]}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetailPage />} />
          <Route path="/kanban" element={<div>看板页标记</div>} />
        </Routes>
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

describe("TaskDetailPage", () => {
  it("renders the full task detail fields", async () => {
    stubFetch([
      settingsRoute,
      [/\/api\/v1\/tasks\/1(?:\?|$)/, () => jsonResponse({ data: detail() })],
    ]);

    renderPage();

    expect(await screen.findByText("深度解析 AI 芯片")).toBeInTheDocument();
    expect(screen.getByText("进行中")).toBeInTheDocument();
    expect(screen.getByText("article")).toBeInTheDocument();
    expect(screen.getByText("摘要内容")).toBeInTheDocument();
    expect(screen.getByText("目标读者")).toBeInTheDocument();
    expect(screen.getByText("1234")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("2026-08-01 10:00:00")).toBeInTheDocument();
    expect(screen.getByText("2026-09-01")).toBeInTheDocument();
    expect(screen.getByText("热点标题一")).toBeInTheDocument();
    expect(screen.getByText("AI")).toBeInTheDocument();
    expect(screen.getByText("芯片")).toBeInTheDocument();
  });

  it("renders the score breakdown with dimensions from settings", async () => {
    stubFetch([
      settingsRoute,
      [/\/api\/v1\/tasks\/1(?:\?|$)/, () => jsonResponse({ data: detail() })],
    ]);

    renderPage();

    expect(await screen.findByTestId("score-dim-facts")).toBeInTheDocument();
    expect(screen.getByTestId("score-total")).toHaveTextContent("8");
    expect(screen.getByTestId("score-badge")).toHaveTextContent("8");
  });

  it("shows 未评分 when the task has no score_breakdown", async () => {
    stubFetch([
      settingsRoute,
      [
        /\/api\/v1\/tasks\/1(?:\?|$)/,
        () =>
          jsonResponse({
            data: detail({ score_breakdown: null, feasibility_score: 0 }),
          }),
      ],
    ]);

    renderPage();

    expect(await screen.findByText("未评分")).toBeInTheDocument();
  });

  it("moves the task to done via the move endpoint", async () => {
    const fetchMock = stubFetch([
      settingsRoute,
      [/\/api\/v1\/tasks\/1(?:\?|$)/, () => jsonResponse({ data: detail() })],
      [
        /\/api\/v1\/tasks\/1\/move/,
        () => jsonResponse({ data: detail({ status: "done" }) }),
      ],
    ]);

    renderPage();
    await screen.findByText("深度解析 AI 芯片");

    await userEvent.click(screen.getByRole("button", { name: /移至已完成/ }));

    await waitFor(() => {
      const moveCall = fetchMock.mock.calls.find(([u]) =>
        String(u).includes("/api/v1/tasks/1/move"),
      );
      expect(moveCall).toBeTruthy();
      expect(JSON.parse(String((moveCall![1] as RequestInit).body))).toEqual({
        to_status: "done",
      });
    });
  });

  it("shows an error message when move returns 409", async () => {
    stubFetch([
      settingsRoute,
      [/\/api\/v1\/tasks\/1(?:\?|$)/, () => jsonResponse({ data: detail() })],
      [
        /\/api\/v1\/tasks\/1\/move/,
        () =>
          jsonResponse(
            {
              error: {
                code: "INVALID_STATUS_TRANSITION",
                message: "Task cannot move to waiting",
              },
            },
            409,
          ),
      ],
    ]);

    renderPage();
    await screen.findByText("深度解析 AI 芯片");

    await userEvent.click(screen.getByRole("button", { name: /移至等待中/ }));

    expect(await screen.findByText(/Task cannot move/)).toBeInTheDocument();
  });

  it("submits edited tags via the tags endpoint", async () => {
    const fetchMock = stubFetch([
      settingsRoute,
      [/\/api\/v1\/tasks\/1(?:\?|$)/, () => jsonResponse({ data: detail() })],
      [
        /\/api\/v1\/tasks\/1\/tags/,
        () =>
          jsonResponse({
            data: detail({ tags: [{ id: 3, name: "新品" }] }),
          }),
      ],
    ]);

    renderPage();
    await screen.findByText("深度解析 AI 芯片");

    await userEvent.click(screen.getByRole("button", { name: /编辑标签/ }));
    const input = await screen.findByLabelText(/标签/);
    await userEvent.clear(input);
    await userEvent.type(input, "新品");
    await userEvent.click(screen.getByRole("button", { name: /保存标签/ }));

    await waitFor(() => {
      const tagsCall = fetchMock.mock.calls.find(([u]) =>
        String(u).includes("/api/v1/tasks/1/tags"),
      );
      expect(tagsCall).toBeTruthy();
      expect(JSON.parse(String((tagsCall![1] as RequestInit).body))).toEqual({
        names: ["新品"],
      });
    });
  });

  it("calls redo, reset-failures and delete endpoints", async () => {
    const fetchMock = stubFetch([
      settingsRoute,
      [
        /\/api\/v1\/tasks\/1(?:\?|$)/,
        (_url, init) => {
          if ((init?.method ?? "GET") === "DELETE") {
            return jsonResponse({ data: { deleted: true } });
          }
          return jsonResponse({ data: detail() });
        },
      ],
      [
        /\/api\/v1\/tasks\/1\/redo/,
        () => jsonResponse({ data: detail({ status: "waiting" }) }),
      ],
      [
        /\/api\/v1\/tasks\/1\/reset-failures/,
        () => jsonResponse({ data: detail({ fail_count: 0 }) }),
      ],
    ]);

    renderPage();
    await screen.findByText("深度解析 AI 芯片");

    await userEvent.click(screen.getByRole("button", { name: /重做/ }));
    await userEvent.click(screen.getByRole("button", { name: /重置失败/ }));
    await userEvent.click(screen.getByRole("button", { name: /删除/ }));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([u]) => String(u));
      expect(urls.some((u) => u.includes("/api/v1/tasks/1/redo"))).toBe(true);
      expect(
        urls.some((u) => u.includes("/api/v1/tasks/1/reset-failures")),
      ).toBe(true);
      const deleteCall = fetchMock.mock.calls.find(
        ([u, init]) =>
          String(u).includes("/api/v1/tasks/1") &&
          !String(u).includes("/move") &&
          (init?.method ?? "GET") === "DELETE",
      );
      expect(deleteCall).toBeTruthy();
    });

    expect(await screen.findByText("看板页标记")).toBeInTheDocument();
  });
});
