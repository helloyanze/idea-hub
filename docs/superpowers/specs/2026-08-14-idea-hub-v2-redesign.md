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
| D13 | 保留：SQLite WAL + 每日备份（含 data/idea.db 与 outputs/ 目录打包）、cron 调度、Basic Auth、Cloudflare Tunnel | 已验证可用，不重写；备份覆盖产物文件，恢复时若文件缺失由 DB 重建 |
| D14 | 生成编排本地化：ideahub 直接调用 DeepSeek LLM 生成 idea（复用执行器的 LLM 调用层 + 生成 prompt 模板），不依赖 Hermes 在线；Hermes 仅作 QQ 交互层（查询/推送/操作） | 消除 ideahub ↔ Hermes 循环耦合；cron 全自动运行不依赖 Hermes 可用性；LLM 调用层已被 executor 验证 |
| D15 | 长任务异步化：pipeline 端点（collect/generate/execute）立即返回 job_id，jobs 表追踪进度，前端轮询（可选 SSE） | 避免请求挂起超时，用户可见进度 |
| D16 | 产物版本：outputs 表 id 自增主键 + (task_id, version) UNIQUE，每版本一行；PUT/上传写入新版本行并更新落盘文件（文件仅保留最新版，历史版本存 DB 可导出）；外部文件回写不递增版本 | 支持真实版本历史回溯，避免自动保存产生垃圾版本；id 主键兼容 FTS5 content_rowid |

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
| `sources` | id, type, name, url, enabled, items_path, title_field, keywords, ttl_hours, channel_config | type 含新增渠道；channel_config 存渠道特定配置（JSON）；ttl_hours = 热点时效窗口，默认 24，允许 NULL（NULL = 热点永不过期）（见 5.4） |
| `hot_items` | id, source_id, title, url, content_snapshot, collected_at, final_score, score_breakdown, verdict, collected_date | verdict: admit/discard；score_breakdown 为评分阶段写入的维度明细（JSON，与 tasks 格式一致）；content_snapshot 截断至 2000 字符；collected_date = DATE(collected_at) 冗余列，专供 discard 清理查询避免函数索引，建 (verdict, collected_date) 联合索引；UNIQUE(source_id, url) 防重复 |
| `tasks` | id, title, idea_summary, ai_summary, content_type, status, feasibility_score, score_breakdown, target_desc, expire_at, token_used, fail_count, last_fail_reason, redo_note, notes, created_at, updated_at, completed_at | status: todo/waiting/in_progress/done；target_desc 为文本（替代 target_id，见 D8）；expire_at 过期逻辑见 5.4；content_type 枚举、score_breakdown 格式、idea_summary/ai_summary 语义见 5.4；落盘路径均为固定模式由 task_id 推导（idea: outputs/tasks/<id>/idea.md，output: outputs/tasks/<id>/output.md），不存字段 |
| `task_links` | task_id, hot_item_id | 多对多关联 |
| `tags` | id, name UNIQUE, color | name 唯一约束 |
| `task_tags` | task_id, tag_id | 标签关联 |
| `settings` | key TEXT PRIMARY KEY, value, value_type | 动态参数：score_todo_threshold、collect_interval_hours、daily_budget_tokens 等（见 D9）；value_type: int/float/string/json |

**新表（4 张）：**

| 表 | 字段要点 | 说明 |
|---|---|---|
| `notifications` | id, type, title, body, level, entity_type, entity_id, is_read, created_at | entity_type/entity_id 关联任务/热点，前端可跳转，Hermes 推送可定位 |
| `outputs` | id INTEGER PK AUTOINCREMENT, task_id, version, filename, content, file_mtime, file_hash, created_at, updated_at | id 为自增主键（FTS5 content_rowid 映射必需）；(task_id, version) UNIQUE 约束，每版本一行；file_mtime/file_hash 供读时校验（见 5.3） |
| `jobs` | id, type, status, progress, result_ref, error, heartbeat_at, token_used, created_at, updated_at | status: pending/running/done/failed；type: collect/generate/execute；result_ref 为 JSON 字符串；heartbeat_at 供崩溃恢复（见 5.5）；token_used 全部类型均记录（execute 与 tasks.token_used 双写），供预算检查与统计（见 5.4） |
| `schema_version` | version | 迁移版本号（未来 schema 演进用，本次不迁移数据） |

**FTS5 虚拟表（3 张，external content + 触发器）：** 全部使用 tokenize='trigram'（SQLite 3.34+，中文按 3-gram 可检索；unicode61 对中文分词无效）

| 表 | 索引内容 | 同步方式 |
|---|---|---|
| `hot_items_fts` | hot_items(title, content_snapshot) | hot_items 表 INSERT/UPDATE/DELETE 触发器 |
| `tasks_fts` | tasks(title, idea_summary, ai_summary) | tasks 表触发器 |
| `outputs_fts` | outputs(content)，content_rowid=outputs.id，附 task_id UNINDEXED 列 | outputs 表触发器（PUT/上传/外部文件回写均经 outputs 表，索引自动刷新）；以单一整数 id 映射 content_rowid（FTS5 external content 要求，复合主键不可用） |

统一搜索：三张 FTS 表分别 MATCH 后 UNION（rank 排序）。FTS5 不支持跨表自动关联，故拆三表 + 触发器，写入路径统一，无同步遗漏。

### 5.2 状态机

- 四状态：`todo`（待办）/ `waiting`（等待）/ `in_progress`（进行中）/ `done`（已完成）
- 无 archived 状态：完成任务停留在 done；低分（< 阈值）直接 discard，不创建任务
- 合法状态迁移矩阵（move 端点与内部状态变更共用）：

| 从 \\ 到 | todo | waiting | in_progress | done |
|---|---|---|---|---|
| todo | - | 允许 | 允许 | 允许（人工） |
| waiting | 允许 | - | 允许 | 允许（人工/过期） |
| in_progress | 禁止 | 允许（执行失败回退） | - | 允许（完成） |
| done | 允许（拖拽回炉，不动 fail_count） | 禁止 | 允许（仅限无产物补执行，条件 UPDATE 限定） | - |

- 所有状态变更必须符合迁移矩阵；move 端点仅允许 API 合法子集（见 6.3），内部服务变更（执行器/调度器）不受 move 端点限制；进入 done 时记录 completed_at
- **两套迁移规则区分**：上表为"内部状态变更"全集（move / execute / redo / 过期 / 执行失败共用）；其中 `done → in_progress` 仅由 execute 端点内部条件 UPDATE 完成（限定 status='done' AND 无 outputs 行），**move 端点一律禁止该迁移**（前端看板拖拽不允许 done → in_progress 列，需先拖回 todo 或触发补执行）
- 执行失败：执行器将任务 in_progress → waiting（fail_count + 1，last_fail_reason 记录），不引入 failed 状态
- 补执行：done 且无产物的任务允许直接触发 execute（done → in_progress，条件 UPDATE 限定 status='done' AND 无 outputs 行），视为补产出
- 过期任务（expire_at < now，见 5.4）：调度器 tick 自动 todo/waiting → done，备注"已过期"并写通知；in_progress 跳过并发警告（避免与执行器写回冲突）
- redo 与 reset-failures 遵循 6.3 前置条件（done 或 fail_count > 0），与上表不冲突（专用端点，不走 move）

### 5.3 产出双写与一致性

- 落盘：`outputs/tasks/<id>/output.md`（仅最新版本；历史版本只存 DB，可通过版本 API 导出）
- 双写：outputs 表每版本一行（content 缓存 + file_mtime + file_hash）
- Web 编辑（PUT）/ 上传：写新版本行（version = max+1）+ 更新落盘文件 + FTS 经触发器刷新
- 外部文件变更检测：读取产物时以文件为准，对比 file_mtime/file_hash，不一致则回写 DB 与 FTS，**不递增版本**（更新当前版本行内容，防编辑器自动保存产生垃圾版本）；不做常驻 watchdog（个人系统，读时校验足够）
- 文件不存在分支：读时校验发现落盘文件缺失（外部误删/恢复不完整）→ 从 DB outputs 表最新版本重建文件，再按正常流程继续
- FTS 索引范围：outputs_fts 仅索引每个 task 的最新版本（触发器判断 version = MAX），FTS 中每个 task 至多一行，避免版本行膨胀与分组复杂度
- outputs_fts 触发器实现策略（SQLite external content 限制）：AFTER INSERT/UPDATE 时若新行是最新版本（version = MAX），先 DELETE 该 task 旧 FTS 行（按旧行 id 定位：SELECT id FROM outputs WHERE task_id=NEW.task_id AND id<>NEW.id ORDER BY version DESC LIMIT 1，DELETE FROM outputs_fts WHERE rowid=旧id），再 INSERT 新行（rowid=NEW.id）；AFTER DELETE 触发器按 rowid=OLD.id 删除对应 FTS 行

### 5.4 字段语义

- `sources.ttl_hours`：该来源热点的时效窗口（小时）。热点 expire_at = collected_at + ttl_hours（动态计算，不落库）；过期热点不再作为生成候选；不参与删除（保留历史）。注意：修改 ttl_hours 会影响该来源全部历史热点的时效判定
- `tasks.expire_at`：任务时效。过期任务由调度器自动完成（move → done + 通知），仅限 todo/waiting；in_progress 跳过并发警告；人工操作不受限
- `tasks.expire_at` 赋值：generate 创建任务时 = 关联热点 collected_at + source.ttl_hours（热点无 ttl_hours 或手动建任务则不设置，为 NULL 表示不过期）；手动建任务 API 支持可选 expire_at
- `hot_items.content_snapshot`：截断至 2000 字符，防长文膨胀 FTS 索引
- `hot_items` 清理策略：discard 热点保留 7 天，调度器每日清理（删行 + FTS 触发器同步）
- `tasks.content_type` 枚举：article（文章，默认）/ video_script（视频脚本）/ tweet（短文）/ newsletter（简报）；生成时由 LLM 根据热点类型与 target_desc 判定，手动建任务可指定
- `tasks.score_breakdown`：JSON 字符串，多维评分明细，如 {"facts": 8, "verification": 7, "timeliness": 9, "value": 8}；维度来源 = settings.score_dimensions（JSON 数组，默认 ["facts","verification","timeliness","value"]，可运行时调整）；feasibility_score = 各维度加权均值（四舍五入）。维度不随 target_desc 变化（target_desc 仅文本描述，不承载配置）
- 评分数据链：评分阶段（S3）写入 hot_items.score_breakdown + final_score；generate 从热点创建任务时继承 score_breakdown 写入 tasks（feasibility_score = final_score）；手动建任务不评分，score_breakdown 为空（评分组件显示"未评分"态）
- 评分触发机制：评分是 collect job 的内部步骤（抓取 → 规则过滤 → LLM 评分 → verdict/final_score/score_breakdown 写入，同一 job 内完成），不设独立 score job；未配置 LLM key 时降级：跳过评分，verdict=admit（全量收录，评分字段为空），generate 候选此时按 collected_at 排序
- 评分权重：默认等权均值（各维度权重 1）；score_dimensions 保持名称数组，暂不支持权重配置
- 评分维度变更：修改 score_dimensions 仅影响后续评分；历史 score_breakdown 按原维度展示，前端评分组件兼容缺失/额外维度（缺失显示 0，额外忽略）
- `tasks.idea_summary`：生成阶段 LLM 产出的一句话摘要（构思主题）；`tasks.ai_summary`：执行完成后 AI 对成文的总结（文章要点）；两者均入 FTS 索引
- idea 文件管理：构思全文落盘 outputs/tasks/<id>/idea.md（固定模式），由 generate 服务（S4）随任务创建时写入；不版本化、不做外部变更检测（只读中间产物）
- `notifications.type` 枚举：collect_done / generate_done / execute_done / job_failed / task_expired / budget_exceeded / discard_cleaned（可扩展）
- `notifications.level` 枚举：info / warn / error；前端角标颜色：info 蓝 / warn 黄 / error 红
- 产物首版创建：执行器执行完成写产物时创建 version=1；编辑器打开无产物时 GET /output 返回 {content: null, version: 0}，PUT 时 base_version=0 表示创建首版
- `outputs.filename` 用途：执行器首版 = "output.md"；上传替换时保存原始文件名（展示与导出用）；导出历史版本下载文件名 = filename
- `outputs.filename` 在 PUT 编辑时：继承当前最新版本的 filename（保持不变），保证历史版本导出文件名稳定；仅上传替换更新为原始名
- 版本递增原子性：PUT/upload 在同一事务内 SELECT MAX(version) → 校验 base_version == MAX → INSERT (MAX+1)，依赖 UNIQUE(task_id, version) 约束兜底，冲突返回 409
- `sources.keywords`：关键词白名单（逗号分隔），标题包含任一关键词才收录；空 = 不过滤；仅匹配标题，不匹配正文
- 标签颜色分配：固定 8 色调色板，新建标签按 (当前标签总数 % 8) 顺序取色；删除重建不复用旧色（按创建顺序轮转），保证幂等
- `tasks.token_used` 统计口径：仅执行阶段 LLM 调用累计 token，redo 重做后累加不清零
- `jobs.token_used`：全部类型 job 均记录（collect/generate/execute）；execute job 的 token 同时写 tasks.token_used 与 jobs.token_used（双写）
- execute job 的 token_used 更新时机：每个任务完成后立即累加更新（与 tasks.token_used 同事务写入），保证预算实时检查可用
- stats 汇总去重：执行累计 = SUM(tasks.token_used)；生成/评分累计 = SUM(jobs.token_used WHERE type IN ('collect','generate'))；展示两数分列，不直接相加
- 每日预算基础：今日消耗 = SUM(jobs.token_used WHERE date(created_at)=today)（含 execute，按日可准确聚合）

### 5.5 异步 job 生命周期

- 状态流转：pending → running → done/failed
- 心跳：子步骤边界更新（每处理完一个 candidate / 一篇产物 / 一个来源）及每次 LLM 尝试前；LLM 单次超时 90s、最多重试 2 次（共 3 次尝试，最坏 270s < 300s 阈值），每次尝试前刷新心跳；stale 阈值 5min
- 崩溃恢复：调度器 tick 发现 running 且 heartbeat_at 超过 5 分钟未更新 → 标记 failed + 写通知
- 崩溃恢复并发安全：标记 failed 用条件 UPDATE（WHERE status='running' AND heartbeat_at < 阈值），rowcount=0 则跳过（job 可能已恢复或已结束），与 6.8 原子变更一致
- job 去重：同类型 job 已有 running 时，POST 返回已有 job_id（不新建）
- result_ref 格式：JSON 字符串，如 {"task_ids": [1,2,3]}（execute/generate）、{"hotspot_count": 42}（collect）
- 调度器持久化（settings 内部键，不通过 PUT 修改）：scheduler_last_tick（每次 tick 更新，/health 判断存活：超过 10 分钟未更新判不健康）；scheduler_last_collect（上次 collect 触发时间，与 collect_interval_hours 配合决定是否触发 collect）
- 内部键初始行为：首次 tick 前两键均不存在——scheduler_last_collect 缺失视为"从未收集"，首个 tick 即触发 collect；scheduler_last_tick 缺失时 /health 返回"调度器从未运行"（不健康）；首个 tick 运行结束写入两键
- 收集频率执行机制：cron 按基础频率（每 5 分钟）唤醒 scheduler tick；tick 读取 collect_interval_hours，距 scheduler_last_collect 超过间隔则触发 collect job 并更新该键——动态参数真正生效
- 调度器触发与去重：tick 触发 collect 走同一 job 去重（同类型 running 存在则跳过本次触发，不更新 scheduler_last_collect，下一 tick 重试）；scheduler_last_collect 在成功创建新 job 后立即更新

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

test 响应：{ok: bool, item_count: int, sample_items: [...], error?: string}——ok=false 时 error 说明失败原因（网络/解析/鉴权），前端展示抓取条目数与示例。test 只验证连通性与解析，**不落库**（sample_items 为内存数据，不写 hot_items）。

### 6.2 hotspots

```
GET /api/v1/hotspots?page=&size=&source_id=&verdict=&q=   # 列表 + 过滤 + FTS 搜索
GET /api/v1/hotspots/{id}
```

q= 走 hot_items_fts 单表检索（页面内过滤）；跨表全局搜索用 /api/v1/search（见 6.7）。tasks?q= 同理走 tasks_fts。

### 6.3 tasks

```
GET    /api/v1/tasks?status=&page=&size=&q=&tag=
GET    /api/v1/tasks/{id}                    # 详情（含 tags、关联热点摘要、产物摘要 {has_output, latest_version, version_count, ai_summary}；正文不随详情返回，编辑器单独 GET /output）
POST   /api/v1/tasks                         # 手动建任务：必填 title、content_type；可选 idea_summary、feasibility_score（默认 0）、score_breakdown、target_desc、notes、hotspot_id（关联热点）、expire_at（不设 = NULL 不过期）；status 默认 todo
PATCH  /api/v1/tasks/{id}                    # 编辑（标题/摘要/评分/备注）
DELETE /api/v1/tasks/{id}                    # 级联：删 task_links、task_tags、outputs 行与落盘文件（outputs/tasks/<id>/ 目录）；notifications 与 jobs 历史保留（跳转 404 时提示"任务已删除"）
POST   /api/v1/tasks/{id}/move      {to_status}   # 遵守 5.2 迁移矩阵；done → in_progress 禁止（补执行仅走 execute）
GET    /api/v1/tags                          # 标签列表（含颜色）
PUT    /api/v1/tasks/{id}/tags      {names: []}  # 替换语义：按名称 upsert（不存在自动创建，颜色从调色板轮转分配），设置任务全部标签
POST   /api/v1/tasks/{id}/execute           # 触发执行（异步，返回 job_id）；允许前置状态：todo / waiting / done（仅限无产物补执行）；in_progress 409。批量 POST /execute 同步校验全部 task_ids：任一非法 → 整体 409 + 附带 {invalid_task_ids: [...]}
POST   /api/v1/tasks/{id}/redo  {note?}     # 重做：仅限 done 或 fail_count > 0（无 failed 状态）；status→waiting、fail_count 清零；redo_note 存时间戳+备注（无 note 仅存时间戳）；其他 409
POST   /api/v1/tasks/{id}/reset-failures    # 清零失败计数：仅限 fail_count > 0；其他 409（区别于 redo：不改变状态）
```

### 6.4 outputs

```
GET    /api/v1/tasks/{id}/output              # 取最新版本 markdown 正文；无产物返回 {content: null, version: 0}
PUT    /api/v1/tasks/{id}/output              # 保存编辑：body 含 content + base_version（无产物时 0 = 创建首版）；乐观锁校验（base_version != MAX 返回 409）；事务内原子递增写新版本 + 落盘。前端 409 恢复：重新拉取最新版本并提示"已有更新，请基于最新版本继续编辑"（不自动 merge）
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

SSE 认证：浏览器 EventSource 无法携带 Authorization header，SSE 端点定位为内部/服务端消费接口（Hermes 巡检、运维脚本用 Basic Auth 访问）；前端一律轮询，不使用 SSE。

### 6.6 stats / notifications / health / settings

```
GET  /api/v1/stats                           # 队列计数 + token 用量 + 今日产出 + 活跃 job
GET  /api/v1/notifications?unread_only=&entity_type=&entity_id=&type=
POST /api/v1/notifications/{id}/read
POST /api/v1/notifications/read-all
GET  /api/v1/health                          # 调度器心跳 + 数据库健康
GET /api/v1/settings                     # 仅返回用户可配置键（7 个预定义键）；内部键（scheduler_last_tick/scheduler_last_collect）不对外暴露，由 /health 与调度器内部使用
PUT /api/v1/settings  {key: value}       # 更新单键（按 value_type 校验 int/float/string/json）
```

settings 校验失败响应：400 + {error: {code: "INVALID_SETTING_VALUE", message}}，与全局错误格式一致。

settings 初始键（S1 schema 初始化时写入，PUT 仅允许更新预定义键，未知键返回 400 UNKNOWN_SETTING）：

| key | value_type | 默认值 | 说明 |
|---|---|---|---|
| score_todo_threshold | int | 8 | 生成分流：>= 阈值入 todo，否则 discard |
| collect_interval_hours | int | 24 | 动态收集间隔：scheduler tick 读取并决定是否触发 collect |
| daily_budget_tokens | int | 50000 | 每日执行预算上限 |
| score_dimensions | json | ["facts","verification","timeliness","value"] | 评分维度 |
| generate_count | int | 10 | generate 默认候选数 |
| done_column_limit | int | 50 | 看板 done 列默认加载条数 |
| discard_retention_days | int | 7 | discard 热点保留天数 |

### 6.7 统一搜索

```
GET /api/v1/search?q=&page=&size=     # 跨热点/任务/产物统一检索
```

实现：三张 FTS 表分别 MATCH 后 UNION；每条结果结构：{entity_type, entity_id, title, snippet, score}（snippet = 命中位置前后截取片段，title = 该实体标题，score = 组内 bm25 排序用）。output 类型结果的 entity_id = task_id（前端跳转任务详情；outputs_fts 的 task_id UNINDEXED 列提供该值，不用 outputs.id）。支持过滤器扩展（entity_type=、source_id=、status=）。

分页策略：三表分别 MATCH 各取前 page × size 条（保证后续页偏移覆盖）→ 应用层按 entity_type 分组（组内 bm25 排序）→ 合并列表后按 page/size 统一分页返回（每页可含多组结果）；前端展示时按 entity_type 分组渲染。

与单表搜索的关系：/hotspots?q=、/tasks?q= 为单表 FTS 检索（页面内过滤），/search 为三表 UNION 全局检索；共用同一匹配实现，仅作用域不同。

### 6.8 统一约定

- 长任务端点一律异步：立即返回 job_id，不阻塞 HTTP 请求
- 写操作幂等：collect/generate/execute 重复触发不重复产出（同类型 running job 去重，返回已有 job_id；执行器幂等兜底）
- 乐观锁：outputs PUT 必须带 base_version
- 限流：应用层请求频率限制 + 登录失败延迟；文档注明可升级 Cloudflare Access
- 原子状态变更：所有任务状态变更（move / execute / redo / 过期完成）用条件 UPDATE（WHERE status IN 允许前置状态），rowcount=0 视为冲突返回 409，杜绝调度器 tick 与 API 并发竞态
- 内部服务写回冲突：执行器/调度器内部条件 UPDATE 失败（rowcount=0，任务已被用户移走/完成）时——产物仍写入 outputs（不丢数据），状态不修改（不覆盖用户操作），写 warn 通知（"任务状态已被外部变更，产物已保存但状态未更新"），该任务计入 job 的 failed_items/skipped，job 继续处理其余任务
- job 去重粒度：type 级别（不区分候选集）；generate 显式传 hotspot_ids 在已有 running generate job 时同样返回已有 job_id，不享受例外；去重命中时响应 {job_id, reused: true}，前端提示"已有进行中的任务"
- execute 幂等兜底：执行器启动前检查 outputs 表——该 task 已有产物行且 status=done → 跳过（视为已执行）；status=done 但无产物（手动移入）→ 允许执行；redo 重置状态后重新可执行
- DELETE source 并发安全：检查关联 → 删除在事务内完成（BEGIN IMMEDIATE check-then-delete）；collect job 仅处理 enabled=1 的来源（删除前需先 toggle 禁用，天然避免写入竞态）
- job 部分成功语义：批量 job 部分失败 → 状态 done + result_ref 含 failed_items 明细（[{"task_id": 5, "error": "..."}]）；全部失败 → failed；error 字段存整体错误或首个错误
- 部分成功通知规则：全部成功 → info（execute_done/generate_done）；部分失败 → warn（同类型事件 + failed_items 数量摘要）；全部失败 → error（job_failed）。每批 job 写一条聚合通知
- 每日预算闭环（不预估，实时检查）：execute job 启动时检查今日消耗（SUM(jobs.token_used WHERE date(created_at)=today)，含 execute）> daily_budget_tokens → job 直接 failed + budget_exceeded 通知，不执行；执行中每完成一个任务实时检查累计消耗，达到预算则停止后续任务（已完成保留，未完成移回 waiting）
- SQLite 外键：db.py 连接时 PRAGMA foreign_keys=ON；级联删除不依赖 ON DELETE CASCADE（需同步 FTS 触发器），由 services 层显式事务删除

## 7. 前端设计

### 7.1 技术底座

- Vite + React 18 + TypeScript
- shadcn/ui（Radix 原语 + Tailwind），简约风
- 暗色模式：Tailwind darkMode: 'class' + 轻量 React Context（跟随系统 + 手动切换）；不引入 next-themes（面向 Next.js，本项目为 Vite SPA）
- React Query：缓存 + 请求去重 + 乐观更新（解决卡顿）
- @dnd-kit：看板拖拽
- 布局：左侧窄栏导航 + 主内容区（解决左侧空白）
- 认证：App 启动时检测本地凭据，无凭据显示登录表单（Basic Auth 用户名/密码）；凭据存 localStorage，fetch 封装自动附带 Authorization header；401 时清凭据回登录页

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
- 未评分态：score_breakdown 为空时显示"未评分"占位（手动建任务）

### 7.4 长任务交互

- 触发收集/生成/执行后：页面显示 job 进度条（React Query 轮询 jobs/{id}，或 SSE 订阅 jobs/{id}/stream，仅订阅自己触发的 job）
- 任务完成/失败 toast 提示；失败可查看 error 详情与重试

### 7.5 性能目标

- 看板列数据独立拉取、卡片组件 memo 化、拖拽不整页重渲染
- 交互目标：点击无感知延迟
- done 列加载策略：从 GET /settings 读取 done_column_limit（读取失败回退 50），默认仅加载最近 N 条（completed_at 降序）+ "加载更多"分页；拖拽到 done 的新任务乐观插入列顶；其余列数量少，全量加载

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
- 备份恢复：每日备份 = data/idea.db + outputs/ 目录（tar 打包）；恢复时若某 task 落盘文件缺失（备份后新增/外部误删），读时校验发现文件不存在 → 从 DB outputs 表最新版本重建文件
- 备份一致性：WAL 模式下不直接复制主库文件（会丢失未 checkpoint 写入）——用 SQLite 在线备份 API（sqlite3 .backup / VACUUM INTO）生成一致性快照后再打包，或同时打包 -wal/-shm 文件

## 10. 测试策略

- 后端：pytest + 内存 SQLite；每切片配套服务层单测 + API 集成测试（jobs 异步、FTS 搜索、版本乐观锁、过期处理、读时校验必测）
- discard 清理测试必覆盖：删 hot_items 行后 FTS 索引同步清除、搜索不返回已删除结果（S7 验收）
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
| 5 | outputs_fts 版本行重复命中 | 采纳（后被第四轮 #13 修订） | 原方案为查询时按 task_id 分组；第四轮 #13 改为索引时过滤——FTS 仅索引每 task 最新版本（5.3），取代查询时分组，避免 rank 精度损失 |
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

### 第五轮审阅回应（2026-08-14）

一、内部不一致：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 1 | 第二轮 #5 与 5.3 矛盾未清理 | 采纳 | 第二轮 #5 记录更新，注明被第四轮 #13 修订为索引时过滤（已改） |
| 2 | idea_path 路径格式未定义 | 采纳 | 删除 idea_path 字段：构思全文固定模式 outputs/tasks/<id>/idea.md，与产物路径对称（5.1 已改） |
| 3 | ai_summary 与 idea_summary 区别未说明 | 采纳 | idea_summary = 生成阶段一句话摘要；ai_summary = 执行后成文总结；均入 FTS（5.4 已改） |
| 4 | redo 前置状态缺失 | 采纳 | redo 仅限 done/failed，其他状态 409（6.3 已改） |
| 5 | reset-failures 前置状态缺失 | 采纳 | 仅限 fail_count > 0，其他 409（6.3 已改） |

二、设计缺口：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 6 | outputs_fts 触发器实现受 SQLite external content 限制 | 采纳 | 注明先删后插两段式策略（按 task_id 删旧行再插新行），AFTER DELETE 同步删 FTS（5.3 已改） |
| 7 | generate job 去重粒度未说明 | 采纳 | 去重为 type 级别，显式 hotspot_ids 不享受例外（6.8 已改） |
| 8 | execute 幂等兜底未定义 | 采纳 | 执行器检查 outputs 表 + status：有产物且 done 跳过；done 无产物允许执行；redo 后重新可执行（6.8 已改） |
| 9 | idea_path 写入时机未说明 | 采纳 | 与 #2 合并：字段删除；idea.md 由 generate 服务随任务创建写入，不版本化（5.4 已改） |
| 10 | settings 校验失败响应未定义 | 采纳 | 400 + INVALID_SETTING_VALUE，与全局错误格式一致（6.6 已改） |
| 11 | sources test 响应格式未定义 | 采纳 | {ok, item_count, sample_items, error?}（6.1 已改） |

三、潜在运行时问题：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 12 | DELETE source 409 检查竞态 | 采纳 | 事务内 check-then-delete + collect 仅处理 enabled 来源（6.8 已改） |
| 13 | 调度器 failed 标记与 job 写回并发 | 采纳 | failed 标记用条件 UPDATE（status='running' AND heartbeat_at < 阈值），rowcount=0 跳过（5.5 已改） |
| 14 | done 列分页策略缺失 | 采纳 | 默认加载最近 50 条 + 加载更多；拖拽乐观插入列顶（7.5 已改） |

四、细微问题：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 15 | notifications.type 枚举未定义 | 采纳 | collect_done/generate_done/execute_done/job_failed/task_expired/budget_exceeded/discard_cleaned；查询端点加 type= 过滤（5.4/6.6 已改） |
| 16 | notifications.level 枚举未定义 | 采纳 | info/warn/error，前端角标蓝/黄/红（5.4 已改） |
| 17 | token_used 统计口径未说明 | 采纳 | 执行阶段累计、redo 累加不清零；生成/评分计入 jobs.token_used；stats = SUM(tasks)+SUM(jobs)（5.4 已改） |
| 18 | discard 清理的 FTS 测试未覆盖 | 采纳 | 单测必覆盖：删 hot_items 后 FTS 同步清除、搜索不返回已删结果（10 已改） |

### 第六轮审阅回应（2026-08-14）

高优先级（内部矛盾 / 实现阻塞）：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 1 | outputs 复合主键与 FTS5 external content 冲突 | 采纳 | outputs 加自增 id 主键（content_rowid 映射），(task_id, version) 改 UNIQUE 约束；触发器按 id 定位删旧插新（5.1/5.3 已改） |
| 2 | redo 的 failed 与四状态矛盾 | 采纳 | 不引入 failed 状态：执行失败 in_progress → waiting（fail_count+1）；redo 条件改为 done 或 fail_count > 0（5.2/6.3 已改） |
| 3 | move 缺少状态迁移矩阵 | 采纳 | 补充完整迁移矩阵（todo/waiting/in_progress/done 双向规则 + 执行失败回退 + 过期 + 拖拽回炉）（5.2 已改） |
| 4 | 评分维度"随目标可配"无配置源 | 采纳 | 维度来源 = settings.score_dimensions（默认四维，可调），不随 target_desc 变化（5.4 已改） |
| 5 | outputs 首版与乐观锁并发未定义 | 采纳 | 首版由执行器创建 version=1；无产物时 GET 返回 version 0、PUT base_version=0 创建首版；事务内原子递增 + UNIQUE 兜底 409（5.4/6.4 已改） |
| 6 | settings 键集合与默认值缺失 | 采纳 | 列出 7 个初始键及默认值；PUT 仅允许预定义键（6.6 已改） |

中优先级（语义缺口 / 运行时风险）：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 7 | 每日预算缺少执行闭环 | 采纳 | execute job 启动时检查预算，超限直接 failed + budget_exceeded 通知；执行中达预算停止后续任务（6.8 已改） |
| 8 | job 部分成功语义不清 | 采纳 | 部分失败 → done + result_ref.failed_items 明细；全部失败 → failed（6.8 已改） |
| 9 | token 统计可能重复 | 采纳 | jobs.token_used 仅 collect/generate 类型；execute job 的 token 全入 tasks.token_used（5.4 已改） |
| 10 | 备份未覆盖落盘产物 | 采纳 | 每日备份含 outputs/ 目录；恢复时文件缺失由 DB 重建（D13/9 已改） |
| 11 | SSE 与 Basic Auth 不兼容 | 采纳 | SSE 定位为内部/服务端消费接口（Basic Auth 可访问）；前端一律轮询（6.5 已改） |
| 12 | FTS5 中文 tokenizer 未定义 | 采纳 | 全部用 tokenize='trigram'（中文 3-gram 可检索）；统一搜索按 entity_type 分组、组内 bm25 排序，不做跨表 rank 比较（5.1/6.7 已改） |

低优先级（实现细节）：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 13 | 去重返回已有 job_id 前端不透明 | 采纳 | 响应加 {job_id, reused: true}，前端提示（6.8 已改） |
| 14 | sources.keywords 语义未定义 | 采纳 | 标题关键词白名单（逗号分隔），空 = 不过滤，仅匹配标题（5.4 已改） |
| 15 | SQLite 外键约束未说明 | 采纳 | PRAGMA foreign_keys=ON；级联删除由 services 显式事务处理（6.8 已改） |
| 16 | 前端 Basic Auth 凭据管理未定义 | 采纳 | 登录表单 + localStorage 存凭据 + 401 回登录页（7.1 已改） |

### 第七轮审阅回应（2026-08-14）

高优先级（数据链 / 状态机闭环）：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 1 | done→in_progress 禁止与 execute 兜底矛盾 | 采纳 | 矩阵放开 done → in_progress 仅限无产物补执行（条件 UPDATE 限定 status='done' AND 无 outputs 行）；execute 允许 todo/waiting/done(无产物)，in_progress 409（5.2/6.3 已改） |
| 2 | 评分明细数据链断裂 | 采纳 | hot_items 增加 score_breakdown（评分阶段写入）；generate 继承到 tasks（feasibility_score = final_score）；手动任务为空显示未评分（5.1/5.4/7.3 已改） |
| 3 | 每日执行预算统计不可实现 | 采纳 | jobs.token_used 覆盖全部类型（execute 双写 tasks+job）；今日消耗 = SUM(jobs.token_used WHERE date(created_at)=today)；stats 两数分列展示防重复（5.4/6.8 已改） |
| 4 | expire_at 赋值来源未定义 | 采纳 | generate 继承热点 collected_at + ttl_hours；手动任务 API 加可选 expire_at；NULL = 不过期（5.4/6.3 已改） |
| 5 | tags 无创建来源 | 采纳 | 新增 GET /tags；PUT /tasks/{id}/tags 改按名称 upsert（自动创建 + 调色板轮转颜色）（6.3 已改） |

中优先级（执行机制 / 数据源）：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 6 | collect_interval_hours 无执行机制 | 采纳 | cron 每 5 分钟唤醒 tick；tick 读 collect_interval_hours 与 scheduler_last_collect 决定是否触发 collect（5.5 已改） |
| 7 | 调度器健康缺持久化数据源 | 采纳 | settings 内部键 scheduler_last_tick（tick 每次更新，/health 超 10 分钟判不健康）（5.5 已改） |
| 8 | 评分维度权重未定义 | 采纳 | 默认等权均值，score_dimensions 为名称数组，暂不支持权重（5.4 已改） |
| 9 | outputs.filename 用途未定义 | 采纳 | 首版 "output.md"；上传保存原始名；导出下载用 filename（5.4 已改） |
| 10 | 统一搜索 output entity_id 指向不明 | 采纳 | output 结果 entity_id = task_id（跳转任务详情），由 outputs_fts.task_id 列提供（6.7 已改） |
| 11 | 文件缺失恢复未写入 5.3 | 采纳 | 5.3 增加文件不存在分支：从 DB 最新版本重建（5.3 已改） |
| 12 | done_column_limit 与前端硬编码不一致 | 采纳 | 前端从 GET /settings 读取，失败回退 50（7.5 已改） |

低优先级：

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 13 | GET /tasks/{id} 产物返回内容未定义 | 采纳 | 详情含产物摘要（has_output/latest_version/version_count/ai_summary），正文单独 GET（6.3 已改） |
| 14 | 部分成功通知规则未定义 | 采纳 | 全成功 info / 部分失败 warn / 全失败 error，每批一条聚合通知（6.8 已改） |
| 15 | settings 未知键拒绝的演进提示 | 采纳（记录） | 新增动态参数需同步 schema 初始化；实施时保持初始化集中管理（S1 实现） |

### 第八轮审阅回应（2026-08-14）

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 1 | move 端点与 done→in_progress 例外未协调 | 采纳 | 明确两套迁移规则：矩阵为内部变更全集，done→in_progress 仅 execute 内部条件 UPDATE 完成，move 端点一律禁止（前端拖拽不允许）（5.2/6.3 已改） |
| 2 | 预估执行成本未定义 | 采纳 | 简化方案：不预估——job 启动检查历史消耗（超限直接 failed），执行中每完成一个实时检查，达预算停止后续（未完成移回 waiting）（6.8 已改） |
| 3 | 统一搜索返回结构与分页未定义 | 采纳 | 结果结构 {entity_type, entity_id, title, snippet, score}；分页 = 三表各取 size → 分组排序 → 合并统一分页（6.7 已改） |
| 4 | settings 内部键是否暴露未明确 | 采纳 | GET /settings 仅返回用户可配置键，内部键不对外（6.6 已改） |
| 5 | sources.ttl_hours 可空性未定义 | 采纳 | 默认 24，允许 NULL（NULL = 永不过期）（5.1 已改） |
| 6 | 调度器 collect 去重与 last_collect 时机未明确 | 采纳 | tick 触发走同一 job 去重（running 存在则跳过且不更新 last_collect）；成功创建 job 后立即更新（5.5 已改） |
| 7 | PUT 编辑时 filename 未定义 | 采纳 | PUT 继承当前最新版 filename 保持不变，仅上传替换更新（5.4 已改） |
| 8 | 分页策略强调 | 并入 #3 | 见上 |
| 9 | collect_interval_hours 描述过时 | 采纳 | 更新为"动态收集间隔，scheduler tick 读取"（6.6 已改） |
| 10 | 部分成功通知聚合提示 | 采纳（记录） | 实现时注意聚合逻辑（每批一条） |
| 11 | hot_items_fts 不含 score_breakdown 无需处理 | 确认 | 无改动（评分变化不影响 FTS） |

### 第九轮审阅回应（2026-08-14）

| # | 意见 | 决定 | 处理 |
|---|---|---|---|
| 1 | 评分环节缺失触发机制（核心链路断裂） | 采纳 | 评分是 collect job 内部步骤（抓取→过滤→评分→verdict 写入同一 job）；无 LLM key 降级 verdict=admit 全收，generate 候选按时间排序（5.4 已改） |
| 2 | 内部状态变更并发冲突未定义 | 采纳 | 内部条件 UPDATE 失败时：产物仍写入、状态不修改、warn 通知、计入 failed_items，不覆盖用户操作（6.8 已改） |
| 3 | 统一搜索分页 page>1 丢数据 | 采纳 | 三表各取 page × size 条再合并分页，偏移覆盖完整（6.7 已改） |
| 4 | 备份遗漏 WAL 文件 | 采纳 | 用 SQLite 在线备份 API（.backup / VACUUM INTO）生成一致性快照，或打包 -wal/-shm（9 已改） |
| 5 | execute 部分非法 task_ids 未定义 | 采纳 | 批量同步校验全部：任一非法 → 整体 409 + invalid_task_ids（6.3 已改） |
| 6 | "状态变更仅通过 move"表述矛盾 | 采纳 | 统一措辞：所有变更必须符合迁移矩阵，move 端点仅 API 合法子集（5.2 已改） |
| 7 | 内部 settings 键初始值未定义 | 采纳 | last_collect 缺失视为从未收集（首 tick 触发）；last_tick 缺失 /health 判"从未运行"（5.5 已改） |
| 8 | execute token_used 实时更新时机未定义 | 采纳 | 每任务完成后立即累加更新（与 tasks.token_used 同事务），预算实时检查可用（5.4 已改） |
| 9 | sources.test 是否写库未定义 | 采纳 | test 只验证连通性与解析，不落库（6.1 已改） |
| 10 | jobs.token_used 表说明旧口径 | 采纳 | 更新为全部类型均记录（execute 双写）（5.1 已改） |
| 11 | 标签调色板轮转规则未定义 | 采纳 | 固定 8 色，按 (标签总数 % 8) 顺序取色，删除重建不复用（5.4 已改） |
| 12 | score_dimensions 修改对历史影响未说明 | 采纳 | 仅影响后续评分，历史按原维度展示，前端兼容缺失/额外维度（5.4 已改） |
| 13 | 前端 reused 避免重复轮询 | 采纳（记录） | 前端维护活跃 job_id 集合，reused 时复用已有轮询（S5 实现） |
