import { useState } from "react"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { Link } from "react-router-dom"

import { apiFetch } from "@/api/client"

interface NotificationItem {
  id: number
  type: string
  title: string
  body: string
  level: "info" | "warn" | "error"
  entity_type: string | null
  entity_id: number | null
  is_read: boolean
  created_at: string
}

interface NotificationPageData {
  items: NotificationItem[]
  total: number
  page: number
  size: number
}

interface UnreadCountData {
  count: number
}

interface MarkReadData {
  id: number
  is_read: true
}

interface MarkAllReadData {
  updated: number
}

function levelClasses(level: NotificationItem["level"]): string {
  if (level === "warn") {
    return "bg-yellow-100 text-yellow-800"
  }

  if (level === "error") {
    return "bg-red-100 text-red-800"
  }

  return "bg-blue-100 text-blue-800"
}

function NotificationsPage() {
  const [level, setLevel] = useState("")
  const [type, setType] = useState("")
  const queryClient = useQueryClient()

  const unreadCountQuery = useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: () =>
      apiFetch<UnreadCountData>("/api/v1/notifications/unread-count"),
    refetchInterval: 30000,
  })

  const notificationsQuery = useInfiniteQuery({
    queryKey: ["notifications", level, type],
    queryFn: ({ pageParam }) => {
      const query = new URLSearchParams({
        page: String(pageParam),
        size: "20",
      })

      if (level) {
        query.set("level", level)
      }
      if (type) {
        query.set("type", type)
      }

      return apiFetch<NotificationPageData>(
        "/api/v1/notifications?" + query.toString(),
      )
    },
    initialPageParam: 1,
    getNextPageParam: (lastPage) =>
      lastPage.items.length < lastPage.total
        ? lastPage.page + 1
        : undefined,
  })

  const markReadMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch<MarkReadData>("/api/v1/notifications/" + id + "/read", {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] })
    },
  })

  const markAllReadMutation = useMutation({
    mutationFn: () =>
      apiFetch<MarkAllReadData>("/api/v1/notifications/read-all", {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] })
    },
  })

  const items =
    notificationsQuery.data?.pages.flatMap((page) => page.items) ?? []
  const unreadCount = unreadCountQuery.data?.count

  return (
    <div className="space-y-5 p-4 md:p-6">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-xl font-semibold">通知中心</h2>
          <button
            type="button"
            onClick={() => markAllReadMutation.mutate()}
            disabled={markAllReadMutation.isPending}
            className="rounded-md border px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
          >
            全部已读
          </button>
        </div>

        {typeof unreadCount === "number" ? (
          <p className="text-sm text-muted-foreground">
            未读 {unreadCount} 条
          </p>
        ) : null}

        <div className="flex flex-col gap-3 sm:flex-row">
          <select
            aria-label="按级别筛选"
            value={level}
            onChange={(event) => setLevel(event.target.value)}
            className="h-9 rounded-md border bg-background px-3 text-sm"
          >
            <option value="">全部</option>
            <option value="info">info</option>
            <option value="warn">warn</option>
            <option value="error">error</option>
          </select>
          <select
            aria-label="按类型筛选"
            value={type}
            onChange={(event) => setType(event.target.value)}
            className="h-9 rounded-md border bg-background px-3 text-sm"
          >
            <option value="">全部</option>
            <option value="collect_done">collect_done</option>
            <option value="generate_done">generate_done</option>
            <option value="execute_done">execute_done</option>
            <option value="job_failed">job_failed</option>
            <option value="task_expired">task_expired</option>
            <option value="budget_exceeded">budget_exceeded</option>
            <option value="discard_cleaned">discard_cleaned</option>
          </select>
        </div>
      </div>

      {notificationsQuery.isPending ? (
        <p className="text-sm text-muted-foreground">加载中...</p>
      ) : null}

      {notificationsQuery.isError ? (
        <p className="text-sm text-destructive">加载通知失败</p>
      ) : null}

      {!notificationsQuery.isPending &&
      !notificationsQuery.isError &&
      items.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无通知</p>
      ) : null}

      {items.length > 0 ? (
        <div className="space-y-3">
          {items.map((item) => (
            <article
              key={item.id}
              className="space-y-3 rounded-xl border bg-card p-4 text-card-foreground shadow-sm"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span
                  data-testid="level-badge"
                  className={
                    "inline-flex rounded-full px-2 py-0.5 text-xs font-medium " +
                    levelClasses(item.level)
                  }
                >
                  {item.level}
                </span>
                <span className="text-xs text-muted-foreground">
                  {item.type}
                </span>
              </div>

              <div className="space-y-1">
                {item.entity_type === "task" &&
                typeof item.entity_id === "number" ? (
                  <Link
                    to={"/tasks/" + item.entity_id}
                    className="font-medium text-primary hover:underline"
                  >
                    {item.title}
                  </Link>
                ) : (
                  <span className="font-medium">{item.title}</span>
                )}
                <p className="text-sm">{item.body}</p>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">
                  {item.created_at}
                </span>
                {item.is_read ? (
                  <span className="text-xs text-muted-foreground">已读</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => markReadMutation.mutate(item.id)}
                    disabled={
                      markReadMutation.isPending &&
                      markReadMutation.variables === item.id
                    }
                    className="rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-accent disabled:opacity-50"
                  >
                    标已读
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : null}

      {notificationsQuery.hasNextPage ? (
        <button
          type="button"
          onClick={() => {
            void notificationsQuery.fetchNextPage()
          }}
          disabled={notificationsQuery.isFetchingNextPage}
          className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
        >
          {notificationsQuery.isFetchingNextPage ? "加载中..." : "加载更多"}
        </button>
      ) : null}
    </div>
  )
}

export { NotificationsPage }
