import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate, useParams } from "react-router-dom"

import { apiFetch } from "@/api/client"
import { ScoreBadge } from "@/components/score/ScoreBadge"
import {
  DEFAULT_DIMENSIONS,
  ScoreBreakdown,
} from "@/components/score/ScoreBreakdown"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

interface TaskTag {
  id: number
  name: string
}

interface Hotspot {
  id: number
  title: string
  url: string
  collected_date: string
}

interface TaskOutput {
  has_output: boolean
  latest_version: number | null
  version_count: number
  ai_summary: string
}

interface TaskDetail {
  id: number
  title: string
  status: string
  content_type: string
  feasibility_score: number
  score_breakdown: Record<string, number> | null
  tags: TaskTag[]
  idea_summary: string
  target_desc: string
  token_used: number | null
  fail_count: number
  last_fail_reason?: string | null
  created_at: string
  expire_at: string | null
  notes?: string | null
  redo_note?: string | null
  hotspots: Hotspot[]
  output: TaskOutput
}

interface Settings {
  score_dimensions?: string[]
}

const STATUS_LABELS: Record<string, string> = {
  todo: "待办",
  waiting: "等待中",
  in_progress: "进行中",
  done: "已完成",
}

function TaskDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [isEditingTags, setIsEditingTags] = useState(false)
  const [tagInput, setTagInput] = useState("")
  const [actionError, setActionError] = useState<string | null>(null)

  const taskQuery = useQuery({
    queryKey: ["task", id],
    queryFn: () => apiFetch<TaskDetail>(`/api/v1/tasks/${id}`),
    enabled: id !== undefined,
  })

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => apiFetch<Settings>("/api/v1/settings"),
  })

  function invalidateTaskData() {
    void queryClient.invalidateQueries({ queryKey: ["task", id] })
    void queryClient.invalidateQueries({ queryKey: ["kanban"] })
  }

  function handleActionError(error: Error) {
    setActionError(error.message)
  }

  const moveMutation = useMutation({
    mutationFn: (toStatus: string) =>
      apiFetch(`/api/v1/tasks/${id}/move`, {
        method: "POST",
        body: JSON.stringify({ to_status: toStatus }),
      }),
    onSuccess: () => {
      setActionError(null)
      invalidateTaskData()
    },
    onError: handleActionError,
  })

  const redoMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/tasks/${id}/redo`, { method: "POST" }),
    onSuccess: () => {
      setActionError(null)
      invalidateTaskData()
    },
    onError: handleActionError,
  })

  const resetFailuresMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/tasks/${id}/reset-failures`, { method: "POST" }),
    onSuccess: () => {
      setActionError(null)
      invalidateTaskData()
    },
    onError: handleActionError,
  })

  const deleteMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/api/v1/tasks/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setActionError(null)
      invalidateTaskData()
      navigate("/kanban")
    },
    onError: handleActionError,
  })

  const tagsMutation = useMutation({
    mutationFn: (names: string[]) =>
      apiFetch(`/api/v1/tasks/${id}/tags`, {
        method: "PUT",
        body: JSON.stringify({ names }),
      }),
    onSuccess: () => {
      setActionError(null)
      invalidateTaskData()
      setIsEditingTags(false)
    },
    onError: handleActionError,
  })

  if (taskQuery.isPending) {
    return <p className="p-6">加载中...</p>
  }

  if (taskQuery.isError) {
    return <p className="p-6 text-destructive">加载失败：{taskQuery.error.message}</p>
  }

  const task = taskQuery.data
  const scoreDimensions =
    settingsQuery.data?.score_dimensions ?? DEFAULT_DIMENSIONS
  const infoItems = [
    ["创意摘要", task.idea_summary],
    ["目标描述", task.target_desc],
    ["Token 使用量", task.token_used],
    ["失败次数", task.fail_count],
    ["最后失败原因", task.last_fail_reason],
    ["创建时间", task.created_at],
    ["过期时间", task.expire_at],
    ["备注", task.notes],
    ["重做备注", task.redo_note],
  ].filter(([, value]) => value !== null && value !== undefined && value !== "")

  function startEditingTags() {
    setTagInput(task.tags.map((tag) => tag.name).join(", "))
    setIsEditingTags(true)
  }

  function saveTags() {
    const names = tagInput
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean)
    tagsMutation.mutate(names)
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-4 md:p-6">
      <Link to="/kanban" className="text-sm text-primary hover:underline">
        返回看板
      </Link>

      <header className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h2 className="text-2xl font-semibold">{task.title}</h2>
          <ScoreBadge score={task.feasibility_score} />
        </div>
        <div className="flex flex-wrap gap-2 text-sm text-muted-foreground">
          <span>{STATUS_LABELS[task.status] ?? task.status}</span>
          <span>{task.content_type}</span>
        </div>
      </header>

      <section className="space-y-3 rounded-xl border p-4">
        <h3 className="font-semibold">评分详情</h3>
        <ScoreBreakdown
          dimensions={scoreDimensions}
          breakdown={task.score_breakdown}
          totalScore={task.feasibility_score}
        />
      </section>

      <section className="space-y-3 rounded-xl border p-4">
        <h3 className="font-semibold">任务信息</h3>
        <dl className="grid gap-3 sm:grid-cols-2">
          {infoItems.map(([label, value]) => (
            <div key={String(label)} className="space-y-1">
              <dt className="text-sm text-muted-foreground">{label}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="space-y-3 rounded-xl border p-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="font-semibold">标签</h3>
          {!isEditingTags ? (
            <Button type="button" variant="outline" size="sm" onClick={startEditingTags}>
              编辑标签
            </Button>
          ) : null}
        </div>
        {isEditingTags ? (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="flex-1 space-y-2">
              <Label htmlFor="task-tags">标签</Label>
              <Input
                id="task-tags"
                aria-label="标签"
                value={tagInput}
                onChange={(event) => setTagInput(event.target.value)}
              />
            </div>
            <Button
              type="button"
              onClick={saveTags}
              disabled={tagsMutation.isPending}
            >
              保存标签
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {task.tags.map((tag) => (
              <span
                key={tag.id}
                className="rounded bg-sky-100 px-2 py-1 text-xs text-sky-700"
              >
                {tag.name}
              </span>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3 rounded-xl border p-4">
        <h3 className="font-semibold">关联热点</h3>
        {task.hotspots.length > 0 ? (
          <ul className="space-y-2">
            {task.hotspots.map((hotspot) => (
              <li key={hotspot.id}>
                <a
                  href={hotspot.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary hover:underline"
                >
                  {hotspot.title}
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">暂无关联热点</p>
        )}
      </section>

      <section className="space-y-3 rounded-xl border p-4">
        <h3 className="font-semibold">产出</h3>
        {task.output.has_output ? (
          <div className="space-y-1">
            <p>有产出</p>
            <p>最新版本 {task.output.latest_version}</p>
            <p>版本数量 {task.output.version_count}</p>
            {task.output.ai_summary ? <p>{task.output.ai_summary}</p> : null}
          </div>
        ) : (
          <p>暂无产出</p>
        )}
      </section>

      <section className="space-y-3 rounded-xl border p-4">
        <h3 className="font-semibold">状态操作</h3>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" onClick={() => moveMutation.mutate("todo")}>
            移至待办
          </Button>
          <Button type="button" variant="outline" onClick={() => moveMutation.mutate("waiting")}>
            移至等待中
          </Button>
          <Button type="button" variant="outline" onClick={() => moveMutation.mutate("in_progress")}>
            移至进行中
          </Button>
          <Button type="button" variant="outline" onClick={() => moveMutation.mutate("done")}>
            移至已完成
          </Button>
          <Button type="button" variant="secondary" onClick={() => redoMutation.mutate()}>
            重做
          </Button>
          <Button type="button" variant="secondary" onClick={() => resetFailuresMutation.mutate()}>
            重置失败
          </Button>
          <Button type="button" variant="destructive" onClick={() => deleteMutation.mutate()}>
            删除
          </Button>
        </div>
        {actionError ? (
          <p data-testid="task-action-error" className="text-destructive">
            {actionError}
          </p>
        ) : null}
      </section>
    </div>
  )
}

export { TaskDetailPage }
