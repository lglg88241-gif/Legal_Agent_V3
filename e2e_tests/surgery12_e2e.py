"""
Surgery #12 端到端联调验证。
验证本地 3B router_llm 在真实对话流中的表现。

用法:
    D:/Dev/python310/python.exe e2e_tests/surgery12_e2e.py
"""
import time

import requests

BASE_URL = "http://127.0.0.1:8000"
POLL_INTERVAL = 1.5
MAX_POLL = 60


def get_token():
    # 本地实际可用账号；admin/admin123 在当前环境中返回 401。
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"username": "lg", "password": "123456"},
        timeout=10,
    )
    if r.status_code == 200:
        return r.json().get("access_token")
    print(f"⚠️  登录失败: {r.status_code} {r.text}")
    return None


def create_conversation(headers):
    r = requests.post(f"{BASE_URL}/conversations", headers=headers, timeout=10)
    if r.status_code in (200, 201):
        data = r.json()
        return data.get("conversation", {}).get("id") or data.get("id")
    return None


def send_and_wait(headers, user_input, conversation_id, force_current_case=False):
    payload = {
        "user_input": user_input,
        "conversation_id": conversation_id,
        "force_current_case": force_current_case,
        "is_precise_mode": False,
        "proactive_graph_mode": False,
    }
    t0 = time.time()
    r = requests.post(f"{BASE_URL}/dialog", headers=headers, json=payload, timeout=15)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "wall_ms": 0, "raw": r.text[:300]}

    data = r.json()
    task_id = data.get("task_id")

    if data.get("needs_confirmation"):
        return {
            "status": "needs_confirmation",
            "wall_ms": int((time.time() - t0) * 1000),
            "raw": data,
        }

    if not task_id:
        return {"error": "no task_id", "wall_ms": 0, "raw": data}

    for _ in range(MAX_POLL):
        time.sleep(POLL_INTERVAL)
        sr = requests.get(f"{BASE_URL}/dialog/status/{task_id}", headers=headers, timeout=10)
        if sr.status_code == 200:
            sd = sr.json()
            if sd.get("status") == "completed":
                d = sd.get("data", {})
                wall = int((time.time() - t0) * 1000)
                return {
                    "status": "completed",
                    "route_intent": d.get("route_intent"),
                    "confidence": d.get("confidence_score"),
                    "latency_ms": d.get("latency_ms"),
                    "wall_ms": wall,
                    "response_preview": (d.get("response") or "")[:150],
                }
            if sd.get("status") == "failed":
                return {"status": "failed", "wall_ms": int((time.time() - t0) * 1000), "raw": sd}

    return {"status": "timeout", "wall_ms": int((time.time() - t0) * 1000)}


def main():
    print("=" * 70)
    print(" Surgery #12 端到端联调验证")
    print("=" * 70)

    token = get_token()
    if not token:
        return
    headers = {"Authorization": f"Bearer {token}"}

    tests = [
        {
            "name": "GREETING",
            "input": "你好",
            "expect_route": "GREETING",
            "new_conv": True,
        },
        {
            "name": "GENERAL_CONSULT",
            "input": "劳动合同到期不续签，公司需要支付经济补偿吗？",
            "expect_route": "GENERAL_CONSULT",
            "new_conv": True,
        },
        {
            "name": "OUT_OF_SCOPE",
            "input": "今天天气怎么样",
            "expect_route": "OUT_OF_SCOPE",
            "new_conv": True,
        },
        {
            "name": "CASE_UPDATE (多轮第1轮)",
            "input": "我2023年3月入职深圳一家科技公司，月薪12000，没签劳动合同，4月14日被口头辞退",
            "expect_route": "GENERAL_CONSULT",
            "new_conv": True,
            "save_conv": True,
        },
        {
            "name": "CASE_UPDATE (多轮第2轮)",
            "input": "我还能要求什么赔偿？",
            "expect_route": "CASE_UPDATE",
            "new_conv": False,
        },
    ]

    print("\n 预热: 发送一个丢弃请求触发模型加载...")
    warmup_conv = create_conversation(headers)
    if warmup_conv:
        send_and_wait(headers, "你好", warmup_conv)
        print("   预热完成\n")

    results = []
    saved_conv_id = None

    for i, test in enumerate(tests, 1):
        print(f"--- 测试 {i}/{len(tests)}: {test['name']} ---")
        print(f"    输入: {test['input'][:50]}...")

        if test.get("new_conv"):
            conv_id = create_conversation(headers)
        else:
            conv_id = saved_conv_id

        if not conv_id:
            print("    ❌ 创建会话失败")
            results.append({"name": test["name"], "pass": False, "reason": "no conv"})
            continue

        result = send_and_wait(headers, test["input"], conv_id)

        if test.get("save_conv"):
            saved_conv_id = conv_id

        actual_route = result.get("route_intent", "N/A")
        expected = test["expect_route"]
        passed = actual_route == expected

        status_icon = "PASS" if passed else "WARN"
        print(f"    {status_icon} 路由: {actual_route} (期望: {expected})")
        print(f"    延迟: {result.get('wall_ms', 'N/A')}ms, confidence: {result.get('confidence', 'N/A')}")
        if result.get("response_preview"):
            print(f"    回答: {result['response_preview'][:80]}...")
        if result.get("error"):
            print(f"    错误: {result.get('error')} {result.get('raw', '')}")
        print()

        results.append(
            {
                "name": test["name"],
                "pass": passed,
                "expected": expected,
                "actual": actual_route,
                "wall_ms": result.get("wall_ms"),
                "confidence": result.get("confidence"),
                "latency_ms": result.get("latency_ms"),
                "status": result.get("status"),
            }
        )

    print("=" * 70)
    print(" Surgery #12 联调结果")
    print("=" * 70)
    print(f"{'测试':<30} {'期望':<20} {'实际':<20} {'判定':<6} {'延迟':<10}")
    print("-" * 90)

    pass_count = 0
    for r in results:
        icon = "PASS" if r["pass"] else "WARN"
        if r["pass"]:
            pass_count += 1
        wall = f"{r.get('wall_ms', 'N/A')}ms"
        print(f"{r['name']:<30} {r.get('expected',''):<20} {r.get('actual',''):<20} {icon:<6} {wall:<10}")

    print("-" * 90)
    print(f"通过: {pass_count}/{len(results)}")

    print("\n 关键检查项:")
    print("  [ ] 检查 Celery 日志中是否出现 'falling back to cloud LLM'")
    print("      → 如果没有出现，说明本地 3B 路由全程正常工作")
    print("      → 如果出现了，说明本地 vLLM 有间歇性失败")

    consult_results = [r for r in results if r.get("actual") == "GENERAL_CONSULT"]
    if consult_results:
        avg_wall = sum(r.get("wall_ms", 0) for r in consult_results) / len(consult_results)
        print(f"  [{'PASS' if avg_wall < 25000 else 'WARN'}] GENERAL_CONSULT 平均延迟: {avg_wall:.0f}ms")

    print("\n完成！请把以上输出 + Celery worker 日志中的路由相关行贴给架构主管审计。")


if __name__ == "__main__":
    main()
