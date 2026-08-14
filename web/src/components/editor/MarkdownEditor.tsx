import { useEffect, useState } from "react"
import ReactMarkdown from "react-markdown"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"

export interface OutputVersionMeta {
  id: number
  task_id: number
  version: number
  filename: string
  created_at: string
}

interface MarkdownEditorProps {
  content: string
  baseVersion: number
  filename: string
  versions: OutputVersionMeta[]
  onSave: (content: string, baseVersion: number) => void
  onViewVersion: (version: number) => void
  saving?: boolean
}

function MarkdownEditor({
  content,
  baseVersion,
  filename,
  versions,
  onSave,
  onViewVersion,
  saving = false,
}: MarkdownEditorProps) {
  const [text, setText] = useState(content)
  const [mode, setMode] = useState<"edit" | "preview">("edit")

  useEffect(() => {
    setText(content)
  }, [content])

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor="markdown-version">版本</Label>
        <select
          id="markdown-version"
          aria-label="版本选择"
          value={baseVersion}
          onChange={(event) => onViewVersion(Number(event.target.value))}
          className="h-8 rounded-md border bg-background px-2 text-sm"
        >
          {versions.map((version) => (
            <option key={version.id} value={version.version}>
              v{version.version}
            </option>
          ))}
        </select>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setMode("edit")}
        >
          编辑
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setMode("preview")}
        >
          预览
        </Button>
        <span className="text-xs text-muted-foreground">{filename}</span>
        <Button
          type="button"
          size="sm"
          className="ml-auto"
          disabled={saving}
          onClick={() => onSave(text, baseVersion)}
        >
          保存
        </Button>
      </div>
      {mode === "edit" ? (
        <textarea
          aria-label="产物内容"
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={18}
          className="w-full rounded border p-3 font-mono text-sm"
        />
      ) : (
        <div className="rounded border p-3">
          <ReactMarkdown>{text}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}

export { MarkdownEditor }
