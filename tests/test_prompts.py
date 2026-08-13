"""内容模板 / 质检 prompt / AI 味规则层 / JSON 容错解析。"""
import idea_hub.prompts as P

TASK = {"title": "测试标题", "idea_summary": "摘要", "content_type": "long",
        "redo_note": None, "idea_path": ""}
TARGET = {"name": "自媒体内容", "description": "面向普通读者的深度文章"}

def test_templates_have_six_sections():
    for ct in ("short", "long", "video_script"):
        t = P.CONTENT_TEMPLATES[ct]
        assert "角色设定" in t and "写作原则" in t and "结构要求" in t
        assert "风格与语言" in t and "禁忌清单" in t

def test_build_generation_prompt_injects_context():
    p = P.build_generation_prompt(TASK, TARGET, ["热点A", "热点B"], None)
    assert "测试标题" in p and "自媒体内容" in p and "热点A" in p
    assert "JSON" in p  # 输出格式要求

def test_redo_note_injected():
    p = P.build_generation_prompt(TASK, TARGET, [], "请写得更口语化")
    assert "请写得更口语化" in p

def test_qa_prompt_contains_schema():
    p = P.build_qa_prompt("正文内容", "long")
    assert '"pass"' in p and "issues" in p and "quote" in p

def test_ai_taste_hits_and_boundary():
    hits = P.check_ai_taste("首先我们要注意，最后总结一下。", ["首先", "其次", "最后"])
    assert set(hits) == {"首先", "最后"}
    # 词边界：'最后' 作为时间状语仍命中（黑名单为模板词策略），但普通词不误伤
    assert P.check_ai_taste("我们今天去爬山。", ["首先", "最后"]) == []

def test_parse_llm_json_tolerant():
    raw = '```json\n{"title": "t", "content": "c"}\n```'
    assert P.parse_llm_json(raw) == {"title": "t", "content": "c"}
    assert P.parse_llm_json('{"a": 1} trailing text') == {"a": 1}
    assert P.parse_llm_json("no json") == {}

def test_regenerate_prompt_includes_fixes():
    qa = {"pass": False, "issues": [{"type": "style", "quote": "值得注意的是",
                                     "problem": "模板词", "fix": "删除或改写"}],
          "suggestions": "整体口语化"}
    p = P.build_regenerate_prompt("原生成 prompt", qa)
    assert "值得注意的是" in p and "整体口语化" in p and "可忽略" in p
