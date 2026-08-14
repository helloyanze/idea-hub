interface ScoreBreakdownProps {
  dimensions?: string[]
  breakdown: Record<string, number> | null | undefined
  totalScore?: number
}

const DEFAULT_DIMENSIONS = ["facts", "verification", "timeliness", "value"]

function ScoreBreakdown({
  dimensions = DEFAULT_DIMENSIONS,
  breakdown,
  totalScore,
}: ScoreBreakdownProps) {
  if (!breakdown || Object.keys(breakdown).length === 0) {
    return <p data-testid="score-unscored">未评分</p>
  }

  return (
    <div className="space-y-3">
      {totalScore !== undefined ? (
        <div data-testid="score-total" className="font-medium">
          总分 {totalScore}
        </div>
      ) : null}

      <div className="space-y-2">
        {dimensions.map((dimension) => {
          const score = breakdown[dimension] ?? 0

          return (
            <div
              key={dimension}
              data-testid={`score-dim-${dimension}`}
              className="grid grid-cols-[minmax(0,1fr)_minmax(7rem,3fr)_2rem] items-center gap-3 text-sm"
            >
              <span className="truncate">{dimension}</span>
              <div
                role="progressbar"
                aria-label={dimension}
                aria-valuemin={0}
                aria-valuemax={10}
                aria-valuenow={score}
                className="h-2 overflow-hidden rounded-full bg-muted"
              >
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width: `${score * 10}%` }}
                />
              </div>
              <span className="text-right tabular-nums">{score}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export { DEFAULT_DIMENSIONS, ScoreBreakdown }
