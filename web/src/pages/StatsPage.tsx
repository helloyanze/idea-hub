import { useQuery } from "@tanstack/react-query"

import { apiFetch } from "@/api/client"
import { SchedulerHealthBar } from "@/components/SchedulerHealthBar"

interface StatsData {
  queue: {
    todo: number
    waiting: number
    in_progress: number
    done: number
  }
  hotspots: {
    total: number
    admit: number
    discard: number
  }
  tokens: {
    execution_total: number
    generation_total: number
  }
  today_produced: number
  active_jobs: number
  scheduler: {
    last_tick: string | null
  }
}

interface TrendItem {
  date: string
  hotspots: number
  tasks: number
  outputs: number
}

interface TrendsData {
  items: TrendItem[]
}

function StatCard({
  label,
  value,
  testId,
}: {
  label: string
  value: number
  testId: string
}) {
  return (
    <div data-testid={testId} className="rounded-xl border p-4">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="mt-2 text-2xl font-semibold">{value}</p>
    </div>
  )
}

function StatsPage() {
  const statsQuery = useQuery({
    queryKey: ["stats"],
    queryFn: () => apiFetch<StatsData>("/api/v1/stats"),
  })
  const trendsQuery = useQuery({
    queryKey: ["stats", "trends", 7],
    queryFn: () => apiFetch<TrendsData>("/api/v1/stats/trends?days=7"),
  })

  const stats = statsQuery.data
  const trendItems = trendsQuery.data?.items ?? []
  const maxCount = Math.max(
    1,
    ...trendItems.map(
      (item) => item.hotspots + item.tasks + item.outputs,
    ),
  )

  return (
    <div className="space-y-6 p-4 md:p-6">
      <h2 className="text-xl font-semibold">统计中心</h2>

      {statsQuery.isPending || trendsQuery.isPending ? (
        <p className="text-sm text-muted-foreground">加载中...</p>
      ) : null}

      {statsQuery.isError || trendsQuery.isError ? (
        <p className="text-sm text-destructive">加载统计失败</p>
      ) : null}

      {stats ? (
        <>
          <SchedulerHealthBar lastTick={stats.scheduler.last_tick} />

          <section className="space-y-3">
            <h3 className="text-lg font-semibold">队列</h3>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard
                testId="queue-todo"
                label="todo"
                value={stats.queue.todo}
              />
              <StatCard
                testId="queue-waiting"
                label="waiting"
                value={stats.queue.waiting}
              />
              <StatCard
                testId="queue-in_progress"
                label="in_progress"
                value={stats.queue.in_progress}
              />
              <StatCard
                testId="queue-done"
                label="done"
                value={stats.queue.done}
              />
            </div>
          </section>

          <section className="space-y-3">
            <h3 className="text-lg font-semibold">热点</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <StatCard
                testId="hotspot-admit"
                label="采纳"
                value={stats.hotspots.admit}
              />
              <StatCard
                testId="hotspot-discard"
                label="丢弃"
                value={stats.hotspots.discard}
              />
            </div>
          </section>

          <section className="space-y-3">
            <h3 className="text-lg font-semibold">Tokens</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <StatCard
                testId="token-execution"
                label="执行消耗"
                value={stats.tokens.execution_total}
              />
              <StatCard
                testId="token-generation"
                label="生成评分"
                value={stats.tokens.generation_total}
              />
            </div>
          </section>

          <section className="grid gap-3 sm:grid-cols-2">
            <StatCard
              testId="today-produced"
              label="今日产出"
              value={stats.today_produced}
            />
            <StatCard
              testId="active-jobs"
              label="活跃任务"
              value={stats.active_jobs}
            />
          </section>
        </>
      ) : null}

      {!trendsQuery.isPending && !trendsQuery.isError ? (
        <section className="space-y-3">
          <h3 className="text-lg font-semibold">近 7 天趋势</h3>
          {trendItems.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无趋势数据</p>
          ) : (
            <div className="flex items-end gap-3 overflow-x-auto pb-2">
              {trendItems.map((item) => {
                const total = item.hotspots + item.tasks + item.outputs
                const height =
                  Math.max(8, Math.round((total / maxCount) * 120)) + "px"

                return (
                  <div
                    key={item.date}
                    className="flex min-w-16 flex-col items-center justify-end gap-2"
                  >
                    <div
                      data-testid="trend-bar"
                      data-date={item.date}
                      data-count={String(total)}
                      title={
                        item.date +
                        " 热点" +
                        item.hotspots +
                        " 任务" +
                        item.tasks +
                        " 产出" +
                        item.outputs
                      }
                      className="w-8 rounded-t-md bg-blue-500"
                      style={{ height }}
                    />
                    <span className="text-xs text-muted-foreground">
                      {item.date}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </section>
      ) : null}
    </div>
  )
}

export { StatsPage }
