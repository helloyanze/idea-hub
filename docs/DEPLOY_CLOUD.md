# Idea Hub 云端部署指南（方案 A：云端全自动）

将 Idea Hub + Hermes 部署到云服务器，每晚自动完成"收集热点 → 生成 idea → 执行等待队列"，数据同步回本地查看。

## 架构

```
┌───────────────────── 云服务器 ─────────────────────┐
│  Hermes Agent（LLM 能力）                           │
│    ├─ 每晚 02:00 cron: collect → 生成 idea（AI）    │
│    └─ 每晚 03:00 cron: 执行等待队列（AI 产出内容）   │
│  Idea Hub（uvicorn :8000，仅 127.0.0.1 监听）       │
│  data/idea.db + outputs/（唯一数据源）              │
└─────────────────────┬──────────────────────────────┘
                      │ SSH 隧道（远程操作 Web）
                      │ scp 每晚同步（数据备份/离线查看）
┌─────────────────────▼──────────────────────────────┐
│  本地 Windows                                        │
│    - 浏览器 http://127.0.0.1:8000 操作云端 Web      │
│    - 每晚拉取 data/idea.db + outputs/ 到本地备份    │
└────────────────────────────────────────────────────┘
```

## 一、服务器准备（一次性）

1. 一台 Linux 云服务器（推荐 Ubuntu 22.04+，2C2G 即可，费用约 30-60 元/月）
2. 开放 SSH（22 端口）；Web 端口不需要公网开放（走 SSH 隧道访问，更安全）

## 二、上传代码与初始化

```bash
# 本地执行：上传项目代码（排除 .venv/data/outputs）
cd /d/Programs/idea-hub
scp -r idea_hub web scripts requirements.txt pytest.ini README.md \
    ubuntu@<服务器IP>:/tmp/idea-hub-upload/
ssh ubuntu@<服务器IP> "sudo mkdir -p $HOME/idea-hub && sudo chown -R ubuntu:ubuntu $HOME/idea-hub && cp -r /tmp/idea-hub-upload/* $HOME/idea-hub/"

# 服务器执行：初始化环境（Python/uv/venv/依赖/Hermes）
ssh ubuntu@<服务器IP> "cd $HOME/idea-hub && bash scripts/deploy/install-server.sh $HOME/idea-hub"
```

## 三、配置 Hermes（服务器上执行一次）

```bash
ssh ubuntu@<服务器IP>
cd $HOME/idea-hub
hermes setup        # 交互式选择 provider（如 DeepSeek）并填入 API key
# 或非交互：
hermes config set model.provider deepseek
hermes config set model.model deepseek-chat
# 在 ~/.hermes/.env 写入 DEEPSEEK_API_KEY=sk-xxx
```

验证：`hermes chat -q "你好"`

## 四、初始化数据（服务器上执行一次）

先启动 Web 服务（见第五节），然后在服务器本机用 API 初始化：

```bash
# 创建目标模式（自媒体内容类）——score_dimensions 为 JSON 字符串
curl -s -X POST http://127.0.0.1:8000/api/targets \
    -H "Content-Type: application/json" \
    -d '{"name":"自媒体内容","description":"生成自媒体内容 idea","score_dimensions":"{\"热度\":0.4,\"相关性\":0.3,\"可执行性\":0.3}"}'
# 激活该目标（返回的 id 假设为 1）
curl -s -X POST http://127.0.0.1:8000/api/targets/1/activate
# 添加来源（热榜 API：url 指向返回 JSON 的接口）
curl -s -X POST http://127.0.0.1:8000/api/sources \
    -H "Content-Type: application/json" \
    -d '{"type":"hotlist","name":"示例热榜","url":"https://api.example.com/hot"}'
# 添加 RSS 来源
curl -s -X POST http://127.0.0.1:8000/api/sources \
    -H "Content-Type: application/json" \
    -d '{"type":"rss","name":"示例RSS","url":"https://example.com/feed.xml"}'
# 测试收集
uv run python -m idea_hub.cli --db data/idea.db collect
```

> 说明：也可以通过 Web 界面（SSH 隧道后打开 http://127.0.0.1:8000 → 来源管理弹窗）添加来源；
> 目标模式切换在界面顶部下拉完成。

## 五、安装服务与定时任务

```bash
# Web 服务常驻（systemd）
sudo cp scripts/deploy/idea-hub.service /etc/systemd/system/
sudo sed -i 's/^User=ubuntu/User='"$USER"'/' /etc/systemd/system/idea-hub.service
sudo systemctl daemon-reload
sudo systemctl enable --now idea-hub

# 每晚任务（crontab）
mkdir -p logs prompts
cp scripts/deploy/prompts/*.txt prompts/
crontab -l > /tmp/cron.bak 2>/dev/null || true
cat scripts/deploy/crontab.txt >> /tmp/cron.bak
crontab /tmp/cron.bak
crontab -l   # 确认
```

## 六、本地访问与同步

### 远程操作 Web（SSH 隧道）

```bat
REM Windows：双击或命令行运行
scripts\sync\tunnel.cmd ubuntu 服务器IP
REM 然后浏览器打开 http://127.0.0.1:8000
```

或手动：`ssh -N -L 8000:127.0.0.1:8000 ubuntu@<服务器IP>`

### 每晚同步数据到本地

```bash
# git-bash 手动同步
bash scripts/sync/sync-from-server.sh ubuntu@<服务器IP> ~/idea-hub-backup
```

**配置 Windows 计划任务自动同步：**

1. 打开"任务计划程序" → 创建基本任务
2. 触发器：每天 08:00（或你起床时间）
3. 操作：启动程序 → 程序 `C:\Program Files\Git\bin\bash.exe`，参数 `-lc "/d/Programs/idea-hub/scripts/sync/sync-from-server.sh ubuntu@<服务器IP> ~/idea-hub-backup"`
4. 完成。每天自动把云端 data + outputs 拉到本地 `~/idea-hub-backup/`

> 首次运行 scp 会要求输入密码；如需免密，配置 SSH 密钥：
> `ssh-keygen`（本地）→ `ssh-copy-id ubuntu@<服务器IP>`（或手动把公钥加到服务器 ~/.ssh/authorized_keys）

### 离线查看

同步完成后，可在本地启动只读查看：
```bash
cd ~/idea-hub-backup && python -m http.server 8080  # 仅查看 outputs 文件
```
（完整 Web 界面在云端，本地数据仅供备份与文件级查看）

## 七、每晚自动运行的完整流程

| 时间 | 任务 | 说明 |
|---|---|---|
| 02:00 | collect + generate | 抓热点 → AI 生成 idea（评分入列：>=6 待办 / <6 留档）→ 关联热点重评分 |
| 03:00 | execute | AI 领取等待队列任务 → 产出文章到 outputs/tasks/<id>/output.md |
| 03:30 | backup | 数据库备份（保留 7 份） |
| 08:00（本地） | 本地同步 | scp 拉取 data + outputs 到本地备份目录 |

## 八、运维

- 日志：`$HOME/idea-hub/logs/{collect,generate,execute,backup}.log`
- Web 服务状态：`sudo systemctl status idea-hub`；重启：`sudo systemctl restart idea-hub`
- 手动触发一次收集：`ssh ubuntu@<服务器IP> "cd $HOME/idea-hub && uv run python -m idea_hub.cli --db data/idea.db collect"`
- 手动触发一次执行：`ssh ubuntu@<服务器IP> "cd $HOME/idea-hub && hermes chat -q \"\$(cat prompts/execute.txt)\""`

## 安全说明

- Web 只监听 127.0.0.1，不直接暴露公网；访问一律走 SSH 隧道
- API key 只存在服务器 ~/.hermes/.env，不进入代码仓库
- 数据同步走 scp（SSH 加密）
