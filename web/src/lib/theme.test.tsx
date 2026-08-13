import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { THEME_KEY, ThemeProvider, useTheme } from "./theme";

function ToggleProbe() {
  const { theme, setTheme } = useTheme();
  return (
    <button
      type="button"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
    >
      toggle:{theme}
    </button>
  );
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.className = "";
});

describe("ThemeProvider", () => {
  it("applies dark class and persists theme to localStorage on manual toggle", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <ToggleProbe />
      </ThemeProvider>,
    );

    await user.click(screen.getByRole("button", { name: /toggle:/ }));

    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
  });
});
