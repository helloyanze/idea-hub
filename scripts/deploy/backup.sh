#!/usr/bin/env bash
# Idea Hub 备份脚本（S8.1）
# 用法: bash scripts/deploy/backup.sh [备份目录，默认 backups/]
# 备份内容: SQLite online backup（db.backup API，含 WAL 未 checkpoint 数据）+ outputs/ 目录
# 保留策略: 最近 7 份 tar 包
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$APP_DIR"
BACKUP_DIR="${1:-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "==> SQLite online backup (db.backup API)"
DB_DEST="$(uv run python -m idea_hub.cli backup --dest-dir "$BACKUP_DIR" | tail -n 1)"
echo "    db copy: $DB_DEST"

echo "==> 打包 db 副本 + outputs/"
STAGE="$BACKUP_DIR/.stage-$STAMP"
mkdir -p "$STAGE"
cp "$DB_DEST" "$STAGE/idea.db"
cp -r outputs "$STAGE/outputs"
tar -czf "$BACKUP_DIR/idea-backup-$STAMP.tar.gz" -C "$STAGE" idea.db outputs
rm -rf "$STAGE"
rm -f "$DB_DEST"

echo "==> 清理旧备份（保留最近 7 份）"
ls -1t "$BACKUP_DIR"/idea-backup-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f

echo "backup done: $BACKUP_DIR/idea-backup-$STAMP.tar.gz"
