interface UnderConstructionProps {
  description?: string;
}

export function UnderConstruction({
  description = "该页面将在后续任务中实现。",
}: UnderConstructionProps) {
  return (
    <div className="flex min-h-full items-center justify-center p-8 text-center">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold">Page under construction</h1>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
    </div>
  );
}
