# Idea Hub v2 重构设计规格

- 日期：2026-08-14
- 状态：已确认（逐节评审通过）；外部审阅后修订（rev2，含审阅回应记录）
- 范围：前后端全面重构，产品级重新设计
- 关联：`docs/superpowers/plans/2026-08-11-idea-queue-implementation.md`、`docs/superpowers/specs/2026-08-13-auto-execution-design.md`（v2 取代并整合两者）

## 1. 背景与动机

Idea Hub v1（2026-08-11 ~ 08-13 快速迭代，70+ 提交）功能覆盖收集、评分、生成、五队列看板、AI 执行产出，但存在以下问题：

1. **前端体验差**：原生 JS 单文件（`web/app.js` 937 行）无组件化，简单交互点击卡顿。
2. **收集质量低**：渠道少、信息零散、无用信息多，源头过滤不足。
3. **QQ Bot 集成不完整**：运行状态查询、主动推送未完成。
4. **产物不可编辑**：文章生成后无法在系统中编辑，无 markdown 编辑能力。
5. **评分展示简陋**：评分无美观的 UI 组件呈现。
6. **布局缺陷**：看板四列布局左侧大量空白、移动端适配差、无暗色模式。
7. **后端架构债务**：`server.py` 406 行集中 35 个路由，业务逻辑未分层。

## 2. 重构目标

- 核心链路保留：收集 → 评分 → 生成 idea → 看板管理 → AI 执行产出
- 次要功能简化：评分三档分流改两档、留档队列砍掉、targets 表砍掉
- 新增能力：markdown 产物编辑（编辑/下载/上传/版本）、热点全文搜索、来源测试抓取、异步任务追踪、知识库底子（FTS5）
- 体验目标：交互无感知延迟、移动端可用、暗色模式、简约风格

## 3. 已确认决策（决策记录）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 前端重写为 React + Vite + shadcn/ui（TypeScript） | 组件化解决卡顿与布局问题；shadcn 简约风、暗色原生支持、中文生态好 |
| D2 | 后端保留 FastAPI + SQLite，重写代码结构与 API 设计 | 单机个人系统该栈成熟；知识库可通过 FTS5 + sqlite-vec 渐进扩展，无需换栈 |
| D3 | 数据全部重来，不迁移（数据库与产出清空） | 当作全新系统，避免迁移兼容成本 |
| D4 | 知识库分三层渐进：L1 FTS5 全文索引（重构内）→ L2 sqlite-vec 向量检索（后续）→ L3 RAG 问答（最后） | 零运维、随备份走；个人系统规模不需要重型向量库 |
| D5 | QQ 集成由 Hermes 接管：ideahub 只提供 API + 事件记录（notifications 表），Hermes 负责查询/推送/操作 | 分工清晰，ideahub 不内嵌 QQ 逻辑 |
| D6 | 评分分流三档（收录/复核/丢弃）简化为两档（收录/丢弃） | 复核档实际利用率低，简化决策 |
| D7 | 状态机四状态：todo / waiting / in_progress / done。不设 archived 状态：完成任务停留在 done（可按完成时间排序），低分直接 discard 不进库 | 留档队列砍掉；"已归档"语义与 todo 相反，不合并，直接取消归档概念 |
| D8 | targets 表砍掉，target 概念并入 tasks.target_desc（普通文本字段，如"自媒体内容"） | 多目标模式实际未用；文本字段避免悬空外键 |
| D9 | 配置分层：静态配置入 config 文件（端口、auth、备份路径）；动态参数（评分阈值、收集频率、每日预算上限）入精简 settings 表 | 运行时需调整的参数不能写死在文件 |
| D10 | execute_requests 表砍掉，并入 tasks 执行字段 | 减少表间关联 |
| D11 | 开发方式：垂直切片，8 个切片逐个交付，每片全链路可运行 | 每步有可验收成果 |
| D12 | 产物双写：markdown 落盘 outputs/tasks/<id>/output.md + outputs 表存元数据与内容缓存；读时以文件为准校验 mtime/hash，不一致自动回写 DB + FTS | 支持 Web 编辑、版本记录、全文索引，同时保持文件可被外部工具使用 |
| D13 | 保留：SQLite WAL + 每日备份、cron 调度、Basic Auth、Cloudflare Tunnel | 已验证可用，不重写 |
| D14 | 生成编排本地化：ideahub 直接调用 DeepSeek LLM 生成 idea（复用执行器的 LLM 调用层 + 生成 prompt 模板），不依赖 Hermes 在线；Hermes 仅作 QQ 交互层（查询/推送/操作） | 消除 ideahub ↔ Hermes 循环耦合；cron 全自动运行不依赖 Hermes 可用性；LLM 调用层已被 executor 验证 |
| D15 | 长任务异步化：pipeline 端点（collect/generate/execute）立即返回 job_id，jobs 表追踪进度，前端轮询（可选 SSE） | 避免请求挂起超时，用户可见进度 |
| D16 | 产物版本：outputs 表每版本一行，(task_id, version) 联合主键；PUT/上传写入新版本行并更新落盘文件（文件仅保留最新版，历史版本存 DB 可导出）；外部文件回写不递增版本 | 支持真实版本历史回溯，避免自动保存产生垃圾版本 |

## 4. 整体架构

```
React SPA (web/)
  Vite + shadcn/ui + Tailwind + React Query + TypeScript
        │ REST /api/v1（Basic Auth）
FastAPI 后端（idea_hub/ 包，领域分层）
  routers/ → services/ → models/ → SQLite (WAL)
  收集器 + 评分 + 生成（本地 LLM）+ 执行器 + 调度器 + jobs
        │ notifications 表 + 事件记录
Hermes（云端 QQ bot，7×24，仅交互层）
  查询状态 / 主动推送 / 菜单操作（经 API 接管）
```

依赖方向：Hermes → ideahub API（单向）。ideahub 不反向调用 Hermes；ideahub 生成/执行直接调用 DeepSeek API（key 在 .env），全自动运行完全自治。

### 4.1 后端目录结构

```
idea_hub/
  main.py            # FastAPI 入口：挂路由、Basic Auth、静态文件、全局异常处理、限流
  config.py          # 静态配置加载（config.yaml）
  db.py              # 连接、schema、轻量迁移（schema_version）、FTS5 触发器、WAL 安全备份
  models.py          # 数据访问层（纯 SQL 封装，无业务逻辑）
  routers/
    sources.py       # 来源 CRUD + toggle + test
    hotspots.py      # 热点列表/过滤/搜索
    tasks.py         # 任务 CRUD + move + tags + execute + redo + reset-failures
    outputs.py       # 产物：GET/PUT/upload/versions
    jobs.py          # 异步任务：创建/查询/列表/SSE 流
    pipeline.py      # collect / generate / execute 触发（异步）
    stats.py         # 统计 + 健康
    notifications.py # 通知列表/已读
  services/
    collect.py       # 收集编排（含源头过滤/去重，异步 job）
    score.py         # 两档分流 + LLM 评分
    generate.py      # 生成编排（本地 LLM，异步 job）
    execute.py       # 执行编排（幂等，异步 job）
    jobs.py          # job 生命周期管理
    notify.py        # 事件写入 notifications
    outputs.py       # 产物读写/版本/文件校验回写
  collectors.py      # 多源抓取（扩渠道）
  scorer.py          # 评分逻辑（重构）
  executor.py        # LLM 执行器（重构）
  scheduler.py       # 无状态 tick（重构）
  prompts.py         # 提示词模板
  cli.py             # 命令行入口
```

### 4.2 前端目录结构

```
web/
  src/
    main.tsx
    App.tsx              # 布局壳：左侧导航 + 主内容区 + 暗色切换
    api/client.ts        # fetch 封装 + 错误统一处理
    api/hooks/           # React Query hooks 按领域拆分（sources/tasks/outputs/jobs/stats/notifications/search），含 job 轮询 hook
    components/
      kanban/            # 看板列、卡片（@dnd-kit 拖拽）
      score/             # 评分徽章、多维评分条、总分色阶
      editor/            # markdown 编辑器（编辑/预览/下载/上传/版本）
      jobs/              # 任务进度条（收集/生成/执行进行中提示）
      notifications/     # 通知列表 + 角标
      common/            # 通用组件（错误态、toast、分页）
    pages/
      KanbanPage.tsx     # 默认页：四列看板
      HotspotsPage.tsx   # 热点流：verdict 徽章 + 过滤 + 搜索
      TaskDetailPage.tsx # 任务详情：构思信息 + 评分明细 + 产物编辑器
      SourcesPage.tsx    # 来源管理：CRUD + 测试抓取 + 渠道预设
      NotificationsPage.tsx
      StatsPage.tsx      # token 用量、队列计数、今日产出、调度器健康
```

## 5. 数据模型

### 5.1 表结构

**核心表（7 张）：**

| 表 | 字段要点 | 说明 |
|---|---|---|
| `sources` | id, type, name, url, enabled, items_path, title_field, keywords, ttl_hours, channel_config | type 含新增渠道；channel_config 存渠道特定配置（JSON）；ttl_hours = 热点时效窗口（见 5.4） |
| `hot_items` | id, source_id, title, url, content_snapshot, collected_at, final_score, verdict, collected_date | verdict: admit/discard；content_snapshot 截断至 2000 字符；collected_date = DATE(collected_at) 冗余列，专供 discard 清理查询避免函数索引，建 (verdict, collected_date) 联合索引；UNIQUE(source_id, url) 防重复 |
| `tasks` | id, title, idea_summary, content_type, status, feasibility_score, score_breakdown, target_desc, expire_at, idea_path, ai_summary, token_used, fail_count, last_fail_reason, redo_note, notes, created_at, updated_at, completed_at | status: todo/waiting/in_progress/done；target_desc 为文本（替代 target_id，见 D8）；expire_at 过期逻辑见 5.4；content_type 枚举与 score_breakdown 格式见 5.4；idea_path = 构思全文落盘路径（相对 base）；产物路径为固定模式 outputs/tasks/<id>/output.md，由 task_id 推导，不存字段 |
| `task_links` | task_id, hot_item_id | 多对多关联 |
| `tags` | id, name UNIQUE, color | name 唯一约束 |
| `task_tags` | task_id, tag_id | 标签关联 |
| `settings` | key TEXT PRIMARY KEY, value, value_type | 动态参数：score_todo_threshold、collect_interval_hours、daily_budget_tokens 等（见 D9）；value_type: int/float/string/json |

**新表（4 张）：**

| 表 | 字段要点 | 说明 |
|---|---|---|
| `notifications` | id, type, title, body, level, entity_type, entity_id, is_read, created_at | entity_type/entity_id 关联任务/热点，前端可跳转，Hermes 推送可定位 |
| `outputs` | task_id, version, filename, content, file_mtime, file_hash, created_at, updated_at | (task_id, version) 联合主键，每版本一行；file_mtime/file_hash 供读时校验（见 5.3） |
| `jobs` | id, type, status, progress, result_ref, error, heartbeat_at, created_at, updated_at | status: pending/running/done/failed；result_ref 为 JSON 字符串；heartbeat_at 供崩溃恢复（见 5.5） |
| `schema_version` | version | 迁移版本号（未来 schema 演进用，本次不迁移数据） |

**FTS5 虚拟表（3 张，external content + 触发器）：**

| 表 | 索引内容 | 同步方式 |
|---|---|---|
| `hot_items_fts` | hot_items(title, content_snapshot) | hot_items 表 INSERT/UPDATE/DELETE 触发器 |
| `tasks_fts` | tasks(title, idea_summary, ai_summary) | tasks 表触发器 |
| `outputs_fts` | outputs(content) | outputs 表触发器（PUT/上传/外部文件回写均经 outputs 表，索引自动刷新） |

统一搜索：三张 FTS 表分别 MATCH 后 UNION（rank 排序）。FTS5 不支持跨表自动关联，故拆三表 + 触发器，写入路径统一，无同步遗漏。

### 5.2 状态机

- 四状态：`todo`（待办）/ `waiting`（等待）/ `in_progress`（进行中）/ `done`（已完成）
- 状态变更仅通过 move 操作；进入 done 时记录 completed_at
- 无 archived 状态：完成任务停留在 done；低分（< 阈值）直接 discard，不创建任务
- 过期任务（expire_at < now，见 5.4）：调度器 tick 自动 move 到 done，备注"已过期"并写通知；仅作用于 todo / waiting，in_progress 任务跳过并发警告（避免与执行器写回冲突）

### 5.3 产出双写与一致性

- 落盘：`outputs/tasks/<id>/output.md`（仅最新版本；历史版本只存 DB，可通过版本 API 导出）
- 双写：outputs 表每版本一行（content 缓存 + file_mtime + file_hash）
- Web 编辑（PUT）/ 上传：写新版本行（version = max+1）+ 更新落盘文件 + FTS 经触发器刷新
- 外部文件变更检测：读取产物时以文件为准，对比 file_mtime/file_hash，不一致则回写 DB 与 FTS，**不递增版本**（更新当前版本行内容，防编辑器自动保存产生垃圾版本）；不做常驻 watchdog（个人系统，读时校验足够）
- FTS 索引范围：outputs_fts 仅索引每个 task 的最新版本（触发器判断 version = MAX），FTS 中每个 task 至多一行，避免版本行膨胀与分组复杂度

### 5.4 字段语义

- `sources.ttl_hours`：该来源热点的时效窗口（小时）。热点 expire_at = collected_at + ttl_hours（动态计算，不落库）；过期热点不再作为生成候选；不参与删除（保留历史）。注意：修改 ttl_hours 会影响该来源全部历史热点的时效判定
- `tasks.expire_at`：任务时效。过期任务由调度器自动完成（move → done + 通知），仅限 todo/waiting；in_progress 跳过并发警告；人工操作不受限
- `hot_items.content_snapshot`：截断至 2000 字符，防长文膨胀 FTS 索引
- `hot_items` 清理策略：discard 热点保留 7 天，调度器每日清理（删行 + FTS 触发器同步）
- `tasks.content_type` 枚举：article（文章，默认）/ video_script（视频脚本）/ tweet（短文）/ newsletter（简报）；生成时由 LLM 根据热点类型与 target_desc 判定，手动建任务可指定
- `tasks.score_breakdown`：JSON 字符串，多维评分明细，如 {"facts": 8, "verification": 7, "timeliness": 9, "value": 8}（维度随目标可配）；feasibility_score = 各维度加权均值（四舍五入）

### 5.5 异步 job 生命周期

- 状态流转：pending → running → done/failed
- 心跳：子步骤边界更新（每处理完一个 candidate / 一篇产物 / 一个来源）及每次 LLM 尝试前；LLM 单次超时 90s、最多重试 2 次（共 3 次尝试，最坏 270s < 300s 阈值），每次尝试前刷新心跳；stale 阈值 5min
- 崩溃恢复：调度器 tick 发现 running 且 heartbeat_at 超过 5 分钟未更新 → 标记 failed + 写通知
- job 去重：同类型 job 已有 running 时，POST 返回已有 job_id（不新建）
- result_ref 格式：JSON 字符串，如 {"task_ids": [1,2,3]}（execute/generate）、{"hotspot_count": 42}（collect）

## 6. API 设计（/api/v1）

统一响应：`{data, error}`；错误 `{error: {code, message}}`；分页 `{items, total, page, size}`。全部端点 Basic Auth 保护 + 应用层限流。

### 6.1 sources

```
GET    /api/v1/sources                # 列表（含 channel_config）
POST   /api/v1/sources                # 新建
PATCH  /api/v1/sources/{id}           # 编辑（含 ttl_hours）
POST   /api/v1/sources/{id}/toggle    # 启停
DELETE /api/v1/sources/{id}             # 仅允许无关联 hot_items 的来源；有关联返回 409（应改用 toggle 禁用）
POST   /api/v1/sources/{id}/test      # 测试抓取（验证渠道可用性）
```

### 6.2 hotspots

```
GET /api/v1/hotspots?page=&size=&source_id=&verdict=&q=   # 列表 + 过滤 + FTS 搜索
GET /api/v1/hotspots/{id}
```

q= 走 hot_items_fts 单表检索（页面内过滤）；跨表全局搜索用 /api/v1/search（见 6.7）。tasks?q= 同理走 tasks_fts。

### 6.3 tasks

```
GET    /api/v1/tasks?status=&page=&size=&q=&tag=
GET    /api/v1/tasks/{id}                    # 详情（含 tags、关联热点、产物）
POST   /api/v1/tasks                         # 手动建任务：必填 title、content_type；可选 idea_summary、feasibility_score（默认 0）、score_breakdown、target_desc、notes、hotspot_id（关联热点）；status 默认 todo
PATCH  /api/v1/tasks/{id}                    # 编辑（标题/摘要/评分/备注）
DELETE /api/v1/tasks/{id}                    # 级联：删 task_links、task_tags、outputs 行与落盘文件（outputs/tasks/<id>/ 目录）；notifications 与 jobs 历史保留（跳转 404 时提示"任务已删除"）
POST   /api/v1/tasks/{id}/move      {to_status}
PUT    /api/v1/tasks/{id}/tags      {tag_ids}    # 替换语义：设置任务全部标签（重传完整数组）
POST   /api/v1/tasks/{id}/execute           # 触发执行（异步，返回 job_id）
POST   /api/v1/tasks/{id}/redo  {note?}     # 重做：status→waiting、fail_count 清零；redo_note 存时间戳+备注（无 note 仅存时间戳）
POST   /api/v1/tasks/{id}/reset-failures    # 清零失败计数（区别于 redo：不改变状态）
```

### 6.4 outputs

```
GET    /api/v1/tasks/{id}/output              # 取最新版本 markdown 正文
PUT    /api/v1/tasks/{id}/output              # 保存编辑：body 含 content + base_version；乐观锁校验（version 不匹配返回 409）；写新版本 + 落盘。前端 409 恢复：重新拉取最新版本并提示"已有更新，请基于最新版本继续编辑"（不自动 merge）
POST   /api/v1/tasks/{id}/output/upload       # 上传替换（multipart，同样写新版本）
GET    /api/v1/tasks/{id}/output/versions     # 版本历史（全部版本行，含时间）
GET    /api/v1/tasks/{id}/output/versions/{version}   # 导出指定历史版本内容
```

### 6.5 pipeline（异步）

```
POST /api/v1/collect    {source_ids?: []}     # 立即返回 {job_id}；省略 source_ids = 全部启用来源
POST /api/v1/generate  {count?, hotspot_ids?} # 生成 idea（本地 LLM），返回 {job_id}；候选策略：verdict=admit 且未关联 task 且未过期（collected_at + ttl_hours > now），按 final_score 降序取前 N（默认 10，settings 可配）；可选显式指定 hotspot_ids 或 count
POST /api/v1/execute    {task_ids: []}        # task_ids 必填（至少 1 个）；执行全部需显式列出
GET  /api/v1/jobs/{job_id}                    # 查询进度 {status, progress, result_ref, error}
GET  /api/v1/jobs?type=&status=&page=         # 任务列表
GET  /api/v1/jobs/{job_id}/stream             # SSE 单 job 进度订阅（可选增强，前端默认轮询）
GET  /api/v1/jobs/stream?type=                # SSE 全局推送（按 type 过滤，可选）
```

### 6.6 stats / notifications / health / settings

```
GET  /api/v1/stats                           # 队列计数 + token 用量 + 今日产出 + 活跃 job
GET  /api/v1/notifications?unread_only=&entity_type=&entity_id=
POST /api/v1/notifications/{id}/read
POST /api/v1/notifications/read-all
GET  /api/v1/health                          # 调度器心跳 + 数据库健康
GET /api/v1/settings                     # 全部动态参数（评分阈值、收集频率、每日预算上限等）
PUT /api/v1/settings  {key: value}       # 更新单键（按 value_type 校验 int/float/string/json）
```

### 6.7 统一搜索

```
GET /api/v1/search?q=&page=&size=     # 跨热点/任务/产物统一检索
```

实现：三张 FTS 表分别 MATCH 后 UNION（rank 排序）；每条结果标注 entity_type（hotspot/task/output）+ entity_id，前端据此跳转。支持过滤器扩展（entity_type=、source_id=、status=）。

与单表搜索的关系：/hotspots?q=、/tasks?q= 为单表 FTS 检索（页面内过滤），/search 为三表 UNION 全局检索；共用同一匹配实现，仅作用域不同。

### 6.8 统一约定

- 长任务端点一律异步：立即返回 job_id，不阻塞 HTTP 请求
- 写操作幂等：collect/generate/execute 重复触发不重复产出（同类型 running job 去重，返回已有 job_id；执行器幂等兜底）
- 乐观锁：outputs PUT 必须带 base_version
- 限流：应用层请求频率限制 + 登录失败延迟；文档注明可升级 Cloudflare Access
- 原子状态变更：所有任务状态变更（move / execute / redo / 过期完成）用条件 UPDATE（WHERE status IN 允许前置状态），rowcount=0 视为冲突返回 409，杜绝调度器 tick 与 API 并发竞态

## 7. 前端设计

### 7.1 技术底座

- Vite + React 18 + TypeScript
- shadcn/ui（Radix 原语 + Tailwind），简约风
- 暗色模式：Tailwind darkMode: 'class' + 轻量 React Context（跟随系统 + 手动切换）；不引入 next-themes（面向 Next.js，本项目为 Vite SPA）
- React Query：缓存 + 请求去重 + 乐观更新（解决卡顿）
- @dnd-kit：看板拖拽
- 布局：左侧窄栏导航 + 主内容区（解决左侧空白）

### 7.2 页面

| 视图 | 内容 |
|---|---|
| 看板（默认） | 四列（todo/waiting/in_progress/done），拖拽流转；卡片：标题、评分徽章、标签、内容类型、时间；移动端横向滚动或 tab 切换 |
| 热点流 | 热点列表，verdict 徽章、评分展示、来源过滤、搜索 |
| 任务详情 | 构思/摘要 + 评分明细组件 + markdown 编辑器（编辑/下载/上传/版本） |
| 来源管理 | 渠道 CRUD + 启停 + 测试抓取 + 渠道预设 |
| 通知中心 | 列表 + 已读/未读 + 角标 + 实体跳转 |
| 统计页 | token 用量、队列计数、今日产出、调度器健康条 |

- 全局搜索框（布局壳顶部）：跨热点/任务/产物统一检索，结果按 entity_type 跳转

### 7.3 评分展示组件

- 多维评分（事实性/验证需求/时效性等）用分组条形图 + 总分徽章
- 数值色阶：>=8 绿、6-7 黄、<6 红

### 7.4 长任务交互

- 触发收集/生成/执行后：页面显示 job 进度条（React Query 轮询 jobs/{id}，或 SSE 订阅 jobs/{id}/stream，仅订阅自己触发的 job）
- 任务完成/失败 toast 提示；失败可查看 error 详情与重试

### 7.5 性能目标

- 看板列数据独立拉取、卡片组件 memo 化、拖拽不整页重渲染
- 交互目标：点击无感知延迟

### 7.6 移动端

- 断点：桌面四列、平板两列、手机单列 + 底部导航

## 8. 垂直切片开发顺序

| 切片 | 内容 | 交付物 |
|---|---|---|
| S1 骨架 | 后端包结构 + schema（含 FTS5 触发器、settings、schema_version）+ config；前端脚手架 + 布局壳 + 暗色 | 空库可启动，健康检查通 |
| S2 收集 | collectors 扩渠道（知乎热榜、微博热搜、V2EX 等）+ 源头过滤/去重 + sources CRUD + test 端点 + collect 异步 job | 手动收集跑通，热点流页面可见，job 进度可见 |
| S3 评分 | 两档分流 + LLM 评分（复用现有 scorer 重构） | 收集结果带 verdict |
| S4 生成 | 生成编排本地化（DeepSeek 直调 + prompt 模板）+ 标签 + generate 异步 job | idea 进看板 |
| S5 看板 | 四列看板 + 拖拽 + 搜索 + 任务详情 + 评分组件 + 过期处理 | 完整看板可用 |
| S6 执行+产物 | 执行器重构 + outputs 表（版本化）+ markdown 编辑器 + 下载/上传/版本 + 读时校验 | 产物全链路 |
| S7 通知+统计+搜索 | 通知事件写入（含实体关联）+ 统计页 + 调度器健康 + discard 清理 + 统一搜索端点 | Hermes 可巡检，全局搜索可用 |
| S8 部署 | cron 脚本 + 部署文档 + QQ bot 对接验证 + 限流验证 | 云端全自动 |

## 9. 错误处理

- 后端：全局异常处理器；业务错误 `{error: {code, message}}`；LLM/抓取失败不中断主流程（job 标记 failed + 降级 + 通知记录）
- 长任务失败：job.error 记录详情，通知表写失败事件（Hermes 推送告警）
- 生成/执行依赖 DeepSeek API：不可用时 job failed + 通知，核心功能（看板/收集规则过滤）不受影响
- 前端：React Query 错误态统一组件（重试按钮）；乐观更新失败自动回滚；网络错误 toast
- 关键操作幂等：collect/generate/execute 重复触发不重复产出
- 限流：应用层频率限制 + 登录失败延迟（防爆破）

## 10. 测试策略

- 后端：pytest + 内存 SQLite；每切片配套服务层单测 + API 集成测试（jobs 异步、FTS 搜索、版本乐观锁、过期处理、读时校验必测）
- 前端：Vitest + React Testing Library；核心交互必须有测试（看板拖拽、评分组件、编辑器、job 轮询）
- e2e：保留一条核心链路测试（收集→评分→生成→执行→产物）
- 回归审阅：实施完经外部 AI 审阅，逐条回应采纳/不采纳并记录

## 11. 知识库演进（后续）

- L1（重构内）：三张 FTS5 表 + 触发器，所有文本内容可检索（热点/任务/产物）
- L2（后续）：sqlite-vec 扩展 + DeepSeek embedding API，语义检索
- L3（最后）：检索结果喂给 Hermes/LLM 做 RAG 问答（QQ bot："我写过关于 XX 的内容吗"）

## 12. 回归审阅要点

1. 前端交互响应是否达到"无感知延迟"（S5 验收）
2. 收集质量：新渠道是否有效、源头过滤是否显著降低无用信息（S2 验收）
3. QQ 推送链路：notifications 表事件（含实体关联）→ Hermes 巡检 → 群内推送是否完整（S7 验收）
4. 产物编辑：Web 编辑 → 落盘文件一致性、版本递增正确性、乐观锁 409（S6 验收）
5. 评分展示美观性、暗色模式、移动端适配（S5 验收）
6. 幂等性：重复触发 collect/generate/execute 不产生重复数据
7. FTS5 搜索：统一搜索端点跨热点/任务/产物准确性、entity_type 标注与跳转、外部改文件后索引刷新（S5/S6/S7 验收）
8. 异步任务：长任务 job 进度可见、失败降级、SSE/轮询正常（S2-S6 验收）
9. 过期处理：expire_at 自动完成 + 通知；discard 7 天清理（S5/S7 验收）
10. 云端全自动运行稳定性：生成不依赖 Hermes 在线（S8 验收）

## 13. 外部审阅回应记录

审阅时间：2026-08-14。逐条回应（采纳 / 不采纳 + 处理方式）：

### 一、严重问题

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 1 | tasks.target_id 悬空外键，与 D8 矛盾 | 采纳 | target_id → target_desc 文本字段（D8 修订，5.1 表结构已改） |
| 2 | outputs 单行 + version 自增无法回溯历史 | 采纳 | outputs 每版本一行，(task_id, version) 联合主键（新增 D16，5.1/6.4 已改） |
| 3 | archived 合并进 todo 语义不通 | 采纳 | 取消 archived 概念：完成任务停留 done，低分直接 discard（D7 修订，5.2 已改） |
| 4 | FTS5 跨三表同步机制缺失 | 采纳 | 三张独立 FTS5 external content 表 + 各表触发器同步，查询 UNION（5.1 FTS 节已明确；outputs 写入路径统一，索引自动刷新） |
| 5 | pipeline 同步阻塞无异步追踪 | 采纳 | 新增 jobs 表 + POST 立即返回 job_id + 轮询/SSE 端点（D15，6.5 已改） |

### 二、重要设计缺口

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 6 | 产物双写一致性：外部改文件后 DB/FTS 旧 | 采纳 | 读时以文件为准校验 mtime/hash，不一致自动回写 DB 新版本 + FTS（D12 修订，5.3 已改） |
| 7 | notifications 缺实体关联 | 采纳 | 增加 entity_type/entity_id（5.1/6.6 已改，前端通知可跳转） |
| 8 | Hermes 生成依赖循环耦合 | 采纳 | 生成编排本地化：ideahub 直调 DeepSeek，不依赖 Hermes 在线；Hermes 仅 QQ 交互层（新增 D14，第 4 节架构已改） |
| 9 | expire_at 无处理逻辑 | 采纳 | 调度器 tick 自动完成过期任务（move → done + 通知），语义定义见 5.4 |
| 10 | redo_note 无对应 API | 采纳 | 新增 POST /tasks/{id}/redo（状态→waiting + 清 fail_count + 记录 redo_note），与 reset-failures 语义区分（6.3 已改） |

### 三、次要问题

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 11 | ttl_hours 语义未定义 | 采纳 | 定义为热点时效窗口，expire_at = collected_at + ttl_hours（5.4 已定义） |
| 12 | outputs PUT 无乐观锁 | 采纳 | PUT 必带 base_version，不匹配返回 409（6.4 已改） |
| 13 | 无 SSE | 采纳 | jobs/stream SSE 端点 + 前端默认轮询（6.5 已改，标注可选增强） |
| 14 | content_snapshot 无大小限制 | 采纳 | 截断至 2000 字符（5.4 已改） |
| 15 | tags.name 无唯一约束 | 采纳 | 加 UNIQUE（5.1 已改） |
| 16 | discard 热点长期累积 | 采纳 | 保留 7 天，调度器每日清理（5.4 已改） |
| 17 | settings 并入文件后运行时不可调 | 采纳 | 分层：静态配置入 config 文件，动态参数入 settings 表（D9 修订，5.1 已加 settings 表） |
| 18 | S1 迁移框架与 D3 混淆 | 采纳 | 注明 schema_version 仅为未来演进预留，本次不迁移数据（5.1 已注） |
| 19 | Basic Auth 无限流 | 采纳 | 应用层限流 + 登录失败延迟；可升级 Cloudflare Access（6.8/9 已改） |
| 20 | execute task_ids 可选危险 | 采纳 | task_ids 必填（至少 1 个），执行全部需显式列出（6.5 已改） |

### 四、总体评价采纳

审阅指出"核心风险集中在数据模型字段残留、版本与搜索机制、长任务异步"——三项均已按上述修正落实；决策记录同步更新（D7/D8/D9/D12 修订，新增 D14/D15/D16）。

### 第二轮审阅回应（2026-08-14）

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 1 | jobs/stream 缺 job_id 定位，前端收到无关事件 | 采纳 | 单 job 订阅 GET /jobs/{job_id}/stream；全局推送另设 /jobs/stream?type=（6.5 已改） |
| 2 | 过期未区分状态，in_progress 与执行器冲突 | 采纳 | 过期自动完成仅作用于 todo/waiting；in_progress 跳过 + 警告通知（5.2/5.4 已改） |
| 3 | result_ref 语义不明 | 采纳 | 明确为 JSON 字符串：{"task_ids":[...]}/{"hotspot_count":N}（5.5 已定义） |
| 4 | 无崩溃恢复，running 卡死 | 采纳 | jobs 加 heartbeat_at；调度器检测超时（>5min）标记 failed + 通知（5.5 已改） |
| 5 | outputs_fts 版本行重复命中 | 采纳 | 搜索结果按 task_id 分组，默认最新版本，标注历史版本命中数（S6 实现） |
| 6 | redo 不接受请求体 | 采纳 | 接受可选 {note?: string}，redo_note 存时间戳+备注（6.3 已改） |
| 7 | ttl_hours 修改影响历史 | 采纳 | 注明动态计算影响全部历史热点（5.4 已注） |
| 8 | hot_items 去重未定义 | 采纳 | UNIQUE(source_id, url) + 服务层 URL 去重（5.1 已改） |
| 9 | job 去重机制未说明 | 采纳 | 同类型 running 存在时返回已有 job_id（5.5/6.7 已改） |
| 10 | settings.value 无类型 | 采纳 | 加 value_type 列（int/float/string/json）（5.1 已改） |

### 第三轮审阅回应（2026-08-14）

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 1 | 缺统一搜索 API：FTS5 已建好无消费入口 | 采纳 | 新增 GET /api/v1/search?q=&page=&size=，UNION 三表，每条标注 entity_type + entity_id，前端可跳转（6.7 已加，S7 交付） |
| 2 | 心跳 30s 定时与阻塞 LLM 调用冲突，会被误判崩溃 | 采纳 | 心跳改为子步骤边界 + LLM 调用前更新；LLM 单次超时 120s，重试前刷新心跳；stale 阈值 5min 不变，单步耗时 < 5min 不误判（5.5 已改） |

### 第四轮审阅回应（2026-08-14）

一、内部不一致 / 矛盾：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 1 | 6.7/6.8 编号错位，审阅表 19 条交叉引用错误 | 采纳 | 第 19 条引用修正为 6.8/9（已改）；6.7 统一搜索 / 6.8 统一约定编号确认无误 |
| 2 | 统一搜索与单表 q= 关系未说明 | 采纳 | 明确：/hotspots?q=、/tasks?q= 走单表 FTS（页面内过滤），/search 走三表 UNION 全局检索，共用匹配实现（6.2/6.7 已改） |
| 3 | 心跳与 stale 阈值安全边际 | 采纳 | LLM 单次超时降为 90s、最多重试 2 次（共 3 次尝试，最坏 270s < 300s），每次尝试前刷新心跳（5.5 已改） |
| 4 | collected_date 与 collected_at 冗余 | 采纳 | 保留冗余列并说明理由：避免 DATE() 函数索引，建 (verdict, collected_date) 联合索引（5.1 已改） |

二、设计缺口 / 未定义行为：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 5 | idea_path/output_path 语义未定义 | 采纳 | idea_path = 构思全文落盘路径（相对 base）；output_path 删除，产物路径为固定模式由 task_id 推导（5.1 已改） |
| 6 | 删除 source 关联处理未定义 | 采纳 | 仅允许删除无关联 hot_items 的来源，有关联返回 409（6.1 已改） |
| 7 | 删除 task 级联未定义 | 采纳 | 级联删 task_links/task_tags/outputs 行与落盘文件；notifications 与 jobs 历史保留（6.3 已改） |
| 8 | content_type 枚举未定义 | 采纳 | article/video_script/tweet/newsletter，生成时 LLM 判定，手动可指定（5.4 已改） |
| 9 | generate 候选策略黑盒 | 采纳 | 候选 = verdict=admit 且未关联 task 且未过期，按 final_score 降序取前 N（默认 10）；支持 hotspot_ids/count 覆盖（6.5 已改） |
| 10 | 文件层仅最新版与 D16 矛盾 | 采纳 | 明确：文件仅保留最新版，历史版本存 DB 并可导出（新增 versions/{version} 端点）；D16 措辞修正（5.3/6.4 已改） |
| 11 | settings 缺 API 端点 | 采纳 | 新增 GET/PUT /api/v1/settings（6.6 已改） |
| 12 | POST /tasks 请求体未定义 | 采纳 | 必填 title/content_type，可选评分/摘要/关联热点，status 默认 todo（6.3 已改） |

三、潜在运行时问题：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 13 | outputs_fts 版本行膨胀 | 采纳 | FTS 仅索引每个 task 最新版本（触发器判断 version = MAX），FTS 中每 task 至多一行（5.3 已改） |
| 14 | 外部变更回写版本膨胀 | 采纳 | 外部文件回写不递增版本，更新当前版本行内容（5.3 已改） |
| 15 | 乐观锁 409 与外部修改冲突 | 采纳 | 前端 409 恢复：重新拉取最新版本并提示基于最新版继续编辑，不做自动 merge（6.4 已改） |
| 16 | 调度器与 API 并发竞态 | 采纳 | 所有状态变更用条件 UPDATE（WHERE status IN 允许前置状态），rowcount=0 返回 409（6.8 已改） |

四、细微问题：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 17 | feasibility_score 与 score_breakdown 关系未定义 | 采纳 | score_breakdown 为 JSON 维度明细，feasibility_score = 加权均值四舍五入（5.4 已改） |
| 18 | tags 端点语义不明 | 采纳 | 改为 PUT 替换语义：设置任务全部标签（6.3 已改） |
| 19 | next-themes 面向 Next.js 不适用 | 采纳 | 改 Tailwind darkMode class + React Context，不引入 next-themes（7.1 已改） |
| 20 | api/hooks.ts 过于集中 | 采纳 | 按领域拆分为 api/hooks/ 目录（4.2 已改） |

审阅总结中"硬伤"（#5/#6/#7/#10/#11）、"模糊"（#2/#8/#9/#12/#17/#18）、"运行时风险"（#13/#14/#16）全部按上表落实。
