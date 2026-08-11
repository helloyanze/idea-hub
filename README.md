# Idea Hub

**把每日热点信息过载，转化为可执行创作任务队列的个人内容管理系统。**

Idea Hub 每日自动从多个来源收集热点，经过规则过滤 + AI 评分筛选，生成带完整构思与多维评分的内容 idea，进入五队列看板管理；确认执行的任务由 AI 自动产出文章并落盘。支持本地运行或部署到云服务器全自动运行。

## 功能特性

- **多源热点收集**：热榜 API（如百度热搜）、RSS、GitHub Trending、HackerNews，来源可配置（启停、关键词白名单、字段映射）
- **评分机制（v1）**：三明治架构——规则过滤层（0 Token：来源分级、标题党/广告检测、时效衰减）→ 轻量 LLM 批量评分（事实性 + 验证需求，每条均摊 <55 Token）→ 加权聚合分流（>=75 收录 / 55-74 待复核 / <55 丢弃）
- **AI 生成 idea**：基于激活目标（如"自媒体内容"），生成标题、一句话摘要、构思全文与多维评分；打分 >=8 入待办、6-7 归档、<6 舍弃（宁缺毋滥）
- **标签系统**：AI 自动打标签（可自定义），按标签过滤
- **五队列看板**：待办 / 等待 / 进行中 / 已完成 + 留档弹窗（低分库存与完成存档）；拖拽流转、批量操作、待办配额（默认 10 条，满员自动留档）
- **执行产出**：确认执行的任务加入执行队列，AI 自动产出正文并落盘为 Markdown
- **Web 界面**：响应式布局（桌面/平板/手机）、深色/浅色主题、全局搜索、评分明细可视化、Basic Auth 可选认证
- **全自动运行**：云服务器部署 + cron 定时（收集 → 生成 → 执行 → 备份），支持"自动运行"总开关
- **数据安全**：SQLite WAL 模式，每日自动备份（保留 7 份），可同步回本地

## 架构概览

```
热点源（热榜/RSS/GitHub Trending/HackerNews）
    ↓ 规则过滤（0 Token）
    ↓ LLM 批量评分 + 加权聚合
    ↓ 收录 / 复核 / 丢弃
hot_items 入库
    ↓ Hermes（LLM Agent）每晚批量生成
idea（标题 + 构思全文 + 多维评分 + 标签）
    ↓ >=8 待办 / 6-7 归档 / <6 舍弃
五队列看板（Web 界面，拖拽管理）
    ↓ 确认执行
AI 自动产出文章 → outputs/tasks/<id>/output.md
```

## 快速开始（本地）

要求：Python 3.11+、[uv](https://docs.astral.sh/uv/)（推荐）或 pip。

```bash
# 1. 安装依赖
uv venv .venv
uv pip install -r requirements.txt

# 2. 启动 Web 看板（首次运行自动初始化数据库：建表 + 默认标签）
uv run uvicorn idea_hub.server:app --host 127.0.0.1 --port 8000

# 3. 浏览器打开 http://127.0.0.1:8000
#    在界面中创建目标模式（如"自媒体内容"）并添加来源（见下方示例）
```

**添加来源示例**（在 Web 界面"来源管理"弹窗中填写）：

| 来源 | 类型 | URL | 条目路径 | 标题字段 |
|---|---|---|---|---|
| 百度热搜 | hotlist | `https://top.baidu.com/api/board?platform=wise&tab=realtime` | `data.cards.0.content.0.content` | `word` |
| GitHub Trending | github-trending | `https://github.com/trending?since=daily` | - | - |
| HackerNews | hackernews | （内置 API） | - | - |

**手动收集测试**：

```bash
uv run python -m idea_hub.cli --db data/idea.db collect
# 输出示例: collected=42 discarded=12 review=6
```

> 完整命令清单见 `docs/CLI.md`。

### 启用评分（可选，需要 DeepSeek API key）

评分机制的 LLM 层调用 DeepSeek（批量、纯数字输出）。设置环境变量 `DEEPSEEK_API_KEY` 后，`collect` 命令自动启用评分分流：

```bash
export DEEPSEEK_API_KEY=sk-...
uv run python -m idea_hub.cli --db data/idea.db collect
# 输出: collected=42 discarded=12 review=6
```

不设置 key 时 collect 降级为"全量入库"（规则层仍生效）。

### 运行测试

```bash
uv run pytest -q   # 67 个测试（模型/收集/评分/CLI/API/端到端）
```

## 数据模型与状态机

- **任务状态**：`archived`（留档）/ `todo`（待办）/ `waiting`（等待）/ `in_progress`（进行中）/ `done`（已完成）。状态变更只能通过移动操作（`move_task`），手动改分不会自动移列
- **可行性评分**：1-10；>=8 待办、6-7 留档、<6 舍弃（生成时）
- **关联**：任务与热点多对多（`task_links`）；新热点与已有任务相关时，自动追加关联、更新构思、重新评分（>=8 且配额允许则升级待办）
- **产出落盘**：构思全文 `outputs/tasks/<id>/idea.md`、执行产出 `outputs/tasks/<id>/output.md`，任务记录仅存摘要与路径
- **数据文件**：全部数据在 `data/idea.db`（SQLite WAL）；`backups/` 保留最近 7 份每日备份

## 项目结构

```
idea_hub/
  cli.py         # 命令行入口（collect/candidates/import-ideas/add-idea/relate/move/next/complete/...）
  collectors.py  # 多源抓取（热榜/RSS/GitHub Trending/HackerNews）+ 关键词过滤
  scorer.py      # 评分机制：规则过滤 + LLM 批量评分 + 加权聚合 + 阈值分流
  models.py      # 数据层（任务/目标/来源/标签/关联/统计）
  db.py          # SQLite 连接、schema、轻量迁移、WAL 安全备份
  server.py      # FastAPI Web 服务（REST API + 静态前端 + 可选 Basic Auth）
web/             # 前端（原生 HTML/CSS/JS，无构建步骤，响应式）
  index.html / style.css / app.js
scripts/
  deploy/        # 云服务器部署（安装脚本、crontab、Hermes prompts）
  sync/          # 本地同步与 SSH 隧道脚本
docs/
  UI_DESIGN.md                     # 界面设计文档（供设计师参考）
  DEPLOY_CLOUD.md                  # 云服务器部署指南
  PUBLIC_ACCESS_ARCHITECTURE.md    # 公网访问架构决策说明
  CLOUDFLARE_TUNNEL_GUIDE.md       # Cloudflare 隧道接入指引
  superpowers/                     # 设计规格（spec）与实施计划
tests/           # pytest 测试（67 个）
```

## 云服务器全自动部署

支持部署到云服务器（Ubuntu），每晚自动运行：收集热点 → AI 生成 idea → 执行等待队列 → 备份。详见 `docs/DEPLOY_CLOUD.md`。

公网访问采用 Cloudflare Tunnel（零端口暴露 + 自动 HTTPS）+ 应用层 Basic Auth，完整方案与备选方案对比见 `docs/PUBLIC_ACCESS_ARCHITECTURE.md`。

## 技术栈

Python 3.11+ · FastAPI · SQLite（WAL）· 原生 JS 前端 · uv · pytest · Hermes（AI Agent，可替换为任意 LLM 工作流）

## License

[MIT](./LICENSE)（暂定，见 LICENSE 文件）
