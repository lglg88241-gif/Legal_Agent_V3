"""
Surgery #13 端到端验证：案件快照持久化 + 案件类型 + 搜索 API。

使用方法（需要后端运行）：
    D:/Dev/python310/python.exe e2e_tests/surgery13_e2e.py

验证内容：
1. 创建会话 → 发送法律问题 → 检查响应中是否包含 case_state_snapshot 和 case_type
2. GET /conversations 检查会话列表是否包含 case_type 和 case_state 字段
3. GET /conversations/search 检查搜索 API 是否可用
4. 多轮对话后检查 case_state_json 是否被持久化
"""
from __future__ import annotations

import os
import sys
import time

import requests

BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 10
POLL_INTERVAL = 1.5
MAX_POLLS = 80


def get_token() -> str:
    """登录获取 JWT token。"""
    candidates = []
    env_user = os.getenv("E2E_USERNAME")
    env_pass = os.getenv("E2E_PASSWORD")
    if env_user and env_pass:
        candidates.append((env_user, env_pass))
    candidates.extend(
        [
            ("admin", "admin123"),
            ("lg", "123456"),
        ]
    )

    for username, password in candidates:
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"username": username, "password": password},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            token = r.json().get("access_token", "")
            print(f"PASS 登录成功，user={username} token={token[:20]}...")
            return token

    print(f"FAIL 登录失败，已尝试 {len(candidates)} 组凭据")
    sys.exit(1)


def create_conversation(headers: dict[str, str]) -> int:
    """创建新会话。"""
    r = requests.post(f"{BASE_URL}/conversations", headers=headers, timeout=TIMEOUT)
    assert r.status_code == 200, f"创建会话失败: {r.status_code}"
    payload = r.json()
    conv = payload.get("conversation", payload)
    conv_id = conv["id"]
    print(f"PASS 创建会话: id={conv_id}")
    return conv_id


def send_and_wait(headers: dict[str, str], conv_id: int, user_input: str) -> dict:
    """发送对话并等待完成。"""
    r = requests.post(
        f"{BASE_URL}/dialog",
        headers=headers,
        json={
            "user_input": user_input,
            "conversation_id": conv_id,
            "force_current_case": False,
            "is_precise_mode": False,
            "proactive_graph_mode": True,
        },
        timeout=TIMEOUT,
    )
    assert r.status_code == 200, f"dialog 提交失败: {r.status_code}"
    data = r.json()

    if data.get("status") == "needs_confirmation":
        r = requests.post(
            f"{BASE_URL}/dialog",
            headers=headers,
            json={
                "user_input": user_input,
                "conversation_id": conv_id,
                "force_current_case": True,
                "is_precise_mode": False,
                "proactive_graph_mode": True,
            },
            timeout=TIMEOUT,
        )
        data = r.json()

    task_id = data.get("task_id")
    if not task_id:
        return data

    print(f"  ... task_id={task_id}，轮询中")
    for _ in range(MAX_POLLS):
        time.sleep(POLL_INTERVAL)
        sr = requests.get(
            f"{BASE_URL}/dialog/status/{task_id}",
            headers=headers,
            timeout=TIMEOUT,
        )
        if sr.status_code == 200:
            result = sr.json()
            if result.get("status") == "completed":
                return result.get("data", {})
            if result.get("status") == "failed":
                print(f"  FAIL 任务失败: {result}")
                return result

    print("  FAIL 轮询超时")
    return {}


def main() -> None:
    print("=" * 60)
    print("Surgery #13 端到端验证")
    print("=" * 60)

    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}

    print("\n--- Test 1: 案件类型推断 ---")
    conv_id = create_conversation(headers)
    result = send_and_wait(
        headers,
        conv_id,
        "我在公司工作了10个月，月薪8000元，公司一直没签劳动合同，现在被辞退了",
    )
    route = result.get("route_intent", "")
    case_summary = result.get("case_summary", "")
    print(f"  route_intent: {route}")
    print(f"  case_type: {result.get('case_type', 'N/A')}")
    print(f"  case_state_snapshot 存在: {result.get('case_state_snapshot') is not None}")
    print(f"  case_summary: {case_summary[:100]}...")
    print(f"  confidence: {result.get('confidence_score', 'N/A')}")

    print("\n--- Test 2: 会话列表检查 ---")
    r = requests.get(f"{BASE_URL}/conversations", headers=headers, timeout=TIMEOUT)
    assert r.status_code == 200
    convs = r.json().get("conversations", [])
    target_conv = next((c for c in convs if c["id"] == conv_id), None)
    if target_conv:
        print(f"  case_type: {target_conv.get('case_type', 'N/A')}")
        print(f"  case_state 存在: {target_conv.get('case_state') is not None}")
        print(f"  case_summary: {(target_conv.get('case_summary') or '')[:80]}...")
        if target_conv.get("case_state"):
            cs = target_conv["case_state"]
            print(f"  case_state.monthly_salary: {cs.get('monthly_salary')}")
            print(f"  case_state.work_months: {cs.get('work_months')}")
            print(f"  case_state.has_unsigned_contract: {cs.get('has_unsigned_contract')}")
            print(f"  case_state.claim_total: {cs.get('claim_total')}")
    else:
        print(f"  WARN 会话 {conv_id} 未在列表中找到")

    print("\n--- Test 3: 案件搜索 API ---")
    r = requests.get(
        f"{BASE_URL}/conversations/search",
        headers=headers,
        params={"keyword": "劳动"},
        timeout=TIMEOUT,
    )
    if r.status_code == 200:
        cases = r.json().get("cases", [])
        print(f"  搜索 '劳动' 命中: {len(cases)} 条")
        for c in cases[:3]:
            print(f"    - id={c['id']} type={c.get('case_type', 'N/A')} title={c.get('title', '')[:30]}")
    else:
        print(f"  FAIL 搜索失败: {r.status_code}")

    r = requests.get(
        f"{BASE_URL}/conversations/search",
        headers=headers,
        params={"case_type": "劳动争议"},
        timeout=TIMEOUT,
    )
    if r.status_code == 200:
        cases = r.json().get("cases", [])
        print(f"  搜索 case_type=劳动争议 命中: {len(cases)} 条")
    else:
        print(f"  FAIL 搜索失败: {r.status_code}")

    print("\n--- Test 4: 多轮对话快照持久化 ---")
    result2 = send_and_wait(headers, conv_id, "公司还拖欠了我2个月工资")
    print(f"  第二轮 route: {result2.get('route_intent', 'N/A')}")
    print(f"  第二轮 confidence: {result2.get('confidence_score', 'N/A')}")

    r = requests.get(f"{BASE_URL}/conversations", headers=headers, timeout=TIMEOUT)
    if r.status_code == 200:
        convs = r.json().get("conversations", [])
        target = next((c for c in convs if c["id"] == conv_id), None)
        if target and target.get("case_state"):
            cs = target["case_state"]
            print(f"  更新后 monthly_salary: {cs.get('monthly_salary')}")
            print(f"  更新后 owed_wages: {cs.get('owed_wages')}")
            print(f"  更新后 claim_total: {cs.get('claim_total')}")
            print(f"  更新后 has_owed_wages: {cs.get('has_owed_wages')}")

    print("\n" + "=" * 60)
    print("Surgery #13 端到端验证完成")
    print("=" * 60)
    print("\n验证清单：")
    print("  [ ] case_type 在会话中被正确推断和持久化")
    print("  [ ] case_state_json 包含结构化数值（salary/months/claim_total）")
    print("  [ ] /conversations/search API 可用")
    print("  [ ] 多轮对话后快照被更新")
    print("\n请人工核对上述输出，确认各项数据符合预期。")


if __name__ == "__main__":
    main()
