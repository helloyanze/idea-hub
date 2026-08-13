import { useEffect, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"

import { apiFetch } from "@/api/client"

const activeJobIds = new Set<number>()

type JobStatus = "pending" | "running" | "done" | "failed"

interface JobData {
  id: number
  type?: string
  status: JobStatus
  progress?: number
  result_ref?: string | null
  error?: string | null
}

interface CollectJob {
  jobId: number
  reused: boolean
}

function registerJobPoll(jobId: number): void {
  activeJobIds.add(jobId)
}

function unregisterJobPoll(jobId: number): void {
  activeJobIds.delete(jobId)
}

function isJobPollActive(jobId: number): boolean {
  return activeJobIds.has(jobId)
}

function resetJobPollRegistry(): void {
  activeJobIds.clear()
}

function useJobPolling(jobId: number | null, enabled = true) {
  const [jobData, setJobData] = useState<JobData | undefined>(undefined)

  useEffect(() => {
    setJobData(undefined)
    if (jobId === null) {
      return
    }

    registerJobPoll(jobId)
    return () => unregisterJobPoll(jobId)
  }, [jobId])

  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: async () => {
      const data = await apiFetch<JobData>(`/api/v1/jobs/${jobId}`)
      setJobData(data)
      return data
    },
    enabled: enabled && jobId !== null,
    refetchInterval: (query) => {
      if (query.state.error) {
        return false
      }
      const status = query.state.data?.status
      if (status === "done" || status === "failed") {
        return false
      }
      return 2000
    },
    refetchIntervalInBackground: true,
  })

  const status = jobData?.status
  const requestError =
    query.error instanceof Error ? query.error.message : null

  return {
    status,
    progress: jobData?.progress,
    error: requestError ?? jobData?.error ?? null,
    resultRef: jobData?.result_ref ?? null,
    isDone: status === "done",
    isFailed: status === "failed",
  }
}

function useCollectTrigger() {
  const [job, setJob] = useState<CollectJob | null>(null)
  const [reusedNotice, setReusedNotice] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<{ job_id: number; reused: boolean }>("/api/v1/collect", {
        method: "POST",
        body: "{}",
      }),
    onSuccess: (data) => {
      if (data.reused && isJobPollActive(data.job_id)) {
        setReusedNotice("已有进行中的任务")
        return
      }

      setJob({ jobId: data.job_id, reused: data.reused })
      setReusedNotice(data.reused ? "已有进行中的任务" : null)
    },
  })

  return {
    trigger: () => mutation.mutate(),
    job,
    reusedNotice,
    isPending: mutation.isPending,
  }
}

export {
  isJobPollActive,
  registerJobPoll,
  resetJobPollRegistry,
  unregisterJobPoll,
  useCollectTrigger,
  useJobPolling,
}
export type { JobData, JobStatus }
