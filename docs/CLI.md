# CLI 命令参考

所有命令格式：`uv run python -m idea_hub.cli --db <数据库路径> <子命令> [参数]`

`--db` 默认 `data/idea.db`；`--base` 默认当前目录（产出文件落盘根目录）。

## 收集与评分

### collect
抓取所有启用的来源，经关键词过滤与评分分流后入库。

```bash
python -m idea_hub.cli --db data/idea.db collect
python -m idea_hub.cli --db data/idea.db collect --no-score   # 跳过 LLM 评分（调试）
```

输出：`collected=<收录数> discarded=<丢弃数> review=<待复核数>`。
未配置 `DEEPSEEK_API_KEY` 时自动降级为全量入库（规则层仍生效）。

### candidates
输出今日已收录但尚未生成 idea 的热点（JSON，供 AI 生成阶段读取）。

```bash
python -m idea_hub.cli --db data/idea.db candidates
```

## idea 生成

### add-idea
单条添加 idea（AI 生成的底层原语）。

```bash
python -m idea_hub.cli --db data/idea.db add-idea \
  --hot-item-id 1 --title "标题" --summary "摘要" \
  --score 8 --dims '{"热度":8,"相关性":7,"可执行性":9}' \
  --detail-path /path/to/idea-draft.md [--tags 1,2]
```

分流规则：score >= 8 待办；6-7 归档；< 6 舍弃（返回 `discarded (score < 6)` 且不创建）。

### import-ideas
批量导入（每晚 AI 生成的标准入口）。读取 JSON 文件（顶层数组），每条：

```json
[
  {
    "hot_item_id": 1,
    "title": "标题",
    "summary": "一句话摘要",
    "score": 8,
    "dims": "{\"热度\":8,\"相关性\":7,\"可执行性\":9}",
    "tags": "1,2",
    "detail": "构思全文（Markdown）",
    "related_task_id": null
  }
]
```

- `related_task_id` 非空时走关联逻辑：追加关联热点、更新构思、重新评分（>=8 且待办配额允许时自动升级待办）
- 文件内容可以是 JSON，也可以是 Markdown 包裹的 ```json 代码块（容错解析）

```bash
python -m idea_hub.cli --db data/idea.db import-ideas --file /path/to/ideas.json
```

### tags
列出标签（id、名称、启用状态），供 AI 生成时选择。

```bash
python -m idea_hub.cli --db data/idea.db tags
```

### relate
将热点关联到已有任务并重评分。

```bash
python -m idea_hub.cli --db data/idea.db relate \
  --task-id 1 --hot-item-id 2 --score 8 \
  --dims '{"热度":9}' --detail-path /path/to/addition.md
```

## 执行队列

### next
领取执行队列中的任务（waiting → in_progress）。

```bash
python -m idea_hub.cli --db data/idea.db next                  # 领取队首
python -m idea_hub.cli --db data/idea.db next --task-id 5      # 定向领取指定任务
```

### complete
标记任务完成（in_progress → done），记录摘要与产出路径。

```bash
python -m idea_hub.cli --db data/idea.db complete \
  --task-id 5 --summary "完成摘要" --output-path /path/to/output.md
```

### fail
任务失败（in_progress → waiting），原因写入备注。

```bash
python -m idea_hub.cli --db data/idea.db fail --task-id 5 --reason "超时"
```

### pending-executions / resolve-execution
查询 / 结算用户触发的执行请求（execute_requests 表）。

```bash
python -m idea_hub.cli --db data/idea.db pending-executions
python -m idea_hub.cli --db data/idea.db resolve-execution --task-id 5 --status done
```

## 说明

- 状态变更（archived/todo/waiting/in_progress/done）只能通过 `next`/`complete`/`fail` 或 Web 界面 API 完成；手动改分不会自动移列
- 待办配额（`settings.todo_limit`，默认 10）：待办满时新 idea 自动转留档
- 数据库在首次运行任一命令时自动初始化（建表 + 默认标签）
