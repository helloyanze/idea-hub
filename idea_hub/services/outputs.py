"""Versioned task output services."""

import hashlib
import os
import sqlite3
from datetime import datetime

from ..errors import AppError, NOT_FOUND


def _task_or_404(conn, task_id):
    task = conn.execute(
        "SELECT 1 FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if task is None:
        raise AppError(404, NOT_FOUND, f"Task not found: {task_id}")


def _file_path(base_path, task_id) -> str:
    return os.path.join(
        base_path,
        "outputs",
        "tasks",
        str(task_id),
        "output.md",
    )


def get_latest(conn, task_id, base_path) -> dict:
    _task_or_404(conn, task_id)
    row = conn.execute(
        "SELECT * FROM outputs WHERE task_id = ? "
        "ORDER BY version DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if row is None:
        raise AppError(404, NOT_FOUND, f"No output for task: {task_id}")

    path = _file_path(base_path, task_id)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as output_file:
            content = output_file.read()
        file_mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if file_mtime != row["file_mtime"] or file_hash != row["file_hash"]:
            conn.execute(
                "UPDATE outputs SET content = ?, file_mtime = ?, file_hash = ? "
                "WHERE id = ?",
                (content, file_mtime, file_hash, row["id"]),
            )
            conn.commit()
    else:
        content = row["content"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as output_file:
            output_file.write(content)
        file_mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        conn.execute(
            "UPDATE outputs SET file_mtime = ?, file_hash = ? WHERE id = ?",
            (file_mtime, file_hash, row["id"]),
        )
        conn.commit()

    task = conn.execute(
        "SELECT ai_summary FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "version": row["version"],
        "content": content,
        "filename": row["filename"],
        "ai_summary": task["ai_summary"],
        "created_at": row["created_at"],
    }


def list_versions(conn, task_id) -> dict:
    _task_or_404(conn, task_id)
    rows = conn.execute(
        "SELECT id, task_id, version, filename, file_mtime, created_at, updated_at "
        "FROM outputs WHERE task_id = ? ORDER BY version DESC",
        (task_id,),
    ).fetchall()
    if not rows:
        raise AppError(404, NOT_FOUND, f"No output for task: {task_id}")
    return {"items": [dict(row) for row in rows]}


def get_version(conn, task_id, version) -> dict:
    _task_or_404(conn, task_id)
    row = conn.execute(
        "SELECT * FROM outputs WHERE task_id = ? AND version = ?",
        (task_id, version),
    ).fetchone()
    if row is None:
        raise AppError(
            404,
            NOT_FOUND,
            f"Output version not found: task {task_id}, version {version}",
        )
    task = conn.execute(
        "SELECT ai_summary FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "version": row["version"],
        "content": row["content"],
        "filename": row["filename"],
        "ai_summary": task["ai_summary"],
        "created_at": row["created_at"],
    }


def save(
    conn,
    task_id,
    content,
    base_path,
    base_version=None,
    ai_summary=None,
    filename=None,
) -> dict:
    _task_or_404(conn, task_id)
    latest = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        version_row = conn.execute(
            "SELECT MAX(version) AS m FROM outputs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        latest = version_row["m"] or 0
        if base_version is not None and base_version != latest:
            raise AppError(
                409,
                "VERSION_CONFLICT",
                f"Version conflict: base {base_version} != latest {latest}",
            )

        new_version = latest + 1
        if filename is None:
            filename_row = conn.execute(
                "SELECT filename FROM outputs WHERE task_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            filename = (
                filename_row["filename"] if filename_row is not None else "output.md"
            )

        cursor = conn.execute(
            "INSERT INTO outputs (task_id, version, filename, content) "
            "VALUES (?, ?, ?, ?)",
            (task_id, new_version, filename, content),
        )
        output_id = cursor.lastrowid

        if ai_summary is not None:
            conn.execute(
                "UPDATE tasks SET ai_summary = ? WHERE id = ?",
                (ai_summary, task_id),
            )

        path = _file_path(base_path, task_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as output_file:
            output_file.write(content)
        file_mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        conn.execute(
            "UPDATE outputs SET file_mtime = ?, file_hash = ? WHERE id = ?",
            (file_mtime, file_hash, output_id),
        )

        output_row = conn.execute(
            "SELECT created_at FROM outputs WHERE id = ?",
            (output_id,),
        ).fetchone()
        task = conn.execute(
            "SELECT ai_summary FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        conn.commit()
        return {
            "id": output_id,
            "task_id": task_id,
            "version": new_version,
            "filename": filename,
            "content": content,
            "ai_summary": task["ai_summary"],
            "created_at": output_row["created_at"],
        }
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise AppError(
            409,
            "VERSION_CONFLICT",
            f"Version conflict for task: {task_id}",
        ) from exc
    except Exception:
        conn.rollback()
        raise


def upload(conn, task_id, filename, content, base_path) -> dict:
    return save(
        conn,
        task_id,
        content,
        base_path,
        filename=filename,
    )
