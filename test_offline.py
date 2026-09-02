# -*- coding: utf-8 -*-
"""新版 Delta-Union-API 工具测试：逻辑单测 + 真实接口连通性"""
import json
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
import main as M

now = int(time.time())
passed = 0

def ok(name, cond, extra=""):
    global passed
    assert cond, f"FAIL: {name} {extra}"
    passed += 1
    print(f"  ✅ {name} {extra}")

print("=== 1. 倒计时逻辑 ===")
ok("时长格式化", M.fmt_duration(90061) == "1天1小时1分")
ok("小时级", M.fmt_duration(3661) == "1小时1分")

rec = {"reason": "违规组队", "start_stmp": now, "duration": 3 * 86400}
b = M.analyze_record(rec, now)
ok("临时封禁3天", b["kind"] == "temp" and "3天" in b["text"], b["text"])

rec2 = {"reason": "外挂", "start_stmp": now, "duration": 3653 * 86400}
b2 = M.analyze_record(rec2, now)
ok("永久封禁", b2["kind"] == "permanent")

rec3 = {"reason": "骂人", "start_stmp": now - 10 * 86400, "duration": 1000}
ok("已过期忽略", M.analyze_record(rec3, now) is None)

# 字段变体兼容
rec_v1 = {"reason": "违规", "punishAt": now, "duration": 2 * 86400}
b_v1 = M.analyze_record(rec_v1, now)
ok("punishAt字段兼容", b_v1["kind"] == "temp")

print("=== 2. summarize 汇总 ===")
ok("永久优先", M.summarize("A", {"records": [rec, rec2]}, now)["status"] == "permanent")
ok("临时封禁", M.summarize("B", {"records": [rec]}, now)["status"] == "banned")
ok("无记录", M.summarize("C", {"records": []}, now)["status"] == "clean")
ok("API错误", M.summarize("D", {"error": "token不存在"}, now)["status"] == "error")

print("=== 3. 匿名Token真实接口 ===")
import requests
cfg = {
    "api_base_url": "https://delta-test-api.shallow.ink",
    "auth_mode": "anonymous",
    "api_key": "",
    "accounts": [],
}
api = M.DeltaAPI(cfg["api_base_url"], cfg)
ok("匿名token自动获取", bool(cfg.get("anonymous_token")), cfg.get("anonymous_token", "")[:20])

print("=== 4. QQSafe 扫码二维码真实接口 ===")
ft, qr_b64 = api.qqsafe_qr()
ok("获取frameworkToken", bool(ft), ft[:20])
ok("获取二维码图片", qr_b64.startswith("data:image/png;base64,"), "len=" + str(len(qr_b64)))

print("=== 5. 查询封禁（token不存在应报错而非认证失败）===")
r = api.ban_history(ft)
ok("返回结果(可能404)", "error" in r or "records" in r, str(r)[:100])

print("=== 6. Tracker 全流程 ===")
cfg["accounts"] = [{"name": "测试号", "framework_token": ft}]
t = M.Tracker(cfg)
res = t.poll()
any_status = all(v["status"] in ("clean", "banned", "permanent", "error") for v in res.values())
ok("tracker轮询完成", any_status, str(res)[:200])

print(f"\n全部通过: {passed} 项 ✅")
print("（注：项目5若报'token不存在'属正常——用真实的frameworkToken即有数据）")