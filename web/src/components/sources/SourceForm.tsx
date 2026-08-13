import { useState } from "react"

import {
  ChannelPresetSelect,
  channelPresets,
  type ChannelType,
} from "@/components/sources/ChannelPresetSelect"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export interface SourceFormInitialValues {
  type: string
  name: string
  url: string
  items_path: string
  title_field: string
}

export interface SourceFormValues {
  type: string
  name: string
  url: string
  items_path: string
  title_field: string
}

interface SourceFormProps {
  initialValues: SourceFormInitialValues | null
  onSubmit: (payload: SourceFormValues) => void
  onCancel: () => void
  submitLabel: string
}

const defaultType: ChannelType = "hotlist"

function SourceForm({
  initialValues,
  onSubmit,
  onCancel,
  submitLabel,
}: SourceFormProps) {
  const initial = initialValues ?? {
    type: defaultType,
    name: "",
    ...channelPresets[defaultType],
  }
  const [values, setValues] = useState<SourceFormValues>(initial)

  function updateField(field: keyof SourceFormValues, value: string) {
    setValues((current) => ({ ...current, [field]: value }))
  }

  function handleTypeChange(type: ChannelType) {
    setValues((current) => ({ ...current, type, ...channelPresets[type] }))
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSubmit(values)
  }

  return (
    <form className="grid gap-4" onSubmit={handleSubmit}>
      <ChannelPresetSelect value={values.type} onChange={handleTypeChange} />
      <div className="grid gap-2">
        <Label htmlFor="source-name">名称</Label>
        <Input
          id="source-name"
          value={values.name}
          onChange={(event) => updateField("name", event.target.value)}
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="source-url">地址</Label>
        <Input
          id="source-url"
          value={values.url}
          onFocus={(event) => event.currentTarget.select()}
          onChange={(event) => updateField("url", event.target.value)}
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="source-items-path">数据路径</Label>
        <Input
          id="source-items-path"
          value={values.items_path}
          onFocus={(event) => event.currentTarget.select()}
          onChange={(event) => updateField("items_path", event.target.value)}
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="source-title-field">标题字段</Label>
        <Input
          id="source-title-field"
          value={values.title_field}
          onFocus={(event) => event.currentTarget.select()}
          onChange={(event) => updateField("title_field", event.target.value)}
        />
      </div>
      <div className="flex gap-2">
        <Button type="submit">{submitLabel}</Button>
        <Button type="button" variant="outline" onClick={onCancel}>
          取消
        </Button>
      </div>
    </form>
  )
}

export { SourceForm }
