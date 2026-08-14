import { describe, expect, it } from "vitest"

import { formatRelativeTime } from "./TaskCard"

function isoSeconds(minutesAgo: number): string {
  const t = new Date(Date.now() - minutesAgo * 60 * 1000)
  const p = (n: number) => String(n).padStart(2, "0")
  return (
    `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ` +
    `${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`
  )
}

describe("formatRelativeTime", () => {
  it("returns empty for missing or invalid timestamps", () => {
    expect(formatRelativeTime(undefined)).toBe("")
    expect(formatRelativeTime("not-a-date")).toBe("")
  })

  it("formats minutes ago", () => {
    expect(formatRelativeTime(isoSeconds(2))).toMatch(/分钟前/)
  })

  it("formats hours ago", () => {
    expect(formatRelativeTime(isoSeconds(5 * 60))).toMatch(/小时前/)
  })

  it("formats days ago and falls back to the date for old timestamps", () => {
    expect(formatRelativeTime(isoSeconds(3 * 24 * 60))).toMatch(/天前/)
    const old = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000)
    const s = old.toISOString().slice(0, 10) + " " + old.toISOString().slice(11, 19)
    expect(formatRelativeTime(s)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})
