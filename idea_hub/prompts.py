"""内容类型模板、质检 prompt 与 AI 味规则层。

模板结构为六段式（角色设定/写作原则/结构要求/风格与语言/标题技巧/禁忌清单），
调研自 Article-Transcription-Assistant final_polishers 与 multi-agent WriterAgent
（见 docs/RESEARCH_COMPETITORS.md）。
"""
import json
import re

# ---- 六段式内容类型模板 ----
CONTENT_TEMPLATES = {
    "short": """## 角色设定
你是自媒体短文写手，擅长微博/知乎风格的短内容。

## 写作原则
- 观点鲜明，200-500 字，可直接发布
- 口语化，拒绝公文腔和空话

## 结构要求
- 必须有标题和正文；正文第一句即观点或钩子
- 单段不超过 5 行

## 风格与语言
- 使用具体案例或数据支撑观点（数据须来自提供的热点信息）
- 避免模板词：首先/其次/最后/总的来说/值得注意的是/综上所述/众所周知/不言而喻/赋能/抓手/闭环

## 标题技巧
- 给出 1 个标题

## 禁忌清单
- 不堆术语、不写无观点资讯、不用震惊体、不编造数据""",

    "long": """## 角色设定
你是资深公众号长文作者，写作 1000-3000 字的深度内容。

## 写作原则
- 移动端优先：每段 3-4 行，段间空行
- 信息密度高：每段都有信息增量，不注水
- 口语化但不失深度

## 结构要求
- 标题 + 开头钩子（50 字内制造冲突或好奇）+ 2-3 个论点（每论点=观点+案例/数据+小结）+ 结尾金句
- 必须有二级小标题分隔论点

## 风格与语言
- 多用类比和比喻，关键数据用阿拉伯数字
- 避免模板词：首先/其次/最后/总的来说/值得注意的是/综上所述/众所周知/不言而喻/赋能/抓手/闭环

## 标题技巧
- 提供 3 个备选标题：A 悬念型 / B 观点型 / C 利益型

## 禁忌清单
- 不写超过 3000 字、不堆砌术语、不写无观点的资讯搬运、不用震惊/重磅等低质词
- 涉及具体数字/日期/引用时必须能由提供的热点信息支撑，否则改为模糊表述""",

    "video_script": """## 角色设定
你是短视频脚本作者，为口播类短视频撰写脚本。

## 写作原则
- 按分镜组织，节奏紧凑，总时长 1-3 分钟（约 250-750 字口播稿）
- 开场 5 秒必须有钩子（悬念/反常识/直接提问）

## 结构要求
- 脚本必须包含：开场钩子 → 主体分镜（每个分镜含画面提示 + 口播词）→ 结尾引导（关注/评论/收藏）
- 每个分镜标注时长（如 0:00-0:05）

## 风格与语言
- 口播词口语化、短句、有情绪起伏；画面提示用【】标注
- 避免模板词：首先/其次/最后/总的来说/值得注意的是/综上所述/众所周知/不言而喻/赋能/抓手/闭环

## 标题技巧
- 给出视频标题 1 个

## 禁忌清单
- 不写长难句、不用书面语堆砌、不编造数据、不用震惊体""",
}

QA_SCHEMA_EXAMPLE = """{
  "pass": true,
  "issues": [
    {"type": "fact|logic|structure|style", "quote": "原句", "problem": "问题", "fix": "怎么改"}
  ],
  "suggestions": "改进方向"
}"""


def build_generation_prompt(task, target, hot_titles, redo_note):
    tpl = CONTENT_TEMPLATES.get(task.get("content_type") or "long",
                                CONTENT_TEMPLATES["long"])
    hot_block = "\n".join(f"- {t}" for t in hot_titles) or "（无）"
    redo_block = f"\n用户修改意见（必须落实）：{redo_note}" if redo_note else ""
    return f"""{tpl}

## 本次任务
- 任务标题：{task['title']}
- 构思摘要：{task.get('idea_summary', '')}
- 目标模式：{target['name']}（{target.get('description', '')}）
- 关联热点素材：
{hot_block}
{redo_block}

## 输出格式
只输出 JSON：{{"title": "最终标题", "content": "正文（Markdown）", "word_count": 实际字数}}
不要输出 JSON 以外的任何内容。"""


def build_qa_prompt(content, content_type):
    return f"""你是严格的内容质检员。检查下面这篇{content_type}内容，输出 JSON。

检查维度：
1. 字数达标（短文 200-500 字；长文 1000-3000 字；视频脚本 250-750 字）
2. AI 味：模板化表达、排比泛滥、套话开头（首先/其次/总的来说/值得注意的是等）
3. 事实性错误：包含具体数字/日期/引用但无来源支撑；推测被写成事实
4. 结构完整性：短文=标题+正文；长文=标题+分段+小标题+结尾；视频脚本=开场钩子+分镜+时长标注

输出格式（严格 JSON，不要其他文字）：
{QA_SCHEMA_EXAMPLE}

pass=false 时 issues 必须包含具体问题，每条含 quote（原句/位置）、problem、fix（可执行修改动作）。

<content>
{content}
</content>"""


def build_regenerate_prompt(base_prompt, qa):
    issues = "\n".join(
        f"- 位置「{i.get('quote', '')}」：{i.get('problem', '')}；建议：{i.get('fix', '')}"
        for i in qa.get("issues", []))
    sug = qa.get("suggestions", "")
    return f"""{base_prompt}

上一版存在以下问题，请修正（如某条与事实不符可忽略）：
{issues}
整体改进方向：{sug}

输出格式不变（只输出 JSON）。"""


def check_ai_taste(content, blacklist):
    """本地规则层：命中黑名单模板词返回命中列表（0 token）。
    黑名单为"模板词"策略：常见套话直接命中；普通语境用词不在黑名单内。"""
    hits = []
    for w in blacklist:
        if w and w in content:
            hits.append(w)
    return hits


def parse_llm_json(raw):
    """容错解析 LLM JSON 输出：剥 code fence、截取首个 {...} 块。"""
    if not raw:
        return {}
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m2 = re.search(r"\{.*\}", text, re.S)
        if m2:
            try:
                return json.loads(m2.group(0))
            except json.JSONDecodeError:
                return {}
        return {}
