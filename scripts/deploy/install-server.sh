#!/usr/bin/env bash
# Idea Hub 云端初始化脚本（在云服务器上以 root 或 sudo 执行）
# 用法: bash install-server.sh $HOME/idea-hub
set -euo pipefail

APP_DIR="${1:-$HOME/idea-hub}"
echo "==> 安装系统依赖 (Python 3.11+, uv, git)"

# Debian/Ubuntu
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y python3 python3-venv python3-pip git curl
fi
# CentOS/RHEL
if command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip git curl
fi

echo "==> 安装 uv"
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

echo "==> 准备应用目录 $APP_DIR"
mkdir -p "$APP_DIR"
cd "$APP_DIR"

echo "==> 创建 venv 并安装依赖"
if [ ! -d .venv ]; then
    uv venv .venv
fi
uv pip install --python .venv/bin/python -r requirements.txt

echo "==> 创建运行时目录"
mkdir -p data outputs backups

echo "==> 安装 Hermes Agent"
if ! command -v hermes >/dev/null 2>&1; then
    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
fi
export PATH="$HOME/.local/bin:$PATH"
hermes --version || true

echo "==> 完成"
echo "下一步（手动执行）:"
echo "  1. hermes setup  # 配置 LLM provider 和 API key"
echo "  2. 配置 sources 表: uv run python -m idea_hub.cli --db data/idea.db 相关命令"
echo "  3. 安装 systemd 服务与 crontab: 参考 docs/DEPLOY_CLOUD.md"
