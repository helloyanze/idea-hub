# Cloudflare Tunnel 接入指引（Idea Hub 公网访问）

> 目标：通过 Cloudflare Tunnel 让 Idea Hub 在公网可访问（https://idea.你的域名），
> 服务器不开放任何公网端口，访问需通过 Cloudflare Access 认证。
> 全程约 20-30 分钟，大部分是等待 DNS 生效。

---

## 第一阶段：接入 Cloudflare（一次性，约 10 分钟）

### 1. 注册 / 登录
访问 https://dash.cloudflare.com 注册账户（邮箱即可，免费版足够）。

### 2. 添加域名
- 首页 → **Add a site** → 输入你的域名（如 example.com）→ **Add site**
- 套餐选择 **Free**（免费计划足够：Tunnel、Access 免费版、TLS 全支持）

### 3. 扫描 DNS 记录
- CF 会自动扫描你域名现有的 DNS 记录（A/CNAME/MX 等），**全部保留，不要删**
- 如果有你正在用的网站/邮箱记录，保持原样
- 点击 **Continue** → **Done**

### 4. 修改注册商的 Nameserver（关键步骤）
- CF 会给你两个 Nameserver 地址（形如 `xxx.ns.cloudflare.com`）
- 去你的**域名注册商**（阿里云/腾讯云/GoDaddy 等）控制台 → 域名管理 → **修改 DNS 服务器/Nameserver**
- 把原来的 NS 替换为 CF 给的两个地址
- 保存后等待生效（几分钟到 24 小时，通常 10-30 分钟）
- 回到 CF 面板，域名状态会从 "Pending Nameserver" 变为 "Active"

> 生效期间旧网站不受影响（记录已保留）。邮箱等 MX 记录照常工作。

---

## 第二阶段：创建 Tunnel（约 5 分钟）

### 1. 进入 Zero Trust 面板
- 访问 https://one.dash.cloudflare.com（用同一个 CF 账户登录）
- 首次会引导创建团队名（随意，如 idea-hub）和免费计划

### 2. 创建 Tunnel
- 左侧 **Networks → Tunnels** → **Create a tunnel**
- 选择 **Cloudflared** → **Next**
- 命名（如 `idea-hub`）→ **Save tunnel**

### 3. 获取安装命令 / Token（给服务器用）
- 安装方式选择 **Debian**（或任意），页面会显示一段命令，形如：
  ```
  sudo cloudflared service install <TOKEN>
  ```
  **复制 TOKEN**（很长的一段，形如 `eyJ...`）——把这个 token 发给我，
  服务器端的安装运行我来完成。

### 4. 配置 Public Hostname（域名路由）
- 在 Tunnel 详情页 → **Public Hostname** 标签 → **Add a public hostname**
- Subdomain：`idea`  |  Domain：你的域名
- Service Type：`HTTP`  |  URL：`127.0.0.1:8000`
- Save

> 完成后：访问 https://idea.你的域名 应能打开 Idea Hub（此时还没有认证，先确认通了再加锁）。

---

## 第三阶段：配置 Access 认证（约 5 分钟，加锁）

### 1. 创建 Access Application
- Zero Trust 面板 → **Access → Applications** → **Add an application**
- 类型选 **Self-hosted**
- 配置：
  - Application domain：`idea.你的域名`（或 `*.你的域名`）
  - Session duration：24h（减少重复验证）
- **Add** 保存

### 2. 配置认证策略
- 在 Application 的 **Policies** 里点 **Add a policy**
  - Policy name：`仅自己`
  - Action：**Allow**
  - Session duration：24h
  - Configure rules：**Include → Emails** → 填你自己的邮箱
    （或选择 **Everyone** + 在 Login method 里配 One-time PIN）

### 3. 配置登录方式（Login methods）
- Access → **Authentication** → **Login methods** → **Add**
- 推荐 **One-time PIN**（输入邮箱收验证码）或 **Email OTP**
- 如果选了 Emails 策略，确保你的邮箱已加入

### 4. 验证
- 打开 https://idea.你的域名
- 应弹出 CF 认证页 → 输入邮箱 → 收验证码 → 进入 Idea Hub
- 勾选 "Remember this browser"（如果有）减少下次验证

---

## 安全收尾建议

1. **服务器防火墙**：确认仅开放 SSH（22），Web 端口（8000）保持仅 127.0.0.1
2. **SSH 保持密钥认证**（已配置），保留 SSH 隧道作为应急备用通道
3. CF 免费版自带 DDoS 防护 + WAF 基础规则（免费计划含托管规则集，建议开启）
4. 认证失败有 CF 侧限速保护，无需额外 fail2ban

## 回滚方式
- 不想用了：Zero Trust → Tunnels → 删除 tunnel；服务器停掉 cloudflared 服务
- 域名改回：注册商处恢复原 NS 即可
