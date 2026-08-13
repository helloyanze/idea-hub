import { useJobPolling } from "@/api/hooks/useJobPolling"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"

interface JobProgressBarProps {
  jobId: number | null
  notice?: string | null
  onRetry?: () => void
  enabled?: boolean
}

function JobProgressBar({
  jobId,
  notice,
  onRetry,
  enabled,
}: JobProgressBarProps) {
  const { progress, error, isDone, isFailed } = useJobPolling(
    jobId,
    enabled,
  )

  if (jobId === null) {
    return null
  }

  return (
    <div className="space-y-2">
      {notice ? <p className="text-sm text-muted-foreground">{notice}</p> : null}
      {isDone ? <p>已完成</p> : null}
      {isFailed ? (
        <>
          <p className="text-destructive">失败</p>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          {onRetry ? (
            <Button size="sm" variant="outline" aria-label="重试" onClick={onRetry}>
              重试
            </Button>
          ) : null}
        </>
      ) : null}
      {!isDone && !isFailed ? (
        <>
          <p>进行中 {progress ?? 0}%</p>
          <Progress value={progress ?? 0} />
        </>
      ) : null}
    </div>
  )
}

export { JobProgressBar }
