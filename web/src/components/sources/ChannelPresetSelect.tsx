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
  "baidu-hotlist",
  "bilibili-hotlist",
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
  // 知乎/微博需登录态：在 channel_config 配置 {"headers": {"Cookie": "..."}}，否则 401/403
  "zhihu-hotlist": {
    url: "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50",
    items_path: "data",
    title_field: "title",
  },
  "weibo-hotlist": {
    url: "https://weibo.com/ajax/side/hotSearch",
    items_path: "data",
    title_field: "title",
  },
  v2ex: {
    url: "https://www.v2ex.com/api/topics/hot.json",
    items_path: "data",
    title_field: "title",
  },
  // 公开可用渠道：百度热榜（无登录即可抓取）
  "baidu-hotlist": {
    url: "https://top.baidu.com/api/board?platform=wise&tab=realtime",
    items_path: "data.cards[].content[].content",
    title_field: "word",
  },
  // 公开可用渠道：B站热门（无登录即可抓取）
  "bilibili-hotlist": {
    url: "https://api.bilibili.com/x/web-interface/popular?ps=50",
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
