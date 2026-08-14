# Idea Hub v2 云端部署指南

本文档适用于 Idea Hub v2。所有常规命令均为 v2 命令；第八节出现的旧命令仅用于识别并替换已废弃的 v1 配置。

## 1. 架构

云端运行一套用户级部署：

- uvicorn 启动 idea_hub.main:app，后端同源托管前端构建产物 web/dist。
- v2 crontab 每 5 分钟唤醒 tick；每天 03:00 执行在线备份。
- data/idea.db 与仓库根目录 outputs/ 是唯一运行数据源。
- backups/ 保存压缩备份，logs/ 保存服务、调度器、备份和健康检查日志。

确认仓库目录：

~~~bash
cd ~/idea-hub
pwd
~~~

## 2. 前置依赖

需要 Python 3.11+、uv、Node.js 22+ 和 pnpm。pnpm 仅用于前端构建，但建议在部署前安装。

~~~bash
python3 --version
curl --version
git --version
node --version
pnpm --version
uv --version
~~~

若尚未安装 uv，使用用户级安装，不需要 sudo：

~~~bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
~~~

## 3. 上传与初始化

在本地项目根目录执行上传（将 <user> 和 <server> 替换为云服务器账号与地址）：

~~~bash
scp -r idea_hub web scripts requirements.txt pytest.ini README.md <user>@<server>:~/idea-hub/
~~~

登录云服务器后运行一键初始化脚本：

~~~bash
cd ~/idea-hub
bash scripts/deploy/install-server.sh
~~~

脚本会完成用户级 uv venv、依赖安装、data/、outputs/、backups/、logs/、prompts/ 目录创建、config.yaml 生成、前端 pnpm build（可选）以及 v2 crontab 安装。

## 4. 配置

config.yaml 的关键字段如下：

~~~yaml
db_path: data/idea.db
base_path: /home/<user>/idea-hub
host: 127.0.0.1
port: 8000
auth_user: admin
auth_pass: <随机密码>
deepseek_api_key: ""
~~~

配置优先级为配置文件后由环境变量覆盖。config.py 的 environment_overrides 映射包括：

~~~bash
export DEEPSEEK_API_KEY="sk-..."
export IDEAHUB_AUTH_USER="admin"
export IDEAHUB_AUTH_PASS="change-this-password"
~~~

如果 config.yaml 不存在，应用使用默认配置，HTTP Basic 认证默认关闭；生产环境应运行初始化脚本生成配置，或手动创建配置并设置认证信息。API key 只通过环境变量或服务器上的配置注入。

## 5. 启动与健康检查

手动启动同源 Web 服务：

~~~bash
cd ~/idea-hub
nohup uv run uvicorn idea_hub.main:app --host 127.0.0.1 --port 8000 >> logs/server.log 2>&1 &
~~~

如果服务器已有 idea-hub.service，可使用现有 systemd unit 管理服务；确认 unit 内容指向 v2 的 idea_hub.main:app 后执行：

~~~bash
sudo systemctl daemon-reload
sudo systemctl enable --now idea-hub.service
sudo systemctl status idea-hub.service
~~~

检查 API 健康状态：

~~~bash
curl -i http://127.0.0.1:8000/api/v1/health
~~~

若配置了认证，使用账号密码检查：

~~~bash
curl -i -u admin:'<auth_pass>' http://127.0.0.1:8000/api/v1/health
~~~

访问 / 时，若 web/dist 存在，FastAPI 会同源返回前端 index.html；不存在时 API 仍可运行。

## 6. crontab 安装与说明

安装仓库提供的 v2 配方：

~~~bash
cd ~/idea-hub
crontab -l > /tmp/cron.bak 2>/dev/null || true
cat scripts/deploy/crontab.txt >> /tmp/cron.bak
crontab /tmp/cron.bak
crontab -l
~~~

v2 调度安排：

- 每 5 分钟执行 uv run python -m idea_hub.cli tick。
- collect 的实际触发频率由数据库 settings.collect_interval_hours 动态控制。
- 每日 03:00 执行 scripts/deploy/backup.sh，保留最近 7 份压缩备份。
- @reboot 使用 uv run uvicorn idea_hub.main:app 自启 Web 服务。
- 每 15 分钟运行 scripts/deploy/healthcheck.sh，检查 scheduler_last_tick。

v2 不再需要 v1 的 collect、generate、execute 定时命令；任务由 tick 与 Web API 驱动。

## 7. 备份与恢复

手动执行备份：

~~~bash
cd ~/idea-hub
bash scripts/deploy/backup.sh
ls -lh backups/idea-backup-*.tar.gz
~~~

脚本产出 backups/idea-backup-<时间戳>.tar.gz，包内含 idea.db 与 outputs/。备份使用 SQLite db.backup 在线备份 API，因此包含尚未 checkpoint 的 WAL 数据。

恢复前停止 Web 服务和调度任务，然后解包到临时目录：

~~~bash
cd ~/idea-hub
mkdir -p /tmp/idea-hub-restore
tar -xzf backups/idea-backup-<时间戳>.tar.gz -C /tmp/idea-hub-restore
~~~

覆盖数据库并恢复输出目录：

~~~bash
cp /tmp/idea-hub-restore/idea.db data/idea.db
rm -rf outputs
cp -r /tmp/idea-hub-restore/outputs outputs
rm -rf /tmp/idea-hub-restore
~~~

恢复完成后重新启动服务，并用健康检查确认数据库可读。

## 8. 云端切换注意（v1 -> v2，Important）

旧配置切换时，不要手工逐条编辑。先备份旧 crontab，再用本仓库的 v2 文件整体替换：

~~~bash
cd ~/idea-hub
crontab -l > /tmp/cron.bak 2>/dev/null || true
crontab scripts/deploy/crontab.txt
crontab -l
~~~

以下仅是 v1 已废弃的旧引用，用来识别需要删除的旧 crontab 行，不要执行：

- v1 已废弃：idea_hub.server:app
- v1 已废弃：python -m idea_hub.scheduler --db data/idea.db
- v1 已废弃：db.backup_db
- v1 已废弃：旧 healthcheck 键 last_scheduler_tick

切换后应只保留 scripts/deploy/crontab.txt 中的 v2 配方；健康检查键已改为 scheduler_last_tick。

## 9. hermes verify 配方修正

.hermes/environment.json 的启动命令已从 v1 已废弃的 idea_hub.server:app 改为 v2 的 idea_hub.main:app：

~~~bash
cat .hermes/environment.json
~~~

verify 使用：

~~~bash
uv pip install -r requirements.txt
uv run uvicorn idea_hub.main:app --host 127.0.0.1 --port 8000
~~~

readinessPath 为 /；当 web/dist 存在时，静态服务返回 index.html，readiness 响应为 HTTP 200。

## 10. 运维

日志位置：

~~~bash
cd ~/idea-hub
ls -lh logs/tick.log logs/backup.log logs/server.log logs/healthcheck.log
~~~

手动触发一次调度 tick：

~~~bash
uv run python -m idea_hub.cli tick
~~~

手动执行备份：

~~~bash
bash scripts/deploy/backup.sh
~~~

通过 Web API 手动触发执行任务：

~~~bash
curl -X POST http://127.0.0.1:8000/api/v1/execute -H 'Content-Type: application/json' -d '{}'
~~~

若启用认证，在 curl 中增加 -u admin:'<auth_pass>'。本地通过 SSH 隧道访问云端 Web：

~~~bash
ssh -N -L 8000:127.0.0.1:8000 <user>@<server>
~~~

然后在本地浏览器打开 http://127.0.0.1:8000。

## 11. 安全说明

- Uvicorn 仅监听 127.0.0.1，公网访问通过 SSH 隧道或受控反向代理。
- DEEPSEEK_API_KEY 等 API key 只存环境变量或服务器上的 config.yaml，不提交到代码仓库。
- config.yaml 含认证密码，应限制文件权限：

~~~bash
chmod 600 config.yaml
~~~

- 备份包包含数据库和输出内容，也应限制访问权限：

~~~bash
chmod 700 backups
chmod 600 backups/idea-backup-*.tar.gz
~~~
