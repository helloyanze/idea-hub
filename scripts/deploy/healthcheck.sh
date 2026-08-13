#!/usr/bin/env bash
# 调度器健康监控：每 15 分钟 cron 运行。last_scheduler_tick 超 15 分钟未更新则 QQ 告警。
set -euo pipefail
cd "$HOME/idea-hub"
LAST=$(.venv/bin/python -c "
from idea_hub import db
c = db.connect('data/idea.db')
print(c.execute(\"SELECT value FROM settings WHERE key='last_scheduler_tick'\").fetchone()[0])
c.close()")
if [ -z "$LAST" ]; then exit 0; fi
MIN=$(.venv/bin/python -c "
from datetime import datetime
from idea_hub import db
c = db.connect('data/idea.db')
ts = c.execute(\"SELECT value FROM settings WHERE key='last_scheduler_tick'\").fetchone()[0]
c.close()
try:
    last = datetime.fromisoformat(ts)
    print(int((datetime.now() - last).total_seconds() // 60))
except Exception:
    print('999')")
if [ "${MIN:-999}" -gt 15 ]; then
  set -a; source .env 2>/dev/null || true; set +a
  hermes send --to "${QQ_TARGET:-qq:$(grep -oP 'qq:\K[0-9]+' .env 2>/dev/null || echo 0)}" \
    "Idea Hub 调度器异常：最后运行于 ${LAST}（${MIN} 分钟前），请检查 crontab 与日志" \
    || echo "healthcheck send failed" >> logs/healthcheck.log
fi
