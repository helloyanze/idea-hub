"""CLI：execute-auto 入口、complete --token-used、fail 计数。"""
import os
import subprocess
import sys
from pathlib import Path
from idea_hub import db, models

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _run_cli(tmp_path: Path, *args):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)  # cwd=tmp_path 时保证 idea_hub 可导入
    return subprocess.run([sys.executable, "-m", "idea_hub.cli", "--db",
                           str(tmp_path / "t.db"), *args],
                          capture_output=True, text=True, cwd=str(tmp_path), env=env)

def _mk_task(tmp_path: Path) -> int:
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)
    models.create_target(conn, name="t", description="d", score_dimensions="{}")
    models.activate_target(conn, 1)
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=1,
                             feasibility_score=9, score_breakdown="{}")
    models.move_task(conn, tid, "waiting")
    conn.close()
    return tid

def test_complete_with_token_used_and_reset(tmp_path):
    tid = _mk_task(tmp_path)
    out = tmp_path / "out.md"
    out.write_text("内容", encoding="utf-8")
    r = _run_cli(tmp_path, "next", "--task-id", str(tid))
    assert r.returncode == 0
    conn = db.connect(str(tmp_path / "t.db"))
    models.update_task(conn, tid, fail_count=2)  # 模拟失败计数
    conn.close()
    r = _run_cli(tmp_path, "complete", "--task-id", str(tid),
                 "--summary", "完成", "--output-path", str(out), "--token-used", "777")
    assert r.returncode == 0
    conn = db.connect(str(tmp_path / "t.db"))
    task = models.get_task(conn, tid)
    assert task["status"] == "done" and task["token_used"] == 777
    assert task["fail_count"] == 0  # 成功清零

def test_fail_increments_count_and_reason(tmp_path):
    tid = _mk_task(tmp_path)
    _run_cli(tmp_path, "next", "--task-id", str(tid))
    r = _run_cli(tmp_path, "fail", "--task-id", str(tid), "--reason", "测试失败")
    assert r.returncode == 0
    conn = db.connect(str(tmp_path / "t.db"))
    task = models.get_task(conn, tid)
    assert task["status"] == "waiting" and task["fail_count"] == 1
    assert task["last_fail_reason"] == "测试失败"

def test_notify_subcommand_writes_notification(tmp_path):
    """cli notify：写 notifications 表（供复杂任务 agent 上报完成/失败/暂停）。"""
    tid = _mk_task(tmp_path)
    r = _run_cli(tmp_path, "notify", "--task-id", str(tid), "--type", "done",
                 "--title", "Idea Hub 任务完成", "--body", "《t》\n摘要：完成")
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    rows = conn.execute("SELECT * FROM notifications").fetchall()
    assert len(rows) == 1
    assert rows[0]["type"] == "done" and rows[0]["task_id"] == tid
    assert rows[0]["title"] == "Idea Hub 任务完成"
    conn.close()


def test_notify_subcommand_rejects_invalid_type(tmp_path):
    """cli notify：--type 只允许 done/failed/paused/expired/budget/scheduler。"""
    _mk_task(tmp_path)
    r = _run_cli(tmp_path, "notify", "--task-id", "1", "--type", "bogus",
                 "--title", "T", "--body", "B")
    assert r.returncode != 0

def test_execute_auto_cli(tmp_path):
    tid = _mk_task(tmp_path)
    _run_cli(tmp_path, "next", "--task-id", str(tid))
    # unittest.mock 无法跨 subprocess：在子进程 runner 内 patch 后调用 CLI main
    runner = tmp_path / "auto_runner.py"
    runner.write_text(
        "import sys\n"
        "from unittest.mock import patch\n"
        "from idea_hub import executor\n"
        "with patch.object(executor, 'execute_task', return_value=0) as m:\n"
        "    from idea_hub.cli import main\n"
        "    sys.argv = ['idea_hub', '--db', sys.argv[1],\n"
        "                'execute-auto', '--task-id', sys.argv[2]]\n"
        "    try:\n"
        "        main()\n"
        "    except SystemExit as e:\n"
        "        print('RC=' + str(e.code))\n"
        "    print('CALLED=' + str(m.called))\n",
        encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    r = subprocess.run([sys.executable, str(runner), str(tmp_path / "t.db"), str(tid)],
                       capture_output=True, text=True, cwd=str(tmp_path), env=env)
    assert r.returncode == 0, r.stderr
    assert "RC=0" in r.stdout and "CALLED=True" in r.stdout
