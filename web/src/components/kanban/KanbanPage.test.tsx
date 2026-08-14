import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { KanbanPage, canDrop } from "@/pages/KanbanPage";
import { clearCredentials } from "@/lib/auth";

interface Tag {
  id: number;
  name: string;
}

interface Task {
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
}

const { dragEndHandler } = vi.hoisted(() => ({
  dragEndHandler: {
    current: null as null | ((event: { active: { id: string }; over: { id: string } | null }) => void),
  },
}));

vi.mock("@dnd-kit/core", () => ({
  DndContext: ({
    children,
    onDragEnd,
  }: {
    children: ReactNode;
    onDragEnd: (event: unknown) => void;
  }) => {
    dragEndHandler.current = onDragEnd as typeof dragEndHandler.current;
    return <div>{children}</div>;
  },
  useDraggable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: () => {},
    transform: null,
    isDragging: false,
  }),
  useDroppable: () => ({
    setNodeRef: () => {},
    isOver: false,
  }),
  DragOverlay: ({ children }: { children?: ReactNode }) =>
    children ? <div>{children}</div> : null,
}));

vi.mock("@dnd-kit/sortable", () => ({
  useSortable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: () => {},
    transform: null,
    transition: null,
    isDragging: false,
  }),
  SortableContext: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  verticalListSortingStrategy: {},
}));

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
    return Promise.resolve(
      jsonResponse({ data: { items: [], total: 0, page: 1, size: 20 } }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function task(overrides: Partial<Task>): Task {
  return {
    id: 1,
    title: "任务一",
    status: "todo",
    content_type: "article",
    feasibility_score: 8,
    score_breakdown: { facts: 8, verification: 7, timeliness: 9, value: 8 },
    tags: [{ id: 1, name: "科技" }],
    idea_summary: "",
    target_desc: "",
    token_used: null,
    fail_count: 0,
    created_at: "2026-08-01 10:00:00",
    expire_at: null,
    ...overrides,
  };
}

const settingsRoute: [RegExp, (url: string, init?: RequestInit) => Response] = [
  /\/api\/v1\/settings/,
  () =>
    jsonResponse({
      data: {
        done_column_limit: 50,
        score_dimensions: ["facts", "verification", "timeliness", "value"],
      },
    }),
];

function listRoute(allTasks: Task[]): [RegExp, (url: string) => Response] {
  return [
    /\/api\/v1\/tasks\?/,
    (url) => {
      const params = new URL(url, "http://localhost:8000").searchParams;
      const status = params.get("status");
      const page = Number(params.get("page") ?? 1);
      const size = Number(params.get("size") ?? 20);
      const items = allTasks.filter((t) => t.status === status);
      return jsonResponse({
        data: {
          items: items.slice((page - 1) * size, page * size),
          total: items.length,
          page,
          size,
        },
      });
    },
  ];
}

function renderPage(initialEntries: string[] = ["/kanban"]) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <KanbanPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  clearCredentials();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  dragEndHandler.current = null;
});

describe("canDrop", () => {
  it("forbids dropping done cards into in_progress", () => {
    expect(canDrop("done", "in_progress")).toBe(false);
    expect(canDrop("done", "todo")).toBe(true);
    expect(canDrop("done", "waiting")).toBe(true);
    expect(canDrop("todo", "in_progress")).toBe(true);
    expect(canDrop("waiting", "todo")).toBe(true);
    expect(canDrop("todo", "todo")).toBe(false);
  });
});

describe("KanbanPage", () => {
  it("renders four columns and groups tasks by status", async () => {
    const allTasks: Task[] = [
      task({ id: 1, title: "待办任务", status: "todo" }),
      task({ id: 2, title: "等待任务", status: "waiting" }),
      task({ id: 3, title: "进行中任务", status: "in_progress" }),
      task({ id: 4, title: "已完成任务", status: "done" }),
    ];
    stubFetch([settingsRoute, listRoute(allTasks)]);

    renderPage();

    for (const header of ["待办", "等待中", "进行中", "已完成"]) {
      expect(await screen.findByText(header)).toBeInTheDocument();
    }
    expect(await screen.findByText("待办任务")).toBeInTheDocument();
    expect(screen.getByText("等待任务")).toBeInTheDocument();
    expect(screen.getByText("进行中任务")).toBeInTheDocument();
    expect(screen.getByText("已完成任务")).toBeInTheDocument();

    const todoColumn = screen.getByTestId("kanban-column-todo");
    expect(todoColumn).toHaveTextContent("待办任务");
    expect(todoColumn).not.toHaveTextContent("已完成任务");
    const doneColumn = screen.getByTestId("kanban-column-done");
    expect(doneColumn).toHaveTextContent("已完成任务");
    expect(doneColumn).not.toHaveTextContent("待办任务");
  });

  it("shows feasibility score and colored tags on cards", async () => {
    const allTasks: Task[] = [
      task({ id: 1, title: "待办任务", status: "todo", tags: [{ id: 1, name: "AI" }, { id: 2, name: "芯片" }] }),
    ];
    stubFetch([settingsRoute, listRoute(allTasks)]);

    renderPage();

    expect(await screen.findByText("待办任务")).toBeInTheDocument();
    const card = screen.getByTestId("task-card-1");
    expect(card).toHaveTextContent("8");
    expect(card).toHaveTextContent("AI");
    expect(card).toHaveTextContent("芯片");
  });

  it("calls the move API when a card is dropped on another column", async () => {
    const allTasks: Task[] = [task({ id: 1, title: "待办任务", status: "todo" })];
    const fetchMock = stubFetch([
      settingsRoute,
      listRoute(allTasks),
      [
        /\/api\/v1\/tasks\/1\/move/,
        () => jsonResponse({ data: task({ id: 1, status: "done" }) }),
      ],
    ]);

    renderPage();
    await screen.findByText("待办任务");

    act(() => {
      dragEndHandler.current?.({ active: { id: "task-1" }, over: { id: "column-done" } });
    });

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

  it("does not call move when a card is dropped on its own column", async () => {
    const allTasks: Task[] = [task({ id: 1, title: "待办任务", status: "todo" })];
    const fetchMock = stubFetch([settingsRoute, listRoute(allTasks)]);

    renderPage();
    await screen.findByText("待办任务");

    act(() => {
      dragEndHandler.current?.({ active: { id: "task-1" }, over: { id: "column-todo" } });
    });

    await new Promise((resolve) => setTimeout(resolve, 50));
    const moveCall = fetchMock.mock.calls.find(([u]) =>
      String(u).includes("/move"),
    );
    expect(moveCall).toBeUndefined();
  });

  it("shows an error and refreshes columns when move returns 409", async () => {
    let listCalls = 0;
    const allTasks: Task[] = [task({ id: 1, title: "待办任务", status: "todo" })];
    stubFetch([
      settingsRoute,
      [
        /\/api\/v1\/tasks\?/,
        (url) => {
          listCalls += 1;
          return listRoute(allTasks)[1](url);
        },
      ],
      [
        /\/api\/v1\/tasks\/1\/move/,
        () =>
          jsonResponse(
            {
              error: {
                code: "INVALID_STATUS_TRANSITION",
                message: "Task cannot move to in_progress",
              },
            },
            409,
          ),
      ],
    ]);

    renderPage();
    await screen.findByText("待办任务");
    const callsBefore = listCalls;

    act(() => {
      dragEndHandler.current?.({
        active: { id: "task-1" },
        over: { id: "column-in_progress" },
      });
    });

    expect(await screen.findByText(/Task cannot move/)).toBeInTheDocument();
    await waitFor(() => {
      expect(listCalls).toBeGreaterThan(callsBefore);
    });
  });

  it("loads the done column with settings.done_column_limit and supports load more", async () => {
    const doneTasks: Task[] = Array.from({ length: 3 }, (_, i) =>
      task({ id: 100 + i, title: `已完成${i}`, status: "done" }),
    );
    const fetchMock = stubFetch([
      [
        /\/api\/v1\/settings/,
        () => jsonResponse({ data: { done_column_limit: 2 } }),
      ],
      listRoute(doneTasks),
    ]);

    renderPage();

    expect(await screen.findByText("已完成0")).toBeInTheDocument();
    expect(screen.getByText("已完成1")).toBeInTheDocument();

    await waitFor(() => {
      const hasSize2Call = fetchMock.mock.calls.some(
        ([u]) =>
          String(u).includes("status=done") && String(u).includes("size=2"),
      );
      expect(hasSize2Call).toBe(true);
    });

    const loadMore = await screen.findByRole("button", { name: /加载更多/ });
    await userEvent.click(loadMore);

    expect(await screen.findByText("已完成2")).toBeInTheDocument();
  });

  it("falls back to size 50 for the done column when settings fail", async () => {
    const fetchMock = stubFetch([
      [/\/api\/v1\/settings/, () => Promise.reject(new Error("settings down"))],
      listRoute([]),
    ]);

    renderPage();
    expect(await screen.findByText("待办")).toBeInTheDocument();
    await waitFor(() => {
      const doneCall = fetchMock.mock.calls.find(([u]) =>
        String(u).includes("status=done"),
      );
      expect(doneCall).toBeTruthy();
      expect(String(doneCall![0])).toContain("size=50");
    });
  });

  it("passes the q search param to task queries and shows a search chip", async () => {
    const taskUrls: string[] = [];
    stubFetch([
      settingsRoute,
      [
        /\/api\/v1\/tasks\?/,
        (url) => {
          taskUrls.push(url);
          return jsonResponse({
            data: { items: [], total: 0, page: 1, size: 20 },
          });
        },
      ],
    ]);

    renderPage(["/kanban?q=gpt"]);

    await waitFor(() => {
      expect(taskUrls.some((u) => u.includes("q=gpt"))).toBe(true);
    });
    expect(await screen.findByText("搜索：gpt")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /清除/ }));

    await waitFor(() => {
      expect(screen.queryByText("搜索：gpt")).not.toBeInTheDocument();
    });
  });
});
