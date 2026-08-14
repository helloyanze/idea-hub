"""Tests for the Idea Hub CLI (S5.5): tick and backup subcommands.

The CLI replaces the retired v1 command set (which imported the deleted v1
scorer). tick runs the scheduler and prints the TickResult JSON; backup
writes a dated copy of the SQLite database.
"""
import json
from datetime import datetime, timezone

from idea_hub import cli, db, scheduler


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _write_config(tmp_path, db_path: str):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"db_path: {db_path}\nauth_user: admin\nauth_pass: secret\n",
        encoding="utf-8",
    )
    return str(cfg_path)


class _OkResponse:
    ok = True


def test_cli_tick_prints_tick_result_json(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, value_type) "
        "VALUES ('scheduler_last_collect', ?, 'string')",
        (_now_str(),),
    )
    conn.commit()
    conn.close()
    cfg_path = _write_config(tmp_path, db_path)

    monkeypatch.setattr(
        scheduler.requests, "post", lambda url, **kwargs: _OkResponse()
    )
    cli.main(["--config", cfg_path, "tick"])

    out = json.loads(capsys.readouterr().out.strip())
    assert set(out.keys()) == {
        "expired_count",
        "cleaned_count",
        "recovered_count",
        "collect_triggered",
    }

    conn = db.connect(db_path)
    assert (
        conn.execute(
            "SELECT 1 FROM settings WHERE key = 'scheduler_last_tick'"
        ).fetchone()
        is not None
    )
    conn.close()


def test_cli_backup_creates_dated_file(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.commit()
    conn.close()
    cfg_path = _write_config(tmp_path, db_path)

    cli.main(["--config", cfg_path, "backup", "--dest-dir", str(tmp_path / "bk")])

    files = list((tmp_path / "bk").glob("idea-*.db"))
    assert len(files) == 1
    assert files[0].stat().st_size > 0
    out = capsys.readouterr().out.strip()
    assert out and "idea-" in out
