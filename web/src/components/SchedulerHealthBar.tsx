interface SchedulerHealthBarProps {
  lastTick: string | null
}

function parseLastTick(lastTick: string | null): Date | null {
  if (lastTick === null || lastTick.trim() === "") {
    return null
  }

  const normalized = lastTick
    .trim()
    .replace(/^(\d{4}-\d{2}-\d{2})\s+(?=\d{2}:\d{2})/, "$1T")
  const parsed = new Date(normalized)

  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function SchedulerHealthBar({ lastTick }: SchedulerHealthBarProps) {
  const parsed = parseLastTick(lastTick)

  if (parsed === null) {
    return (
      <div
        data-testid="scheduler-health"
        className="inline-flex items-center gap-2 rounded-full bg-red-100 px-3 py-1 text-sm font-medium text-red-800"
      >
        <span>调度器</span>
        <span>未运行</span>
      </div>
    )
  }

  const minutesAgo = Math.floor((Date.now() - parsed.getTime()) / 60000)

  if (minutesAgo > 10) {
    return (
      <div
        data-testid="scheduler-health"
        className="inline-flex items-center gap-2 rounded-full bg-red-100 px-3 py-1 text-sm font-medium text-red-800"
      >
        <span>调度器</span>
        <span>{"异常（" + minutesAgo + " 分钟未更新）"}</span>
      </div>
    )
  }

  return (
    <div
      data-testid="scheduler-health"
      className="inline-flex items-center gap-2 rounded-full bg-green-100 px-3 py-1 text-sm font-medium text-green-800"
    >
      <span>调度器</span>
      <span>
        {minutesAgo === 0 ? "运行中（刚刚）" : "运行中（" + minutesAgo + " 分钟前）"}
      </span>
    </div>
  )
}

export { SchedulerHealthBar }
