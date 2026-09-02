#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 定时拉取三角洲封禁数据 → status.json (给 Pages 静态页用)
用法: python fetch_status.py [api_key]
输出: web/status.json
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 固定北京时间（Actions 运行在 UTC，必须显式指定，否则解封时间会差 8 小时）
CN = timezone(timedelta(hours=8))
DEFAULT_COOLDOWN_HOURS = 24  # 冷号默认冷却时长
OUT = Path(__file__).resolve().parent.parent.parent / "web" / "status.json"
API_BASE = "https://delta-test-api.shallow.ink"

try:
    import requests
except ImportError:
    print("缺少 requests，正在安装...")
    os.system(f"{sys.executable} -m pip install -q requests")
    import requests  # noqa: E402


def analyze_record(rec, now_ts):
    """与 main.py 同逻辑：解析单条封禁记录返回倒计时"""
    try:
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
        # ★ 游戏名：方便知道是哪个游戏被封（API 返回 game_name / game_id，与 main.py 同逻辑）
        game = rec.get("game_name") or rec.get("game") or ""
        game_id = rec.get("game_id") or rec.get("gameId")
        if game and game_id:
            game = f"{game}({game_id})"
        if not game and game_id:
            game = f"游戏ID {game_id}"
        PERMANENT = 365 * 9 * 86400
        if duration >= PERMANENT:
            return {"status": "permanent", "reason": reason, "text": "永久", "end_str": "—", "game": game}
        end = start + duration
        remaining = end - now_ts
        if remaining <= 0:
            return None
        return {
            "status": "banned", "reason": reason, "remaining": remaining,
            "end_str": cn_ts(end), "game": game,
        }
    except Exception as e:
        return {"status": "error", "reason": f"解析异常: {e}", "remaining": 0}


def fmt_duration(sec):
    d, rem = divmod(int(sec), 86400)
    h, m = divmod(rem, 3600)
    m //= 60
    if d > 0:
        return f"{d}天{h}小时{m}分"
    if h > 0:
        return f"{h}小时{m}分"
    return f"{m}分钟"


def cn_ts(ts):
    """unix 秒 → 北京时间字符串"""
    return datetime.fromtimestamp(ts, CN).strftime("%m-%d %H:%M")


def main():
    key = os.environ.get("DELTA_API_KEY") or (sys.argv[1] if len(sys.argv) > 1 else "")
    acc_json = os.environ.get("DELTA_ACCOUNTS", "[]")
    if not key or acc_json == "[]":
        print("缺少 DELTA_API_KEY 或 DELTA_ACCOUNTS，跳过更新（保留旧数据）")
        return

    try:
        accounts = json.loads(acc_json)
    except Exception as e:
        print(f"DELTA_ACCOUNTS 解析失败: {e}, 退出")
        return

    now = int(time.time())
    s = requests.Session()
    s.headers.update({"X-API-Key": key})

    results = {}
    for acc in accounts:
        name = acc.get("name", "?")
        token = acc.get("framework_token", "")
        role_name = acc.get("role_name", "")
        platform = acc.get("platform", "qqsafe")

        # ---- 冷号模式：先算冷却状态（若有配置且未结束）----
        cd_status = None
        cd = acc.get("cooldown") or {}
        try:
            cd_start = int(cd.get("start") or 0)
        except Exception:
            cd_start = 0
        if cd_start:
            cd_hours = int(cd.get("duration_h") or DEFAULT_COOLDOWN_HOURS)
            cd_end = cd_start + cd_hours * 3600
            cd_remaining = cd_end - now
            if cd_remaining > 0:
                total = cd_hours * 3600
                cd_status = {
                    "status": "cooldown",
                    "cooldown": {
                        "remaining": cd_remaining,
                        "text": fmt_duration(cd_remaining),
                        "end_str": cn_ts(cd_end),
                        "pct": max(0, min(100, int((total - cd_remaining) / total * 100))),
                    },
                }

        # ---- 观察期(固定3天)：与本地同逻辑，优先于冷号 ----
        ob_status = None
        ob = acc.get("observe") or {}
        try:
            ob_start = int(ob.get("start") or 0)
        except Exception:
            ob_start = 0
        if ob_start:
            ob_hours = int(ob.get("duration_h") or 72)
            ob_end = ob_start + ob_hours * 3600
            ob_remaining = ob_end - now
            if ob_remaining > 0:
                ob_total = ob_hours * 3600
                ob_status = {
                    "status": "observe",
                    "observe": {
                        "remaining": ob_remaining,
                        "text": fmt_duration(ob_remaining),
                        "end_str": cn_ts(ob_end),
                        "pct": max(0, min(100, int((ob_total - ob_remaining) / ob_total * 100))),
                    },
                }

        if not token:
            results[name] = ob_status or cd_status or {"status": "error", "msg": "未配置 framework_token"}
            continue
        try:
            r = s.get(f"{API_BASE}/api/v1/df/qqsafe/ban",
                      headers={"X-Framework-Token": token}, timeout=20)
            d = r.json()
            if d.get("code") != 0:
                results[name] = cd_status or {"status": "error", "msg": d.get("message") or f"code={d.get('code')}"}
                continue
            raw = d.get("data")
            if isinstance(raw, dict):
                raw = raw.get("list") or raw.get("data") or []
            recs = [analyze_record(x, now) for x in (raw or [])]
            recs = [x for x in recs if x]
            if any(x["status"] == "permanent" for x in recs):
                results[name] = {"status": "permanent", "bans": recs}
            elif recs:
                results[name] = {"status": "banned", "bans": recs,
                                 "nearest": min(recs, key=lambda x: x.get("remaining", 99**9))}
            else:
                results[name] = {"status": "clean", "bans": []}
            # 观察期 > 冷号 > 常规状态
            if ob_status:
                results[name] = {**results[name], "status": "observe", "observe": ob_status["observe"]}
            elif cd_status:
                results[name] = {**results[name], "status": "cooldown", "cooldown": cd_status["cooldown"]}
        except Exception as e:
            results[name] = ob_status or cd_status or {"status": "error", "msg": f"网络/解析异常: {e}"}

    # ★ 脱敏：Pages 公开可见，只保留状态与倒计时，隐藏角色名/封禁原因/头像/游戏名
    for name, r in results.items():
        st = r.get("status", "error")
        slim = {"status": st}
        if st == "banned" and r.get("nearest"):
            n = r["nearest"]
            slim["text"] = n.get("text") or fmt_duration(n.get("remaining", 0))
            slim["end_str"] = n.get("end_str", "")
            slim["remaining"] = n.get("remaining", 0)
        elif st == "permanent":
            slim["text"] = "永久"
        elif st == "cooldown" and r.get("cooldown"):
            slim["cooldown"] = r["cooldown"]
        elif st == "observe" and r.get("observe"):
            slim["observe"] = r["observe"]
        elif st == "error":
            slim["msg"] = r.get("msg", "查询失败")
        results[name] = slim

    payload = {
        "updated_at": datetime.now(CN).strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(results),
        "results": results,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ status.json 已写入: {OUT} ({len(results)} 个账号)")


if __name__ == "__main__":
    main()