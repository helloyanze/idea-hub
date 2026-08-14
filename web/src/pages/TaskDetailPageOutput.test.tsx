import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TaskDetailPage } from "@/pages/TaskDetailPage";
import { clearCredentials } from "@/lib/auth";

const versions = [
  { id: 2, task_id: 1, version: 2, filename: "output.md", created_at: "2026-08-02 10:00:00" },
  { id: 1, task_id: 1, version: 1, filename: "output.md", created_at: "2026-08-01 10:00:00" },
];

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Route {
  pattern: RegExp;
  respond: (url: string, init?: RequestInit) => Response | Promise<Response>;
}

function stubFetch(routes: Route[]) {
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const u = String(input);
    for (const { pattern, respond } of routes) {
      if (pattern.test(u)) {
        return Promise.resolve(respond(u, init));
      }
    }
    return Promise.resolve(jsonResponse({ data: null }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function taskDetail(hasOutput = true) {
  return {
    data: {
      id: 1,
      title: "深度解析 AI 芯片",
      status: "done",
      content_type: "article",
      feasibility_score: 8,
      score_breakdown: null,
      tags: [],
      idea_summary: "摘要",
      target_desc: "",
      token_used: 10,
      fail_count: 0,
      created_at: "2026-08-01 10:00:00",
      expire_at: null,
      notes: "",
      ai_summary: "",
      last_fail_reason: null,
      redo_note: null,
      updated_at: "2026-08-02 10:00:00",
      completed_at: "2026-08-02 10:00:00",
      hotspots: [],
      output: hasOutput
        ? { has_output: true, latest_version: 2, version_count: 2, ai_summary: "" }
        : { has_output: false, latest_version: null, version_count: 0, ai_summary: "" },
    },
  };
}

function outputBody(version = 2, content = "## 最新版内容") {
  return {
    data: {
      id: version,
      task_id: 1,
      version,
      content,
      filename: "output.md",
      ai_summary: "",
      created_at: "2026-08-02 10:00:00",
    },
  };
}

const settingsRoute: Route = {
  pattern: /\/api\/v1\/settings/,
  respond: () => jsonResponse({ data: { score_dimensions: ["facts"] } }),
};

const detailRoute: Route = {
  pattern: /\/api\/v1\/tasks\/1(?:\?|$)/,
  respond: () => jsonResponse(taskDetail(true)),
};

function baseRoutes(overrides: { output?: () => Response; versions?: () => Response } = {}) {
  return [
    settingsRoute,
    detailRoute,
    {
      pattern: /\/api\/v1\/tasks\/1\/output\/versions\/\d+$/,
      respond: (url: string) => {
        const v = Number(url.split("/").pop());
        return jsonResponse(outputBody(v, `## 版本${v}内容`));
      },
    },
    {
      pattern: /\/api\/v1\/tasks\/1\/output\/versions$/,
      respond: () => jsonResponse({ data: { items: versions } }),
    },
    {
      pattern: /\/api\/v1\/tasks\/1\/output(?:\?|$)/,
      respond: () => (overrides.output ? overrides.output() : jsonResponse(outputBody())),
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

describe("TaskDetailPage output section", () => {
  it("shows 未生成 when the task has no output", async () => {
    stubFetch([
      settingsRoute,
      {
        pattern: /\/api\/v1\/tasks\/1(?:[?/]|$)/,
        respond: () => jsonResponse(taskDetail(false)),
      },
    ]);
    renderPage();
    expect(await screen.findByText("未生成")).toBeInTheDocument();
  });

  it("renders the editor with latest content and the version list", async () => {
    stubFetch(baseRoutes());
    renderPage();

    const textarea = (await screen.findByLabelText("产物内容")) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.value).toBe("## 最新版内容"));
    expect(screen.getByLabelText("版本选择")).toBeInTheDocument();
    // version history list rendered
    expect(await screen.findByText(/2026-08-02 10:00:00/)).toBeInTheDocument();
    expect(screen.getAllByText(/2026-08-01 10:00:00/).length).toBeGreaterThan(0);
  });

  it("saves with content and base_version via PUT", async () => {
    const fetchMock = stubFetch([
      ...baseRoutes(),
      {
        pattern: /\/api\/v1\/tasks\/1\/output(?:\?|$)/,
        respond: (url, init) => {
          void url;
          if ((init?.method ?? "GET") === "PUT") {
            return jsonResponse(outputBody(3, "保存后的内容"));
          }
          return jsonResponse(outputBody());
        },
      },
    ]);
    renderPage();

    const textarea = (await screen.findByLabelText("产物内容")) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.value).toBe("## 最新版内容"));
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "保存后的内容");
    await userEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(
        ([u, init]) => String(u).includes("/output") && (init?.method ?? "GET") === "PUT",
      );
      expect(putCall).toBeTruthy();
      expect(JSON.parse(String((putCall![1] as RequestInit).body))).toEqual({
        content: "保存后的内容",
        base_version: 2,
      });
    });
  });

  it("handles VERSION_CONFLICT by reloading the latest version", async () => {
    let outputCalls = 0;
    const fetchMock = stubFetch([
      settingsRoute,
      detailRoute,
      {
        pattern: /\/api\/v1\/tasks\/1\/output\/versions\/\d+$/,
        respond: () => jsonResponse(outputBody(2, "## 最新版内容")),
      },
      {
        pattern: /\/api\/v1\/tasks\/1\/output\/versions$/,
        respond: () => jsonResponse({ data: { items: versions } }),
      },
      {
        pattern: /\/api\/v1\/tasks\/1\/output(?:\?|$)/,
        respond: (url, init) => {
          void url;
          if ((init?.method ?? "GET") === "PUT") {
            return jsonResponse(
              {
                error: { code: "VERSION_CONFLICT", message: "Version conflict" },
              },
              409,
            );
          }
          outputCalls += 1;
          // initial load serves v2 with the pre-edit content; the
          // post-conflict reload serves a NEWER version with DIFFERENT
          // content — identical content would be skipped by React's state
          // bail-out and the editor would never refresh
          return jsonResponse(
            outputBody(
              outputCalls === 1 ? 2 : 3,
              outputCalls === 1 ? "## 初始内容" : "## 已被更新的内容",
            ),
          );
        },
      },
    ]);
    renderPage();

    const textarea = (await screen.findByLabelText("产物内容")) as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.value).toBe("## 初始内容"));
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "过期编辑");
    await userEvent.click(screen.getByRole("button", { name: /保存/ }));

    expect(await screen.findByText(/内容已被更新，重新加载最新版/)).toBeInTheDocument();
    await waitFor(() => {
      expect(outputCalls).toBeGreaterThanOrEqual(2);
      const textareaAfter = screen.getByLabelText("产物内容") as HTMLTextAreaElement;
      expect(textareaAfter.value).toBe("## 已被更新的内容");
    });
    expect(fetchMock).toBeTruthy();
  });

  it("uploads a file with its original filename", async () => {
    const fetchMock = stubFetch([
      ...baseRoutes(),
      {
        pattern: /\/api\/v1\/tasks\/1\/output\/upload/,
        respond: (url, init) => {
          void url;
          if ((init?.method ?? "POST") === "POST") {
            return jsonResponse({ data: { version: 3, filename: "draft.md" } });
          }
          return jsonResponse({ data: null });
        },
      },
    ]);
    renderPage();
    await screen.findByLabelText("产物内容");
    // jsdom lacks Blob.text() — define it so the component's file.text() works.
    // (vi.spyOn throws: the property does not exist on the prototype.)
    Object.defineProperty(File.prototype, "text", {
      configurable: true,
      value: vi.fn().mockResolvedValue("上传的文件内容"),
    });

    const file = new File(["上传的文件内容"], "draft.md", { type: "text/markdown" });
    const input = screen.getByLabelText("上传文件") as HTMLInputElement;
    await userEvent.upload(input, file);

    await waitFor(() => {
      const uploadCall = fetchMock.mock.calls.find(([u]) =>
        String(u).includes("/output/upload"),
      );
      expect(uploadCall).toBeTruthy();
      expect(JSON.parse(String((uploadCall![1] as RequestInit).body))).toEqual({
        filename: "draft.md",
        content: "上传的文件内容",
      });
    });
  });

  it("downloads the latest output as a blob with the stored filename", async () => {
    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    stubFetch(baseRoutes());
    renderPage();
    await screen.findByLabelText("产物内容");

    const downloadLink = screen.getByRole("link", { name: /下载/ });
    expect(downloadLink).toHaveAttribute("download", "output.md");
    expect(downloadLink).toHaveAttribute("href", "blob:mock-url");
    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("views an older version from the selector", async () => {
    stubFetch(baseRoutes());
    renderPage();

    const textarea = (await screen.findByLabelText("产物内容")) as HTMLTextAreaElement;
    await userEvent.selectOptions(screen.getByLabelText("版本选择"), "1");
    await waitFor(() => {
      expect((screen.getByLabelText("产物内容") as HTMLTextAreaElement).value).toBe(
        "## 版本1内容",
      );
    });
    expect(textarea).toBeTruthy();
  });
});
