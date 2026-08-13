# Idea Hub v2 全栈重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Idea Hub v1 重构为 React + FastAPI 领域分层架构，8 个垂直切片（S1-S8）逐个交付，每片全链路可运行。

**Architecture:** React SPA（Vite + shadcn/ui + React Query）消费 /api/v1 REST；FastAPI 后端按领域分层（routers → services → models → SQLite WAL）；FTS5 trigram 全文索引（热点/任务/产物三表 + 触发器）；长任务全部异步（jobs 表 + 心跳 + 崩溃恢复）；生成/执行直接调 DeepSeek（生成本地化，Hermes 仅 QQ 交互层）。

**Tech Stack:** Python 3.11+ / uv / FastAPI / SQLite(WAL+FTS5) / pytest；React 18 / Vite / TypeScript / shadcn-ui / Tailwind / React Query / @dnd-kit / Vitest。

**设计规格（权威）：** `docs/superpowers/specs/2026-08-14-idea-hub-v2-redesign.md`（含九轮审阅，125 条决策）。本计划所有接口、字段、语义以 spec 为准，冲突时 spec 优先。

**竞品参考（已浅克隆至 D:\Programs\idea-hub-refs\）：** tech-content-curator（多维评分结构、收集器按源拆分、URL+语义去重）、ai-content（Source 类型化 rss/api/crawler、素材库）、AutoContents（RSS 聚合 + 关键词过滤 + 推送闭环）。实现时按需查阅。

## Global Constraints

- Python 3.11+，依赖管理用 uv；包名保持 `idea_hub`
- SQLite WAL 模式；连接时 `PRAGMA foreign_keys=ON`；所有 FTS5 表 `tokenize='trigram'`
- API 全部在 `/api/v1` 前缀下；统一响应 `{data, error}`；错误 `{error: {code, message}}`；分页 `{items, total, page, size}`
- 全部端点 Basic Auth 保护（config 读取凭据）+ 应用层限流
- 所有任务状态变更用条件 UPDATE（WHERE status IN 允许前置状态），rowcount=0 → API 层 409
- 长任务端点立即返回 `{job_id}`；job 心跳在子步骤边界 + LLM 尝试前更新（单次 LLM 超时 90s、最多重试 2 次）
- 版本递增原子性：同一事务内 SELECT MAX → 校验 base_version → INSERT，UNIQUE(task_id, version) 兜底
- 数据全部重来（D3）：开发期直接删 data/、outputs/，不迁移
- 代码统一用 codex CLI 编写（`codex exec`），本计划提供接口签名与测试断言，实现细节由 codex 完成
- 产出文件固定模式：`outputs/tasks/<id>/idea.md`、`outputs/tasks/<id>/output.md`
- 所有交付内容（代码/文档/提交信息）不含 emoji

---

## S1 骨架（后端包结构 + schema + 前端脚手架）

### Task S1.1: 后端包结构与 config

**Files:**
- Create: `idea_hub/__init__.py`, `idea_hub/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.load(path: str | None = None) -> Config`；`Config` 字段：`host, port, db_path, base_path, auth_user, auth_pass, deepseek_api_key, rate_limit_per_min, log_level`

- [ ] **Step 1: 写失败测试** — 断言 `config.load()` 读默认路径（`data/idea.db`、base 为 cwd）、`config.load("tests/fixtures/config.yaml")` 读自定义值、缺 auth_user 时抛 `ConfigError`
- [ ] **Step 2: 运行测试确认失败** — `uv run pytest tests/test_config.py -v`，Expected: FAIL（模块不存在）
- [ ] **Step 3: 实现** — `config.py` 用 pyyaml 读 `config.yaml`（不存在则用默认值），支持 env 覆盖（`DEEPSEEK_API_KEY` 等）
- [ ] **Step 4: 运行测试确认通过** — Expected: PASS
- [ ] **Step 5: Commit** — `git add idea_hub tests && git commit -m "feat(config): typed config loader with env override"`

### Task S1.2: db.py — schema、FTS5 触发器、settings 初始键

**Files:**
- Create: `idea_hub/db.py`
- Test: `tests/test_db_schema.py`

**Interfaces:**
- Produces: `db.connect(path: str) -> sqlite3.Connection`（row_factory=Row、foreign_keys=ON、WAL）；`db.init_schema(conn)`（幂等建全部表 + FTS 触发器 + settings 初始键 + schema_version=1）；`db.backup(db_path, dest_path)`（SQLite online backup API）
- Consumes: `config.Config`

**表清单（spec 5.1）：** sources / hot_items / tasks / task_links / tags / task_tags / settings / notifications / outputs / jobs / schema_version + FTS 虚拟表 hot_items_fts / tasks_fts / outputs_fts

**settings 初始键（spec 6.6）：** score_todo_threshold=8(int)、collect_interval_hours=24(int)、daily_budget_tokens=50000(int)、score_dimensions=["facts","verification","timeliness","value"](json)、generate_count=10(int)、done_column_limit=50(int)、discard_retention_days=7(int)

- [ ] **Step 1: 写失败测试** — `test_init_schema_creates_all_tables`（查 sqlite_master 含 11 张表 + 3 张 fts 表）、`test_settings_seeded`（settings 表有 7 键且 value_type 正确）、`test_fts_triggers_exist`（sqlite_master 含 hot_items_ai/au/ad、tasks_ai/au/ad、outputs_ai/au/ad 触发器）
- [ ] **Step 2: 运行确认失败** — Expected: FAIL（ImportError）
- [ ] **Step 3: 实现 db.py** — 关键：outputs 表 `id INTEGER PRIMARY KEY AUTOINCREMENT` + `UNIQUE(task_id, version)`；FTS 触发器实现 spec 5.3 先删后插策略（outputs_fts 的 INSERT/UPDATE 触发器：仅当新行 version=MAX 时，先按旧行 id DELETE 再 INSERT 新行）
- [ ] **Step 4: 运行确认通过** — `uv run pytest tests/test_db_schema.py -v`
- [ ] **Step 5: Commit** — `git commit -m "feat(db): full schema with FTS5 trigram triggers and settings seed"`

### Task S1.3: main.py — FastAPI 入口、Basic Auth、限流、全局异常

**Files:**
- Create: `idea_hub/main.py`（含 routers 占位注册）
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `app = FastAPI()`；`create_app(config) -> FastAPI`（可测试注入）；中间件：BasicAuth（401 无凭据）、限流（慢启动令牌桶，超过 rate_limit_per_min 返回 429 `{error:{code:"RATE_LIMITED"}}`）；全局异常处理器：业务 `AppError` → 对应 4xx `{error:{code,message}}`，未知异常 → 500 `{error:{code:"INTERNAL"}}`
- Consumes: `config.Config`

- [ ] **Step 1: 写失败测试** — `test_health_no_auth_401`、`test_health_with_auth_200`、`test_rate_limit_429`（连发 N+1 次）、`test_unknown_error_500_shape`（注入抛异常路由）
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — `GET /api/v1/health` 返回 `{data:{status:"ok", db:"ok", scheduler_last_tick: null}}`；Auth 用 `fastapi.security.HTTPBasic`；限流用内存字典 + 时间窗
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(api): app factory, basic auth, rate limit, error contract"`

### Task S1.4: 前端脚手架 — Vite + React + shadcn + 布局壳 + 登录

**Files:**
- Create: `web/`（`pnpm create vite` 模板 react-ts）、`web/src/App.tsx`、`web/src/api/client.ts`、`web/src/components/LoginForm.tsx`、`web/src/components/ThemeToggle.tsx`、`web/src/lib/theme.tsx`、`tailwind.config.js`、`components.json`
- Test: `web/src/App.test.tsx`（Vitest + RTL）

**Interfaces:**
- Produces: `api/client.ts` 导出 `apiFetch(path, options)`（自动带 localStorage 凭据的 Authorization header，401 时清凭据触发回登录页，统一 `{data,error}` 解包抛 `ApiError{code,message}`）；`ThemeProvider`（Tailwind `darkMode:'class'` + 跟随系统 + 手动切换，localStorage 持久化）；`App` 组件：无凭据显示 LoginForm，有凭据显示左侧导航 + 主内容区 + ThemeToggle
- Consumes: `/api/v1/health`（登录后探活）

- [ ] **Step 1: 脚手架** — `cd web && pnpm create vite . --template react-ts && pnpm add @tanstack/react-query tailwindcss @tailwindcss/vite class-variance-authority clsx tailwind-merge lucide-react && pnpm dlx shadcn@latest init`
- [ ] **Step 2: 写登录/认证测试** — `test_login_success_sets_credential`（mock fetch，登录后 localStorage 有凭据）、`test_401_clears_and_returns_login`、`test_theme_toggle_switches_dark_class`
- [ ] **Step 3: 实现布局壳** — 左侧窄导航（看板/热点/来源/通知/统计占位项）+ 暗色切换；Tailwind dark 类模式
- [ ] **Step 4: 运行前端测试** — `pnpm test`，Expected: PASS；`pnpm build` 无 TS 错误
- [ ] **Step 5: Commit** — `git commit -m "feat(web): vite scaffold, auth-aware api client, layout shell, theme"`

**S1 验收：** `uv run uvicorn idea_hub.main:app --port 8000` 启动，`curl -u user:pass http://127.0.0.1:8000/api/v1/health` 返回 ok；前端 `pnpm dev` 登录页可访问、暗色切换生效。

---

## S2 收集（渠道扩展 + 过滤去重 + sources API + collect job）

### Task S2.1: collectors 基类与现有渠道重构

**Files:**
- Create: `idea_hub/collectors/base.py`, `idea_hub/collectors/hotlist.py`, `idea_hub/collectors/rss.py`, `idea_hub/collectors/github.py`, `idea_hub/collectors/hackernews.py`, `idea_hub/collectors/__init__.py`（orchestrator）
- Test: `tests/test_collectors_v2.py`

**Interfaces:**
- Produces: `BaseCollector.fetch() -> list[RawItem]`；`RawItem = {title, url, content_snapshot, source_id}`；`orchestrator.collect_all(conn, source_ids: list[int] | None, limit_per_source=50) -> CollectResult{items, errors}`（仅处理 enabled=1 来源，逐个 try/except 不中断）；`collector_registry: dict[str, type[BaseCollector]]`（按 sources.type 注册）
- Consumes: db schema（sources 表）

- [ ] **Step 1: 写失败测试** — `test_hotlist_parse`（用百度热榜 API 样例 JSON 断言提取 title/url）、`test_rss_parse`（feedparser 样例）、`test_github_trending_parse`、`test_hn_parse`、`test_orchestrator_skips_disabled_and_continues_on_error`（一个来源抛错其余照常）
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — 从 v1 `collectors.py` 迁移逻辑，按源拆分文件；base 定义 `__init__(self, source_row: dict)`；orchestrator 用 registry 实例化
- [ ] **Step 4: 运行确认通过** — `uv run pytest tests/test_collectors_v2.py -v`
- [ ] **Step 5: Commit** — `git commit -m "feat(collect): collector registry with per-source modules"`

### Task S2.2: 新渠道（知乎热榜、微博热搜、V2EX）

**Files:**
- Create: `idea_hub/collectors/zhihu.py`, `idea_hub/collectors/weibo.py`, `idea_hub/collectors/v2ex.py`
- Test: `tests/test_collectors_new_channels.py`

**Interfaces:**
- Produces: 三个渠道各实现 `BaseCollector`，注册 type：`zhihu-hotlist` / `weibo-hotlist` / `v2ex`；各自 `fetch()` 返回 RawItem 列表
- Consumes: `collector_registry`

- [ ] **Step 1: 写失败测试** — 用各渠道真实 API 样例断言解析（知乎 `https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50` 的 JSON 结构；微博 `https://weibo.com/ajax/side/hotSearch` 的 data.realtime[].word；V2EX `https://www.v2ex.com/api/topics/hot.json`）
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — 注意 UA 伪装与超时（httpx timeout=10s）；微博需 `User-Agent` 头；解析失败抛 `CollectorError` 由 orchestrator 捕获
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(collect): zhihu/weibo/v2ex channels"`

### Task S2.3: 源头过滤与 URL 去重

**Files:**
- Create: `idea_hub/services/filtering.py`
- Test: `tests/test_filtering.py`

**Interfaces:**
- Produces: `apply_keywords_filter(items, keywords: str) -> list[RawItem]`（标题包含任一关键词才保留，空 = 不过滤，spec 5.4）；`dedup_by_url(conn, items) -> list[RawItem]`（UNIQUE(source_id,url) 冲突 + 内存集合去重，并发 collect 安全，spec 5.1）；`truncate_snapshot(text, max_len=2000) -> str`
- Consumes: db schema（hot_items UNIQUE 约束）

- [ ] **Step 1: 写失败测试** — `test_keywords_filter_or_semantics`（"AI,科技" 标题含任一保留）、`test_empty_keywords_no_filter`、`test_dedup_against_db_and_batch`（库中已有 url 与同批重复 url 均跳过）、`test_truncate_snapshot_2000`
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(collect): keyword whitelist filter and url dedup"`

### Task S2.4: sources API（CRUD + toggle + test）

**Files:**
- Create: `idea_hub/routers/sources.py`, `idea_hub/services/sources.py`
- Test: `tests/test_api_sources.py`

**Interfaces:**
- Produces: 路由（全部 auth）`GET/POST /api/v1/sources`、`PATCH/DELETE /api/v1/sources/{id}`、`POST /api/v1/sources/{id}/toggle`、`POST /api/v1/sources/{id}/test`；test 响应 `{ok, item_count, sample_items, error?}`（**不落库**，spec 6.1）；DELETE 有关联 hot_items 返回 409 `{error:{code:"SOURCE_HAS_ITEMS"}}`（事务内 check-then-delete）
- Consumes: collectors registry、db

- [ ] **Step 1: 写失败测试** — CRUD 全流程、toggle 切换 enabled、test 成功/失败两种响应、DELETE 有热点 409、DELETE 无热点成功
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — 薄路由 + services；POST body 校验 type 必须在 registry 中
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(api): sources CRUD, toggle, test endpoint"`

### Task S2.5: collect 异步 job

**Files:**
- Create: `idea_hub/services/jobs.py`, `idea_hub/routers/pipeline.py`, `idea_hub/routers/jobs.py`
- Test: `tests/test_jobs.py`, `tests/test_api_pipeline.py`

**Interfaces:**
- Produces: `services.jobs.create_job(conn, type, payload) -> int`；`jobs.mark_running(job_id)` / `jobs.heartbeat(job_id)` / `jobs.finish(job_id, status, result_ref, error, token_used)`（全部条件 UPDATE）；`jobs.dedup_running(conn, type) -> int | None`；`POST /api/v1/collect {source_ids?}` → 去重后返回 `{job_id, reused: bool}`；`GET /api/v1/jobs/{id}`、`GET /api/v1/jobs?type=&status=&page=`
- Consumes: filtering、orchestrator、db

**collect job 流程（spec 5.4/5.5）：** 创建 pending → running + heartbeat → 逐来源抓取 → 过滤/去重 → 写入 hot_items（评分步骤 S3 接入，S2 阶段降级 verdict=admit）→ finish(done, `{"hotspot_count": N, "errors": [...]}`)；失败 finish(failed, error)
- [ ] **Step 1: 写失败测试** — job 生命周期（create→running→done）、`test_collect_creates_hot_items`（入库条数、verdict=admit 降级）、`test_collect_dedup_returns_same_job`（running 时二次 POST 返回 reused:true 同一 id）、`test_job_heartbeat_and_failed_condition_update`（finish 时 status 被改则 rowcount=0）
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — job 执行用 `asyncio.to_thread` 跑同步收集逻辑；heartbeat 在每来源边界调用
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(jobs): async collect job with dedup and heartbeat"`

**S2 验收：** 手动 POST /collect 后热点流可见新数据（verdict=admit）；重复触发返回同一 job_id；test 端点验证渠道连通性；zhihu/weibo/v2ex 可建源并抓取。

---

## S3 评分（两档分流 + LLM 评分）

### Task S3.1: scorer 重构

**Files:**
- Create: `idea_hub/scorer.py`（重写 v1 逻辑）
- Test: `tests/test_scorer_v2.py`

**Interfaces:**
- Produces: `score_items(items: list[dict], api_key: str | None, dimensions: list[str]) -> list[dict]`（每条附 final_score、score_breakdown、verdict）；无 api_key → 全 verdict=admit 降级；`score_todo_threshold` 由调用方传入；等权均值 `feasibility = round(mean(dims))`
- Consumes: settings（score_dimensions、score_todo_threshold）、prompts

- [ ] **Step 1: 写失败测试** — `test_no_key_all_admit`、`test_score_breakdown_shape`（mock DeepSeek 返回 JSON 含各维度数字）、`test_verdict_by_threshold`（>=8 admit / <8 discard）、`test_round_mean_equality`（[8,7,9,8] → 8）
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — 规则过滤层（v1 的标题党/广告/时效衰减，0 token）→ LLM 批量评分（prompts 模板，JSON 输出，超时 90s 重试 2 次）→ 等权聚合分流
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(score): two-tier verdict with LLM batch scoring"`

### Task S3.2: collect job 集成评分

**Files:**
- Modify: `idea_hub/services/jobs.py`（collect 流程）
- Test: `tests/test_jobs_scoring.py`

**Interfaces:**
- Consumes: `score_items`、settings
- Produces: collect job 完成后 hot_items 含 final_score/score_breakdown/verdict（admit/discard）

- [ ] **Step 1: 写失败测试** — `test_collect_scores_items_when_key_present`（mock scorer 后 verdict 分流正确）、`test_collect_falls_back_admit_without_key`
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — collect 流程插入评分步骤（抓取 → 过滤去重 → 评分 → 入库）；评分 token 计入 job.token_used
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(collect): integrate scoring step into collect job"`

### Task S3.3: hot_items API

**Files:**
- Create: `idea_hub/routers/hotspots.py`, `idea_hub/services/hotspots.py`
- Test: `tests/test_api_hotspots.py`

**Interfaces:**
- Produces: `GET /api/v1/hotspots?page=&size=&source_id=&verdict=&q=`（q 走 hot_items_fts MATCH）、`GET /api/v1/hotspots/{id}`；响应每条含 title/url/source_name/collected_at/final_score/score_breakdown/verdict/linked_task_count
- Consumes: FTS 表、db

- [ ] **Step 1: 写失败测试** — 分页、verdict 过滤、source_id 过滤、`q=中文关键词` 命中（trigram 生效）、detail 含 linked_task_count
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — FTS MATCH 用 `content_fts MATCH ?`（trigram 直接 LIKE 式匹配）；空 q 走普通分页查询
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(api): hotspots list with fts search"`

**S3 验收：** 配置 DEEPSEEK_API_KEY 后 collect 产生带多维评分与 verdict 的热点；前端热点流可过滤 verdict 与搜索。

---

## S4 生成（候选策略 + DeepSeek 直调 + 任务创建）

### Task S4.1: 生成候选策略服务

**Files:**
- Create: `idea_hub/services/generate.py`
- Test: `tests/test_generate_candidates.py`

**Interfaces:**
- Produces: `get_candidates(conn, count: int | None, hotspot_ids: list[int] | None) -> list[dict]`（候选 = verdict=admit 且未关联 task 且未过期（collected_at + ttl_hours > now 或 ttl NULL）；指定 hotspot_ids 则忽略过滤条件直接取；默认按 final_score DESC 取 count（settings.generate_count 默认 10）；无评分热点按 collected_at DESC）
- Consumes: settings、db

- [ ] **Step 1: 写失败测试** — admit 未关联未过期入选、discard/已关联/过期排除、hotspot_ids 显式指定、count 生效、无评分排序回退
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(generate): candidate selection policy"`

### Task S4.2: DeepSeek 直调层 + 生成 prompts

**Files:**
- Create: `idea_hub/llm.py`, `idea_hub/prompts.py`（重写）
- Test: `tests/test_llm.py`, `tests/test_prompts.py`

**Interfaces:**
- Produces: `llm.chat_json(messages, api_key, timeout=90, retries=2) -> dict`（JSON 输出解析 + 重试 + 每尝试前可注入心跳回调）；`llm.chat_text(...)`；prompts：`generate_prompt(hotspot, target_desc, content_type, dimensions) -> str`（要求 JSON：title/idea_summary/full_idea/content_type/tags[]/score_breakdown{维度}/ai_summary 留空）；`execute_prompt(task, ...)`（产出 markdown 正文 + ai_summary）
- Consumes: config（deepseek_api_key）

- [ ] **Step 1: 写失败测试** — `test_chat_json_retries_on_timeout`（mock httpx 先超时后成功）、`test_chat_json_parse_strips_code_fence`、`test_generate_prompt_contains_dimensions`、`test_execute_prompt_returns_markdown_contract`
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — httpx 同步调用 DeepSeek `/chat/completions`（base https://api.deepseek.com）；JSON 提取兼容 ```json 围栏
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(llm): deepseek direct client with retry, prompts v2"`

### Task S4.3: generate job + 任务创建

**Files:**
- Modify: `idea_hub/services/jobs.py`, `idea_hub/services/generate.py`
- Create: `idea_hub/services/tasks.py`
- Test: `tests/test_generate_job.py`

**Interfaces:**
- Produces: `POST /api/v1/generate {count?, hotspot_ids?}` → job；generate job 流程：get_candidates → 逐个 chat_json(generate_prompt) → `tasks.create_from_generation(conn, gen: dict, hotspot_id) -> task_id`（继承 score_breakdown、feasibility_score=final_score、expire_at=collected_at+ttl、写入 idea.md 落盘、标签按名 upsert、status=todo 或 discard（score < threshold 不建任务））→ finish(done, `{"task_ids": [...]}`)；token 累加 jobs.token_used
- Consumes: llm、prompts、candidates、settings

- [ ] **Step 1: 写失败测试** — `test_generate_creates_tasks`（mock llm 返回 2 个生成结果 → 2 个 todo 任务 + idea.md 落盘 + 关联 task_links）、`test_generate_low_score_discarded`（score 6 < 8 不建任务）、`test_expire_at_inherited`、`test_tags_upserted`、`test_generate_dedup_running`
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — 每候选处理完调用心跳回调；idea.md 写 `base_path/outputs/tasks/<id>/idea.md`
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(generate): async job creating tasks from hotspots"`

### Task S4.4: tags API

**Files:**
- Create: `idea_hub/routers/tags.py`, `idea_hub/services/tags.py`
- Test: `tests/test_api_tags.py`

**Interfaces:**
- Produces: `GET /api/v1/tags`；`PUT /api/v1/tasks/{id}/tags {names: []}`（替换语义、按名 upsert、8 色调色板 `(总数 % 8)` 取色、spec 5.4）；`services.tags.upsert_by_names(conn, names) -> list[tag_id]`
- Consumes: db

- [ ] **Step 1: 写失败测试** — 列表、按名创建（重复名幂等）、颜色轮转（第 9 个标签回第 1 色）、替换语义（重传数组移除旧标签）
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(api): tags list and task tag replace"`

**S4 验收：** POST /generate 后 todo 列出现带评分、标签、expire_at 的任务；idea.md 落盘；低分候选被丢弃。

---

## S5 看板（tasks CRUD + 迁移矩阵 + 拖拽 + 评分组件 + 搜索 + 过期）

### Task S5.1: tasks CRUD + move（迁移矩阵）

**Files:**
- Modify: `idea_hub/services/tasks.py`
- Create: `idea_hub/routers/tasks.py`
- Test: `tests/test_api_tasks.py`

**Interfaces:**
- Produces: `GET /api/v1/tasks?status=&page=&size=&q=&tag=`；`GET /api/v1/tasks/{id}`（含 tags、关联热点摘要、产物摘要 {has_output, latest_version, version_count, ai_summary}，正文不返回）；`POST /api/v1/tasks`（必填 title/content_type，可选 idea_summary/feasibility_score(默认0)/score_breakdown/target_desc/notes/hotspot_id/expire_at，status 默认 todo）；`PATCH`；`DELETE`（级联删 task_links/task_tags/outputs 行+落盘目录，notifications/jobs 保留）；`POST /api/v1/tasks/{id}/move {to_status}`（按 spec 5.2 矩阵条件 UPDATE：done→in_progress 禁止、done→todo 允许、todo→done 允许等；rowcount=0 → 409）
- Consumes: tags、db

- [ ] **Step 1: 写失败测试** — 迁移矩阵全组合（todo→waiting OK、done→in_progress 409、in_progress→todo 409、done→todo OK）、创建默认值、详情含产物摘要、级联删除（task 删除后 outputs 行无 + 目录无 + notifications 保留）、q= 走 tasks_fts、tag 过滤
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — move 用 `UPDATE tasks SET status=? WHERE id=? AND status IN (前置集合)`；done 迁移时写 completed_at
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(api): tasks CRUD with migration matrix enforcement"`

### Task S5.2: 看板前端（四列 + 拖拽 + 移动端）

**Files:**
- Create: `web/src/pages/KanbanPage.tsx`, `web/src/components/kanban/KanbanColumn.tsx`, `web/src/components/kanban/TaskCard.tsx`
- Test: `web/src/components/kanban/KanbanPage.test.tsx`

**Interfaces:**
- Consumes: `GET /tasks?status=`（四列独立拉取）、`POST /tasks/{id}/move`；React Query `useQuery` per column + `useMutation` move（乐观更新 + 失败回滚）；done 列读取 settings.done_column_limit + "加载更多"
- Produces: 拖拽（@dnd-kit）触发 move；禁止 done→in_progress（拖拽目标列过滤）；移动端断点：桌面四列/平板两列/手机单列

- [ ] **Step 1: 写失败测试** — 四列渲染 mock 数据、拖拽结束调用 move mutation（mock dnd-kit 事件）、move 409 时回滚并 toast、done 列分页"加载更多"
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — dnd-kit `DndContext` + `useDroppable/useDraggable`；卡片 memo；列数据 `staleTime` 缓存
- [ ] **Step 4: 运行确认通过** — `pnpm test`
- [ ] **Step 5: Commit** — `git commit -m "feat(web): kanban board with dnd-kit drag"`

### Task S5.3: 任务详情 + 评分组件

**Files:**
- Create: `web/src/pages/TaskDetailPage.tsx`, `web/src/components/score/ScoreBadge.tsx`, `web/src/components/score/ScoreBreakdown.tsx`
- Test: `web/src/components/score/ScoreBreakdown.test.tsx`

**Interfaces:**
- Consumes: `GET /tasks/{id}`、score_breakdown JSON、settings.score_dimensions
- Produces: 评分徽章（>=8 绿 / 6-7 黄 / <6 红）；多维分组条形图（维度名 + 分数条，缺失维度显示 0、额外维度忽略，spec 5.4）；未评分态占位；任务信息区（摘要/目标/内容类型/标签可编辑 PUT /tags）

- [ ] **Step 1: 写失败测试** — 徽章色阶、条形图渲染各维度、缺失维度补 0、空 breakdown 显示未评分
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — 纯展示组件，数据来自 React Query
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(web): score badge and breakdown components"`

### Task S5.4: 前端搜索（单表 + 全局入口）

**Files:**
- Create: `web/src/components/SearchBox.tsx`, `web/src/pages/HotspotsPage.tsx`
- Test: `web/src/pages/HotspotsPage.test.tsx`

**Interfaces:**
- Consumes: `GET /hotspots?q=`、`GET /tasks?q=`（全局搜索框在 S7 接 /search，本任务先做单表）
- Produces: 热点流页（verdict 徽章 + 来源过滤 + 搜索框 + 分页）；看板页搜索框（tasks q=）

- [ ] **Step 1: 写失败测试** — 输入关键词触发带 q 的查询、verdict 徽章渲染、空态
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(web): hotspots page and per-table search"`

### Task S5.5: 调度器 tick（过期处理 + 持久化）

**Files:**
- Create: `idea_hub/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Produces: `scheduler.tick(conn) -> TickResult`：1) 更新 settings.scheduler_last_tick；2) 过期任务处理（todo/waiting 且 expire_at < now → done + completed_at + 备注 + task_expired 通知；in_progress 跳过）；3) discard 清理（verdict=discard 且 collected_date < now-7d → 删行）；4) 崩溃恢复（running 且 heartbeat_at 超 5min → failed + 通知，条件 UPDATE）；5) collect 触发判断（读 collect_interval_hours + scheduler_last_collect，间隔到且无 running collect → 创建 collect job）
- Consumes: settings、jobs、db

- [ ] **Step 1: 写失败测试** — 过期任务自动完成 + 通知、in_progress 不过期、discard 清理 + FTS 同步清除（spec 10 必测：删后搜索不返回）、stale job 标记 failed（条件 UPDATE 并发安全）、collect 触发/去重/间隔逻辑、内部键初始缺失行为
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — 无状态函数，一次 tick 一个事务
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(scheduler): stateless tick with expiry, cleanup, recovery"`

**S5 验收：** 看板四列拖拽流转正确（非法迁移 409 toast）；评分组件美观展示多维；搜索可用；调度器 tick 处理过期与清理。

---

## S6 执行 + 产物（执行器 + outputs 版本化 + markdown 编辑器）

### Task S6.1: 执行器重构（LLM 调用 + 幂等 + 心跳）

**Files:**
- Create: `idea_hub/services/execute.py`, `idea_hub/executor.py`（重构 v1）
- Test: `tests/test_executor_v2.py`

**Interfaces:**
- Produces: `execute_one(conn, task_id, api_key, heartbeat_cb) -> ExecuteResult{ok, token_used, error?}`：前置检查（todo/waiting 或 done 无产物 → in_progress 条件 UPDATE）；chat_text(execute_prompt) → 产出 markdown；写 outputs 首版（version=1, filename="output.md"）；写 ai_summary；任务 → done + completed_at；token 双写（tasks.token_used += N、job.token_used 由调用方累加）；失败 → in_progress → waiting + fail_count+1 + last_fail_reason；内部状态冲突（rowcount=0）→ 产物仍写 + warn 通知 + 计入 failed_items（spec 6.8）
- Consumes: llm、prompts、outputs 服务

- [ ] **Step 1: 写失败测试** — 成功链路（产出落盘 + version=1 + done）、LLM 失败回退 waiting + fail_count、幂等（done 有产物跳过）、done 无产物补执行、状态冲突不覆盖（mock 任务被外部改 waiting 后产物仍写）
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(execute): executor with idempotency and conflict policy"`

### Task S6.2: execute job（批量 + 预算 + 部分成功）

**Files:**
- Modify: `idea_hub/services/jobs.py`
- Test: `tests/test_execute_job.py`

**Interfaces:**
- Produces: `POST /api/v1/execute {task_ids}`（同步校验全部合法，任一非法 → 409 + invalid_task_ids）；job 流程：预算检查（SUM(jobs.token_used WHERE date(created_at)=today) > daily_budget_tokens → failed + budget_exceeded 通知）→ 逐任务 execute_one → 每任务完成立即累加 job.token_used + 实时预算检查（达限停止，未完成移回 waiting）→ finish（部分失败 done + failed_items / 全失败 failed）；通知：全成功 info execute_done / 部分 warn / 全失败 error job_failed（聚合一条）
- Consumes: execute_one、settings

- [ ] **Step 1: 写失败测试** — 批量执行、非法 task_ids 409 列表、预算超限 failed + 通知、执行中达预算停止、部分成功 done + failed_items、通知分级
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(jobs): execute job with budget gate and partial success"`

### Task S6.3: outputs 版本化 API

**Files:**
- Create: `idea_hub/routers/outputs.py`, `idea_hub/services/outputs.py`
- Test: `tests/test_api_outputs.py`

**Interfaces:**
- Produces: `GET /api/v1/tasks/{id}/output`（无产物 {content:null, version:0}；有产物先读时校验）；`PUT`（body content+base_version，0=创建首版；事务内 MAX 校验 + INSERT 新版本 + 落盘；冲突 409）；`POST /upload`（multipart，filename=原始名）；`GET /versions`；`GET /versions/{version}`（导出）；读时校验：文件存在 → 比对 mtime/hash 不一致回写（不递增版本）；文件缺失 → 从 DB 重建（spec 5.3）
- Consumes: outputs 表、FTS 触发器（自动同步）

- [ ] **Step 1: 写失败测试** — 首版创建（base 0）、版本递增、乐观锁 409、upload 存原始名、PUT 继承 filename、外部改文件回写不递增版本、文件缺失重建、版本历史/导出
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — 版本递增原子事务；file_mtime/file_hash 在每次写后记录
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(api): versioned outputs with optimistic lock"`

### Task S6.4: markdown 编辑器前端

**Files:**
- Create: `web/src/components/editor/MarkdownEditor.tsx`（textarea + 实时预览，或 @uiw/react-md-editor）
- Test: `web/src/components/editor/MarkdownEditor.test.tsx`

**Interfaces:**
- Consumes: `GET/PUT /output`、`POST /output/upload`、`GET /versions`
- Produces: 编辑/预览切换、保存（带 base_version）、下载原文件、上传替换、版本历史列表 + 导出、409 冲突恢复（重新拉取最新版 + 提示"已有更新，请基于最新版本继续编辑"）

- [ ] **Step 1: 写失败测试** — 保存携带 base_version、409 后重新加载、上传触发 multipart、版本列表渲染
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(web): markdown editor with versioning"`

**S6 验收：** 执行后产物落盘 + 编辑器可编辑/下载/上传/看版本；外部改文件后 Web 读取同步；乐观锁冲突正确处理。

---

## S7 通知 + 统计 + 统一搜索

### Task S7.1: notifications 服务与 API

**Files:**
- Create: `idea_hub/services/notify.py`, `idea_hub/routers/notifications.py`
- Test: `tests/test_notifications.py`

**Interfaces:**
- Produces: `notify.emit(conn, type, title, body, level, entity_type=None, entity_id=None)`；type 枚举（collect_done/generate_done/execute_done/job_failed/task_expired/budget_exceeded/discard_cleaned，spec 5.4）；`GET /api/v1/notifications?unread_only=&entity_type=&entity_id=&type=`；`POST /{id}/read`、`POST /read-all`
- Consumes: db

- [ ] **Step 1: 写失败测试** — emit 写入、过滤查询、已读状态流转
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(api): notifications emit and query"`

### Task S7.2: stats API

**Files:**
- Create: `idea_hub/routers/stats.py`, `idea_hub/services/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Produces: `GET /api/v1/stats`：队列计数（四状态各 N）、执行累计 SUM(tasks.token_used)、生成/评分累计 SUM(jobs.token_used WHERE type IN ('collect','generate'))（两数分列，spec 5.4）、今日产出（done 且 completed_at=今天）、活跃 job 数
- Consumes: db

- [ ] **Step 1: 写失败测试** — 各统计项在造数后数值正确、token 两数分列不重复
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(api): stats endpoint with split token accounting"`

### Task S7.3: 统一搜索端点

**Files:**
- Create: `idea_hub/routers/search.py`, `idea_hub/services/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Produces: `GET /api/v1/search?q=&page=&size=`：三表各 MATCH 取 page×size 条 → 按 entity_type 分组（组内 bm25 排序）→ 合并统一分页（spec 6.7）；每条 `{entity_type, entity_id, title, snippet, score}`；output 类型 entity_id=task_id
- Consumes: 三张 FTS 表

- [ ] **Step 1: 写失败测试** — 三类型混合命中、snippet 截取、分组排序、page 2 不丢数据（每组造 >size 条验证偏移）、output entity_id=task_id
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** — FTS 查询按 content 列映射回实体表取 title；trigram 匹配用 bm25 排序
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(api): unified search across three tables"`

### Task S7.4: 前端通知中心 + 统计页 + 全局搜索 + 健康条

**Files:**
- Create: `web/src/pages/NotificationsPage.tsx`, `web/src/pages/StatsPage.tsx`, `web/src/components/SchedulerHealthBar.tsx`
- Modify: `web/src/components/SearchBox.tsx`（接 /search）、`web/src/App.tsx`（导航项 + 角标）
- Test: `web/src/pages/StatsPage.test.tsx`

**Interfaces:**
- Consumes: `GET /notifications`、`GET /stats`、`GET /search`、`GET /health`（轮询 30s）
- Produces: 通知列表（level 角标色 info 蓝/warn 黄/error 红 + 实体跳转 + 已读操作）、统计页（token 两栏 + 队列 + 今日产出 + 活跃 job）、全局搜索框（结果按 entity_type 分组渲染 + 跳转）、调度器健康条（last_tick 超 10min 红色警告）

- [ ] **Step 1: 写失败测试** — 通知角标色、统计渲染、全局搜索结果分组
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: Commit** — `git commit -m "feat(web): notifications, stats, global search, health bar"`

**S7 验收：** 收集/生成/执行后通知可查；统计页 token 分列正确；全局搜索跨三类跳转；健康条反映调度器状态。

---

## S8 部署（cron + 文档 + e2e + QQ 对接）

### Task S8.1: cron 脚本与部署文档

**Files:**
- Create: `scripts/deploy/crontab.txt`（`*/5 * * * *` scheduler tick + `0 2 * * *` 每日备份）、`scripts/deploy/backup.sh`（SQLite online backup + outputs tar，保留 7 份）、`scripts/deploy/install-server.sh`（重写：uv 装依赖、config.yaml 生成、crontab 安装、systemd 或 nohup 起 uvicorn）
- Modify: `docs/DEPLOY_CLOUD.md`

- [ ] **Step 1: 写备份脚本** — sqlite3 `.backup` 到临时文件再打包 data/idea.db + outputs/，保留 7 份，含日期
- [ ] **Step 2: 本机验证** — `bash scripts/deploy/backup.sh` 产出备份 tar 且可解压恢复
- [ ] **Step 3: 写 install-server.sh 与 crontab** — scheduler 入口 `python -m idea_hub.cli tick`
- [ ] **Step 4: 更新部署文档** — 含限流、备份恢复（文件缺失由 DB 重建）说明
- [ ] **Step 5: Commit** — `git commit -m "feat(deploy): cron, backup script, install script, docs"`

### Task S8.2: e2e 全链路测试

**Files:**
- Create: `tests/test_e2e_v2.py`
- Test: 一条核心链路：collect（mock 渠道 + mock scorer）→ generate（mock llm）→ 看板 move → execute（mock llm）→ 产物版本编辑 → 通知存在

- [ ] **Step 1: 写 e2e 测试**（内存 SQLite + mock 所有外部调用）
- [ ] **Step 2: 运行确认通过** — `uv run pytest tests/test_e2e_v2.py -v`
- [ ] **Step 3: 全量回归** — `uv run pytest -q` 全绿
- [ ] **Step 4: Commit** — `git commit -m "test(e2e): full pipeline collect->generate->execute->output"`

### Task S8.3: QQ bot 对接验证

**Files:**
- Modify: `docs/PUBLIC_ACCESS_ARCHITECTURE.md`（若需更新）、`scripts/ihub-api.sh`（对齐 /api/v1 路径）
- 动作：云端部署后验证 `ihub-api.sh GET /api/v1/stats`、`GET /api/v1/notifications`；确认 Hermes 侧菜单查询/推送消费新 API（不写 QQ 代码，ideahub 只保证 API 契约）

- [ ] **Step 1: 更新 ihub-api.sh** 路径为 /api/v1
- [ ] **Step 2: 云端部署**（按 DEPLOY_CLOUD.md，含 CF tunnel + Basic Auth + 限流验证）
- [ ] **Step 3: 验证 API 契约** — stats/notifications/health 响应符合 spec
- [ ] **Step 4: Commit** — `git commit -m "chore(deploy): align api script with v1 paths"`

**S8 验收：** 云端全自动运行 24h 无异常；备份可恢复；QQ bot 可查状态与通知。

---

## Self-Review（写作时已对照 spec 检查）

1. **Spec 覆盖**：S1→schema/settings/FTS 触发器/健康；S2→渠道/过滤/去重/sources/collect job；S3→评分数据链；S4→候选/生成/标签；S5→迁移矩阵/看板/评分组件/过期/调度器持久化；S6→执行器/预算/版本化/编辑器；S7→通知/统计/统一搜索；S8→备份 WAL/部署/e2e。spec 第 5-9 节要求均有对应任务。
2. **占位符**：无 TBD/TODO；每个任务含测试断言与接口签名。
3. **类型一致性**：`execute_one`（S6.1）→ job（S6.2）→ e2e（S8.2）；`score_items`（S3.1）→ collect 集成（S3.2）；`upsert_by_names`（S4.4）→ generate（S4.3）；`emit`（S7.1）→ S5.5/S6.2 通知调用——命名一致。
4. **执行顺序依赖**：S3.2 依赖 S3.1；S4.3 依赖 S4.1/S4.2；S5.5 依赖 S5.1（迁移）与 S3（收集）；S6.2 依赖 S6.1/S6.3；S8.2 依赖全部——计划内顺序已满足。
