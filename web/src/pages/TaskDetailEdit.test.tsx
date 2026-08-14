import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
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

function taskRoute(overrides: Partial<TaskDetail> = {}): [RegExp, (url: string, init?: RequestInit) => Response] {
  return [
    /\/api\/v1\/tasks\/1$/,
    (_url, init) => {
      if ((init?.method ?? "GET") === "PATCH") {
        return jsonResponse({ data: detail(overrides) });
      }
      return jsonResponse({ data: detail() });
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

function getInfoSection() {
  return screen.getByRole("heading", { name: "任务信息" }).closest("section") as HTMLElement;
}

describe("TaskDetailPage 信息编辑", () => {
  it("点击编辑按钮显示表单并预填当前值", async () => {
    stubFetch([settingsRoute, taskRoute()]);

    renderPage();

    expect(await screen.findByText("深度解析 AI 芯片")).toBeInTheDocument();

    const infoSection = getInfoSection();
    await userEvent.click(within(infoSection).getByRole("button", { name: "编辑" }));

    expect(within(infoSection).getByRole("textbox", { name: "标题" })).toHaveValue("深度解析 AI 芯片");
    expect(within(infoSection).getByRole("textbox", { name: "目标描述" })).toHaveValue("目标读者");
    expect(within(infoSection).getByRole("combobox", { name: "内容类型" })).toHaveValue("article");
    expect(within(infoSection).getByLabelText("过期时间")).toHaveValue("2026-09-01T00:00");
    expect(within(infoSection).getByRole("button", { name: "保存" })).toBeInTheDocument();
    expect(within(infoSection).getByRole("button", { name: "取消" })).toBeInTheDocument();
  });

  it("只改标题时 PATCH 只提交变更字段", async () => {
    const fetchMock = stubFetch([settingsRoute, taskRoute({ title: "新标题" })]);

    renderPage();
    expect(await screen.findByText("深度解析 AI 芯片")).toBeInTheDocument();

    const infoSection = getInfoSection();
    await userEvent.click(within(infoSection).getByRole("button", { name: "编辑" }));
    const titleInput = within(infoSection).getByRole("textbox", { name: "标题" });
    await userEvent.clear(titleInput);
    await userEvent.type(titleInput, "新标题");
    await userEvent.click(within(infoSection).getByRole("button", { name: "保存" }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([u, init]) =>
          /\/api\/v1\/tasks\/1$/.test(String(u)) && (init?.method ?? "GET") === "PATCH",
      );
      expect(patchCall).toBeTruthy();
      expect(JSON.parse(String((patchCall![1] as RequestInit).body))).toEqual({
        title: "新标题",
      });
    });
  });

  it("只改内容类型时 PATCH 只提交该字段", async () => {
    const fetchMock = stubFetch([settingsRoute, taskRoute({ content_type: "tweet" })]);

    renderPage();
    expect(await screen.findByText("深度解析 AI 芯片")).toBeInTheDocument();

    const infoSection = getInfoSection();
    await userEvent.click(within(infoSection).getByRole("button", { name: "编辑" }));
    await userEvent.selectOptions(within(infoSection).getByRole("combobox", { name: "内容类型" }), "tweet");
    await userEvent.click(within(infoSection).getByRole("button", { name: "保存" }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([u, init]) =>
          /\/api\/v1\/tasks\/1$/.test(String(u)) && (init?.method ?? "GET") === "PATCH",
      );
      expect(patchCall).toBeTruthy();
      expect(JSON.parse(String((patchCall![1] as RequestInit).body))).toEqual({
        content_type: "tweet",
      });
    });
  });

  it("保存成功后退出编辑模式并重新获取任务", async () => {
    const fetchMock = stubFetch([settingsRoute, taskRoute({ title: "新标题" })]);

    renderPage();
    expect(await screen.findByText("深度解析 AI 芯片")).toBeInTheDocument();

    const infoSection = getInfoSection();
    await userEvent.click(within(infoSection).getByRole("button", { name: "编辑" }));
    const titleInput = within(infoSection).getByRole("textbox", { name: "标题" });
    await userEvent.clear(titleInput);
    await userEvent.type(titleInput, "新标题");
    await userEvent.click(within(infoSection).getByRole("button", { name: "保存" }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([u, init]) =>
          /\/api\/v1\/tasks\/1$/.test(String(u)) && (init?.method ?? "GET") === "PATCH",
      );
      expect(patchCall).toBeTruthy();

      const getCalls = fetchMock.mock.calls.filter(
        ([u, init]) =>
          /\/api\/v1\/tasks\/1$/.test(String(u)) && (init?.method ?? "GET") === "GET",
      );
      expect(getCalls.length).toBeGreaterThanOrEqual(2);
      expect(screen.queryByRole("textbox", { name: "标题" })).toBeNull();
      expect(screen.getByText("目标读者")).toBeInTheDocument();
    });
  });

  it("取消编辑不提交任何请求", async () => {
    const fetchMock = stubFetch([settingsRoute, taskRoute()]);

    renderPage();
    expect(await screen.findByText("深度解析 AI 芯片")).toBeInTheDocument();

    const infoSection = getInfoSection();
    await userEvent.click(within(infoSection).getByRole("button", { name: "编辑" }));
    const titleInput = within(infoSection).getByRole("textbox", { name: "标题" });
    await userEvent.clear(titleInput);
    await userEvent.type(titleInput, "新标题");
    await userEvent.click(within(infoSection).getByRole("button", { name: "取消" }));

    expect(
      fetchMock.mock.calls.some(
        ([u, init]) =>
          /\/api\/v1\/tasks\/1$/.test(String(u)) && (init?.method ?? "GET") === "PATCH",
      ),
    ).toBe(false);
    expect(screen.queryByRole("textbox", { name: "标题" })).toBeNull();
  });

  it("标题为空时保存显示校验错误且不提交", async () => {
    const fetchMock = stubFetch([settingsRoute, taskRoute()]);

    renderPage();
    expect(await screen.findByText("深度解析 AI 芯片")).toBeInTheDocument();

    const infoSection = getInfoSection();
    await userEvent.click(within(infoSection).getByRole("button", { name: "编辑" }));
    const titleInput = within(infoSection).getByRole("textbox", { name: "标题" });
    await userEvent.clear(titleInput);
    await userEvent.click(within(infoSection).getByRole("button", { name: "保存" }));

    expect(await screen.findByText("标题不能为空")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([u, init]) =>
          /\/api\/v1\/tasks\/1$/.test(String(u)) && (init?.method ?? "GET") === "PATCH",
      ),
    ).toBe(false);
  });
});
