import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"

import { ScoreBadge } from "./ScoreBadge"

describe("ScoreBadge", () => {
  it("renders the rounded score", () => {
    render(<ScoreBadge score={8.6} />)
    expect(screen.getByTestId("score-badge")).toHaveTextContent("9")
  })

  it("grades scores >= 8 green", () => {
    render(<ScoreBadge score={8} />)
    expect(screen.getByTestId("score-badge").className).toContain("bg-green-100")
  })

  it("grades scores 6-7 blue", () => {
    render(<ScoreBadge score={7} />)
    expect(screen.getByTestId("score-badge").className).toContain("bg-blue-100")
  })

  it("grades scores < 6 gray, including 0", () => {
    const { rerender } = render(<ScoreBadge score={5} />)
    expect(screen.getByTestId("score-badge").className).toContain("bg-gray-100")
    rerender(<ScoreBadge score={0} />)
    expect(screen.getByTestId("score-badge").className).toContain("bg-gray-100")
  })
})
