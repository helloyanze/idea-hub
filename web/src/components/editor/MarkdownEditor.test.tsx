import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MarkdownEditor } from "@/components/editor/MarkdownEditor";

const versions = [
  { id: 2, task_id: 1, version: 2, filename: "output.md", created_at: "2026-08-02 10:00:00" },
  { id: 1, task_id: 1, version: 1, filename: "output.md", created_at: "2026-08-01 10:00:00" },
];

function renderEditor(overrides: Partial<Parameters<typeof MarkdownEditor>[0]> = {}) {
  const props = {
    content: "# 标题\n\n正文内容",
    baseVersion: 2,
    filename: "output.md",
    versions,
    onSave: vi.fn(),
    onViewVersion: vi.fn(),
    saving: false,
    ...overrides,
  };
  return {
    props,
    ...render(<MarkdownEditor {...props} />),
  };
}

describe("MarkdownEditor", () => {
  it("renders a textarea with the current content", () => {
    renderEditor();
    const textarea = screen.getByLabelText("产物内容") as HTMLTextAreaElement;
    expect(textarea).toBeInTheDocument();
    expect(textarea.value).toBe("# 标题\n\n正文内容");
  });

  it("switches to preview and renders markdown", async () => {
    renderEditor();
    await userEvent.click(screen.getByRole("button", { name: /预览/ }));
    expect(screen.queryByLabelText("产物内容")).not.toBeInTheDocument();
    expect(document.querySelector("h1")).toHaveTextContent("标题");
  });

  it("saves with content and base version", async () => {
    const onSave = vi.fn();
    renderEditor({ onSave });
    const textarea = screen.getByLabelText("产物内容");
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "新内容");
    await userEvent.click(screen.getByRole("button", { name: /保存/ }));
    expect(onSave).toHaveBeenCalledWith("新内容", 2);
  });

  it("renders the version selector and notifies on change", async () => {
    const onViewVersion = vi.fn();
    renderEditor({ onViewVersion });
    const select = screen.getByLabelText("版本选择") as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    await userEvent.selectOptions(select, "1");
    expect(onViewVersion).toHaveBeenCalledWith(1);
  });

  it("disables the save button while saving", () => {
    renderEditor({ saving: true });
    expect(screen.getByRole("button", { name: /保存/ })).toBeDisabled();
  });
});
