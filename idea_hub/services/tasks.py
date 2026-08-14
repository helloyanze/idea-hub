"""生成构思对应的任务创建服务。"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from .tags import upsert_by_names


def create_from_generation(conn, gen: dict, candidate: dict, base_path: str) -> int:
    """由生成结果和候选热点创建待办任务。"""
    feasibility = (
        int(round(candidate["final_score"]))
        if candidate["final_score"] is not None
        else 0
    )
    score_breakdown_json = json.dumps(
        candidate["score_breakdown"] or {}, ensure_ascii=False
    )
    if candidate["ttl_hours"] is None or not candidate["collected_at"]:
        expire_at = None
    else:
        expire_at = (
            datetime.strptime(candidate["collected_at"], "%Y-%m-%d %H:%M:%S")
            + timedelta(hours=int(candidate["ttl_hours"]))
        ).strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.execute(
        "INSERT INTO tasks "
        "(title, idea_summary, content_type, status, feasibility_score, "
        "score_breakdown, target_desc, expire_at) "
        "VALUES (?, ?, ?, 'todo', ?, ?, ?, ?)",
        (
            gen["title"],
            gen["idea_summary"],
            gen["content_type"],
            feasibility,
            score_breakdown_json,
            candidate["title"],
            expire_at,
        ),
    )
    task_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO task_links (task_id, hot_item_id) VALUES (?, ?)",
        (task_id, candidate["hotspot_id"]),
    )
    tag_ids = upsert_by_names(conn, gen["tags"])
    for tag_id in tag_ids:
        conn.execute(
            "INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?, ?)",
            (task_id, tag_id),
        )
    conn.commit()

    idea_path = Path(base_path) / "outputs" / "tasks" / str(task_id) / "idea.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text(gen["full_idea"], encoding="utf-8")
    return task_id
