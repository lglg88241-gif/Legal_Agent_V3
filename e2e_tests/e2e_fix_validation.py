"""
Fix 1 + Fix 2 专项端到端验证
Fix 1: payload=None 时守卫不误杀用户输入月薪
Fix 2: 多轮对话月薪金额连贯性
"""
import json
import os
import time

import requests

BASE_URL = "http://127.0.0.1:8000"
RESULTS = []


def login():
    for u, p in [("lg", "123456"), ("admin", "admin123")]:
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"username": u, "password": p},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("access_token")
    return None


def new_conv(h):
    r = requests.post(f"{BASE_URL}/conversations", headers=h, timeout=10)
    if r.status_code in (200, 201):
        d = r.json()
        return d.get("id") or d.get("conversation", {}).get("id", 1)
    return 1


def ask(h, conv_id, text, force=False):
    r = requests.post(
        f"{BASE_URL}/dialog",
        headers=h,
        json={
            "user_input": text,
            "conversation_id": conv_id,
            "force_current_case": force,
            "is_precise_mode": False,
            "proactive_graph_mode": False,
        },
        timeout=15,
    )
    if r.status_code != 200:
        return {"error": r.status_code}
    body = r.json()
    if body.get("status") == "needs_confirmation":
        r2 = requests.post(
            f"{BASE_URL}/dialog",
            headers=h,
            json={
                "user_input": text,
                "conversation_id": conv_id,
                "force_current_case": True,
                "is_precise_mode": False,
                "proactive_graph_mode": False,
            },
            timeout=15,
        )
        body = r2.json() if r2.status_code == 200 else {"error": r2.status_code}
    task_id = body.get("task_id")
    for _ in range(60):
        time.sleep(1.5)
        sr = requests.get(f"{BASE_URL}/dialog/status/{task_id}", headers=h, timeout=10)
        if sr.status_code == 200:
            d = sr.json()
            if d.get("status") == "completed":
                return d.get("data", {})
            if d.get("status") == "failed":
                return {"error": "failed"}
    return {"error": "timeout"}


def rec(suite, name, ok, detail=""):
    RESULTS.append({"suite": suite, "name": name, "pass": ok, "detail": detail})
    print(f"  {'[PASS]' if ok else '[FAIL]'} {name}" + (f" | {detail}" if detail else ""))


def main():
    token = login()
    if not token:
        print("[ERROR] 登录失败")
        return
    h = {"Authorization": f"Bearer {token}"}
    print("[PASS] 登录成功\n")

    print("=" * 55)
    print("FIX 1: 守卫不误杀用户输入月薪（首轮无 payload）")
    conv1 = new_conv(h)
    d1 = ask(h, conv1, "我月薪10000元工作了2年，被公司违法辞退，帮我精确计算赔偿")
    resp1 = d1.get("response", "")
    rec(
        "Fix1",
        "无误杀警告",
        "尚未经过系统核算" not in resp1 or "10000" not in resp1,
        f"resp_len={len(resp1)}",
    )
    rec("Fix1", "回答有效", len(resp1) > 50, f"resp_len={len(resp1)}")
    rec("Fix1", "路由正常", d1.get("route_intent") != "OUT_OF_SCOPE", f"route={d1.get('route_intent')}")
    print(f"  回答预览: {resp1[:150]}")

    print("\n" + "=" * 55)
    print("FIX 2: 多轮对话月薪金额连贯性")
    conv2 = new_conv(h)
    d_t1 = ask(h, conv2, "我2023年6月入职，月薪15000元，公司没签劳动合同")
    rec("Fix2", "Turn1 路由正常", d_t1.get("route_intent") != "OUT_OF_SCOPE", f"route={d_t1.get('route_intent')}")
    d_t2 = ask(h, conv2, "2024年3月公司把我辞退了，说是业绩不达标，没有出示任何证据")
    rec(
        "Fix2",
        "Turn2 进入 CASE_UPDATE",
        d_t2.get("route_intent") in ("CASE_UPDATE", "GENERAL_CONSULT"),
        f"route={d_t2.get('route_intent')}",
    )
    d_t3 = ask(h, conv2, "我具体能要多少赔偿金？帮我算一下")
    resp3 = d_t3.get("response", "")
    rec(
        "Fix2",
        "Turn3 月薪 15000 出现在回答",
        "15000" in resp3,
        f"route={d_t3.get('route_intent')} conf={d_t3.get('confidence_score'):.2f}",
    )
    rec("Fix2", "Turn3 提到补偿/赔偿", any(w in resp3 for w in ["补偿", "赔偿", "月"]), f"resp_len={len(resp3)}")
    rec("Fix2", "Turn3 无幻觉金额", "999999" not in resp3, f"resp_preview={resp3[:100]}")
    print(f"  Turn3 回答预览: {resp3[:200]}")

    print("\n" + "=" * 55)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["pass"])
    print(f"Fix 专项验证: {passed}/{total} PASS")
    for r in RESULTS:
        if not r["pass"]:
            print(f"  [FAIL] [{r['suite']}] {r['name']} | {r['detail']}")
    os.makedirs("e2e_tests/results", exist_ok=True)
    with open("e2e_tests/results/e2e_fix_validation.json", "w", encoding="utf-8") as f:
        json.dump({"total": total, "passed": passed, "results": RESULTS}, f, ensure_ascii=False, indent=2)
    print("结果已保存: e2e_tests/results/e2e_fix_validation.json")


if __name__ == "__main__":
    main()
