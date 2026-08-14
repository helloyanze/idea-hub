import { Children, type ReactNode } from "react"
import { useDroppable } from "@dnd-kit/core"

import { Button } from "@/components/ui/button"
import { STATUS_COLORS } from "@/components/kanban/TaskCard"

interface KanbanColumnProps {
  status: string
  title: string
  count: number
  children: ReactNode
  onLoadMore?: () => void
  hasMore?: boolean
  loadingMore?: boolean
}

function KanbanColumn({
  status,
  title,
  count,
  children,
  onLoadMore,
  hasMore = false,
  loadingMore = false,
}: KanbanColumnProps) {
  const { setNodeRef, isOver } = useDroppable({ id: "column-" + status })
  const empty = Children.count(children) === 0

  return (
    <section
      ref={setNodeRef}
      data-testid={"kanban-column-" + status}
      className={
        "flex min-h-64 flex-col rounded-xl border bg-muted/30 p-3 transition-colors " +
        (isOver ? "border-primary/60 bg-primary/5 ring-2 ring-primary/20" : "")
      }
    >
      <h3 className="mb-3 flex items-center gap-2 font-semibold">
        <span
          aria-hidden
          className={"h-2 w-2 shrink-0 rounded-full " + (STATUS_COLORS[status] ?? "bg-slate-400")}
        />
        <span className="min-w-0 flex-1 truncate">{title}</span>
        <span className="rounded-full bg-background px-2 py-0.5 text-xs font-medium text-muted-foreground">
          {count}
        </span>
      </h3>
      {empty ? (
        <div className="flex flex-1 items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/20 p-4 text-sm text-muted-foreground">
          暂无任务
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-3">{children}</div>
      )}
      {hasMore ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="mt-3 w-full"
          onClick={onLoadMore}
          disabled={loadingMore}
        >
          加载更多
        </Button>
      ) : null}
    </section>
  )
}

export { KanbanColumn }
