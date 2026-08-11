# Idea Hub

## 简介

Idea Hub 是一个轻量的「热点 → 选题 → 创作」内容生产工作台：每天定时从热榜 API 与 RSS 源采集热点，由 Hermes 智能体评估打分生成选题（评分 ≥ 阈值 6 进入「待办」队列，否则进入「留档」），通过看板式的五队列（留档/待办/等待/进行中/已完成）管理选题，再由执行 cron 轮询领取任务、以创作者身份产出正文并落盘到 `outputs/tasks/<id>/`。全程本地运行：SQLite 存储（WAL 模式 + 自动备份保留 7 份）、无认证、无云依赖、无通知（YAGNI）。

## 快速开始

```bash
# 1. 启动（自动创建 .venv、安装依赖、创建 data/outputs/backups 目录，然后起服务）
bash scripts/start.sh

# 2. 浏览器打开
#    http://127.0.0.1:8000
```

首次启动后，先在前端「来源」弹窗中添加数据源（hotlist / RSS）并创建/激活一个内容目标（目标决定评分维度），即可开始每日采集流程。

## 配置来源

- **hotlist（热榜 API）**：`sources.type='hotlist'`，`url` 指向返回 JSON 的热榜接口。采集器默认按 `items_path="data"` 取列表（`idea_hub/collectors.py::fetch_hotlist` 的点路径参数），即接口需返回形如：

  ```json
  {"data": [{"title": "标题", "url": "https://...", "hot": 88, "rank": 1, "desc": "简介"}]}
  ```

  其中 `title`/`url` 必填，`hot`/`rank`/`desc` 可选（会拼入 `content_snapshot` 供评分参考）。若你的接口结构不同（例如 `{"result": {"list": [...]}}`），把 `items_path` 改为 `"result.list"` 即可（当前 CLI 使用默认值 `"data"`，改动点在 `collectors.py`）。

- **RSS**：`sources.type='rss'`，`url` 指向 RSS/Atom feed。用 feedparser 解析，取每条的 `title`/`link`/`summary`（截断 500 字）作为内容快照。

- 来源可在前端「来源」弹窗中启用/禁用（`enabled` 字段），`collect` 只抓取启用中的来源；单个来源抓取失败会打印 `ERROR` 并跳过，不影响其他来源。

## 每日流程说明

1. **collect**：采集 cron 每天 08:00（或手动 `uv run python -m idea_hub.cli collect`）抓取所有启用来源，新热点写入 `hot_items`（按 `source_id + url` 去重），输出 `collected=N`。
2. **candidates**：`uv run python -m idea_hub.cli candidates` 列出今天采集、且尚未关联任何任务的热点（JSON 行，含 id/title/url/content_snapshot）。
3. **add-idea**：对每个候选，Hermes 按目标评分维度（热度/相关性/可执行性等）评估打分，写构思草稿 markdown 后执行 `add-idea --hot-item-id <id> --title <标题> --summary <摘要> --score <0-10> --dims '<维度 JSON>' --detail-path <草稿>`。草稿落盘为 `outputs/tasks/<id>/idea.md`；评分 ≥ 6 → 「待办」，< 6 → 「留档」。
4. **relate**：新热点与「留档」任务比对，若相关则 `relate --task-id <归档任务> --hot-item-id <热点> --score <重评分> --dims '<JSON>' --detail-path <关联说明>` 重评分并把关联信息追加进 idea.md；重评分 ≥ 6 的任务自动移回「待办」。

## 执行流程

- **手动触发**：前端把卡片拖到「等待」列（`POST /api/tasks/{id}/move`，`to_status=waiting`），再点卡片上的「执行」按钮（`POST /api/tasks/{id}/execute`）把任务 id 写入 `execute_requests`（status=pending）。
- **执行 cron（每 15 分钟）**：
  1. `uv run python -m idea_hub.cli pending-executions` 列出 pending 的 task id；
  2. 对每个 pending id 执行 `uv run python -m idea_hub.cli next --task-id <id>` —— 定向领取该任务（任务须处于 `waiting`，否则退出码非 0），输出任务 JSON（含 idea_path/notes）；若命令失败（退出码非 0，如任务已不在 waiting），运行 `uv run python -m idea_hub.cli resolve-execution --task-id <id>` 清掉该 pending 请求并跳过该任务；
  3. 以创作者身份执行任务：阅读 `idea_path` 指向的 idea.md 构思与 notes，产出正文；
  4. `complete --task-id <id> --summary <摘要> --output-path <产出文件>` 把正文写入 `outputs/tasks/<id>/output.md` 并将任务置为「已完成」；若无法完成，`fail --task-id <id> --reason <原因>` 会把原因追加进 notes 并把任务退回「等待」；
  5. `complete`/`fail` 会自动把该任务的 pending execute_request 置为 done，不会重复执行。

## 队列说明

- **五队列**（`tasks.status`）：`archived` 留档 / `todo` 待办 / `waiting` 等待 / `in_progress` 进行中 / `done` 已完成。
- **阈值 6**（`models.SCORE_THRESHOLD`）：`add-idea`/`relate` 评分 ≥ 6 入「待办」，< 6 入「留档」；`relate` 重评分 ≥ 6 时「留档」自动移回「待办」。
- **手动改分不移列**：前端抽屉中直接改分（`PATCH /api/tasks/{id}` 仅更新 `feasibility_score` 等字段）**不会**改变任务状态；状态只由拖拽（`move`）或 `next`/`complete`/`fail` 变更。
- 队列领取由 `next` 原子完成（`try_start_task`：仅 `waiting → in_progress` 可成功），多进程并发安全。

## 目录结构

```
idea-hub/
├── idea_hub/                 # Python 包
│   ├── db.py                 # SQLite 连接（WAL）/建表/备份（保留 7 份）
│   ├── models.py             # 数据模型：任务/目标/来源/设置/阈值规则/并发领取
│   ├── collectors.py         # 采集器：hotlist JSON + RSS
│   ├── cli.py                # CLI：collect/candidates/add-idea/relate/next/complete/fail/pending-executions/resolve-execution
│   └── server.py             # FastAPI 后端（create_app 工厂 + 模块级 app，uvicorn 入口）
├── web/                      # 前端：index.html / style.css / app.js / vendor/sortable.min.js
├── tests/                    # pytest：test_models / test_collectors / test_cli_flow / test_api / test_e2e
├── scripts/
│   └── start.sh              # 一键启动：venv + 依赖 + 目录 + uvicorn 127.0.0.1:8000
├── data/                     # SQLite 数据库（git 忽略）
├── outputs/tasks/<id>/       # 产出物 idea.md / output.md（git 忽略）
├── backups/                  # 数据库备份（git 忽略）
├── requirements.txt
└── pytest.ini
```

## Hermes cron 定义

两个 cron 均以项目根目录为 `workdir` 运行，`uv run python -m idea_hub.cli` 的 `--db`/`--base` 默认值（`data/idea.db`、当前目录）即可直接使用。

### 1. Collect cron（每天 08:00）

完整 prompt：

```text
你是 idea-hub 的内容采集与选题执行器。项目根目录为 D:\Programs\idea-hub（git-bash 下为 /d/Programs/idea-hub），所有命令都从项目根目录执行。

1. 运行 `uv run python -m idea_hub.cli collect`，确认输出包含 collected=N。
2. 运行 `uv run python -m idea_hub.cli candidates`，逐条读取候选热点 JSON（含 id/title/url/content_snapshot）。
3. 对每个候选，以激活目标（如「自媒体内容」）的评分维度（热度/相关性/可执行性，阈值 6）评估打分：
   a. 把选题构思写成 markdown 草稿，保存到临时文件（如 .cron-drafts/<id>.md）；
   b. 运行 `uv run python -m idea_hub.cli add-idea --hot-item-id <id> --title <标题> --summary <一句话摘要> --score <0-10 整数> --dims '<维度 JSON，如 {"热度":8,"相关性":6,"可执行性":7}>' --detail-path <草稿路径>`；
   c. 评分 >= 6 自动进入「待办」，< 6 自动进入「留档」；草稿用完删除。
4. relate 关联重评分：对 candidates 中的新热点，与「留档」中已归档任务比对；若某归档任务与某新热点相关，运行
   `uv run python -m idea_hub.cli relate --task-id <归档任务 id> --hot-item-id <热点 id> --score <重评分> --dims '<JSON>' --detail-path <关联说明草稿>`，
   重评分 >= 6 的任务会自动移回「待办」。
5. 全部完成后，用一句话汇报：采集数量、新建选题数量、关联重评分数量。
```

创建命令示例（Hermes CLI，也可在会话中用 `cronjob` 工具创建，schedule 填 `0 8 * * *`、workdir 填 `D:\Programs\idea-hub`）：

```bash
hermes cron create --name idea-hub-collect --deliver local --workdir D:/Programs/idea-hub '0 8 * * *' "<上面的完整 prompt 文本>"
```

### 2. Execute cron（每 15 分钟）

完整 prompt：

```text
你是 idea-hub 的执行调度器。项目根目录为 D:\Programs\idea-hub（git-bash 下为 /d/Programs/idea-hub），所有命令都从项目根目录执行。

1. 运行 `uv run python -m idea_hub.cli pending-executions`，得到待执行的 task id 列表（每行一个）。
2. 对每个待执行任务：
   a. 运行 `uv run python -m idea_hub.cli next --task-id <id>` 定向领取该任务（任务须处于 waiting，否则退出码非 0），输出任务 JSON（含 id/title/idea_path/notes）；若命令失败（退出码非 0，如任务不在 waiting），运行 `uv run python -m idea_hub.cli resolve-execution --task-id <id>` 清掉该 pending 请求并跳过该任务；
   b. 以创作者身份执行任务：阅读 idea_path 指向的 idea.md（构思全文）与 notes，产出正文；
   c. 把产出写入临时文件（如 .cron-outputs/<task_id>.md）；
   d. 运行 `uv run python -m idea_hub.cli complete --task-id <id> --summary <一句话完成摘要> --output-path <产出文件路径>`；若因信息不足等原因无法完成，运行 `uv run python -m idea_hub.cli fail --task-id <id> --reason <原因>`（任务退回「等待」）。
3. complete/fail 会自动把该任务的 pending execute_request 置为 done，无需手动清理；不要重复执行已完成的 id。
4. 汇报：本轮执行了哪些任务、各自完成/失败。
```

创建命令示例：

```bash
hermes cron create --name idea-hub-execute --deliver local --workdir D:/Programs/idea-hub '*/15 * * * *' "<上面的完整 prompt 文本>"
```

> 注：cron 创建属于部署步骤，仓库内不包含 cron 配置；按上述命令在部署环境创建即可。
