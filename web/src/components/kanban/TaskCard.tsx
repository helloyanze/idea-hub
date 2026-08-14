import { useDraggable } from "@dnd-kit/core"
import { Link } from "react-router-dom"

import { ScoreBadge } from "@/components/score/ScoreBadge"

interface TaskTag {
  id: number
  name: string
}

interface TaskListItem {
  id: number
  title: string
  status: string
  feasibility_score: number
  tags: TaskTag[]
}

interface TaskCardProps {
  task: TaskListItem
}

function TaskCard({ task }: TaskCardProps) {
  const { attributes, listeners, setNodeRef } = useDraggable({
    id: `task-${task.id}`,
  })

  return (
    <Link to={`/tasks/${task.id}`} className="block">
      <div
        ref={setNodeRef}
        data-testid={`task-card-${task.id}`}
        className="cursor-grab space-y-3 rounded-lg border bg-card p-3 text-card-foreground shadow-sm transition-shadow hover:shadow-md active:cursor-grabbing"
        {...attributes}
        {...listeners}
      >
        <div className="flex items-start justify-between gap-3">
          <h4 className="min-w-0 flex-1 font-medium leading-snug">{task.title}</h4>
          <ScoreBadge score={task.feasibility_score} />
        </div>
        {task.tags.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {task.tags.map((tag) => (
              <span
                key={tag.id}
                className="rounded bg-sky-100 px-1.5 py-0.5 text-xs text-sky-700"
              >
                {tag.name}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </Link>
  )
}

export { TaskCard }
export type { TaskListItem, TaskTag }
