"""
LegalAgentClient — 封装后端 API 调用的客户端。

对话二阶段流程：
  POST /dialog → task_id → 轮询 GET /dialog/status/{task_id} → completed/failed

自动处理：
  - needs_confirmation（案件边界）：带 force_current_case=True 重投
  - 网络异常：try/except，不中断测试
  - 超时（120 秒）：返回 {"status": "failed", "error": "timeout"}
"""
from __future__ import annotations

import time
import random
import string


try:
    import requests
except ImportError:
    raise SystemExit("缺少 requests，请先执行：pip install requests")

POLL_INTERVAL = 1.5   # 秒
MAX_WAIT      = 120   # 秒


class LegalAgentClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")
        self.token: str | None = None
        self.user_id: int | None = None
        self.headers: dict = {}

    # ── 认证 ────────────────────────────────────────────────────────

    def register_and_login(self, username: str, password: str) -> str:
        """注册（已存在则登录），返回 JWT token。"""
        for endpoint in ("/auth/register", "/auth/login"):
            try:
                r = requests.post(
                    f"{self.base_url}{endpoint}",
                    json={"username": username, "password": password},
                    timeout=15,
                )
                if r.status_code == 200:
                    data = r.json()
                    self.token = data["access_token"]
                    self.user_id = data["user"]["id"]
                    self.headers = {"Authorization": f"Bearer {self.token}"}
                    print(f"  ✅ 认证成功（{endpoint}），user_id={self.user_id}")
                    return self.token
                if endpoint == "/auth/register" and r.status_code == 409:
                    continue   # 用户已存在，改用登录
            except Exception as exc:
                print(f"  ⚠️  {endpoint} 异常: {exc}")
        raise RuntimeError("注册和登录均失败，请检查后端服务是否运行。")

    # ── 会话管理 ────────────────────────────────────────────────────

    def create_conversation(self, title: str = "测试卷宗") -> int:
        """创建新会话，返回 conversation_id。"""
        try:
            r = requests.post(
                f"{self.base_url}/conversations",
                json={"title": title},
                headers=self.headers,
                timeout=15,
            )
            r.raise_for_status()
            conv_id = r.json()["conversation"]["id"]
            print(f"  📁 新建会话 id={conv_id}，标题=《{title}》")
            return conv_id
        except Exception as exc:
            raise RuntimeError(f"创建会话失败: {exc}") from exc

    def get_messages(self, conversation_id: int) -> list:
        """获取消息历史。"""
        try:
            r = requests.get(
                f"{self.base_url}/conversations/{conversation_id}/messages",
                headers=self.headers,
                timeout=15,
            )
            r.raise_for_status()
            return r.json().get("messages", [])
        except Exception as exc:
            print(f"  ⚠️  获取消息历史失败: {exc}")
            return []

    def get_conversation_graph(self, conversation_id: int) -> dict:
        """获取会话专属案情图谱。"""
        try:
            r = requests.get(
                f"{self.base_url}/conversations/{conversation_id}/graph",
                headers=self.headers,
                timeout=20,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            print(f"  ⚠️  获取图谱失败: {exc}")
            return {}

    # ── 对话核心：二阶段 ─────────────────────────────────────────────

    def send_message(
        self,
        conversation_id: int,
        message: str,
        force_current_case: bool = False,
        is_precise_mode: bool = False,
        proactive_graph_mode: bool = True,
    ) -> dict:
        """
        发送一条消息并等待最终结果。

        返回 dict，关键字段：
          status           "completed" | "failed"
          response         回答文本
          sources          RAG 引用列表
          confidence_score 置信度
          route_intent     路由意图
          case_summary     案情摘要
          latency_ms       后端处理时间
          wall_ms          端到端墙钟时间（含轮询）
        """
        wall_start = time.time()

        payload = {
            "user_input": message,
            "conversation_id": conversation_id,
            "force_current_case": force_current_case,
            "is_precise_mode": is_precise_mode,
            "proactive_graph_mode": proactive_graph_mode,
        }

        # ── 第一阶段：入队 ───────────────────────────────────────────
        try:
            r = requests.post(
                f"{self.base_url}/dialog",
                json=payload,
                headers=self.headers,
                timeout=30,
            )
            r.raise_for_status()
            first = r.json()
        except Exception as exc:
            return {"status": "failed", "error": f"POST /dialog 异常: {exc}",
                    "wall_ms": int((time.time() - wall_start) * 1000)}

        # 案件边界确认：自动带 force_current_case=True 重投
        if first.get("status") == "needs_confirmation":
            print("    ↩️  检测到案件边界，自动 force_current_case=True 重投…")
            return self.send_message(
                conversation_id, message,
                force_current_case=True,
                is_precise_mode=is_precise_mode,
                proactive_graph_mode=proactive_graph_mode,
            )

        task_id = first.get("task_id")
        if not task_id:
            return {"status": "failed", "error": f"未获取到 task_id，响应: {first}",
                    "wall_ms": int((time.time() - wall_start) * 1000)}

        # ── 第二阶段：轮询结果 ────────────────────────────────────────
        deadline = time.time() + MAX_WAIT
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            try:
                r2 = requests.get(
                    f"{self.base_url}/dialog/status/{task_id}",
                    headers=self.headers,
                    timeout=15,
                )
                r2.raise_for_status()
                poll = r2.json()
            except Exception as exc:
                print(f"    ⚠️  轮询异常: {exc}")
                continue

            status = poll.get("status")
            if status == "completed":
                data = poll.get("data", {})
                data["status"] = "completed"
                data["wall_ms"] = int((time.time() - wall_start) * 1000)
                return data

            if status == "failed":
                return {
                    "status": "failed",
                    "error": poll.get("error", "unknown"),
                    "wall_ms": int((time.time() - wall_start) * 1000),
                }
            # status == "processing"：继续等待

        return {"status": "failed", "error": "timeout (120s)",
                "wall_ms": int((time.time() - wall_start) * 1000)}

    # ── 工具方法 ─────────────────────────────────────────────────────

    @staticmethod
    def random_username(prefix: str = "e2e_") -> str:
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{prefix}{suffix}"

    def check_health(self) -> bool:
        """检查后端是否可达。"""
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False
