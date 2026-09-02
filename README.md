# 🗓 三角洲封号查询

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

《三角洲行动》多账号封禁/处罚倒计时查询工具。QQ 安全中心 / WeGame 扫码即可绑定账号，自动轮询封禁记录、实时计算解封倒计时，支持 **终端面板 / 本地网页面板 / GitHub Pages 远程页面** 三种展示方式。

> ⚠️ 本工具只做「查询 + 展示」，不具备任何封号/解封能力；数据来自第三方公开接口（Delta-UnionAPI），数据版权归腾讯所有。

## ✨ 功能特性

| 功能 | 说明 |
|---|---|
| 📋 多账号管理 | 一个配置文件管理 N 个账号，网页端支持 ✏️ 改名 / 备注 |
| 🪪 扫码绑定 | `--login` 生成 QQ安全中心二维码，扫码即绑定，**无需手动抓 token**；网页端还支持 WeGame 扫码 |
| 🆓 零门槛 | 自动申请匿名 Token，免注册即可查询（也可配置 API Key） |
| ⏳ 自动倒计时 | 根据封禁开始时间 + 时长自动计算剩余时间，24h 内变红 |
| ♾ 永久封禁识别 | 时长超过 9 年自动按「永久」处理 |
| 🧊 冷号模式 | 为账号设置冷却期，倒计时展示、一键解冻 |
| 🔔 状态提醒 | 新封禁 / 解封 / 临期（24h、6h、1h 可配）自动提示 |
| 🖥 双端展示 | 终端实时面板 + 本地网页面板（自适应卡片） |
| 🌐 GitHub Pages | Actions 每 30 分钟自动查询并部署**脱敏**静态页，手机也能看 |

## 📦 安装教程

### 环境要求

- Python 3.8+（Windows / macOS / Linux 均可）
- 唯一第三方依赖：`requests`

### 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/hxt6666/delta-ban-query.git
cd delta-ban-query

# 2. 安装依赖
pip install requests
```

首次运行任何命令时会自动生成 `accounts.json` 配置文件，无需手动创建。

## 🚀 快速开始

### 第 1 步：扫码绑定账号

```bash
python main.py --login
```

执行后：

1. 输入账号备注名（如：大号 / 小号）
2. 程序自动申请匿名 Token 并生成 **QQ安全中心** 登录二维码（自动弹出 `login_qr.png`）
3. 用 **QQ安全中心 / 三角洲行动 App** 扫码（3 分钟内有效）
4. 扫码成功 → `frameworkToken` 自动写入 `accounts.json`
5. 多个账号：重复执行 `--login` 逐个添加即可

### 第 2 步：查看面板

```bash
# 本地网页面板（推荐）：浏览器打开 http://127.0.0.1:8808
python main.py --web

# 自定义端口
python main.py --web --port 9000

# 终端实时面板（每 5 秒刷新）
python main.py

# 只查询一次就退出（适合计划任务/crontab）
python main.py --once
```

### 网页面板能做什么

- 卡片式展示每个账号的状态：✅ 正常 / 🟠🔴 封禁倒计时 / ♾ 永久 / 🧊 冷号 / ⚠ 失败
- ➕ 扫码绑定新账号（QQ安全中心 / WeGame 双 Tab）
- ✏️ 改名 / 写备注（显示在卡片上）
- 🧊 设置冷号冷却（1天 / 3天 / 7天 快捷按钮，可提前解冻）
- 🗑 删除账号

### 挂后台持续监控（Windows）

- 方式一：双击 `start.bat`（等同 `pythonw main.py --web`，可放入启动文件夹开机常驻）
- 方式二：计划任务（schtasks）每 30 分钟执行一次 `python main.py --once`

## 🌐 进阶：GitHub Pages 远程查看（可选）

想在手机 / 其他设备上查看？用 GitHub Actions 定时查询并部署静态页：

1. 把本仓库推送到你自己的 GitHub 仓库
2. 在仓库 **Settings → Secrets and variables → Actions** 添加两个 Secret：
   | Secret | 内容 |
   |---|---|
   | `DELTA_API_KEY` | 在 [delta-test.shallow.ink](https://delta-test.shallow.ink) 注册后在个人面板创建的 API Key |
   | `DELTA_ACCOUNTS` | 本地 `accounts.json` 中 `accounts` 数组的完整 JSON 内容 |
3. 在 **Settings → Pages** 将 Source 设置为 **GitHub Actions**
4. 完成：每 30 分钟自动更新，访问 `https://<用户名>.github.io/<仓库名>/`

> 🔒 **隐私说明**：Pages 公开页面已做脱敏——只显示账号备注名、状态与倒计时，**不包含**角色名、封禁原因、头像、游戏名等任何敏感信息（由 `.github/scripts/fetch_status.py` 在数据源头剔除）。

## ⚙️ 配置说明（`accounts.json`）

```json
{
  "api_base_url": "https://delta-test-api.shallow.ink",
  "auth_mode": "anonymous",
  "api_key": "",
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
| `auth_mode` | `anonymous` | 认证方式：`anonymous` / `api_key` / `bearer` |
| `api_key` | 空 | 注册平台后创建的 API Key（可选，比匿名 Token 稳定） |
| `poll_interval_minutes` | 30 | 轮询间隔（分钟） |
| `notify_before_hours` | `[24, 6, 1]` | 解封前提醒时间点 |
| `accounts[].name` | 自动 | 账号显示名 |
| `accounts[].framework_token` | 扫码自动 | 账号登录凭证 |

匿名 Token 24 小时有效且会自动刷新；想更稳定可在平台注册后把 API Key 填入 `api_key` 并设置 `"auth_mode": "api_key"`。

## 📁 项目结构

```
delta-ban-query/
├── main.py                        # 核心单文件：API 客户端 / 轮询 / 终端+网页面板
├── login_scan.py                  # 独立扫码绑定脚本
├── accounts.example.json          # 配置模板（真实 accounts.json 自动生成，已被 gitignore）
├── start.bat                      # Windows 后台启动脚本
├── test_offline.py                # 逻辑测试
├── web/
│   └── index.html                 # GitHub Pages 静态页（读脱敏 status.json）
└── .github/
    ├── scripts/fetch_status.py    # Actions 数据拉取（含源头脱敏）
    └── workflows/update-status.yml # 每 30 分钟部署 Pages
```

## 🔒 安全须知

- `frameworkToken` 等同于账号登录凭证，请勿泄露给他人
- `accounts.json`、日志、二维码等敏感文件已在 `.gitignore` 中排除，不会被提交
- 公开仓库部署 Pages 时，请使用本项目自带的脱敏数据流，不要把完整数据直接提交到公开仓库

## ⚠️ 免责声明

- 本工具仅供查询**自己**账号的封禁状态，不参与、不干扰任何游戏行为
- 第三方接口存在失效或变更风险，数据以游戏内官方展示为准
- 请遵守《三角洲行动》用户协议及腾讯相关服务条款；使用本工具产生的任何后果由使用者自行承担

## 📄 License

[MIT](LICENSE)
