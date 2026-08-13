# Idea Hub v2 重构设计规格

- 日期：2026-08-14
- 状态：已确认（逐节评审通过）
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
- 次要功能简化：评分三档分流改两档、留档队列砍掉、targets 砍掉
- 新增能力：markdown 产物编辑（编辑/下载/上传/版本）、热点全文搜索、来源测试抓取、知识库底子（FTS5）
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
| D7 | 状态机五状态减为四状态：archived 合并进 todo，低分直接 discard 不进库 | 留档队列砍掉 |
| D8 | targets（目标模式）表砍掉 | 多目标模式实际未用，target 概念并入任务字段 |
| D9 | settings 表砍掉，并入 config 文件 | 配置项少，无需库表 |
| D10 | execute_requests 表砍掉，并入 tasks 执行字段 | 减少表间关联 |
| D11 | 开发方式：垂直切片，8 个切片逐个交付，每片全链路可运行 | 每步有可验收成果 |
| D12 | 产物双写：markdown 落盘 outputs/tasks/<id>/output.md + outputs 表存元数据与内容缓存 | 支持 Web 编辑、版本记录、全文索引，同时保持文件可被外部工具使用 |
| D13 | 保留：SQLite WAL + 每日备份、cron 调度、Basic Auth、Cloudflare Tunnel、Hermes agent 工作流 | 已验证可用，不重写 |

## 4. 整体架构

```
React SPA (web/)
  Vite + shadcn/ui + Tailwind + React Query + TypeScript
        │ REST /api/v1（Basic Auth）
FastAPI 后端（idea_hub/ 包，领域分层）
  routers/ → services/ → models/ → SQLite (WAL)
  收集器 + 评分 + 生成编排 + 执行器 + 调度器
        │ notifications 表 + 事件记录
Hermes（云端 QQ bot，7×24）
  查询状态 / 主动推送 / 菜单操作（经 API 接管）
```

### 4.1 后端目录结构

```
idea_hub/
  main.py            # FastAPI 入口：挂路由、Basic Auth、静态文件、全局异常处理
  db.py              # 连接、schema、轻量迁移、FTS5、WAL 安全备份
  models.py          # 数据访问层（纯 SQL 封装，无业务逻辑）
  routers/
    sources.py       # 来源 CRUD + toggle + test
    hotspots.py      # 热点列表/过滤/搜索
    tasks.py         # 任务 CRUD + move + tags + execute + reset-failures
    outputs.py       # 产物：GET/PUT/upload/versions
    pipeline.py      # collect / generate / execute 触发
    stats.py         # 统计 + 健康
    notifications.py # 通知列表/已读
  services/
    collect.py       # 收集编排（含源头过滤/去重）
    score.py         # 两档分流 + LLM 评分
    generate.py      # 生成编排（Hermes agent 工作流入口）
    execute.py       # 执行编排（幂等）
    notify.py        # 事件写入 notifications
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
    api/hooks.ts         # React Query hooks
    components/
      kanban/            # 看板列、卡片（@dnd-kit 拖拽）
      score/             # 评分徽章、多维评分条、总分色阶
      editor/            # markdown 编辑器（编辑/预览/下载/上传/版本）
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

**核心表（6 张）：**

| 表 | 字段要点 | 说明 |
|---|---|---|
| `sources` | id, type, name, url, enabled, items_path, title_field, keywords, ttl_hours, channel_config | 渠道类型含新增渠道；channel_config 存渠道特定配置（JSON） |
| `hot_items` | id, source_id, title, url, content_snapshot, collected_at, final_score, verdict | verdict: admit/discard（两档） |
| `tasks` | id, title, idea_summary, content_type, status, feasibility_score, score_breakdown, target_id, expire_at, idea_path, output_path, ai_summary, token_used, fail_count, last_fail_reason, redo_note, notes, created_at, updated_at, completed_at | status: todo/waiting/in_progress/done |
| `task_links` | task_id, hot_item_id | 多对多关联 |
| `tags` | id, name, color | 标签 |
| `task_tags` | task_id, tag_id | 标签关联 |

**新表（2 张）：**

| 表 | 字段要点 | 说明 |
|---|---|---|
| `notifications` | id, type, title, body, level, is_read, created_at | 事件记录，Hermes 巡检推送源 |
| `outputs` | id, task_id, filename, content, version, created_at, updated_at | 产物元数据 + markdown 内容缓存，支持版本历史 |

**FTS5 虚拟表（1 张）：**

| 表 | 索引内容 |
|---|---|
| `content_fts` | hot_items(title, content_snapshot) + tasks(title, idea_summary, ai_summary) + outputs(content) |

### 5.2 状态机

- 四状态：`todo`（待办）/ `waiting`（等待）/ `in_progress`（进行中）/ `done`（已完成）
- 状态变更仅通过 move 操作；进入 done 时记录 completed_at
- 低分（< 阈值）直接 discard，不创建任务

### 5.3 产出文件结构

- 落盘：`outputs/tasks/<id>/output.md`
- 双写：outputs 表存元数据 + 内容缓存（版本递增）
- 外部工具可直接编辑落盘文件；Web 编辑后重新同步

## 6. API 设计（/api/v1）

统一响应：`{data, error}`；错误 `{error: {code, message}}`；分页 `{items, total, page, size}`。全部端点 Basic Auth 保护。

### 6.1 sources

```
GET    /api/v1/sources                # 列表（含 channel_config）
POST   /api/v1/sources                # 新建
PATCH  /api/v1/sources/{id}           # 编辑（含 ttl_hours）
POST   /api/v1/sources/{id}/toggle    # 启停
DELETE /api/v1/sources/{id}
POST   /api/v1/sources/{id}/test      # 测试抓取（验证渠道可用性）
```

### 6.2 hotspots

```
GET /api/v1/hotspots?page=&size=&source_id=&verdict=&q=   # 列表 + 过滤 + FTS 搜索
GET /api/v1/hotspots/{id}
```

### 6.3 tasks

```
GET    /api/v1/tasks?status=&page=&size=&q=&tag=
GET    /api/v1/tasks/{id}                    # 详情（含 tags、关联热点、产物）
POST   /api/v1/tasks                         # 手动建任务
PATCH  /api/v1/tasks/{id}                    # 编辑（标题/摘要/评分/备注）
DELETE /api/v1/tasks/{id}
POST   /api/v1/tasks/{id}/move      {to_status}
POST   /api/v1/tasks/{id}/tags      {tag_ids}
POST   /api/v1/tasks/{id}/execute
POST   /api/v1/tasks/{id}/reset-failures
```

### 6.4 outputs

```
GET    /api/v1/tasks/{id}/output             # 取 markdown 正文
PUT    /api/v1/tasks/{id}/output             # 保存编辑（写库 + 落盘 + 版本+1）
POST   /api/v1/tasks/{id}/output/upload      # 上传替换（multipart）
GET    /api/v1/tasks/{id}/output/versions    # 版本历史
```

### 6.5 pipeline

```
POST /api/v1/collect    {source_ids?}        # 收集（可指定来源或全部）
POST /api/v1/generate                        # 生成 idea（Hermes 工作流入口）
POST /api/v1/execute    {task_ids?}          # 批量执行
```

### 6.6 stats / notifications / health

```
GET  /api/v1/stats                           # 队列计数 + token 用量 + 今日产出
GET  /api/v1/notifications?unread_only=
POST /api/v1/notifications/{id}/read
POST /api/v1/notifications/read-all
GET  /api/v1/health                          # 调度器心跳 + 数据库健康
```

## 7. 前端设计

### 7.1 技术底座

- Vite + React 18 + TypeScript
- shadcn/ui（Radix 原语 + Tailwind），简约风
- 暗色模式：next-themes（跟随系统 + 手动切换）
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
| 通知中心 | 列表 + 已读/未读 + 角标 |
| 统计页 | token 用量、队列计数、今日产出、调度器健康条 |

### 7.3 评分展示组件

- 多维评分（事实性/验证需求/时效性等）用分组条形图 + 总分徽章
- 数值色阶：>=8 绿、6-7 黄、<6 红

### 7.4 性能目标

- 看板列数据独立拉取、卡片组件 memo 化、拖拽不整页重渲染
- 交互目标：点击无感知延迟

### 7.5 移动端

- 断点：桌面四列、平板两列、手机单列 + 底部导航

## 8. 垂直切片开发顺序

| 切片 | 内容 | 交付物 |
|---|---|---|
| S1 骨架 | 后端 app/ 包 + schema + FTS5 + 迁移框架；前端脚手架 + 布局壳 + 暗色 | 空库可启动，健康检查通 |
| S2 收集 | collectors 扩渠道（知乎热榜、微博热搜、V2EX 等）+ 源头过滤/去重 + sources CRUD + test 端点 | 手动收集跑通，热点流页面可见 |
| S3 评分 | 两档分流 + LLM 评分（复用现有 scorer 重构） | 收集结果带 verdict |
| S4 生成 | Hermes agent 工作流接入（candidates → generate → import-ideas）+ 标签 | idea 进看板 |
| S5 看板 | 四列看板 + 拖拽 + 搜索 + 任务详情 + 评分组件 | 完整看板可用 |
| S6 执行+产物 | 执行器重构 + outputs 表 + markdown 编辑器 + 下载/上传/版本 | 产物全链路 |
| S7 通知+统计 | 通知事件写入 + 统计页 + 调度器健康 | Hermes 可巡检 |
| S8 部署 | cron 脚本 + 部署文档 + QQ bot 对接验证 | 云端全自动 |

## 9. 错误处理

- 后端：全局异常处理器；业务错误 `{error: {code, message}}`；LLM/抓取失败不中断主流程（降级 + 通知记录）
- 前端：React Query 错误态统一组件（重试按钮）；乐观更新失败自动回滚；网络错误 toast
- 关键操作幂等：执行、生成重复触发不重复产出

## 10. 测试策略

- 后端：pytest + 内存 SQLite；每切片配套服务层单测 + API 集成测试
- 前端：Vitest + React Testing Library；核心交互必须有测试（看板拖拽、评分组件、编辑器）
- e2e：保留一条核心链路测试（收集→评分→生成→执行→产物）
- 回归审阅：实施完经外部 AI 审阅，逐条回应采纳/不采纳并记录

## 11. 知识库演进（后续）

- L1（重构内）：FTS5 全文索引，所有文本内容可检索
- L2（后续）：sqlite-vec 扩展 + DeepSeek embedding API，语义检索
- L3（最后）：检索结果喂给 Hermes/LLM 做 RAG 问答（QQ bot："我写过关于 XX 的内容吗"）

## 12. 回归审阅要点

1. 前端交互响应是否达到"无感知延迟"（S5 验收）
2. 收集质量：新渠道是否有效、源头过滤是否显著降低无用信息（S2 验收）
3. QQ 推送链路：notifications 表事件 → Hermes 巡检 → 群内推送是否完整（S7 验收）
4. 产物编辑：Web 编辑 → 落盘文件一致性、版本递增正确性（S6 验收）
5. 评分展示美观性、暗色模式、移动端适配（S5 验收）
6. 幂等性：重复触发 collect/generate/execute 不产生重复数据
7. FTS5 搜索：热点/任务/产物搜索准确性（S5/S6 验收）
8. 云端全自动运行稳定性（S8 验收）
