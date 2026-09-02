#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫码绑定账号：获取 QQ安全中心 二维码 → 保存 login_qr.png → 轮询扫码结果 → 自动写入 accounts.json
用法: python login_scan.py [账号名]   (默认 账号N)
"""
import base64
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import main  # noqa: E402


def now_str():
    return datetime.now().strftime("%m-%d %H:%M:%S")


def main_flow():
    cfg = main.load_config()
    name_argv = sys.argv[1] if len(sys.argv) > 1 else ""
    name = name_argv.strip() or f"账号{len(cfg['accounts'])+1}"

    api = main.DeltaAPI(cfg["api_base_url"], cfg)

    # 1) 获取二维码
    print(f"[{now_str()}] 正在向 QQ安全中心申请登录二维码...")
    try:
        framework_token, qr_b64 = api.qqsafe_qr()
    except Exception as e:
        print(f"[!] 获取二维码失败: {e}")
        sys.exit(1)
    print(f"[{now_str()}] 二维码申请成功 frameworkToken={framework_token}", flush=True)

    # 2) 保存二维码
    qr_file = BASE / "login_qr.png"
    try:
        raw = base64.b64decode(qr_b64.split(",", 1)[-1])
        qr_file.write_bytes(raw)
        print(f"[{now_str()}] 二维码已保存: {qr_file}", flush=True)
        try:
            import os
            os.startfile(str(qr_file))
        except Exception:
            pass
    except Exception as e:
        print(f"[!] 二维码图片解析失败: {e}")
        sys.exit(1)

    # 3) 轮询扫码状态（最长 3 分钟）
    deadline = time.time() + 180
    last_msg = ""
    while time.time() < deadline:
        try:
            st = api.qqsafe_status(framework_token)
            code = st.get("code")
            if code == 0:
                data = st.get("data") or {}
                real_token = (data.get("framework_token") or data.get("frameworkToken")
                              or data.get("token") or framework_token)
                cfg["accounts"].append({
                    "name": name,
                    "framework_token": real_token,
                    "note": f"绑定于 {now_str()}",
                })
                main.save_config(cfg)
                print(f"[{now_str()}] BIND_OK:{name}:{real_token}", flush=True)
                print(f"[{now_str()}] ✅ {name} 绑定成功！", flush=True)
                sys.exit(0)
            elif code in (2, 3):
                msg = st.get("message") or f"状态码{code}"
            elif code == 1:
                msg = st.get("message") or "等待扫码..."
            else:
                msg = st.get("message") or f"code={code}"
            if msg != last_msg:
                print(f"[{now_str()}] {msg}", flush=True)
                last_msg = msg
        except Exception as e:
            err = f"轮询异常: {e}"
            if err != last_msg:
                print(f"[{now_str()}] {err}", flush=True)
                last_msg = err
        time.sleep(3)

    print(f"[{now_str()}] TIMEOUT 扫码超时（3分钟）", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main_flow()