#!/usr/bin/env bash
# 从云服务器同步 Idea Hub 数据到本地（在本地 Windows git-bash 中运行）
# 用法: bash sync-from-server.sh <user@server> [目标目录]
# 示例: bash sync-from-server.sh ubuntu@1.2.3.4 ~/idea-hub-backup
set -euo pipefail

SERVER="${1:?用法: bash sync-from-server.sh <user@server> [目标目录]}"
DEST="${2:-$HOME/idea-hub-backup}"
REMOTE_DIR="$HOME/idea-hub"

echo "==> 同步 $SERVER:$REMOTE_DIR/{data,outputs} -> $DEST"
mkdir -p "$DEST"

# data/idea.db + outputs/ 递归同步（--delete 保持目标与源一致）
scp -r "$SERVER:$REMOTE_DIR/data" "$SERVER:$REMOTE_DIR/outputs" "$DEST/"

echo "==> 同步完成"
echo "   本地数据: $DEST/data/idea.db"
echo "   本地产出: $DEST/outputs/"
echo "   备份时间: $(date '+%Y-%m-%d %H:%M:%S')"
