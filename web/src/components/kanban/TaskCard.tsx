import { useDraggable } from "@dnd-kit/core"
import { Link } from "react-router-dom"

import { ScoreBadge } from "@/components/score/ScoreBadge"

interface TaskTag {
  id: number
  name: string
  color?: string
}

interface TaskListItem {
  id: number
  title: string
  status: string
  feasibility_score: number
  tags: TaskTag[]
  created_at?: string
}

const STATUS_COLORS: Record<string, string> = {
  todo: "bg-slate-400",
  waiting: "bg-amber-400",
  in_progress: "bg-blue-500",
  done: "bg-green-500",
}

function formatRelativeTime(createdAt?: string): string {
  if (!createdAt) {
    return ""
  }
  const time = new Date(createdAt.replace(" ", "T")).getTime()
  if (Number.isNaN(time)) {
    return ""
  }
  const diffMs = Date.now() - time
  if (diffMs < 60 * 1000) {
    return "刚刚"
  }
  const minutes = Math.floor(diffMs / (60 * 1000))
  if (minutes < 60) {
    return minutes + "分钟前"
  }
  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    return hours + "小时前"
  }
  const days = Math.floor(hours / 24)
  if (days < 7) {
    return days + "天前"
  }
  return createdAt.slice(0, 10)
}

interface TaskCardProps {
  task: TaskListItem
}

function TaskCard({ task }: TaskCardProps) {
  const { attributes, listeners, setNodeRef } = useDraggable({
    id: "task-" + task.id,
  })
  const statusColor = STATUS_COLORS[task.status] ?? "bg-slate-400"
  const relativeTime = formatRelativeTime(task.created_at)

  return (
    <Link to={"/tasks/" + task.id} className="block">
      <div
        ref={setNodeRef}
        data-testid={"task-card-" + task.id}
        className="flex cursor-grab gap-3 rounded-xl border bg-card p-4 text-card-foreground shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md active:cursor-grabbing"
        {...attributes}
        {...listeners}
      >
        <span aria-hidden className={"w-1 shrink-0 rounded-full " + statusColor} />
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex items-start justify-between gap-3">
            <h4 className="min-w-0 flex-1 font-medium leading-snug">{task.title}</h4>
            <ScoreBadge score={task.feasibility_score} />
          </div>
          {task.tags.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {task.tags.map((tag) =>
                tag.color ? (
                  <span
                    key={tag.id}
                    className="rounded-md border px-1.5 py-0.5 text-xs font-medium"
                    style={{
                      backgroundColor: tag.color + "1a",
                      borderColor: tag.color + "33",
                      color: tag.color,
                    }}
                  >
                    {tag.name}
                  </span>
                ) : (
                  <span
                    key={tag.id}
                    className="rounded-md bg-sky-100 px-1.5 py-0.5 text-xs font-medium text-sky-700"
                  >
                    {tag.name}
                  </span>
                ),
              )}
            </div>
          ) : null}
          {relativeTime ? (
            <p className="text-xs text-muted-foreground">{relativeTime}</p>
          ) : null}
        </div>
      </div>
    </Link>
  )
}

export { STATUS_COLORS, TaskCard, formatRelativeTime }
export type { TaskListItem, TaskTag }
