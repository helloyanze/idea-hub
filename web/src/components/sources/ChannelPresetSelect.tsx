import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"

export const channelTypes = [
  "hotlist",
  "rss",
  "github-trending",
  "hackernews",
  "zhihu-hotlist",
  "weibo-hotlist",
  "v2ex",
] as const

export type ChannelType = (typeof channelTypes)[number]

export interface ChannelPreset {
  url: string
  items_path: string
  title_field: string
}

export const channelPresets: Record<ChannelType, ChannelPreset> = {
  hotlist: {
    url: "https://tophub.today/",
    items_path: "data",
    title_field: "title",
  },
  rss: {
    url: "https://example.com/feed.xml",
    items_path: "data",
    title_field: "title",
  },
  "github-trending": {
    url: "https://github.com/trending",
    items_path: "data",
    title_field: "title",
  },
  hackernews: {
    url: "https://hn.algolia.com/api/v1/search?tags=front_page",
    items_path: "data",
    title_field: "title",
  },
  "zhihu-hotlist": {
    url: "https://www.zhihu.com/hot",
    items_path: "data",
    title_field: "title",
  },
  "weibo-hotlist": {
    url: "https://s.weibo.com/top/summary",
    items_path: "data",
    title_field: "title",
  },
  v2ex: {
    url: "https://www.v2ex.com/api/topics/hot.json",
    items_path: "data",
    title_field: "title",
  },
}

interface ChannelPresetSelectProps {
  value: string
  onChange: (type: ChannelType) => void
}

function ChannelPresetSelect({ value, onChange }: ChannelPresetSelectProps) {
  return (
    <div className="grid gap-2">
      <Label htmlFor="source-type">类型</Label>
      <select
        id="source-type"
        data-slot="channel-preset-select"
        className={cn(
          "h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
        )}
        value={value}
        onChange={(event) => onChange(event.target.value as ChannelType)}
      >
        {channelTypes.map((type) => (
          <option key={type} value={type}>
            {type}
          </option>
        ))}
      </select>
    </div>
  )
}

export { ChannelPresetSelect }
