import { useEffect, useState } from "react"
import { useInfiniteQuery } from "@tanstack/react-query"

import { apiFetch } from "@/api/client"
import { SearchBox } from "@/components/SearchBox"
import { ScoreBadge } from "@/components/score/ScoreBadge"

interface Hotspot {
  id: number
  title: string
  url: string
  final_score: number
  score_breakdown: Record<string, number> | null
  verdict: string | null
  source_name: string
  collected_date: string
  linked_task_count: number
}

interface HotspotPageData {
  items: Hotspot[]
  total: number
  page: number
  size: number
}

function verdictClasses(verdict: string | null): string {
  if (verdict === "admit") {
    return "bg-green-100 text-green-800"
  }

  return "bg-gray-100 text-gray-700"
}

function HotspotsPage() {
  const [verdict, setVerdict] = useState("")
  const [search, setSearch] = useState("")
  const [q, setQ] = useState("")

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setQ(search.trim())
    }, 500)

    return () => window.clearTimeout(timeoutId)
  }, [search])

  const hotspotsQuery = useInfiniteQuery({
    queryKey: ["hotspots", verdict, q],
    queryFn: ({ pageParam }) => {
      const query = new URLSearchParams({
        page: String(pageParam),
        size: "20",
      })

      if (q) {
        query.set("q", q)
      }
      if (verdict) {
        query.set("verdict", verdict)
      }

      return apiFetch<HotspotPageData>(`/api/v1/hotspots?${query.toString()}`)
    },
    initialPageParam: 1,
    getNextPageParam: (lastPage) =>
      lastPage.items.length < lastPage.total
        ? lastPage.page + 1
        : undefined,
  })

  const items = hotspotsQuery.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <div className="space-y-5 p-4 md:p-6">
      <div className="space-y-3">
        <h2 className="text-xl font-semibold">热点流</h2>
        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="min-w-0 flex-1">
            <SearchBox
              label="搜索热点"
              placeholder="搜索热点..."
              value={search}
              onChange={setSearch}
              onSearch={(value) => setQ(value.trim())}
            />
          </div>
          <select
            aria-label="按判定筛选"
            value={verdict}
            onChange={(event) => setVerdict(event.target.value)}
            className="h-9 rounded-md border bg-background px-3 text-sm"
          >
            <option value="">全部</option>
            <option value="admit">admit</option>
            <option value="discard">discard</option>
          </select>
        </div>
      </div>

      {hotspotsQuery.isPending ? (
        <p className="text-sm text-muted-foreground">加载中...</p>
      ) : null}

      {hotspotsQuery.isError ? (
        <p className="text-sm text-destructive">加载热点失败</p>
      ) : null}

      {!hotspotsQuery.isPending && !hotspotsQuery.isError && items.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无热点</p>
      ) : null}

      {items.length > 0 ? (
        <div className="space-y-3">
          {items.map((item) => (
            <article
              key={item.id}
              className="space-y-3 rounded-xl border bg-card p-4 text-card-foreground shadow-sm"
            >
              <div className="flex items-start justify-between gap-4">
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-primary hover:underline"
                >
                  {item.title}
                </a>
                <ScoreBadge score={item.final_score} />
              </div>
              <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <span>{item.source_name}</span>
                <span
                  data-testid="verdict-badge"
                  className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${verdictClasses(item.verdict)}`}
                >
                  {item.verdict ?? "未判定"}
                </span>
                <span>{item.collected_date}</span>
                {item.linked_task_count > 0 ? (
                  <span>已生成 {item.linked_task_count} 任务</span>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      ) : null}

      {hotspotsQuery.hasNextPage ? (
        <button
          type="button"
          onClick={() => {
            void hotspotsQuery.fetchNextPage()
          }}
          disabled={hotspotsQuery.isFetchingNextPage}
          className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
        >
          {hotspotsQuery.isFetchingNextPage ? "加载中..." : "加载更多"}
        </button>
      ) : null}
    </div>
  )
}

export { HotspotsPage }
