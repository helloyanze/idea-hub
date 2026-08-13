# 同类开源项目代码调研报告

> 日期：2026-08-13
> 目的：为 Idea Hub 自动执行调度设计（2026-08-13-auto-execution-design.md）提供可复用的实现参考
> 来源：依据《Idea-Hub 产品与技术审阅报告》第 3.1 节开源竞品清单，挑选与"自动调度执行、分层执行器、内容质检、通知"最相关的 6 个项目
> 代码位置：`D:\Programs\idea-hub-refs\`（浅克隆，仅供阅读参考）

---

## 1. 调研项目清单

| 项目 | 仓库 | 调研重点 |
|---|---|---|
| Article-Transcription-Assistant | aaa85338477/Article-Transcription-Assistant | 审稿/去 AI 味流程与 prompt |
| tech-content-curator | Hardcoreprawn/tech-content-curator | 事实核查、质量评分、写作风格 |
| multi-agent | gonelake/multi-agent | 多智能体审校-修改循环 |
| ip-publisher | veeicwgy/ip-publisher | 去 AI 味（humanizer skill） |
| AIMedia | Anning01/AIMedia | 全自动流水线架构 |
| AutoContents | JnyRoad/AutoContents | 通知推送（飞书/微信） |

---

## 2. 关键发现与可复用点

### 2.1 审稿/质检（与本次"轻量质检"设计最相关）

**Article-Transcription-Assistant（prompts.json → reviewer）**

审稿 prompt 的核心结构（可直接借鉴到质检维度设计）：

- 审核结论分级：`通过 / 小修 / 大修 / 退回重写`
- 修改任务清单：3-8 条，每条必须含 4 要素：问题类型 / 对应位置或原句 / 为什么要改 / 应该怎么改
- 检查维度：
  1. 事实错误（产品/公司/时间/数据与素材不一致；推测被写成事实）
  2. 逻辑断层（结论超出材料支持范围；因果不成立；论证跳步）
  3. 结构缺陷（标题缺失或不一致；无二级标题；层级失衡）
  4. 表达问题（AI 腔、套话、空话、翻译腔；表述过满过硬）
- 工作原则：只识别问题不重写全文；每条意见必须能直接转化为修改操作；无法确认真伪时标注"素材不足，建议保守表述"

**tech-content-curator（article_reviewer.py + quality_scorer.py）**

- `ArticleReview`：overall_score + 分维度评分（clarity/accuracy/voice/engagement）+ strengths + weaknesses + actionable_feedback + **regeneration_recommended**（是否建议重新生成）
- `QualityScorer`：overall 0-100 + **passed_threshold（默认 70）** + improvement_suggestions
- `fact_check.py`：URL 可达性 HEAD 检查 + 指数退避重试（带 jitter）——偏工程性事实核查，非 LLM 侧

**multi-agent（ReviewerAgent + orchestrator.py）**

- 审校-修改循环：Writer → Reviewer → 评估反馈 → 修改 → 再审，**max_revisions=2 防死循环**
- 关键设计：WriterAgent 收到审校反馈后，先用 `EVALUATE_PROMPT` 让 LLM 评估反馈是否值得采纳（`{"should_revise": bool, "reason": "..."}`，解析异常时默认接受）——防止低质量审校意见把文章改坏

### 2.2 写作 prompt 模板（可改造为 content_type 模板）

**multi-agent（WriterAgent SYSTEM_PROMPT，公众号长文）**

结构清晰，七段式：
1. 角色设定（资深微信公众号科技专栏作家，10 万+爆款经验）
2. 写作原则（移动端优先、口语化、信息密度高）
3. 排版规范（每段 3-4 行、段间空行、加粗每段最多 1 处）
4. 文章结构（钩子→背景→核心分析 2-3 论点→影响解读→结尾金句）
5. 标题技巧（A 悬念型 / B 观点型 / C 利益型 3 个备选）
6. 语言风格（类比比喻、反问句、阿拉伯数字、避免套话）
7. 禁忌清单（不超 800 字、不堆术语、不写无观点资讯、不用震惊体）

输出：JSON（`titles` 3 备选 + `title` + `content` Markdown + `word_count` 实际字数）——**字数由 LLM 自报，便于程序校验**

**Article-Transcription-Assistant（final_polishers，按平台角色润色）**

六段式角色模板：角色设定 / 核心目标 / 语气与风格 / 行为准则 / 输出格式与结构 / 限制与红线。示例：玩家社区版（"玩家黑话翻译行业黑话"）、微信公众号版（"数据驱动、克制文风"）。

**tech-content-curator（voices）**

- 7 个写作 voice（taylor/sam/aria/quinn/riley/jordan/emerson），每个有独立人格/风格/指南
- **banned phrases 机制**：每个 voice 配禁用词列表，`check_for_banned_phrases(content, voice_id)` 本地正则扫描，命中返回位置——**0 token 的 AI 味检测规则层**

### 2.3 去 AI 味（ip-publisher humanizer skill）

规则式去 AI 味，动作清单：
- 替换 AI 套话黑名单表达
- 删除"首先/其次/最后/总的来说/值得注意的是"等模板词
- 打散过于工整的并列句式
- 注入个人判断、语气词和真实感细节
- 保留事实与逻辑，不改立场

### 2.4 通知推送（AutoContents）

- `feishuService.js` / `wechatService.js`（Node.js）：飞书/微信推送
- 技术栈不同（Idea Hub 用 hermes send），参考价值有限；推送内容结构（标题 + 摘要 + 链接）与我们的通知设计一致

### 2.5 全自动流水线（AIMedia）

- Django 后端 + PySide6 桌面端，"热点抓取→AI 创作→多平台自动发布"
- 架构层面对 Idea Hub 无新启发（我们已有更精细的五队列 + 分层执行）

---

## 3. 对 Idea Hub 设计的落地建议（已并入规格 v4）

### 3.1 质检维度升级（规格 5.4）

在 v3 三维度基础上，借鉴 ATA 审稿检查维度，调整为四维度（保持轻量、单次短输出）：

1. 字数达标（按类型最低字数）
2. AI 味密度（模板化表达、排比泛滥、套话开头）
3. 事实性错误（包含具体数字/日期/引用未标注来源；推测被写成事实）
4. 结构完整性（短文：标题+正文；长文：标题+分段+小标题+结尾；视频脚本：开场钩子+分镜+时长标注）

**质检输出 schema（升级为带位置的可操作意见）：**

```json
{
  "pass": true,
  "issues": [
    {"type": "fact", "quote": "原句", "problem": "问题", "fix": "怎么改"}
  ],
  "suggestions": "改进方向"
}
```

`issues` 每条含 `quote`（对应位置）+ `fix`（可执行动作）——重生成时直接注入，与 ATA 修改任务清单同构。

### 3.2 AI 味规则层（0 token，规格 5.4 新增）

借鉴 tech-content-curator banned phrases + ip-publisher humanizer 黑名单，executor 落盘前本地正则扫描：

- 模板词黑名单：首先/其次/最后/总的来说/值得注意的是/综上所述/众所周知/不言而喻/赋能/抓手/闭环（可配置）
- 命中任一 → 计入质检意见并触发重生成（不额外消耗 LLM 质检调用）

### 3.3 重生成注入格式（规格 5.4 细化）

借鉴 multi-agent REVISE_PROMPT 模式：重试 prompt = 原生成模板 + 注入块：

```
上一版存在以下问题，请修正（如某条与事实不符可忽略）：
- 位置"原句"：问题；建议：怎么改
```

加"与事实不符可忽略"缓解质检误判（对应 multi-agent 的 EVALUATE_PROMPT 思路，但用轻量提示替代额外 API 调用）。

### 3.4 写作模板结构（规格 5.5 细化）

content_type 模板采用六段式结构（借鉴 ATA final_polishers + multi-agent WriterAgent）：
角色设定 → 写作原则 → 结构要求 → 风格与语言 → 标题技巧（可选）→ 禁忌清单。

长文类型增强（可选，实施时定）：要求 LLM 输出 JSON `{title, content, word_count}` 并给 3 个备选标题。

### 3.5 审校循环上限验证

multi-agent 的 max_revisions=2 验证了我们的"质检重试 1 次"设计合理（更克制）；不引入反馈评估层（EVALUATE_PROMPT），以重试注入提示语替代。

---

## 4. 结论

6 个项目的代码调研确认了本次设计的架构方向（分层执行、质检门槛、失败重试）与同类成熟实现一致，没有发现需要推翻设计的冲突点。直接可复用的是：

1. 审稿维度清单与修改任务清单格式（ATA）—— 升级质检 schema
2. 模板词黑名单（ip-publisher / tech-content-curator）—— 0 token AI 味规则层
3. 六段式写作模板结构（ATA / multi-agent）—— content_type 模板重写
4. JSON 输出 + 字数自报（multi-agent）—— 便于校验
5. max_revisions 防死循环（multi-agent）—— 验证重试 1 次设计

调研参考代码保留在 `D:\Programs\idea-hub-refs\`（不进入 Idea Hub 仓库）。
