#!/usr/bin/env bash
# Idea Hub v2 云端初始化脚本（用户级部署，免 sudo）
# 用法: bash scripts/deploy/install-server.sh [应用目录，默认 ~/idea-hub]
set -euo pipefail

APP_DIR="${1:-$HOME/idea-hub}"
echo "==> 应用目录: $APP_DIR"

echo "==> 检查系统依赖"
for cmd in python3 curl git; do
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "    $cmd: $(command -v "$cmd")"
    else
        echo "    $cmd: 缺失！请先安装（如 Ubuntu: sudo apt-get install -y $cmd）"
        exit 1
    fi
done
if command -v pnpm >/dev/null 2>&1; then
    echo "    pnpm: $(command -v pnpm)"
else
    echo "    警告：pnpm 缺失，前端构建将跳过（可稍后安装 Node.js 22+ 与 pnpm 后执行构建）"
fi

echo "==> 安装 uv（用户级，$HOME/.local/bin）"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "uv 安装失败，请检查 $HOME/.local/bin/uv" >&2
    exit 1
fi
uv --version

echo "==> 准备应用目录"
mkdir -p "$APP_DIR"
cd "$APP_DIR"

if [ ! -f requirements.txt ]; then
    echo "requirements.txt 不存在，请先上传项目文件" >&2
    exit 1
fi

echo "==> 创建 venv 并安装依赖"
if [ ! -d .venv ]; then
    uv venv .venv
fi
uv pip install --python .venv/bin/python -r requirements.txt

echo "==> 创建运行时目录并复制 prompts"
mkdir -p data outputs backups logs prompts
cp scripts/deploy/prompts/*.txt prompts/

if [ ! -f config.yaml ]; then
    if command -v openssl >/dev/null 2>&1; then
        AUTH_PASS="$(openssl rand -hex 8)"
    else
        AUTH_PASS="$(printf '%s%s' "$RANDOM" "$RANDOM")"
    fi
    cat > config.yaml <<EOF
db_path: data/idea.db
base_path: $APP_DIR
host: 127.0.0.1
port: 8000
auth_user: admin
auth_pass: $AUTH_PASS
deepseek_api_key: ""
EOF
    echo "==> 已生成 config.yaml（auth_user=admin，随机 auth_pass 已写入文件）"
    echo "    请设置 DEEPSEEK_API_KEY 环境变量后再运行需要 LLM 的任务"
else
    echo "==> config.yaml 已存在，保留现有配置"
fi

echo "==> 构建前端（可选）"
if [ -f web/package.json ] && command -v pnpm >/dev/null 2>&1; then
    if (cd web && pnpm install && pnpm build); then
        echo "    前端构建完成：web/dist"
    else
        echo "    警告：前端构建失败，继续完成后端部署"
    fi
elif [ ! -f web/package.json ]; then
    echo "    web/package.json 不存在，跳过前端构建"
else
    echo "    pnpm 不可用，跳过前端构建"
fi

echo "==> 安装用户级 crontab"
if command -v crontab >/dev/null 2>&1; then
    crontab -l > /tmp/cron.bak 2>/dev/null || true
    cat scripts/deploy/crontab.txt >> /tmp/cron.bak
    crontab /tmp/cron.bak
    echo "    crontab v2 已安装"
else
    echo "    警告：crontab 命令不可用，跳过安装；请参考 docs/DEPLOY_CLOUD.md 手动配置"
fi

echo "==> 初始化完成"
echo "下一步："
echo "  nohup uv run uvicorn idea_hub.main:app --host 127.0.0.1 --port 8000 >> logs/server.log 2>&1 &"
echo "  或配置 systemd；参考 docs/DEPLOY_CLOUD.md"
