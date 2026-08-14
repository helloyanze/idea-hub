import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ScoreBadge } from "@/components/score/ScoreBadge";
import { ScoreBreakdown } from "@/components/score/ScoreBreakdown";

const dimensions = ["facts", "verification", "timeliness", "value"];

describe("ScoreBadge", () => {
  it("renders green for score >= 8", () => {
    render(<ScoreBadge score={8} />);
    const badge = screen.getByTestId("score-badge");
    expect(badge).toHaveTextContent("8");
    expect(badge.className).toContain("green");
    expect(badge.className).not.toContain("red");
  });

  it("renders yellow for score 6-7", () => {
    const { rerender } = render(<ScoreBadge score={7} />);
    const badge7 = screen.getByTestId("score-badge");
    expect(badge7.className).toContain("yellow");
    expect(badge7.className).not.toContain("green");

    rerender(<ScoreBadge score={6} />);
    const badge6 = screen.getByTestId("score-badge");
    expect(badge6.className).toContain("yellow");
    expect(badge6.className).not.toContain("red");
  });

  it("renders red for score < 6", () => {
    render(<ScoreBadge score={5} />);
    const badge = screen.getByTestId("score-badge");
    expect(badge).toHaveTextContent("5");
    expect(badge.className).toContain("red");
    expect(badge.className).not.toContain("green");
    expect(badge.className).not.toContain("yellow");
  });
});

describe("ScoreBreakdown", () => {
  it("renders each configured dimension with its score bar", () => {
    render(
      <ScoreBreakdown
        dimensions={dimensions}
        breakdown={{ facts: 8, verification: 7, timeliness: 9, value: 6 }}
        totalScore={8}
      />,
    );

    for (const dim of dimensions) {
      const row = screen.getByTestId(`score-dim-${dim}`);
      expect(row).toHaveTextContent(dim);
      const bar = within(row).getByRole("progressbar");
      expect(bar).toHaveAttribute("aria-valuemax", "10");
    }
    expect(
      within(screen.getByTestId("score-dim-facts")).getByRole("progressbar"),
    ).toHaveAttribute("aria-valuenow", "8");
    expect(
      within(screen.getByTestId("score-dim-value")).getByRole("progressbar"),
    ).toHaveAttribute("aria-valuenow", "6");
  });

  it("shows the total score", () => {
    render(
      <ScoreBreakdown
        dimensions={dimensions}
        breakdown={{ facts: 8, verification: 7, timeliness: 9, value: 6 }}
        totalScore={8}
      />,
    );
    expect(screen.getByTestId("score-total")).toHaveTextContent("8");
  });

  it("fills missing dimensions with 0", () => {
    render(
      <ScoreBreakdown
        dimensions={dimensions}
        breakdown={{ facts: 8 }}
        totalScore={8}
      />,
    );
    const row = screen.getByTestId("score-dim-verification");
    expect(within(row).getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "0",
    );
    expect(row).toHaveTextContent("0");
  });

  it("ignores extra dimensions not in the configured list", () => {
    render(
      <ScoreBreakdown
        dimensions={dimensions}
        breakdown={{ facts: 8, novelty: 9 }}
        totalScore={8}
      />,
    );
    expect(screen.queryByText("novelty")).not.toBeInTheDocument();
  });

  it("shows 未评分 when breakdown is empty", () => {
    render(<ScoreBreakdown dimensions={dimensions} breakdown={{}} totalScore={0} />);
    expect(screen.getByText("未评分")).toBeInTheDocument();
  });

  it("shows 未评分 when breakdown is null", () => {
    render(<ScoreBreakdown dimensions={dimensions} breakdown={null} totalScore={0} />);
    expect(screen.getByText("未评分")).toBeInTheDocument();
  });

  it("falls back to default dimensions when none are provided", () => {
    render(<ScoreBreakdown breakdown={{ facts: 8 }} totalScore={8} />);
    for (const dim of ["facts", "verification", "timeliness", "value"]) {
      expect(screen.getByTestId(`score-dim-${dim}`)).toBeInTheDocument();
    }
  });
});
