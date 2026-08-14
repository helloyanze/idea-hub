interface ScoreBadgeProps {
  score: number
}

function ScoreBadge({ score }: ScoreBadgeProps) {
  const colorClasses =
    score >= 8
      ? "bg-green-100 text-green-800"
      : score >= 6
        ? "bg-blue-100 text-blue-700"
        : "bg-gray-100 text-gray-600"

  return (
    <span
      data-testid="score-badge"
      className={"inline-flex min-w-7 items-center justify-center rounded-full px-2 py-0.5 text-xs font-semibold " + colorClasses}
    >
      {Math.round(score)}
    </span>
  )
}

export { ScoreBadge }
