"""
Legal Agent V3 — 服务预热脚本

在服务启动后运行，触发所有 lazy-init 组件的首次加载，
降低首请求的冷启动延迟尖峰。

用法：
    D:/Dev/python310/python.exe scripts/warmup.py
    D:/Dev/python310/python.exe scripts/warmup.py --base-url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import random
import string
import sys
import time

import requests

POLL_INTERVAL = 1.5
MAX_POLL_TIMES = 60


def _random_username(prefix: str = "warmup_") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{prefix}{suffix}"


def _wait_for_service(base_url: str) -> dict:
    print(f"[预热] 目标服务：{base_url}")
    for attempt in range(30):
        try:
            response = requests.get(f"{base_url}/system/status", timeout=5)
            response.raise_for_status()
            payload = response.json()
            print(f"[预热] 状态检查 #{attempt + 1}: {payload}")
            return payload
        except Exception as exc:
            print(f"[预热] 等待服务启动... ({exc})")
            time.sleep(2)
    raise RuntimeError("服务未在 60 秒内响应 /system/status")


def _register_and_login(base_url: str) -> dict:
    username = _random_username()
    password = "Test1234!"
    for endpoint in ("/auth/register", "/auth/login"):
        response = requests.post(
            f"{base_url}{endpoint}",
            json={"username": username, "password": password},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            token = data.get("access_token", "")
            user = data.get("user", {})
            print(f"[预热] 认证成功：{endpoint}，user_id={user.get('id')}")
            return {
                "username": username,
                "password": password,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        if endpoint == "/auth/register" and response.status_code == 409:
            continue
    raise RuntimeError("预热账号注册/登录失败")


def _create_conversation(base_url: str, headers: dict[str, str]) -> int:
    response = requests.post(
        f"{base_url}/conversations",
        headers=headers,
        json={"title": "系统预热会话"},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    conversation_id = data.get("conversation", {}).get("id")
    if not conversation_id:
        raise RuntimeError(f"未能从 /conversations 响应中解析 conversation_id: {data}")
    print(f"[预热] 创建会话成功，conversation_id={conversation_id}")
    return int(conversation_id)


def _dispatch_warmup_query(base_url: str, headers: dict[str, str], conversation_id: int) -> str:
    response = requests.post(
        f"{base_url}/dialog",
        headers=headers,
        json={
            "user_input": "劳动合同解除的经济补偿标准",
            "conversation_id": conversation_id,
            "force_current_case": False,
            "is_precise_mode": False,
            "proactive_graph_mode": False,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    task_id = data.get("task_id")
    if not task_id:
        raise RuntimeError(f"预热请求未返回 task_id: {data}")
    print(f"[预热] 已派发预热请求，task_id={task_id}")
    return str(task_id)


def _poll_task(base_url: str, headers: dict[str, str], task_id: str) -> dict:
    for _ in range(MAX_POLL_TIMES):
        time.sleep(POLL_INTERVAL)
        response = requests.get(
            f"{base_url}/dialog/status/{task_id}",
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if status == "completed":
            return payload.get("data", {})
        if status == "failed":
            raise RuntimeError(f"预热请求失败: {payload}")
    raise TimeoutError("预热轮询超时（90 秒）")


def warmup(base_url: str) -> None:
    started_at = time.time()
    _wait_for_service(base_url)
    auth = _register_and_login(base_url)
    conversation_id = _create_conversation(base_url, auth["headers"])
    task_id = _dispatch_warmup_query(base_url, auth["headers"], conversation_id)
    print("[预热] 轮询等待预热任务完成...")
    result = _poll_task(base_url, auth["headers"], task_id)
    elapsed = time.time() - started_at
    print(
        "[预热] ✅ 预热完成："
        f"内部延迟={result.get('latency_ms', 'N/A')}ms，"
        f"confidence={result.get('confidence_score', 'N/A')}，"
        f"总耗时={elapsed:.1f}s"
    )
    print("[预热] 所有 lazy-init 组件已进入热态，后续首轮请求延迟应显著降低。")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Legal Agent V3 服务预热脚本")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    warmup(args.base_url.rstrip("/"))
