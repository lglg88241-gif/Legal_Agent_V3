"""
Legal Agent V3 — 全面集成测试（Surgery #16）

覆盖：
1. 认证 / 健康检查
2. 会话创建 / 列表 / 消息持久化
3. OOS 决定性拦截（外国法、非法律）
4. 法律问题不得被错误 OOS
5. 多轮劳动争议链路
6. 会话图谱 / 用户图谱 / 全局子图接口
7. 搜索接口基本可用性

说明：
- 严格按当前后端真实合同：
  POST /dialog -> task_id
  GET /dialog/status/{task_id} -> {status, data}
- 自动处理 needs_confirmation。
- 不假设最终返回包含 case_state_snapshot / case_type。
"""
from __future__ import annotations

import json
import random
import string
import time
from dataclasses import dataclass
from typing import Any

import requests

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 1.5
RESULTS: list[dict[str, Any]] = []


@dataclass
class Session:
    token: str
    user_id: int
    username: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def random_username(prefix: str = "it16_") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}{suffix}"


def login_or_register() -> Session:
    candidates: list[tuple[str, str, bool]] = []
    candidates.extend([
        ("lg", "123456", False),
        ("admin", "admin123", False),
    ])
    candidates.append((random_username(), "123456", True))

    for username, password, try_register in candidates:
        endpoints = ["/auth/register", "/auth/login"] if try_register else ["/auth/login"]
        for endpoint in endpoints:
            r = requests.post(
                f"{BASE_URL}{endpoint}",
                json={"username": username, "password": password},
                timeout=15,
            )
            if r.status_code == 409 and endpoint == "/auth/register":
                continue
            if r.status_code == 200:
                payload = r.json()
                return Session(
                    token=payload["access_token"],
                    user_id=int(payload["user"]["id"]),
                    username=payload["user"]["username"],
                )
    raise RuntimeError("登录/注册失败")


def create_conversation(session: Session, title: str) -> int:
    r = requests.post(
        f"{BASE_URL}/conversations",
        json={"title": title},
        headers=session.headers,
        timeout=15,
    )
    r.raise_for_status()
    return int(r.json()["conversation"]["id"])


def send_dialog(session: Session, conversation_id: int, text: str, *, force: bool = False) -> dict[str, Any]:
    r = requests.post(
        f"{BASE_URL}/dialog",
        headers=session.headers,
        json={
            "user_input": text,
            "conversation_id": conversation_id,
            "force_current_case": force,
            "is_precise_mode": False,
            "proactive_graph_mode": True,
        },
        timeout=30,
    )
    r.raise_for_status()
    first = r.json()

    if first.get("status") == "needs_confirmation":
        return send_dialog(session, conversation_id, text, force=True)

    task_id = first.get("task_id")
    if not task_id:
        return {"status": "failed", "error": f"missing_task_id: {first}"}

    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        sr = requests.get(
            f"{BASE_URL}/dialog/status/{task_id}",
            headers=session.headers,
            timeout=15,
        )
        sr.raise_for_status()
        status_payload = sr.json()
        if status_payload.get("status") == "completed":
            data = status_payload.get("data", {})
            data["poll_status"] = "completed"
            return data
        if status_payload.get("status") == "failed":
            return {
                "status": "failed",
                "error": status_payload.get("error", "unknown"),
                "payload": status_payload,
            }

    return {"status": "failed", "error": f"timeout task_id={task_id}"}


def check(data: dict[str, Any], checks: list[tuple[str, str, object]]) -> list[str]:
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
        elif op == "non_empty":
            if not str(val).strip():
                fails.append(f"{field} is empty")
        elif op == "is_list":
            if not isinstance(val, list):
                fails.append(f"{field} is not list")
        elif op == "has_key":
            if expected not in data:
                fails.append(f"missing key {expected!r}")
    return fails


def run_turns(name: str, session: Session, conversation_id: int, turns: list[tuple[str, list[tuple[str, str, object]]]]) -> None:
    result = {"name": name, "pass": True, "turns": []}
    for i, (text, checks_spec) in enumerate(turns, start=1):
        data = send_dialog(session, conversation_id, text)
        fails = check(data, checks_spec)
        turn = {
            "turn": i,
            "input": text[:80],
            "route": data.get("route_intent", "N/A"),
            "confidence": data.get("confidence_score", 0),
            "latency_ms": data.get("latency_ms", 0),
            "pass": not fails,
            "fails": fails,
            "error": data.get("error"),
        }
        result["turns"].append(turn)
        result["pass"] = result["pass"] and not fails
        print(
            f"  Turn {i}: route={turn['route']} conf={turn['confidence']:.2f} "
            f"lat={turn['latency_ms']} {'[PASS]' if turn['pass'] else '[FAIL] ' + str(fails)}"
        )
    RESULTS.append(result)


def run_http_check(name: str, response: requests.Response, extra_checks: list[tuple[str, str, object]] | None = None) -> None:
    payload: dict[str, Any]
    try:
        payload = response.json()
    except Exception:
        payload = {}
    checks_spec = [("status_code", "eq", 200)]
    synthetic = {"status_code": response.status_code, **payload}
    if extra_checks:
        checks_spec.extend(extra_checks)
    fails = check(synthetic, checks_spec)
    RESULTS.append({"name": name, "pass": not fails, "turns": [], "fails": fails})
    print(f"  {name}: {'[PASS]' if not fails else '[FAIL] ' + str(fails)}")


def main() -> None:
    health = requests.get(f"{BASE_URL}/health", timeout=5)
    print(f"[INFO] health={health.status_code}")
    if health.status_code != 200:
        print("[FAIL] /health 不可用，终止")
        return

    session = login_or_register()
    print(f"[PASS] auth user={session.username} user_id={session.user_id}\n")

    print("=" * 60)
    print("TEST 1: 会话创建 / 列表")
    conv_id = create_conversation(session, "Integration Full Surgery16")
    list_r = requests.get(f"{BASE_URL}/conversations", headers=session.headers, timeout=15)
    run_http_check("会话列表", list_r, [("status", "eq", "success")])
    print(f"  conversation_id={conv_id}")

    print("\nTEST 2: 外国法与非法律 OOS 决定性拦截")
    conv_oos = create_conversation(session, "Integration OOS")
    run_turns("OOS foreign/non-legal", session, conv_oos, [
        ("美国加州离婚法怎么规定的", [("route_intent", "eq", "OUT_OF_SCOPE")]),
        ("帮我改一段Python代码", [("route_intent", "eq", "OUT_OF_SCOPE")]),
    ])

    print("\nTEST 3: 中国法律问题不得误判 OOS")
    conv_legal = create_conversation(session, "Integration Legal")
    run_turns("Legal not OOS", session, conv_legal, [
        ("我在公司工作三年被辞退了，能要求赔偿吗", [
            ("route_intent", "ne", "OUT_OF_SCOPE"),
            ("response", "not_contains", "无法回答该类问题"),
            ("confidence_score", "gt", 0),
            ("response", "non_empty", None),
        ]),
    ])

    print("\nTEST 4: 多轮劳动争议")
    conv_multi = create_conversation(session, "Integration Multi-turn")
    run_turns("Labor multi-turn", session, conv_multi, [
        ("你好", [("route_intent", "eq", "GREETING")]),
        ("我2023年3月入职深圳一家科技公司，月薪12000元，没签劳动合同", [
            ("route_intent", "ne", "OUT_OF_SCOPE"),
            ("confidence_score", "gt", 0),
        ]),
        ("公司4月14日口头通知我不用来了，说试用期不合格", [
            ("route_intent", "ne", "OUT_OF_SCOPE"),
        ]),
        ("我还能要求什么赔偿？", [
            ("route_intent", "eq", "CASE_UPDATE"),
            ("response", "not_contains", "999999"),
        ]),
    ])

    print("\nTEST 5: 会话消息 / 搜索")
    msg_r = requests.get(f"{BASE_URL}/conversations/{conv_multi}/messages", headers=session.headers, timeout=15)
    run_http_check("会话消息", msg_r, [("status", "eq", "success"), ("messages", "is_list", None)])
    search_r = requests.get(
        f"{BASE_URL}/conversations/search",
        headers=session.headers,
        params={"keyword": "劳动", "limit": 5},
        timeout=15,
    )
    run_http_check("历史搜索", search_r, [("status", "eq", "success"), ("cases", "is_list", None)])

    print("\nTEST 6: 会话图谱 / 用户图谱 / 全局子图")
    conv_graph_r = requests.get(f"{BASE_URL}/conversations/{conv_multi}/graph", headers=session.headers, timeout=20)
    run_http_check(
        "会话图谱",
        conv_graph_r,
        [("status", "eq", "success"), ("has_key", "has_key", "nodes"), ("has_key", "has_key", "edges")],
    )
    user_cases_r = requests.get(f"{BASE_URL}/graph/user/{session.user_id}/cases", headers=session.headers, timeout=20)
    run_http_check("用户图谱案件概览", user_cases_r, [("cases", "is_list", None)])
    cross_entities_r = requests.get(
        f"{BASE_URL}/graph/user/{session.user_id}/cross-case-entities",
        headers=session.headers,
        timeout=20,
    )
    run_http_check("跨案实体", cross_entities_r, [("shared_entities", "is_list", None)])
    subgraph_r = requests.get(
        f"{BASE_URL}/graph/subgraph",
        params={"query": "劳动关系"},
        timeout=20,
    )
    run_http_check("全局子图", subgraph_r, [("nodes", "is_list", None), ("edges", "is_list", None)])

    print("\n" + "=" * 60)
    print("SUMMARY")
    total = len(RESULTS)
    passed = sum(1 for item in RESULTS if item.get("pass"))
    print(f"groups: {passed}/{total} PASS")
    for item in RESULTS:
        icon = "[PASS]" if item.get("pass") else "[FAIL]"
        print(f"  {icon} {item['name']}")
        for turn in item.get("turns", []):
            if not turn["pass"]:
                print(f"      Turn {turn['turn']}: {turn['fails']}")
        for fail in item.get("fails", []):
            print(f"      {fail}")

    with open("e2e_tests/results/integration_full.json", "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print("\nSaved: e2e_tests/results/integration_full.json")


if __name__ == "__main__":
    main()
