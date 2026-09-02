# 🗓 三角洲行动 · 多账号封禁倒计时管理工具

管理你的多个《三角洲行动》账号封禁/处罚时间，自动计算解封倒计时，支持终端面板和网页版。

基于 **新版 Delta-Union-API**（作者公测中的集成后端，持续更新，功能更多）。

## ✨ 功能特性

| 功能 | 说明 |
|---|---|
| 📋 多账号管理 | 一个文件管理 N 个账号（自定义名称 + Token）|
| 🪪 内置扫码绑定 | `--login` 一键生成 QQ安全中心二维码，扫码即绑定账号，**不用手动抠 token** |
| ✏️ 改名/备注 | 绑定后在网页卡片点 ✏️，随时改名、写备注（备注显示在卡片上）|
| 🆓 零门槛使用 | 自动申请匿名 Token，**免注册**即可查询 |
| ⏳ 自动倒计时 | 根据封禁开始时间 + 时长自动算剩余时间 |
| ♾ 永久封禁识别 | 时长超过 9 年自动按"永久"处理 |
| 🔔 状态提醒 | 新封禁 / 解封 / 临期（24h、6h、1h 可配）自动提示 |
| 🖥 双端展示 | 终端实时面板 + 本地网页面板（自适应卡片）|
| 🔄 自动轮询 | 默认每 30 分钟拉取一次，可配置 |

## 🚀 快速开始

### 1. 安装依赖

需要 Python 3.8+：

```bash
pip install requests
```

### 2. 绑定账号（扫码即可）

```bash
# 第一次：绑定第一个账号
python main.py --login

# 多个账号：重复执行 --login 即可逐个添加
```

执行后程序会：
1. 自动申请匿名 Token
2. 生成 QQ安全中心 登录二维码（自动弹出图片）
3. 你用 **QQ安全中心 / 三角洲行动** 扫码
4. 登录成功 → frameworkToken 自动写入 `accounts.json`

### 3. 运行

```bash
# 终端实时面板（每5秒刷新）
python main.py

# 网页面板
python main.py --web            # http://127.0.0.1:8808

# 只查询一次（适合 crontab/计划任务）
python main.py --once
```

## ⚙️ 配置说明 (`accounts.json`)

```json
{
  "api_base_url": "https://delta-test-api.shallow.ink",
  "auth_mode": "anonymous",        // anonymous | api_key | bearer
  "api_key": "",                   // 注册平台后可在前端申请 API Key（可选，替代匿名）
  "poll_interval_minutes": 30,
  "notify_before_hours": [24, 6, 1],
  "accounts": [
    { "name": "大号", "framework_token": "扫码自动填入" }
  ]
}
```

| 字段 | 默认值 | 说明 |
|---|---|---|
| `api_base_url` | `https://delta-test-api.shallow.ink` | 新版后端地址 |
| `auth_mode` | `anonymous` | 认证方式（匿名/API Key/Bearer）|
| `api_key` | 空 | 注册用户在控制台创建的 Key（可选）|
| `poll_interval_minutes` | 30 分钟 | 轮询间隔 |
| `notify_before_hours` | `[24,6,1]` | 解封前提醒时间点 |
| `accounts[].name` | 自动 | 账号显示名 |
| `accounts[].framework_token` | 扫码自动 | QQ安全中心登录凭证 |

## 🔌 接口说明（新版 Delta-UnionAPI，数据版权归腾讯）

```
认证: POST /api/v1/auth/anonymous-token  {fingerprint: UUID}
扫码: GET  /api/v1/login/qqsafe/qr          → frameworkToken + QR图片
轮询: GET  /api/v1/login/qqsafe/status     (X-Framework-Token)
封禁: GET  /api/v1/df/qqsafe/ban           (X-Framework-Token)
```

所有请求认证头：
- `X-Anonymous-Token`（匿名，工具自动）
- 或 `X-API-Key`（注册后）
- 或 `Authorization: Bearer xxx`

## 🔌 高级：注册账户 + 申请 API Key

匿名 Token 24 小时有效，会**自动刷新**（工具重启自动重新申请，账号 token 不受影响）。
如果你想要更稳定的凭据：

1. 访问前端 `https://delta-test.shallow.ink` 注册账户
   （支持邮箱注册 / QQ / GitHub / Google / 熵增开放平台 登录）
2. 登录后在个人面板创建 **API Key**
3. 填到 `accounts.json` 的 `api_key` 字段，并设 `"auth_mode": "api_key"`

## ⚠️ 注意

- 本工具仅做"查询 + 展示"，不提供任何封号/解封操作
- 使用第三方 API 有失效风险，数据以游戏内为准
- frameworkToken 是账号登录凭据，请勿泄露
- 新版后端处于**公测阶段**，接口可能有变更；本工具已做字段容错