import type { ReactNode } from "react"
import { useDroppable } from "@dnd-kit/core"

import { Button } from "@/components/ui/button"

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
  const { setNodeRef } = useDroppable({ id: `column-${status}` })

  return (
    <section
      ref={setNodeRef}
      data-testid={`kanban-column-${status}`}
      className="flex min-h-64 flex-col rounded-xl border bg-muted/30 p-3"
    >
      <h3 className="mb-3 flex items-center justify-between gap-3 font-semibold">
        <span>{title}</span>
        <span className="rounded-full bg-background px-2 py-0.5 text-xs text-muted-foreground">
          {count}
        </span>
      </h3>
      <div className="flex flex-1 flex-col gap-3">{children}</div>
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
