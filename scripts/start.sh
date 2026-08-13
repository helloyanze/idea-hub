#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then uv venv .venv; fi
uv pip install -r requirements.txt --quiet
mkdir -p data outputs backups
exec uv run uvicorn idea_hub.main:app --host 127.0.0.1 --port 8000
