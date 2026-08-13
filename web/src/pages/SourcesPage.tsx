import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { ApiError, apiFetch } from "@/api/client"
import {
  SourceForm,
  type SourceFormInitialValues,
  type SourceFormValues,
} from "@/components/sources/SourceForm"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"

interface Source extends SourceFormInitialValues {
  id: number
  enabled: boolean
  keywords: string
  ttl_hours: number | null
  channel_config: Record<string, unknown>
}

interface TestResult {
  ok: boolean
  item_count: number
  sample_items: Array<{
    title: string
    url: string
    content_snapshot: string
  }>
  error?: string
}

type CreateSourcePayload = SourceFormValues & { enabled: true }
type UpdateSourcePayload = Partial<SourceFormValues>

const sourceQueryKey = ["sources"] as const

function SourcesPage() {
  const queryClient = useQueryClient()
  const [editingSource, setEditingSource] = useState<Source | null | undefined>(
    undefined,
  )
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<number, TestResult>>({})

  const sourcesQuery = useQuery({
    queryKey: sourceQueryKey,
    queryFn: () => apiFetch<Source[]>("/api/v1/sources"),
  })

  const createMutation = useMutation({
    mutationFn: (payload: CreateSourcePayload) =>
      apiFetch<Source>("/api/v1/sources", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: sourceQueryKey })
      setEditingSource(undefined)
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: UpdateSourcePayload }) =>
      apiFetch<Source>(`/api/v1/sources/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: sourceQueryKey })
      setEditingSource(undefined)
    },
  })

  const toggleMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch<Source>(`/api/v1/sources/${id}/toggle`, { method: "POST" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: sourceQueryKey })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch<void>(`/api/v1/sources/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setDeleteError(null)
      void queryClient.invalidateQueries({ queryKey: sourceQueryKey })
    },
    onError: (error: Error) => {
      setDeleteError(
        error instanceof ApiError && error.code === "SOURCE_HAS_ITEMS"
          ? "该来源有关联热点，无法删除"
          : error.message,
      )
    },
  })

  const testMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch<TestResult>(`/api/v1/sources/${id}/test`, { method: "POST" }),
    onSuccess: (result, id) => {
      setTestResults((current) => ({ ...current, [id]: result }))
    },
  })

  function handleSubmit(values: SourceFormValues) {
    if (editingSource === null) {
      createMutation.mutate({ ...values, enabled: true })
      return
    }

    if (editingSource !== undefined) {
      const payload = Object.fromEntries(
        Object.entries(values).filter(
          ([key, value]) =>
            value !== editingSource[key as keyof SourceFormInitialValues],
        ),
      ) as UpdateSourcePayload
      updateMutation.mutate({ id: editingSource.id, payload })
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-6">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-xl font-semibold">来源</h2>
        <Button onClick={() => setEditingSource(null)}>新建来源</Button>
      </div>

      {editingSource !== undefined ? (
        <Card>
          <CardHeader>
            <CardTitle>{editingSource === null ? "新建来源" : "编辑来源"}</CardTitle>
          </CardHeader>
          <CardContent>
            <SourceForm
              key={editingSource?.id ?? "create"}
              initialValues={editingSource}
              onSubmit={handleSubmit}
              onCancel={() => setEditingSource(undefined)}
              submitLabel="保存"
            />
          </CardContent>
        </Card>
      ) : null}

      {deleteError ? <p className="text-sm text-destructive">{deleteError}</p> : null}
      {sourcesQuery.isPending ? <p>加载中...</p> : null}
      {sourcesQuery.isError ? <p className="text-destructive">加载来源失败</p> : null}

      <div className="grid gap-4">
        {sourcesQuery.data?.map((source) => {
          const testResult = testResults[source.id]
          return (
            <Card key={source.id}>
              <CardHeader className="gap-3">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <CardTitle>{source.name}</CardTitle>
                    <p className="text-sm text-muted-foreground">{source.type}</p>
                  </div>
                  <Switch
                    checked={source.enabled}
                    aria-label={`启用 ${source.name}`}
                    onCheckedChange={() => toggleMutation.mutate(source.id)}
                  />
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="break-all text-sm text-muted-foreground">{source.url}</p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    aria-label={`测试抓取 ${source.name}`}
                    onClick={() => testMutation.mutate(source.id)}
                  >
                    测试抓取
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    aria-label={`编辑 ${source.name}`}
                    onClick={() => setEditingSource(source)}
                  >
                    编辑
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    aria-label={`删除 ${source.name}`}
                    onClick={() => deleteMutation.mutate(source.id)}
                  >
                    删除
                  </Button>
                </div>
                {testResult ? (
                  <div className="space-y-1 text-sm">
                    {testResult.ok ? (
                      <>
                        <p>抓取成功：{testResult.item_count} 条</p>
                        {testResult.sample_items.map((item) => (
                          <p key={`${item.title}-${item.url}`}>{item.title}</p>
                        ))}
                      </>
                    ) : (
                      <p className="text-destructive">{testResult.error}</p>
                    )}
                  </div>
                ) : null}
              </CardContent>
            </Card>
          )
        })}
      </div>
    </div>
  )
}

export { SourcesPage }
