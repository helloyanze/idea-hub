#!/usr/bin/env bash
# Idea Hub 云端初始化脚本（用户级部署，免 sudo）
# 用法: bash install-server.sh [应用目录，默认 ~/idea-hub]
set -euo pipefail

APP_DIR="${1:-$HOME/idea-hub}"
echo "==> 应用目录: $APP_DIR"

echo "==> 检查系统依赖 (python3/curl/git)"
for cmd in python3 curl git; do
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "    $cmd: $(command -v $cmd)"
    else
        echo "    $cmd: 缺失！请先安装（如 Ubuntu: sudo apt-get install -y $cmd）"
        exit 1
    fi
done

echo "==> 安装 uv（用户级）"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version

echo "==> 准备应用目录"
mkdir -p "$APP_DIR"
cd "$APP_DIR"

echo "==> 创建 venv 并安装依赖"
if [ ! -d .venv ]; then
    uv venv .venv
fi
uv pip install --python .venv/bin/python -r requirements.txt

echo "==> 创建运行时目录"
mkdir -p data outputs backups logs prompts
cp -n scripts/deploy/prompts/*.txt prompts/ 2>/dev/null || true

echo "==> 安装 Hermes Agent（用户级）"
if ! command -v hermes >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/hermes" ]; then
    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
fi
export PATH="$HOME/.local/bin:$PATH"
hermes --version 2>/dev/null || echo "    (hermes 已安装或需手动 hermes setup)"

echo "==> 完成"
echo "下一步:"
echo "  1. hermes setup 配置 LLM provider 和 API key"
echo "  2. crontab -l > /tmp/cron.bak && cat scripts/deploy/crontab.txt >> /tmp/cron.bak && crontab /tmp/cron.bak"
echo "  3. 参考 docs/DEPLOY_CLOUD.md 完成初始化"
