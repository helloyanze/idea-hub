import type { ComponentProps } from "react"
import { useState } from "react"
import { DndContext } from "@dnd-kit/core"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useNavigate, useSearchParams } from "react-router-dom"

import { apiFetch } from "@/api/client"
import { KanbanColumn } from "@/components/kanban/KanbanColumn"
import {
  TaskCard,
  type TaskListItem,
} from "@/components/kanban/TaskCard"

interface TaskPage {
  items: TaskListItem[]
  total: number
  page: number
  size: number
}

interface Settings {
  done_column_limit?: number
}

interface MoveVariables {
  id: number
  toStatus: string
}

const COLUMNS = [
  { status: "todo", title: "待办" },
  { status: "waiting", title: "等待中" },
  { status: "in_progress", title: "进行中" },
  { status: "done", title: "已完成" },
] as const

function canDrop(fromStatus: string, toStatus: string): boolean {
  if (fromStatus === toStatus) {
    return false
  }

  return !(fromStatus === "done" && toStatus === "in_progress")
}

function useKanbanQuery(
  status: string,
  size: number,
  q: string | undefined,
  enabled: boolean,
) {
  return useInfiniteQuery({
    queryKey: ["kanban", status, size, q],
    queryFn: ({ pageParam }) =>
      apiFetch<TaskPage>(
        `/api/v1/tasks?status=${status}&page=${pageParam}&size=${size}${
          q ? `&q=${encodeURIComponent(q)}` : ""
        }`,
      ),
    initialPageParam: 1,
    getNextPageParam: (lastPage) =>
      lastPage.items.length < lastPage.total
        ? lastPage.page + 1
        : undefined,
    enabled,
  })
}

function KanbanPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const q = searchParams.get("q") ?? undefined
  const [moveError, setMoveError] = useState<string | null>(null)

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () =>
      apiFetch<Settings>("/api/v1/settings"),
  })
  const doneLimit = settingsQuery.data?.done_column_limit ?? 50
  const queriesEnabled = !settingsQuery.isPending

  const todoQuery = useKanbanQuery("todo", 20, q, queriesEnabled)
  const waitingQuery = useKanbanQuery("waiting", 20, q, queriesEnabled)
  const inProgressQuery = useKanbanQuery("in_progress", 20, q, queriesEnabled)
  const doneQuery = useKanbanQuery("done", doneLimit, q, queriesEnabled)

  const todoItems = todoQuery.data?.pages.flatMap((page) => page.items) ?? []
  const waitingItems =
    waitingQuery.data?.pages.flatMap((page) => page.items) ?? []
  const inProgressItems =
    inProgressQuery.data?.pages.flatMap((page) => page.items) ?? []
  const doneItems = doneQuery.data?.pages.flatMap((page) => page.items) ?? []

  const columnData = [
    { ...COLUMNS[0], items: todoItems },
    { ...COLUMNS[1], items: waitingItems },
    { ...COLUMNS[2], items: inProgressItems },
    { ...COLUMNS[3], items: doneItems },
  ]
  const totalCount = columnData.reduce((sum, column) => sum + column.items.length, 0)

  const moveMutation = useMutation({
    mutationFn: ({ id, toStatus }: MoveVariables) =>
      apiFetch(`/api/v1/tasks/${id}/move`, {
        method: "POST",
        body: JSON.stringify({ to_status: toStatus }),
      }),
    onSuccess: () => {
      setMoveError(null)
      void queryClient.invalidateQueries({ queryKey: ["kanban"] })
    },
    onError: (error: Error) => {
      setMoveError(error.message)
      void queryClient.invalidateQueries({ queryKey: ["kanban"] })
    },
  })

  type DragEndHandler = NonNullable<
    ComponentProps<typeof DndContext>["onDragEnd"]
  >

  const handleDragEnd: DragEndHandler = (event) => {
    if (!event.over) {
      return
    }

    const activeId = String(event.active.id)
    const overId = String(event.over.id)
    if (!activeId.startsWith("task-") || !overId.startsWith("column-")) {
      return
    }

    const taskId = Number(activeId.slice("task-".length))
    const toStatus = overId.slice("column-".length)
    const task = columnData
      .flatMap((column) => column.items)
      .find((item) => item.id === taskId)

    if (!task || !canDrop(task.status, toStatus)) {
      return
    }

    setMoveError(null)
    moveMutation.mutate({ id: taskId, toStatus })
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-xl font-semibold">任务看板</h2>
          <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
            共 {totalCount} 个任务
          </span>
        </div>
        {q ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>搜索：{q}</span>
            <button
              type="button"
              onClick={() => navigate("/kanban")}
              className="rounded-md border px-2 py-1 text-xs hover:bg-accent"
            >
              清除
            </button>
          </div>
        ) : null}
        {moveError ? (
          <p data-testid="kanban-move-error" className="text-destructive">
            {moveError}
          </p>
        ) : null}
      </div>

      <DndContext onDragEnd={handleDragEnd}>
        <div className="grid grid-cols-1 gap-4 lg:gap-5 xl:grid-cols-4 xl:gap-6">
          {columnData.map((column) => (
            <KanbanColumn
              key={column.status}
              status={column.status}
              title={column.title}
              count={column.items.length}
              onLoadMore={
                column.status === "done"
                  ? () => {
                      void doneQuery.fetchNextPage()
                    }
                  : undefined
              }
              hasMore={
                column.status === "done" ? doneQuery.hasNextPage : undefined
              }
              loadingMore={
                column.status === "done"
                  ? doneQuery.isFetchingNextPage
                  : undefined
              }
            >
              {column.items.map((task) => (
                <TaskCard key={task.id} task={task} />
              ))}
            </KanbanColumn>
          ))}
        </div>
      </DndContext>
    </div>
  )
}

export { COLUMNS, KanbanPage, canDrop }
