import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { CREDENTIALS_KEY, setCredentials } from "@/lib/auth";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const okHealth = () => jsonResponse({ data: { status: "ok" } });

beforeEach(() => {
  localStorage.clear();
  document.documentElement.className = "";
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("App auth flow", () => {
  it("shows LoginForm when no credentials are stored", () => {
    render(<App />);
    expect(screen.getByLabelText(/用户名/)).toBeInTheDocument();
    expect(screen.getByLabelText(/密码/)).toBeInTheDocument();
  });

  it("stores credentials on login submit and shows the shell", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okHealth()));
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText(/用户名/), "alice");
    await user.type(screen.getByLabelText(/密码/), "s3cret");
    await user.click(screen.getByRole("button", { name: /登录/ }));

    expect(JSON.parse(localStorage.getItem(CREDENTIALS_KEY)!)).toEqual({
      user: "alice",
      pass: "s3cret",
    });
    expect(await screen.findByRole("link", { name: /看板/ })).toBeInTheDocument();
  });

  it("clears credentials and returns to login when health probe returns 401", async () => {
    setCredentials("alice", "s3cret");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { error: { code: "UNAUTHORIZED", message: "bad credentials" } },
          401,
        ),
      ),
    );

    render(<App />);

    expect(await screen.findByLabelText(/用户名/)).toBeInTheDocument();
    expect(localStorage.getItem(CREDENTIALS_KEY)).toBeNull();
  });

  it("toggles dark class on documentElement via ThemeToggle", async () => {
    setCredentials("alice", "s3cret");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okHealth()));
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("link", { name: /看板/ });

    expect(document.documentElement.classList.contains("dark")).toBe(false);
    await user.click(screen.getByRole("button", { name: /切换主题/ }));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
