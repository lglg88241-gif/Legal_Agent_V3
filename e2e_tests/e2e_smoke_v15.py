"""
Legal Agent V3 — Surgery #15 后端到端冒烟测试
覆盖：OOS 修复验证 + 核心链路回归 + 用户图谱 API
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

import requests

BASE_URL = "http://127.0.0.1:8000"
RESULTS: list[dict] = []
TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 1.5


def login() -> Optional[tuple[str, int, str]]:
    candidates = []
    env_user = os.getenv("E2E_USERNAME")
    env_pass = os.getenv("E2E_PASSWORD")
    if env_user and env_pass:
        candidates.append((env_user, env_pass))
    candidates.extend([
        ("admin", "admin123"),
        ("lg", "123456"),
    ])

    for username, password in candidates:
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("access_token"), int(data.get("user", {}).get("id", 0)), username
    return None


def new_conv(headers: dict[str, str]) -> Optional[int]:
    r = requests.post(
        f"{BASE_URL}/conversations",
        json={"title": "Surgery15 Smoke"},
        headers=headers,
        timeout=10,
    )
    if r.status_code in (200, 201):
        payload = r.json()
        conversation = payload.get("conversation", {})
        return conversation.get("id")
    return None


def ask(headers: dict[str, str], conv_id: int, text: str, force: bool = False) -> dict:
    r = requests.post(
        f"{BASE_URL}/dialog",
        headers=headers,
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
        return {"error": r.status_code, "phase": "submit"}

    first = r.json()
    if first.get("status") == "needs_confirmation":
        return ask(headers, conv_id, text, force=True)

    task_id = first.get("task_id")
    if not task_id:
        return {"error": "missing_task_id", "payload": first}

    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        sr = requests.get(f"{BASE_URL}/dialog/status/{task_id}", headers=headers, timeout=10)
        if sr.status_code == 200:
            d = sr.json()
            if d.get("status") == "completed":
                return d.get("data", {})
            if d.get("status") == "failed":
                return {"error": "task_failed", "payload": d}
    return {"error": "timeout", "task_id": task_id}


def check(data: dict, checks: list[tuple[str, str, object]]) -> list[str]:
    fails: list[str] = []
    for field, op, expected in checks:
        val = data.get(field, "")
        if op == "eq" and val != expected:
            fails.append(f"{field}={val!r} != {expected!r}")
        elif op == "ne" and val == expected:
            fails.append(f"{field}={val!r} should not be {expected!r}")
        elif op == "contains" and expected not in str(val):
            fails.append(f"{field} does not contain {expected!r}")
        elif op == "not_contains" and expected in str(val):
            fails.append(f"{field} should not contain {expected!r}")
        elif op == "gt":
            try:
                if not (float(val or 0) > float(expected)):
                    fails.append(f"{field}={val} not > {expected}")
            except Exception:
                fails.append(f"{field}={val!r} cannot compare > {expected!r}")
        elif op == "ne_zero":
            try:
                if float(val or 0) == 0:
                    fails.append(f"{field}={val} should not be 0")
            except Exception:
                fails.append(f"{field}={val!r} is not numeric for ne_zero")
    return fails


def run_test(name: str, headers: dict[str, str], conv_id: int, turns: list[tuple[str, list[tuple[str, str, object]]]]):
    result = {"name": name, "turns": [], "pass": True}
    for i, (text, checks) in enumerate(turns):
        data = ask(headers, conv_id, text)
        fails = check(data, checks)
        turn = {
            "turn": i + 1,
            "input": text[:60],
            "route": data.get("route_intent", "N/A"),
            "confidence": data.get("confidence_score", 0),
            "latency_ms": data.get("latency_ms", 0),
            "pass": len(fails) == 0,
            "fails": fails,
            "raw_error": data.get("error"),
        }
        result["turns"].append(turn)
        if fails:
            result["pass"] = False
        print(
            f"  Turn {i + 1}: route={turn['route']} conf={turn['confidence']:.2f} "
            f"{'[PASS]' if turn['pass'] else '[FAIL] ' + str(fails)}"
        )
    RESULTS.append(result)
    return result


def main() -> None:
    auth = login()
    if not auth:
        print("[FAIL] 登录失败，终止")
        return

    token, user_id, username = auth
    headers = {"Authorization": f"Bearer {token}"}
    print(f"[PASS] 登录成功 user={username} user_id={user_id}\n")

    print("=" * 55)
    print("TEST 1: OOS 误判修复（劳动争议不再被拒答）")
    conv1 = new_conv(headers)
    run_test("OOS修复-劳动争议", headers, conv1, [
        ("我在公司工作三年被辞退了，能要求赔偿吗", [
            ("route_intent", "ne", "OUT_OF_SCOPE"),
            ("response", "not_contains", "无法回答该类问题"),
        ]),
    ])

    print("\nTEST 2: OOS 正常拦截")
    conv2 = new_conv(headers)
    run_test("OOS拦截-非法律", headers, conv2, [
        ("帮我写一段Python排序代码", [
            ("route_intent", "eq", "OUT_OF_SCOPE"),
        ]),
        ("美国加州劳动法怎么规定的", [
            ("route_intent", "eq", "OUT_OF_SCOPE"),
        ]),
    ])

    print("\nTEST 3: 多轮劳动争议")
    conv3 = new_conv(headers)
    run_test("多轮劳动争议", headers, conv3, [
        ("你好", [
            ("route_intent", "eq", "GREETING"),
        ]),
        ("我2023年3月入职深圳一家科技公司，月薪12000元，没签劳动合同", [
            ("route_intent", "ne", "OUT_OF_SCOPE"),
            ("confidence_score", "ne_zero", None),
        ]),
        ("公司4月14日口头通知我不用来了，说试用期不合格", [
            ("route_intent", "ne", "OUT_OF_SCOPE"),
        ]),
        ("我能要多少赔偿？", [
            ("route_intent", "ne", "OUT_OF_SCOPE"),
            ("response", "not_contains", "999999"),
        ]),
    ])

    print("\nTEST 4: Confidence 启发式覆盖")
    conv4 = new_conv(headers)
    run_test("Confidence非零", headers, conv4, [
        ("劳动合同解除的经济补偿金标准是什么", [
            ("route_intent", "eq", "GENERAL_CONSULT"),
            ("confidence_score", "ne_zero", None),
        ]),
    ])

    print("\nTEST 5: 用户图谱 API")
    r = requests.get(f"{BASE_URL}/graph/user/{user_id}/cases", headers=headers, timeout=10)
    api_ok = r.status_code == 200
    cross_r = requests.get(
        f"{BASE_URL}/graph/user/{user_id}/cross-case-entities",
        headers=headers,
        timeout=10,
    )
    cross_ok = cross_r.status_code == 200
    print(f"  /graph/user/{user_id}/cases: {'[PASS]' if api_ok else '[FAIL]'} ({r.status_code})")
    print(f"  /graph/user/{user_id}/cross-case-entities: {'[PASS]' if cross_ok else '[FAIL]'} ({cross_r.status_code})")
    RESULTS.append({
        "name": "用户图谱API",
        "pass": api_ok and cross_ok,
        "turns": [],
    })

    print("\n" + "=" * 55)
    print("汇总")
    total = len(RESULTS)
    passed = sum(1 for item in RESULTS if item["pass"])
    print(f"测试组: {passed}/{total} PASS")
    for item in RESULTS:
        icon = "[PASS]" if item["pass"] else "[FAIL]"
        print(f"  {icon} {item['name']}")
        for turn in item.get("turns", []):
            if not turn["pass"]:
                print(f"      Turn {turn['turn']}: {turn['fails']}")

    os.makedirs("e2e_tests/results", exist_ok=True)
    with open("e2e_tests/results/e2e_smoke_v15.json", "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print("\n结果已保存: e2e_tests/results/e2e_smoke_v15.json")


if __name__ == "__main__":
    main()
