#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三角洲行动 · 多账号封禁倒计时管理工具 (Delta-Union-API 新版后端)
================================================================
功能：
  1. 自动申请匿名 Token（免注册）或使用 API Key 认证
  2. 内置 QQ安全中心 扫码登录：生成二维码 - 扫码 - 自动保存 frameworkToken
  3. 多账号统一管理，定时轮询违规记录，自动计算解封倒计时
  4. 终端实时面板 / 本地网页面板 双端展示
  5. 新封禁 / 解封 / 临期(24h/6h/1h) 自动提醒

接口（新版公共后端，数据版权归腾讯）：
  基础:  POST /api/v1/auth/anonymous-token       (匿名Token, fingerprint=UUID)
  扫码:  GET  /api/v1/login/qqsafe/qr            → {frameworkToken, qr_image(base64)}
  轮询:  GET  /api/v1/login/qqsafe/status        (X-Framework-Token)
  封禁:  GET  /api/v1/df/qqsafe/ban              (X-Framework-Token)
  认证头: X-Anonymous-Token / X-API-Key / Authorization: Bearer

用法：
  python main.py                  进入终端实时面板
  python main.py --login          扫码绑定新账号（QQ安全中心）
  python main.py --once           只查询一次
  python main.py --web            启动网页版面板 http://127.0.0.1:8800
  python main.py --web --port 9000
"""

import argparse
import base64
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG = BASE_DIR / "accounts.json"
CONFIG_EXAMPLE = BASE_DIR / "accounts.example.json"

PERMANENT_SEC = 365 * 9 * 86400  # >9年视为永久

try:
    import requests
except ImportError:
    print("[!] 缺少依赖 requests，请先安装:  pip install requests")
    sys.exit(1)


def now_str():
    return datetime.now().strftime("%m-%d %H:%M:%S")


def resolve_avatar(icon):
    """把角色 icon 字段转成可访问的头像 URL。
    icon 可能是完整 URL（http...），也可能是纯数字（p.qlogo.cn 头像 id）。"""
    if not icon:
        return ""
    icon = str(icon).strip()
    if icon.startswith("http"):
        return icon
    if icon.isdigit():
        return f"https://p.qlogo.cn/gh/{icon}/{icon}/0"
    return ""


# ============================================================
# 配置
# ============================================================
def load_config():
    if not CONFIG.exists():
        # 首次运行：基于示例生成空配置（账号清空），保证 python main.py --login 开箱即用
        if not CONFIG_EXAMPLE.exists():
            print(f"[!] 未找到 {CONFIG}")
            sys.exit(1)
        cfg = json.loads(CONFIG_EXAMPLE.read_text(encoding="utf-8"))
        cfg["accounts"] = []
        save_config(cfg)
        print(f"[i] 首次运行，已生成 {CONFIG.name}（账号为空，请先执行 python main.py --login 扫码绑定）")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg.setdefault("api_base_url", "https://delta-test-api.shallow.ink")
    cfg.setdefault("auth_mode", "anonymous")
    cfg.setdefault("api_key", "")
    cfg.setdefault("poll_interval_minutes", 30)
    cfg.setdefault("notify_before_hours", [24, 6, 1])
    cfg.setdefault("accounts", [])
    return cfg


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# API 客户端（新版）
# ============================================================
class DeltaAPI:
    def __init__(self, base_url, cfg):
        self.base_url = base_url.rstrip("/")
        self.cfg = cfg
        self.session = requests.Session()
        self._ensure_auth()

    def _headers(self, extra=None):
        h = {}
        mode = self.cfg.get("auth_mode", "anonymous")
        if mode == "api_key":
            h["X-API-Key"] = self.cfg.get("api_key", "")
        elif mode == "bearer":
            h["Authorization"] = f"Bearer {self.cfg.get('api_key', '')}"
        else:
            tok = self.cfg.get("anonymous_token", "")
            if tok:
                h["X-Anonymous-Token"] = tok
        if extra:
            h.update(extra)
        return h

    def _ensure_auth(self):
        """确保有可用认证凭证（匿名 token 可自动获取）"""
        if self.cfg.get("auth_mode") in ("api_key", "bearer"):
            return
        if self.cfg.get("anonymous_token"):
            return
        fp = self.cfg.get("fingerprint") or str(uuid.uuid4())
        try:
            r = self.session.post(
                f"{self.base_url}/api/v1/auth/anonymous-token",
                json={"fingerprint": fp},
                timeout=15,
            )
            d = r.json()
            if d.get("code") == 0:
                self.cfg["anonymous_token"] = d["data"]["token"]
                self.cfg["fingerprint"] = fp
            else:
                print(f"[!] 匿名Token获取失败: {d.get('message')}")
        except Exception as e:
            print(f"[!] 匿名Token获取异常: {e}")

    # ---- QQSafe 扫码 ----
    def qqsafe_qr(self):
        """获取 QQ安全中心扫码二维码，返回 (frameworkToken, base64图片)"""
        r = self.session.get(f"{self.base_url}/api/v1/login/qqsafe/qr",
                             headers=self._headers(), timeout=15)
        d = r.json()
        if d.get("code") != 0:
            raise Exception(d.get("message") or f"QR获取失败 code={d.get('code')}")
        data = d["data"]
        return data["frameworkToken"], data["qr_image"]

    def qqsafe_status(self, framework_token):
        """轮询扫码登录状态（新版返回嵌套：外层code=0表示请求成功，
        真正状态在内层 data.code：1=等待扫码 2/3=已扫待确认 0=扫码成功）"""
        r = self.session.get(f"{self.base_url}/api/v1/login/qqsafe/status",
                             headers=self._headers({"X-Framework-Token": framework_token}),
                             timeout=15)
        return r.json()

    # ---- WeGame 扫码 ----
    def wegame_qr(self):
        """获取 WeGame 扫码二维码，返回 (frameworkToken, base64图片)"""
        r = self.session.get(f"{self.base_url}/api/v1/login/wegame/qr",
                             headers=self._headers(), timeout=15)
        d = r.json()
        if d.get("code") != 0:
            raise Exception(d.get("message") or f"WeGame QR获取失败 code={d.get('code')}")
        data = d["data"]
        return data["frameworkToken"], data["qr_image"]

    def wegame_status(self, framework_token):
        """轮询 WeGame 扫码登录状态，返回原始 JSON"""
        r = self.session.get(f"{self.base_url}/api/v1/login/wegame/status",
                            headers=self._headers({"X-Framework-Token": framework_token}),
                            timeout=15)
        return r.json()

    def wegame_role(self, framework_token):
        """获取 WeGame 三角洲角色信息（角色名/等级/资产），返回 data dict"""
        try:
            r = self.session.get(f"{self.base_url}/api/v1/df/wegame/role",
                                 headers=self._headers({"X-Framework-Token": framework_token}),
                                 timeout=15)
            d = r.json()
        except Exception as e:
            return {"error": f"网络/解析失败: {e}"}
        if d.get("code") != 0:
            return {"error": d.get("message") or f"code={d.get('code')}"}
        data = d.get("data")
        return data if isinstance(data, dict) else {}

    def ban_history(self, framework_token):
        """查询环数与惩罚记录（每次独立 Session，避免多线程共享 Session 卡死）"""
        try:
            # 独立 session：requests.Session 非线程安全，多线程 poll 时复用会互相阻塞
            _s = requests.Session()
            r = _s.get(f"{self.base_url}/api/v1/df/qqsafe/ban",
                       headers=self._headers({"X-Framework-Token": framework_token}),
                       timeout=15)
            d = r.json()
            _s.close()
        except Exception as e:
            return {"error": f"网络/解析失败: {e}"}
        if d.get("code") != 0:
            return {"error": d.get("message") or f"code={d.get('code')}"}
        raw = d.get("data")
        if isinstance(raw, dict):
            raw = raw.get("list") or raw.get("data") or []
        elif raw is None:
            raw = []
        return {"records": raw if isinstance(raw, list) else []}


# ============================================================
# 状态计算（与旧版相同，兼容字段）
# ============================================================
def fmt_duration(sec):
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, m = divmod(rem, 3600)
    m //= 60
    if d > 0:
        return f"{d}天{h}小时{m}分"
    if h > 0:
        return f"{h}小时{m}分"
    return f"{m}分钟"


def analyze_record(rec, now_ts):
    """解析单条封禁记录；支持 start_stmp+duration 或 startTime+durationSeconds 等变体"""
    # 兼容多种字段名
    start = (rec.get("start_stmp") or rec.get("start_time") or
             rec.get("punishAt") or rec.get("startTime") or 0)
    duration = rec.get("duration") or 0
    if not duration and rec.get("end_stmp"):
        duration = int(rec["end_stmp"]) - int(start)
    if not duration and rec.get("end_time") and start:
        try:
            et = datetime.fromisoformat(str(rec["end_time"]).replace("Z", "+00:00")).timestamp()
            duration = int(et - int(start))
        except Exception:
            pass
    try:
        start = int(start)
        duration = int(duration)
    except Exception:
        start, duration = 0, 0

    reason = (rec.get("reason") or rec.get("type") or
              rec.get("punishReason") or rec.get("detail") or "未知")
    # ★ 游戏名：方便知道是哪个游戏被封（API 返回 game_name / game_id）
    game = rec.get("game_name") or rec.get("game") or ""
    game_id = rec.get("game_id") or rec.get("gameId")
    if game and game_id:
        game = f"{game}({game_id})"
    if not game and game_id:
        game = f"游戏ID {game_id}"
    if duration >= PERMANENT_SEC:
        return {"kind": "permanent", "reason": reason, "text": "永久", "game": game,
                "end": None, "end_str": "—", "remaining": None}
    end = start + duration
    remaining = end - now_ts
    if remaining <= 0:
        return None
    return {
        "kind": "temp", "reason": reason,
        "text": fmt_duration(remaining), "remaining": remaining, "game": game,
        "end": end, "end_str": datetime.fromtimestamp(end).strftime("%m-%d %H:%M"),
    }


def summarize(name, result, now):
    if "error" in result:
        return {"name": name, "status": "error", "msg": result["error"], "bans": []}
    bans = [b for b in (analyze_record(r, now) for r in result["records"]) if b]
    if any(b["kind"] == "permanent" for b in bans):
        return {"name": name, "status": "permanent", "bans": bans}
    if bans:
        near = min(bans, key=lambda b: b["remaining"])
        return {"name": name, "status": "banned", "bans": bans, "nearest": near}
    return {"name": name, "status": "clean", "bans": []}


# ============================================================
# 冷号模式（冷却倒计时）
# ============================================================
DEFAULT_COOLDOWN_HOURS = 24  # 默认冷却 24 小时


def apply_cooldown(acc, result, now_ts):
    """根据账号配置的 cooldown 字段，把冷却状态合并进 result。
    cooldown 结构: {"start": unix秒, "duration_h": 小时数}
    返回: (result, 是否还在冷却中)
    """
    cd = acc.get("cooldown") or {}
    start = cd.get("start")
    try:
        start = int(start)
    except Exception:
        return result, False
    if not start:
        return result, False
    dur = int(cd.get("duration_h") or DEFAULT_COOLDOWN_HOURS)
    end = start + dur * 3600
    remaining = end - now_ts
    if remaining <= 0:
        # 冷却已结束，自动清除
        acc.pop("cooldown", None)
        return result, False
    total = dur * 3600
    pct = max(0, min(100, int((total - remaining) / total * 100)))
    cooldown = {
        "kind": "cooldown",
        "remaining": remaining,
        "text": fmt_duration(remaining),
        "end": end,
        "end_str": datetime.fromtimestamp(end).strftime("%m-%d %H:%M"),
        "pct": pct,
    }
    return {**result, "status": "cooldown", "cooldown": cooldown}, True


# ============================================================
# 追踪器
# ============================================================
class Tracker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.api = DeltaAPI(cfg["api_base_url"], cfg)
        self.accounts = cfg["accounts"]
        self.interval_min = cfg.get("poll_interval_minutes", 30)
        self.notify_hours = sorted(cfg.get("notify_before_hours", [24, 6, 1]), reverse=True)
        self.lock = threading.Lock()
        self.results = {}
        self.last_scan = None
        self._prev = {}
        self._stop = threading.Event()

    def poll(self):
        now = int(time.time())
        results = {}
        seen = {}
        for acc in self.accounts:
            name = acc.get("name") or acc.get("framework_token", "?")[:10]
            token = acc.get("framework_token", "")
            # 同名账号用 token 前 8 位做后缀区分，避免 key 覆盖
            disp = name
            if seen.get(name):
                disp = f"{name} #{token[:8]}"
            else:
                seen[name] = token
            if not token:
                results[disp] = summarize(disp, {"error": "未配置 framework_token"}, now)
            else:
                results[disp] = summarize(disp, self.api.ban_history(token), now)
            # 冷号模式：若账号配置了冷却，无论有无封禁都叠加冷却状态
            results[disp], _ = apply_cooldown(acc, results[disp], now)
            # 记录 uid，方便精确删除/操作（同名时前端也能区分）
            results[disp]["uid"] = token
        with self.lock:
            self.results = results
            self.last_scan = now
        self._notify(results)
        return results

    def poll_async(self):
        """异步轮询，不阻塞调用线程（HTTP handler 用）"""
        def _w():
            try:
                self.poll()
            except Exception as e:
                print(f"[{now_str()}] 异步轮询异常: {e}")
        threading.Thread(target=_w, daemon=True).start()

    def _notify(self, results):
        prev, self._prev = self._prev, results
        if not prev:
            return
        for name, r in results.items():
            p = prev.get(name)
            if not p:
                continue
            if r["status"] in ("banned", "permanent") and p["status"] in ("clean", "error"):
                b = r["bans"][0]
                print(f"[{now_str()}] ★ 提醒: {name} 新封禁 | {b['reason']} | {b['text'] or '永久'}")
            if r["status"] == "clean" and p["status"] in ("banned", "permanent"):
                print(f"[{now_str()}] ✅ {name} 已解封")
            if r["status"] == "banned":
                near = r["nearest"]
                prev_near = p["nearest"]["remaining"] if p.get("status") == "banned" and p.get("nearest") else 10**12
                for h in self.notify_hours:
                    if near["remaining"] <= h * 3600 < prev_near:
                        print(f"[{now_str()}] 📢 {name} 即将解封(约{h}小时): 剩余 {near['text']}")

    def background_loop(self):
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as e:
                print(f"[{now_str()}] 轮询异常: {e}")
            self._stop.wait(self.interval_min * 60)

    def start_background(self):
        t = threading.Thread(target=self.background_loop, daemon=True)
        t.start()
        return t


# ============================================================
# 扫码绑定账号（新版 QQSafe）
# ============================================================
def run_login(cfg):
    """内置 QQ安全中心 扫码绑定流程"""
    api = DeltaAPI(cfg["api_base_url"], cfg)

    name = input("  给这个账号起个名字（如 大号/A号）: ").strip() or f"账号{len(cfg['accounts'])+1}"

    print("\n[i] 正在向 QQ安全中心请求登录二维码...")
    try:
        framework_token, qr_b64 = api.qqsafe_qr()
    except Exception as e:
        print(f"[!] 获取二维码失败: {e}")
        return

    # 保存二维码为本地文件 + 支持终端显示
    qr_file = BASE_DIR / "login_qr.png"
    try:
        img = base64.b64decode(qr_b64.split(",", 1)[-1])
        qr_file.write_bytes(img)
        print(f"[i] 二维码已保存: {qr_file}")
        print("=" * 50)
        print("  请用【QQ安全中心/三角洲 扫码】完成登录")
        print("  扫码后本程序会自动继续。")
        print("=" * 50)
        # 尝试在 Windows 打开图片
        if sys.platform == "win32":
            try:
                os.startfile(str(qr_file))
            except Exception:
                pass
    except Exception:
        print("[i] 二维码图片解析失败，仅显示 frameworkToken")

    # 轮询状态 (最长3分钟)
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            st = api.qqsafe_status(framework_token)
            outer = st.get("code")
            if outer != 0:
                print(f"[i] 请求异常: {st.get('message') or outer}")
                time.sleep(3)
                continue
            inner = st.get("data") or {}
            inner_code = inner.get("code")
            if inner_code == 0:
                # 登录成功，凭证可能是刷新的 framework_token
                real_token = (inner.get("framework_token") or inner.get("frameworkToken")
                              or inner.get("token") or framework_token)
                cfg["accounts"].append({
                    "name": name,
                    "framework_token": real_token,
                    "note": f"绑定于 {now_str()}",
                })
                save_config(cfg)
                print(f"\n✅ {name} 绑定成功！frameworkToken 已保存到 accounts.json")
                return
            elif inner_code in (2, 3):
                print(f"[i] {inner.get('msg') or inner.get('message') or '已扫码，等待确认'}")
            elif inner_code == 1:
                print(f"[i] 等待扫码: {inner.get('msg') or inner.get('message') or ''}")
            else:
                print(f"[i] 未知状态码: {inner_code} {inner}")
        except Exception:
            pass
        time.sleep(3)

    print("[!] 扫码超时（3分钟），请重试")


# ============================================================
# 终端展示
# ============================================================
def render_terminal(results):
    line = "=" * 60
    print("\n" + line)
    print(f"  🗓 三角洲封禁倒计时    刷新: {now_str()}")
    print(line)
    if not results:
        print("  (暂无账号：请先执行 python main.py --login 扫码绑定)")
        print(line)
        return
    for r in results.values():
        if r["status"] == "error":
            print(f"  ⚠ {r['name']}: 查询失败 - {r['msg']}")
        elif r["status"] == "clean":
            print(f"  ✅ {r['name']}: 无封禁记录")
        elif r["status"] == "cooldown":
            c = r["cooldown"]
            print(f"  🧊 {r['name']}: 冷号中 剩余 {c['text']} | 解冻 {c['end_str']} (进度{c['pct']}%)")
        elif r["status"] == "permanent":
            for b in r["bans"]:
                print(f"  ❌ {r['name']}: 永久封禁 | {b['reason']}")
        else:
            n = r["nearest"]
            flag = "🔴" if n["remaining"] <= 6 * 3600 else "🟠"
            extra = f" (+{len(r['bans'])-1}条)" if len(r["bans"]) > 1 else ""
            print(f"  {flag} {r['name']}: 剩余 {n['text']} | 解封 {n['end_str']} | {n['reason']}{extra}")
    print(line)


def run_terminal(cfg, once=False):
    tracker = Tracker(cfg)
    if once:
        tracker.poll()
        render_terminal(tracker.results)
        return
    tracker.start_background()
    print(f"[i] 后台每 {tracker.interval_min} 分钟轮询一次，Ctrl+C 退出。")
    try:
        while True:
            with tracker.lock:
                results = dict(tracker.results)
            render_terminal(results)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n退出。")


# ============================================================
# WEB 面板
# ============================================================
WEB_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>三角洲封号倒计时</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#0f1115;--card:#171a21;--text:#e6e9f0;--muted:#8b93a7;
--red:#ff4757;--orange:#ffa502;--green:#2ed573;--border:#2a2f3d}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:"Segoe UI","Microsoft YaHei",sans-serif;padding:24px}
h1{font-size:22px;margin-bottom:4px}
.top{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.sub{color:var(--muted);font-size:13px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px}
.name{font-size:16px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.dot.ok{background:var(--green)}.dot.ban{background:var(--red)}
.dot.perm{background:var(--red);box-shadow:0 0 8px var(--red)}.dot.err{background:var(--orange)}
.dot.cd{background:#20c997;box-shadow:0 0 8px #20c997}
.countdown{font-size:27px;font-weight:700;margin:4px 0 2px;font-variant-numeric:tabular-nums}
.countdown.red{color:var(--red)}.countdown.orange{color:var(--orange)}
.meta{font-size:12px;color:var(--muted);margin-top:4px}
.footer{margin-top:24px;color:var(--muted);font-size:12px}
.tag{display:inline-block;background:#252a3a;border:1px solid var(--border);color:var(--muted);
font-size:11px;padding:2px 8px;border-radius:20px;margin-left:auto}
.progress{width:100%;height:6px;background:#1d2230;border-radius:4px;margin-top:10px;overflow:hidden}
.progress>i{display:block;height:100%;background:linear-gradient(90deg,var(--orange),var(--red));border-radius:4px;transition:width .5s}
.btn{background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:8px 16px;
font-size:13px;cursor:pointer;font-family:inherit}
.btn:hover{background:#2f6fed}.btn:disabled{opacity:.5;cursor:not-allowed}
.btn.ghost{background:transparent;border:1px solid var(--border);color:var(--muted)}
.btn-sm{padding:5px 12px;font-size:12px;border-radius:6px}
.btn-warn{background:#28a745}
.btn-warn:hover{background:#218838}
.btn-del{background:transparent;border:1px solid var(--border);color:#e05252;border-radius:6px;
width:26px;height:26px;font-size:13px;cursor:pointer;line-height:1;display:inline-flex;
align-items:center;justify-content:center;margin-left:4px;flex-shrink:0}
.btn-del:hover{background:#e05252;color:#fff;border-color:#e05252}
.btn-edit{background:transparent;border:1px solid var(--border);color:var(--muted);border-radius:6px;
width:26px;height:26px;font-size:12px;cursor:pointer;line-height:1;display:inline-flex;
align-items:center;justify-content:center;margin-left:4px;flex-shrink:0}
.btn-edit:hover{background:#3b82f6;color:#fff;border-color:#3b82f6}
.btn{background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:8px 16px;
font-size:13px;cursor:pointer;font-family:inherit}
.btn:hover{background:#2f6fed}.btn:disabled{opacity:.5;cursor:not-allowed}
.btn.ghost{background:transparent;border:1px solid var(--border);color:var(--muted)}
.quick{display:flex;gap:6px;margin-top:8px}
.chip{flex:1;background:#10131c;border:1px solid var(--border);color:var(--muted);border-radius:8px;
padding:7px 0;font-size:12px;cursor:pointer;font-family:inherit;transition:all .15s}
.chip:hover{border-color:#3b82f6;color:var(--text)}
.chip.active{background:#3b82f6;border-color:#3b82f6;color:#fff}
input{background:#10131c;border:1px solid var(--border);color:var(--text);border-radius:8px;
padding:9px 12px;font-size:14px;width:100%;box-sizing:border-box;outline:none}
input:focus{border-color:#3b82f6}
#modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:10;align-items:center;justify-content:center}
#cdModal,#editModal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:11;align-items:center;justify-content:center}
#modalBox,#cdModalBox,#editModalBox{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:22px;width:340px;max-width:92vw}
#modalBox h3,#cdModalBox h3,#editModalBox h3{margin:0 0 6px;font-size:16px}
#qrBox{display:none;margin-top:14px;text-align:center}
#qrImg{width:170px;height:170px;background:#fff;border-radius:10px;padding:8px}
#qrMsg{color:var(--muted);font-size:12px;margin-top:10px;min-height:16px}
.row{display:flex;gap:8px;margin-top:12px}
.tabs{display:flex;gap:6px;margin-bottom:10px}
.tab{flex:1;background:#10131c;border:1px solid var(--border);color:var(--muted);
border-radius:8px;padding:8px 0;font-size:13px;cursor:pointer;text-align:center;font-family:inherit;transition:all .15s}
.tab:hover{border-color:#3b82f6;color:var(--text)}
.tab.active{background:#3b82f6;border-color:#3b82f6;color:#fff}
.role-name{color:#20c997;font-size:12px;margin-top:2px}
.ava{width:38px;height:38px;border-radius:50%;object-fit:cover;border:2px solid var(--border);margin-bottom:6px}
</style>
</head>
<body>
<div class="top">
  <h1>📋 三角洲封号倒计时</h1>
  <button class="btn" id="btnBind">➕ 扫码绑定账号</button>
</div>
<div class="sub" id="sub">加载中...</div>
<div class="grid" id="grid"></div>
<div class="footer">页面每 15 秒自动刷新 · 后台每 {POLL_MIN} 分钟轮询一次 · 新版 Delta-Union-API</div>

<div id="modal">
  <div id="modalBox">
    <h3>扫码绑定账号</h3>
    <div class="sub" style="margin-bottom:0">输入账号名字（可自定义，支持中文）</div>
    <div class="tabs" id="qrTabs">
      <button class="tab active" data-tab="qqsafe">QQ安全中心</button>
      <button class="tab" data-tab="wegame">WeGame</button>
    </div>
    <input id="inpName" placeholder="如：大号 / 小号 / 测试号" maxlength="20">
    <div class="row">
      <button class="btn ghost" id="btnClose">取消</button>
      <button class="btn" id="btnQr">获取二维码</button>
    </div>
    <div id="qrBox">
      <img id="qrImg" alt="二维码">
      <div id="qrMsg"></div>
    </div>
  </div>
</div>

<div id="cdModal">
  <div id="cdModalBox">
    <h3>🧊 <span id="cdModalTitle">设置冷号冷却</span></h3>
    <div class="sub" style="margin-bottom:0">为账号设置冷却时间，期间显示倒计时不可用</div>
    <div style="margin-top:10px">
      <div class="meta" style="margin-bottom:4px">冷却时长</div>
      <input id="cdHours" type="number" value="24" min="1" max="720" style="width:100%">
      <div class="quick">
        <button class="chip" data-h="24">1天</button>
        <button class="chip" data-h="72">3天</button>
        <button class="chip" data-h="168">7天</button>
      </div>
    </div>
    <div style="margin-top:10px">
      <div class="meta" style="margin-bottom:4px">开始时间（默认当前，可改过去）</div>
      <input id="cdStart" type="datetime-local" style="width:100%">
    </div>
    <div class="row">
      <button class="btn ghost" id="cdClose">取消</button>
      <button class="btn" id="cdOk">开始冷却</button>
    </div>
  </div>
</div>

<div id="editModal">
  <div id="editModalBox">
    <h3>✏️ 编辑账号</h3>
    <div class="meta" style="margin-bottom:4px">账号名称（卡片上显示的名字）</div>
    <input id="editName" maxlength="20" placeholder="如：大号 / 小号 / 测试号">
    <div class="meta" style="margin:10px 0 4px">备注（可选，显示在卡片上，清空保存即删除备注）</div>
    <input id="editNote" maxlength="60" placeholder="如：朋友的小号 / 主力号">
    <div class="row">
      <button class="btn ghost" id="editClose">取消</button>
      <button class="btn" id="editOk">保存</button>
    </div>
  </div>
</div>

<script>
const stateMap={clean:['ok','正常'],banned:['ban','封禁中'],permanent:['perm','永久'],cooldown:['cd','冷号中'],error:['err','失败']};
const $=id=>document.getElementById(id);
function esc(s){const d=document.createElement('div');d.textContent=(s||'');return d.innerHTML}
function escAttr(s){return esc(s).replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
let accountCount=0, curToken=null, pollTimer=null, modalClosing=false, cdTarget=null, editUid=null;

function localToTs(v){
  if(!v) return '';
  const dt=new Date(v);
  return Math.floor(dt.getTime()/1000);
}
function tsInputVal(ts){
  const d=new Date(ts*1000);
  const p=n=>String(n).padStart(2,'0');
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+'T'+p(d.getHours())+':'+p(d.getMinutes());
}
function openCdModal(name){
  cdTarget=name;
  $('cdModalTitle')&&($('cdModalTitle').textContent=name);
  $('cdHours').value=24;
  $('cdStart').value=tsInputVal(Math.floor(Date.now()/1000)); // 默认当前时间
  // 快捷按钮高亮：默认选中 1天
  document.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active', b.dataset.h==='24'));
  $('cdModal').style.display='flex';
}
// 快捷时长按钮：点击填入对应小时数
document.querySelectorAll('.chip').forEach(btn=>{
  btn.onclick=()=>{
    $('cdHours').value=parseInt(btn.dataset.h);
    document.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active', b===btn));
  };
});
// 手动改小时数时取消快捷高亮
$('cdHours').addEventListener('input',()=>{
  document.querySelectorAll('.chip').forEach(b=>b.classList.remove('active'));
});
$('cdClose').onclick=()=>{ cdTarget=null; $('cdModal').style.display='none'; };
$('cdModal').addEventListener('click',e=>{ if(e.target===$('cdModal')){ cdTarget=null; $('cdModal').style.display='none'; } });
// ===== 编辑账号（改名 / 备注）=====
function openEditModal(uid, name, note){
  editUid=uid;
  $('editName').value=name||'';
  $('editNote').value=note||'';
  $('editModal').style.display='flex';
  setTimeout(()=>{ $('editName').focus(); },50);
}
$('editClose').onclick=()=>{ editUid=null; $('editModal').style.display='none'; };
$('editModal').addEventListener('click',e=>{ if(e.target===$('editModal')){ editUid=null; $('editModal').style.display='none'; } });
$('editOk').onclick=async()=>{
  if(!editUid) return;
  const newName=$('editName').value.trim();
  if(!newName){ alert('账号名称不能为空'); return; }
  const newNote=$('editNote').value.trim();
  $('editOk').disabled=true;
  try{
    const r=await (await fetch('/api/account/edit?uid='+encodeURIComponent(editUid)
      +'&name='+encodeURIComponent(newName)
      +'&note='+encodeURIComponent(newNote))).json();
    if(r.code===0){ $('editModal').style.display='none'; editUid=null; load(); }
    else{ alert('保存失败: '+(r.msg||'')); }
  }catch(e){ alert('请求失败: '+e.message); }
  finally{ $('editOk').disabled=false; }
};
$('cdOk').onclick=async()=>{
  if(!cdTarget) return;
  const hours=parseInt($('cdHours').value)||24;
  const start=$('cdStart').value;
  const startTs=localToTs(start);
  let url='/api/cooldown?name='+encodeURIComponent(cdTarget)+'&hours='+hours;
  if(startTs) url+='&start='+startTs;
  await fetch(url);
  $('cdModal').style.display='none';
  cdTarget=null;
  load();
};

function startCooldown(nameEnc){
  openCdModal(decodeURIComponent(nameEnc));
}
// 删除账号
async function delAccount(uid, name){
  if(!confirm('确定删除账号「'+name+'」？ 删除后需重新扫码绑定。')) return;
  const r=await (await fetch('/api/account/delete?uid='+encodeURIComponent(uid))).json();
  if(r.code===0){ $('sub').textContent='✅ '+r.msg; load(); }
  else{ alert('删除失败: '+(r.msg||'')); }
}
async function clearCd(nameEnc){
  const name=decodeURIComponent(nameEnc);
  if(!confirm('提前结束「'+name+'」的冷却？')) return;
  await fetch('/api/cooldown?name='+encodeURIComponent(name));
  load();
}

async function load(){
  try{
    const data=await (await fetch('/api/status')).json();
    accountCount=data.count;
    $('sub').textContent='最后轮询: '+(data.last_scan||'—')+' · 共 '+data.count+' 个账号 · 自动刷新中';
    const grid=$('grid'); grid.innerHTML='';
    if(data.count===0){ grid.innerHTML='<div class="card"><div class="meta">还没有绑定账号，点击右上角 "扫码绑定账号" 添加</div></div>'; }
    for(const [name,r] of Object.entries(data.results)){
      const [dot,label]=stateMap[r.status]||stateMap.error;
      let body,dotCls=dot;
      if(r.status==='clean'){
        body='<div class="countdown" style="color:var(--green)">✅ 正常</div><div class="meta">无封禁记录</div>'
          +'<div style="margin-top:8px"><button class="btn btn-sm" onclick="startCooldown(&quot;'+encodeURIComponent(name)+'&quot;)">🧊 设为冷号</button></div>';
      }else if(r.status==='error'){
        body='<div class="countdown" style="font-size:15px">⚠ 查询失败</div><div class="meta">'+esc(r.msg||'')+'</div>';
      }else if(r.status==='permanent'){
        body='<div class="countdown red">♾ 永久封禁</div>'
          +'<div class="meta">'+esc((r.bans[0]&&r.bans[0].game)?('🎮 '+r.bans[0].game+' · '):'')+esc(r.bans.map(b=>b.reason).join(' / '))+'</div>';
      }else if(r.status==='cooldown'){
        const c=r.cooldown;
        body='<div class="countdown" style="color:#20c997;font-size:24px">🧊 '+esc(c.text)+'</div>'
          +'<div class="meta">冷号中 · 预计 '+esc(c.end_str)+' 解冻</div>'
          +'<div class="progress" style="background:#1d2230"><i style="width:'+c.pct+'%;background:linear-gradient(90deg,#20c997,var(--green))"></i></div>'
          +'<div style="margin-top:8px"><button class="btn btn-sm btn-warn" onclick="clearCd(&quot;'+encodeURIComponent(name)+'&quot;)">✅ 提前解冻</button></div>';
      }else{
        const n=r.nearest;
        body='<div class="countdown '+(n.remaining<=21600?'red':'orange')+'">'+esc(n.text)+'</div>'
          +'<div class="meta">'+esc(n.game?('🎮 '+n.game+' · '):'')+'预计解封 '+esc(n.end_str)+' · '+esc(n.reason)+'</div>'
          +(r.bans.length>1?'<div class="meta">另有 '+(r.bans.length-1)+' 条处罚</div>':'')
          +'<div class="progress"><i style="width:'+(n.remaining<=21600?90:70)+'%"></i></div>'
          +'<div style="margin-top:8px"><button class="btn btn-sm" onclick="startCooldown(&quot;'+encodeURIComponent(name)+'&quot;)">🧊 设为冷号</button></div>';
      }
      const card=document.createElement('div'); card.className='card';
      const nm=escAttr(r.name||name);
      card.innerHTML='<div class="name"><span class="dot '+dotCls+'"></span>'+esc(r.name||name)+'<span class="tag">'+label+'</span>'
        +(r.uid?'<button class="btn-edit" title="改名 / 备注" onclick="openEditModal(&quot;'+escAttr(r.uid)+'&quot;,&quot;'+nm+'&quot;,&quot;'+escAttr(r.note||'')+'&quot;)">✏️</button>'
              +'<button class="btn-del" title="删除账号" onclick="delAccount(&quot;'+escAttr(r.uid)+'&quot;,&quot;'+nm+'&quot;)">🗑</button>':'')
        +'</div>'
        +(r.avatar?'<img class="ava" src="'+esc(r.avatar)+'" alt="" onerror="this.remove()">':'')
        +(r.role_name?'<div class="role-name">🎮 '+esc(r.role_name)+(r.platform==='wegame'?' · WeGame':'')+'</div>':'')
        +(r.note?'<div class="meta">📝 '+esc(r.note)+'</div>':'')
        +body;
      grid.appendChild(card);
    }
  }catch(e){ $('sub').textContent='加载失败: '+e.message; }
}
load(); setInterval(load,15000);

// ===== 扫码绑定 =====
$('btnBind').onclick=()=>{
  modalClosing=false; $('qrBox').style.display='none'; $('qrMsg').textContent='';
  $('inpName').value='账号'+(accountCount+1);
  $('modal').style.display='flex';
};
function closeModal(){
  modalClosing=true;
  if(pollTimer){ clearInterval(pollTimer); pollTimer=null; }
  $('modal').style.display='none';
}
$('btnClose')&&($('btnClose').onclick=closeModal);
$('modal').addEventListener('click',e=>{ if(e.target===$('modal')&&!modalClosing) closeModal(); });

$('btnQr').onclick=async()=>{
  // ★ 关键修复：点击时先清掉旧轮询，否则旧 interval 会把新二维码的状态覆盖掉
  if(pollTimer){ clearInterval(pollTimer); pollTimer=null; }
  const name=$('inpName').value.trim()||('账号'+(accountCount+1));
  const platform=document.querySelector('#qrTabs .tab.active')?.dataset.tab||'qqsafe';
  $('btnQr').disabled=true; $('qrBox').style.display='block'; $('qrMsg').textContent='正在获取二维码…';
  try{
    const r=await (await fetch('/api/login/qrcode?name='+encodeURIComponent(name)+'&type='+platform)).json();
    if(r.code!==0){ $('qrMsg').textContent='获取失败: '+(r.msg||''); return; }
    $('qrImg').src=r.qr; curToken=r.token;
    $('qrMsg').textContent=(platform==='wegame'?'请用【WeGame】扫码':'请用【QQ安全中心 / 三角洲行动】扫码')+'（3分钟内有效）';
    // 闭包捕获 sid：即使旧 interval 残留，也只轮询自己的 token，不读全局 curToken
    const sid=r.token;
    pollTimer=setInterval(async()=>{
      try{
        const s=await (await fetch('/api/login/status?token='+sid)).json();
        if(s.msg&&!s.done) $('qrMsg').textContent=s.msg;
        if(s.done){
          clearInterval(pollTimer); pollTimer=null;
          $('qrMsg').innerHTML='<b style="color:var(--green)">✅ '+esc(s.name)+' 绑定成功！</b>'
            +(s.avatar?'<div style="margin-top:6px"><img src="'+esc(s.avatar)+'" style="width:36px;height:36px;border-radius:50%;object-fit:cover;border:2px solid var(--green)"></div>':'')
            +(s.role_name?'<div style="color:#20c997;font-size:12px;margin-top:4px">🎮 角色: '+esc(s.role_name)+'</div>':'');
          setTimeout(()=>{ closeModal(); load(); },2000);
        }else if(s.expired){
          clearInterval(pollTimer); pollTimer=null;
          $('qrMsg').textContent='⚠ 二维码已过期，请重新获取';
        }
      }catch(e){ $('qrMsg').textContent='轮询失败: '+e.message; }
    },3000);
  }catch(e){
    $('qrMsg').textContent='请求失败: '+e.message;
  }finally{ $('btnQr').disabled=false; }
};
// 平台 Tab 切换：切到别平台时清掉旧轮询
$('qrTabs').addEventListener('click',e=>{
  const btn=e.target.closest('.tab'); if(!btn) return;
  if(btn.classList.contains('active')) return;
  if(pollTimer){ clearInterval(pollTimer); pollTimer=null; }
  document.querySelectorAll('#qrTabs .tab').forEach(t=>t.classList.toggle('active', t===btn));
  $('qrBox').style.display='none'; $('qrMsg').textContent='';
});
</script>
</body>
</html>"""


def run_web(cfg, port=8808):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    tracker = Tracker(cfg)
    # 启动即后台异步轮询，不阻塞 serve（首次数据几秒内就绪）
    tracker.poll_async()
    tracker.start_background()

    def snap():
        # 锁有界等待：poll 在跑时最多等 2 秒，拿不到就返回当前缓存结果（避免前端一直转圈）
        if not tracker.lock.acquire(timeout=2):
            return {
                "last_scan": None,
                "count": 0,
                "results": {},
                "busy": True,
            }
        try:
            results = {}
            for k, r in tracker.results.items():
                r = dict(r)
                r["bans"] = [dict(b) for b in r.get("bans", [])]
                uid = r.get("uid", "")
                # 按 uid(即 framework_token) 精确匹配账号配置
                acc = next((a for a in cfg["accounts"] if a.get("framework_token") == uid), None) or {}
                r["role_name"] = acc.get("role_name", "")
                r["platform"] = acc.get("platform", "qqsafe")
                r["avatar"] = acc.get("avatar", "")
                r["note"] = acc.get("note", "")
                # 若前端给了后缀 #xxxx,还原纯名字用于显示
                base = k.split(" #")[0]
                if base != k:
                    r["name"] = base
                if r.get("nearest"):
                    r["nearest"] = {kk: vv for kk, vv in r["nearest"].items()
                                    if kk in ("text", "end_str", "reason", "remaining", "game")}
                results[k] = r
            last = tracker.last_scan
        finally:
            tracker.lock.release()
        return {
            "last_scan": datetime.fromtimestamp(last).strftime("%m-%d %H:%M:%S") if last else None,
            "count": len(results),
            "results": results,
        }

    class Handler(BaseHTTPRequestHandler):
        # 扫码绑定会话：token -> {name, framework_token, status}
        qr_sessions = {}

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            from urllib.parse import urlparse, parse_qs

            path = urlparse(self.path).path
            qs = parse_qs(urlparse(self.path).query)

            # ---- 展示页面 ----
            if path == "/":
                html = WEB_HTML.replace("{POLL_MIN}", str(cfg.get("poll_interval_minutes", 30)))
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            # ---- 状态 ----
            if path == "/api/status":
                self._json(snap())
                return

            # ---- 获取扫码二维码 ----
            if path == "/api/login/qrcode":
                name = (qs.get("name") or ["账号N"])[0].strip()
                platform = (qs.get("type") or ["qqsafe"])[0].strip().lower()
                try:
                    if platform == "wegame":
                        framework_token, qr_b64 = tracker.api.wegame_qr()
                    else:
                        framework_token, qr_b64 = tracker.api.qqsafe_qr()
                    qr_sid = uuid.uuid4().hex
                    Handler.qr_sessions[qr_sid] = {
                        "framework_token": framework_token,
                        "platform": platform,
                        "status": "pending",
                        "name": name,
                        "created": time.time(),
                    }
                    self._json({"code": 0, "token": qr_sid, "platform": platform,
                                "qr": "data:image/png;base64," + qr_b64.split(",", 1)[-1]})
                except Exception as e:
                    self._json({"code": 1, "msg": f"获取二维码失败: {e}"})
                return

            # ---- 轮询扫码状态 ----
            if path == "/api/login/status":
                sid = qs.get("token", [""])[0]
                sess = Handler.qr_sessions.get(sid)
                if not sess:
                    self._json({"done": False, "expired": True, "msg": "会话不存在/已过期"})
                    return
                # 会话超过 3 分钟清掉
                if time.time() - sess["created"] > 180:
                    Handler.qr_sessions.pop(sid, None)
                    self._json({"done": False, "expired": True, "msg": "二维码已过期"})
                    return
                try:
                    platform = sess.get("platform", "qqsafe")
                    if platform == "wegame":
                        st = tracker.api.wegame_status(sess["framework_token"])
                        outer = st.get("code")
                        if outer != 0:
                            msg = st.get("message") or f"外层code={outer}"
                            self._json({"done": False, "expired": False, "msg": msg})
                            return
                        inner = (st.get("data") or {})
                        inner_code = inner.get("code")
                        # WeGame 登录成功判定：data.code=0 且可能带 framework_token
                        if inner_code == 0:
                            real_token = (inner.get("framework_token") or inner.get("frameworkToken")
                                          or inner.get("token") or sess["framework_token"])
                            # 拉取 WeGame 角色名（data 嵌套：{data: {name, level, ...}}）
                            role = tracker.api.wegame_role(real_token)
                            role_name = ""
                            lv = ""
                            avatar = ""
                            note = f"绑定于 {now_str()}"
                            if isinstance(role, dict) and "error" not in role:
                                rdata = role.get("data") or {}
                                role_name = (rdata.get("name") or rdata.get("role_name")
                                             or rdata.get("nickname") or rdata.get("player_name") or "")
                                lv = rdata.get("level") or rdata.get("role_level") or ""
                                avatar = resolve_avatar(rdata.get("icon"))
                                note_parts = [f"绑定于 {now_str()}"]
                                if role_name:
                                    note_parts.append(f"角色 {role_name}")
                                if lv:
                                    note_parts.append(f"等级{lv}")
                                note = " · ".join(note_parts)
                            # ★ WeGame 绑定角色合并逻辑：
                            # 若同名账号已存在（QQSafe），把角色名并入原账号，不新增重复项；
                            # 否则新建 WeGame 独立账号（封禁查询会报错，提示需 QQSafe）
                            existed = next((a for a in cfg["accounts"]
                                            if a.get("name") == sess["name"]), None)
                            if existed:
                                if role_name:
                                    existed["role_name"] = role_name
                                    existed["note"] = note
                                if avatar:
                                    existed["avatar"] = avatar
                                # 保底：如果原账号没平台标记，补一个 wegame_token 引用
                                if not existed.get("wegame_token"):
                                    existed["wegame_token"] = real_token
                                save_config(cfg)
                                tracker.accounts = cfg["accounts"]
                                try:
                                    tracker.poll_async()
                                except Exception as e:
                                    print(f"[{now_str()}] 绑定后轮询异常(不影响绑定): {e}")
                                Handler.qr_sessions.pop(sid, None)
                                self._json({"done": True, "name": sess["name"],
                                            "role_name": role_name, "avatar": avatar, "merged": True})
                                return
                            acc_entry = {
                                "name": sess["name"],
                                "framework_token": real_token,
                                "platform": "wegame",
                                "note": note,
                            }
                            if role_name:
                                acc_entry["role_name"] = role_name
                            if avatar:
                                acc_entry["avatar"] = avatar
                            cfg["accounts"] = [a for a in cfg["accounts"]
                                               if a.get("framework_token") != real_token]
                            cfg["accounts"].append(acc_entry)
                            save_config(cfg)
                            tracker.accounts = cfg["accounts"]
                            try:
                                tracker.poll_async()
                            except Exception as e:
                                print(f"[{now_str()}] 绑定后轮询异常(不影响绑定): {e}")
                            Handler.qr_sessions.pop(sid, None)
                            self._json({"done": True, "name": sess["name"],
                                        "role_name": role_name, "avatar": avatar, "merged": False})
                            return
                        msg = inner.get("msg") or inner.get("message") or \
                            ("等待扫码..." if inner_code == 1 else f"状态码 {inner_code}")
                        self._json({"done": False, "expired": False, "msg": msg})
                        return
                    # ---- QQSafe 原有逻辑 ----
                    st = tracker.api.qqsafe_status(sess["framework_token"])
                    outer = st.get("code")
                    if outer != 0:
                        msg = st.get("message") or f"外层code={outer}"
                        self._json({"done": False, "expired": False, "msg": msg})
                        return
                    # 真正状态在内层 data
                    inner = (st.get("data") or {})
                    inner_code = inner.get("code")
                    if inner_code == 0:
                        real_token = (inner.get("framework_token") or inner.get("frameworkToken")
                                      or inner.get("token") or sess["framework_token"])
                        cfg["accounts"] = [a for a in cfg["accounts"]
                                           if a.get("framework_token") != real_token]
                        cfg["accounts"].append({
                            "name": sess["name"],
                            "framework_token": real_token,
                            "note": f"绑定于 {now_str()}",
                        })
                        save_config(cfg)
                        tracker.accounts = cfg["accounts"]
                        # ★ 修复：绑定成功后立即重新轮询，让新账号马上出现在网页面板
                        # 此前只更新了 accounts，tracker.results 没刷新，/api/status 的 count 仍是旧值
                        try:
                            tracker.poll_async()
                        except Exception as e:
                            print(f"[{now_str()}] 绑定后轮询异常(不影响绑定): {e}")
                        Handler.qr_sessions.pop(sid, None)
                        self._json({"done": True, "name": sess["name"]})
                        return
                    msg = inner.get("msg") or inner.get("message") or \
                        ("等待扫码..." if inner_code == 1 else f"状态码 {inner_code}")
                    self._json({"done": False, "expired": False, "msg": msg})
                except Exception as e:
                    self._json({"done": False, "expired": False, "msg": f"轮询异常: {e}"})
                return

            # ---- 设置/清除账号冷却(冷号模式) ----
            if path == "/api/cooldown":
                name = (qs.get("name") or [""])[0].strip()
                if not name:
                    self._json({"code": 1, "msg": "缺少 name 参数"})
                    return
                acc = next((a for a in cfg["accounts"] if a.get("name") == name), None)
                if not acc:
                    self._json({"code": 1, "msg": f"账号 {name} 不存在"})
                    return
                # 有 hours 参数且 >0 → 设置冷却；否则解除
                if qs.get("hours"):
                    try:
                        hours = max(1, int(qs["hours"][0]))
                    except Exception:
                        hours = DEFAULT_COOLDOWN_HOURS
                    # 起始时间：指定 start(秒) 则用，否则当前时间
                    try:
                        start = int(qs.get("start", [""])[0]) if qs.get("start") else int(time.time())
                    except Exception:
                        start = int(time.time())
                    acc["cooldown"] = {"start": start, "duration_h": hours}
                    from datetime import datetime as _dt
                    _t = _dt.fromtimestamp(start).strftime("%m-%d %H:%M")
                    msg = f"{name} 冷却已设置({hours}小时,从{_t}起)"
                else:
                    acc.pop("cooldown", None)
                    msg = f"{name} 冷却已解除"
                save_config(cfg)
                tracker.accounts = cfg["accounts"]
                tracker.poll_async()
                self._json({"code": 0, "msg": msg})
                return

            # ---- 编辑账号：改名 / 修改备注 ----
            if path == "/api/account/edit":
                uid = (qs.get("uid") or [""])[0].strip()
                new_name = (qs.get("name") or [""])[0].strip()
                new_note = (qs.get("note") or [""])[0].strip()
                if not uid:
                    self._json({"code": 1, "msg": "缺少 uid 参数"})
                    return
                acc = next((a for a in cfg["accounts"] if a.get("framework_token") == uid), None)
                if not acc:
                    self._json({"code": 1, "msg": "账号不存在"})
                    return
                parts = []
                if new_name and new_name != acc.get("name"):
                    acc["name"] = new_name
                    parts.append(f"名称→{new_name}")
                if new_note:
                    if new_note != acc.get("note", ""):
                        acc["note"] = new_note
                        parts.append("备注已更新")
                elif acc.pop("note", None) is not None:
                    parts.append("备注已清空")
                if not parts:
                    self._json({"code": 0, "msg": "没有改动"})
                    return
                save_config(cfg)
                tracker.accounts = cfg["accounts"]
                try:
                    tracker.poll_async()
                except Exception as e:
                    print(f"[{now_str()}] 编辑后轮询异常(不影响保存): {e}")
                self._json({"code": 0, "msg": "✅ " + "，".join(parts)})
                return

            # ---- 删除账号: 支持 uid(推荐) 或 name ----
            if path == "/api/account/delete":
                uid = (qs.get("uid") or [""])[0].strip()
                name = (qs.get("name") or [""])[0].strip()
                if not uid and not name:
                    self._json({"code": 1, "msg": "缺少 uid 或 name 参数"})
                    return
                before = len(cfg["accounts"])
                if uid:
                    cfg["accounts"] = [a for a in cfg["accounts"]
                                       if a.get("framework_token") != uid]
                    msg = f"已删除账号(uid={uid[:8]})"
                else:
                    cfg["accounts"] = [a for a in cfg["accounts"] if a.get("name") != name]
                    msg = f"已删除账号 {name}(同名可能全部删除)"
                if len(cfg["accounts"]) == before:
                    self._json({"code": 1, "msg": "账号不存在"})
                    return
                save_config(cfg)
                tracker.accounts = cfg["accounts"]
                try:
                    tracker.poll_async()
                except Exception as e:
                    print(f"[{now_str()}] 删除后轮询异常(不影响删除): {e}")
                self._json({"code": 0, "msg": msg})
                return

            self._json({"code": 404, "msg": "not found"}, 404)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[i] 网页面板: http://127.0.0.1:{port}   (Ctrl+C 退出)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n退出。")


# ============================================================
# 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="三角洲封号倒计时管理工具 (新版API)")
    parser.add_argument("--web", action="store_true", help="启动网页面板")
    parser.add_argument("--port", type=int, default=8808, help="网页面板端口 (默认8808)")
    parser.add_argument("--once", action="store_true", help="只查询一次并退出")
    parser.add_argument("--login", action="store_true", help="扫码绑定新账号")
    args = parser.parse_args()

    cfg = load_config()

    if args.login:
        run_login(cfg)
        return
    if args.web:
        run_web(cfg, args.port)
    else:
        run_terminal(cfg, once=args.once)


if __name__ == "__main__":
    main()