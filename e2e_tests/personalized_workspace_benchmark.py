"""
API-level regression benchmark for the H2-H5 personalized workspace flow.

This benchmark is intentionally separate from quality_benchmark.py:
  - quality_benchmark.py focuses on the legacy ordinary legal-QA / general consult path
  - personalized_workspace_benchmark.py focuses on mode consent, dossier workflow,
    dossier memory, participant resolution, and archive semantics
"""
from __future__ import annotations

import argparse
import builtins
import io
import json
import random
import re
import string
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )


def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    return builtins.print(*args, **kwargs)

BASE_URL = "http://127.0.0.1:8000"
POLL_INTERVAL = 1.5
POLL_TIMEOUT = 120
POLL_PROGRESS_INTERVAL = 10.0
WORKSPACE_GRAPH_POLL_INTERVAL = 2.0
WORKSPACE_GRAPH_TIMEOUT_SMOKE = 60.0
WORKSPACE_GRAPH_TIMEOUT_FULL = 90.0
RESULTS_DIR = Path("e2e_tests/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class CheckResult:
    label: str
    passed: bool
    detail: str = ""


@dataclass
class BenchmarkCase:
    code: str
    name: str
    checks: list[CheckResult] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> tuple[int, int]:
        passed = sum(1 for check in self.checks if check.passed)
        return passed, len(self.checks)

    @property
    def passed(self) -> bool:
        if not self.checks:
            return False
        return all(check.passed for check in self.checks)


@dataclass(frozen=True)
class WorkspaceCasePlan:
    code: str
    key: str
    name: str
    turns: int
    checks: int


@dataclass(frozen=True)
class WorkspaceTurnProgress:
    case_index: int
    case_total: int
    case_key: str
    turn_index: int
    turn_total: int


@dataclass
class WorkspaceRunProgress:
    case_total: int
    check_total: int
    check_index: int = 0


def print_case(case: BenchmarkCase) -> None:
    passed, total = case.score
    icon = "[PASS]" if case.passed else "[FAIL]"
    print(f"\n{'=' * 72}")
    print(f"{icon} [{case.code}] {case.name}")
    print(f"{'=' * 72}")
    for check in case.checks:
        check_icon = "[PASS]" if check.passed else "[FAIL]"
        detail = f" | {check.detail}" if check.detail else ""
        print(f"  {check_icon} {check.label}{detail}")
    print(f"  Score: {passed}/{total}")
    if case.notes:
        print(f"  Notes: {json.dumps(case.notes, ensure_ascii=False)}")


WORKSPACE_CASE_PLANS = [
    WorkspaceCasePlan("A", "A_legal_qa", "A LEGAL_QA does not write structured case or graph", 1, 7),
    WorkspaceCasePlan("B", "B_temporary", "B temporary mode keeps plain messages but skips structured persistence", 2, 9),
    WorkspaceCasePlan("C", "C_tracked", "C tracked mode writes structured case and graph", 2, 7),
    WorkspaceCasePlan("D", "D_dossier_binding", "D dossier creation and tracked conversation attachment", 0, 5),
    WorkspaceCasePlan("E", "E_dossier_created_conversation", "E dossier-bound conversation defaults to tracked", 0, 4),
    WorkspaceCasePlan("F", "F_memory_query", "F dossier memory query answers from dossier context", 1, 3),
    WorkspaceCasePlan("G", "G_explicit_law_query", "G explicit law query in dossier conversation still behaves like legal QA", 1, 5),
    WorkspaceCasePlan("H", "H_participant_resolution", "H participant candidates and confirmation API flow", 1, 7),
    WorkspaceCasePlan("I", "I_archive_semantics", "I archive dossier keeps historical data accessible", 0, 4),
]

WORKSPACE_ONLY_GROUPS: dict[str, tuple[str, ...]] = {
    "baseline": ("A",),
    "consent": ("B", "C"),
    "temporary": ("B",),
    "tracked": ("C",),
    "dossier": ("D", "E"),
    "memory": ("F", "G"),
    "graph": ("C", "F", "H"),
    "participant": ("H",),
    "archive": ("I",),
    "delete": (),
}
DELETE_GROUP_IMPLEMENTED = False


@dataclass
class WorkspaceScenarioContext:
    headers: dict[str, str]
    smoke: bool
    legal_question: str = "劳动合同法第八十七条规定了什么？"
    case_message: str = (
        "我补充一下案情：我在A公司工作两年，月薪12000，公司一直没签劳动合同，"
        "2024年4月4日公司口头辞退我。"
    )
    followup_fact_message: str = (
        "我再补充一下案情：后来用人单位的人事今天又通知我尽快办离职，"
        "老板还说A公司不会给我任何补偿。"
    )
    memory_question: str = "这个案卷里之前提到的公司是谁？"
    tracked_case: dict[str, Any] | None = None
    dossier_setup: dict[str, Any] | None = None
    dossier_conversation: dict[str, Any] | None = None
    participant_resolution: dict[str, Any] | None = None


def _workspace_case_plans(smoke: bool) -> list[WorkspaceCasePlan]:
    if smoke:
        return WORKSPACE_CASE_PLANS[:7]
    return WORKSPACE_CASE_PLANS[:]


def _format_seconds(value: float | int) -> str:
    return f"{float(value):.1f}s"


def _preview_text(text: str, limit: int = 80) -> str:
    compact = " ".join(str(text or "").strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _workspace_status_label(status: str | None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "success"}:
        return "success"
    return normalized or "unknown"


def _workspace_check_key(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(label or "").strip().lower()).strip("_")
    return slug or "check"


def _workspace_case_prefix(progress: WorkspaceTurnProgress) -> str:
    return (
        f"[workspace] case {progress.case_index}/{progress.case_total} "
        f"{progress.case_key}"
    )


def _build_workspace_turn_progress(
    *,
    case_index: int,
    case_total: int,
    case_key: str,
    turn_index: int,
    turn_total: int,
) -> WorkspaceTurnProgress:
    return WorkspaceTurnProgress(
        case_index=case_index,
        case_total=case_total,
        case_key=case_key,
        turn_index=turn_index,
        turn_total=turn_total,
    )


def _workspace_case_start(
    *,
    case_index: int,
    case_total: int,
    case_key: str,
) -> float:
    print(f"[workspace] case {case_index}/{case_total} {case_key} START")
    return time.perf_counter()


def _workspace_log_turn_send(progress: WorkspaceTurnProgress, text: str) -> None:
    print(
        f"{_workspace_case_prefix(progress)} turn {progress.turn_index}/{progress.turn_total} "
        f'SEND query="{_preview_text(text)}"'
    )


def _workspace_log_turn_polling(progress: WorkspaceTurnProgress, task_id: str) -> None:
    print(
        f"{_workspace_case_prefix(progress)} turn {progress.turn_index}/{progress.turn_total} "
        f"task_id={task_id} POLLING"
    )


def _workspace_log_turn_running(progress: WorkspaceTurnProgress, elapsed_s: float) -> None:
    print(
        f"{_workspace_case_prefix(progress)} turn {progress.turn_index}/{progress.turn_total} "
        f"still running elapsed={int(elapsed_s)}s"
    )


def _workspace_log_turn_done(
    progress: WorkspaceTurnProgress,
    result: dict[str, Any],
) -> None:
    status = _workspace_status_label(result.get("status"))
    latency_s = float(result.get("latency_ms", 0) or 0) / 1000.0
    extra = ""
    error = str(result.get("error") or "").strip()
    if error:
        extra += f" error={error}"
    message = str(result.get("message") or "").strip()
    if message:
        extra += f' message="{_preview_text(message, 120)}"'
    print(
        f"{_workspace_case_prefix(progress)} turn {progress.turn_index}/{progress.turn_total} "
        f"DONE status={status}{extra} latency={_format_seconds(latency_s)}"
    )


def _workspace_log_check(
    progress: WorkspaceRunProgress,
    check: CheckResult,
) -> None:
    progress.check_index += 1
    status = "PASS" if check.passed else "FAIL"
    detail = f" reason={check.detail}" if check.detail else ""
    print(
        f"[workspace] check {progress.check_index}/{progress.check_total} "
        f"{_workspace_check_key(check.label)} {status}{detail}"
    )


def _workspace_log_case_end(
    case: BenchmarkCase,
    *,
    case_index: int,
    case_total: int,
    case_key: str,
    started: float,
) -> None:
    passed, total = case.score
    elapsed_s = time.perf_counter() - started
    latency_ms = 0
    try:
        latency_ms = int(case.notes.get("latency_ms", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        latency_ms = 0
    latency_s = (latency_ms / 1000.0) if latency_ms > 0 else elapsed_s
    if case.passed:
        print(
            f"[workspace] case {case_index}/{case_total} PASS {case_key} "
            f"score={passed}/{total} latency={_format_seconds(latency_s)} "
            f"elapsed={_format_seconds(elapsed_s)}"
        )
        return

    failed_check = next((check for check in case.checks if not check.passed), None)
    reason = failed_check.label if failed_check else "case_failed"
    if failed_check and failed_check.detail:
        reason = f"{reason} | {failed_check.detail}"
    print(
        f"[workspace] case {case_index}/{case_total} FAIL {case_key} "
        f"score={passed}/{total} latency={_format_seconds(latency_s)} "
        f"elapsed={_format_seconds(elapsed_s)} reason={reason}"
    )


def _record_workspace_case(
    *,
    case: BenchmarkCase,
    case_index: int,
    case_total: int,
    case_key: str,
    started: float,
    progress: WorkspaceRunProgress,
) -> None:
    for check in case.checks:
        _workspace_log_check(progress, check)
    _workspace_log_case_end(
        case,
        case_index=case_index,
        case_total=case_total,
        case_key=case_key,
        started=started,
    )


def _latency_sum_ms(*results: dict[str, Any] | None) -> int:
    total = 0
    for result in results:
        if not isinstance(result, dict):
            continue
        try:
            total += int(result.get("latency_ms", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def random_username(prefix: str = "workspace_bench_") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}{suffix}"


def request_json(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
) -> tuple[int, dict[str, Any] | str]:
    url = path if path.startswith("http") else f"{BASE_URL}{path}"
    response = requests.request(
        method,
        url,
        headers=headers,
        json=json_body,
        params=params,
        timeout=timeout,
    )
    try:
        payload: dict[str, Any] | str = response.json()
    except ValueError:
        payload = response.text
    return response.status_code, payload


def assert_ok(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    status_code, payload = request_json(
        method,
        path,
        headers=headers,
        json_body=json_body,
        params=params,
        timeout=timeout,
    )
    if status_code >= 400:
        raise AssertionError(
            f"{method} {path} failed with HTTP {status_code}: {payload}"
        )
    if not isinstance(payload, dict):
        raise AssertionError(f"{method} {path} returned non-JSON payload: {payload}")
    return payload


def login_or_register(username: str, password: str) -> tuple[str, dict[str, Any]]:
    for endpoint in ("/auth/register", "/auth/login"):
        status_code, payload = request_json(
            "POST",
            endpoint,
            json_body={"username": username, "password": password},
            timeout=15,
        )
        if status_code == 200 and isinstance(payload, dict):
            token = str(payload.get("access_token") or "").strip()
            user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
            if token:
                return token, user
        if endpoint == "/auth/register" and status_code == 409:
            continue
    raise AssertionError("Failed to register/login benchmark user")


def create_conversation(
    headers: dict[str, str],
    *,
    title: str,
    dossier_id: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": title}
    if dossier_id is not None:
        payload["dossier_id"] = dossier_id
    body = assert_ok("POST", "/conversations", headers=headers, json_body=payload)
    conversation = body.get("conversation") if isinstance(body.get("conversation"), dict) else None
    if not conversation:
        raise AssertionError(f"Conversation payload missing: {body}")
    return conversation


def get_conversation_snapshot(headers: dict[str, str], conversation_id: int) -> dict[str, Any]:
    return assert_ok("GET", f"/conversations/{conversation_id}/messages", headers=headers)


def get_conversation_graph(headers: dict[str, str], conversation_id: int) -> dict[str, Any]:
    return assert_ok("GET", f"/conversations/{conversation_id}/graph", headers=headers)


def create_dossier(
    headers: dict[str, str],
    *,
    title: str,
    case_type: str,
    summary: str,
) -> dict[str, Any]:
    body = assert_ok(
        "POST",
        "/dossiers",
        headers=headers,
        json_body={"title": title, "case_type": case_type, "summary": summary},
    )
    dossier = body.get("dossier") if isinstance(body.get("dossier"), dict) else None
    if not dossier:
        raise AssertionError(f"Dossier payload missing: {body}")
    return dossier


def list_dossiers(headers: dict[str, str]) -> list[dict[str, Any]]:
    body = assert_ok("GET", "/dossiers", headers=headers)
    dossiers = body.get("dossiers")
    return dossiers if isinstance(dossiers, list) else []


def get_dossier(headers: dict[str, str], dossier_id: int) -> dict[str, Any]:
    body = assert_ok("GET", f"/dossiers/{dossier_id}", headers=headers)
    dossier = body.get("dossier") if isinstance(body.get("dossier"), dict) else None
    if not dossier:
        raise AssertionError(f"Dossier detail missing: {body}")
    return dossier


def get_dossier_graph(headers: dict[str, str], dossier_id: int) -> dict[str, Any]:
    return assert_ok("GET", f"/dossiers/{dossier_id}/graph", headers=headers)


def get_dossier_participants(headers: dict[str, str], dossier_id: int) -> dict[str, Any]:
    return assert_ok("GET", f"/dossiers/{dossier_id}/participants", headers=headers)


def get_dossier_participant_candidates(headers: dict[str, str], dossier_id: int) -> dict[str, Any]:
    return assert_ok("GET", f"/dossiers/{dossier_id}/participant-candidates", headers=headers)


def update_dossier_participant_aliases(
    headers: dict[str, str],
    dossier_id: int,
    *,
    primary_label: str,
    primary_role: str,
    opposing_label: str,
    opposing_role: str,
) -> dict[str, Any]:
    body = assert_ok(
        "PATCH",
        f"/dossiers/{dossier_id}/participants",
        headers=headers,
        json_body={
            "participant_aliases": {
                "primary_party": {
                    "label": primary_label,
                    "role": primary_role,
                    "party_side": "claimant",
                },
                "opposing_party": {
                    "label": opposing_label,
                    "role": opposing_role,
                    "party_side": "opponent",
                },
            }
        },
    )
    dossier = body.get("dossier") if isinstance(body.get("dossier"), dict) else None
    if not dossier:
        raise AssertionError(f"Dossier participant alias update failed: {body}")
    return dossier


def attach_conversation_to_dossier(
    headers: dict[str, str],
    conversation_id: int,
    dossier_id: int,
) -> dict[str, Any]:
    body = assert_ok(
        "POST",
        f"/conversations/{conversation_id}/attach-dossier",
        headers=headers,
        json_body={"dossier_id": dossier_id},
    )
    conversation = body.get("conversation") if isinstance(body.get("conversation"), dict) else None
    if not conversation:
        raise AssertionError(f"Attach dossier failed: {body}")
    return conversation


def set_conversation_mode_consent(
    headers: dict[str, str],
    conversation_id: int,
    *,
    mode: str,
    consent: str,
    reason: str,
) -> dict[str, Any]:
    body = assert_ok(
        "POST",
        f"/conversations/{conversation_id}/mode-consent",
        headers=headers,
        json_body={"mode": mode, "consent": consent, "reason": reason},
    )
    conversation = body.get("conversation") if isinstance(body.get("conversation"), dict) else None
    if not conversation:
        raise AssertionError(f"Mode consent update failed: {body}")
    return conversation


def resolve_dossier_participant(
    headers: dict[str, str],
    dossier_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return assert_ok(
        "POST",
        f"/dossiers/{dossier_id}/participants/resolve",
        headers=headers,
        json_body=payload,
    )


def archive_dossier(headers: dict[str, str], dossier_id: int) -> dict[str, Any]:
    body = assert_ok("DELETE", f"/dossiers/{dossier_id}", headers=headers)
    dossier = body.get("dossier") if isinstance(body.get("dossier"), dict) else None
    if not dossier:
        raise AssertionError(f"Archive dossier failed: {body}")
    return dossier


def start_dialog(
    headers: dict[str, str],
    *,
    conversation_id: int,
    text: str,
    force_current_case: bool = False,
    mode_consent_enabled: bool = False,
    proactive_graph_mode: bool = True,
) -> tuple[int, dict[str, Any] | str]:
    return request_json(
        "POST",
        "/dialog",
        headers=headers,
        json_body={
            "user_input": text,
            "conversation_id": conversation_id,
            "force_current_case": force_current_case,
            "is_precise_mode": False,
            "proactive_graph_mode": proactive_graph_mode,
            "mode_consent_enabled": mode_consent_enabled,
        },
        timeout=20,
    )


def run_dialog_once(
    headers: dict[str, str],
    *,
    conversation_id: int,
    text: str,
    force_current_case: bool = False,
    mode_consent_enabled: bool = False,
    proactive_graph_mode: bool = True,
    progress: WorkspaceTurnProgress | None = None,
) -> tuple[int, dict[str, Any] | str, dict[str, Any]]:
    started = time.perf_counter()
    if progress is not None:
        _workspace_log_turn_send(progress, text)
    status_code, payload = start_dialog(
        headers,
        conversation_id=conversation_id,
        text=text,
        force_current_case=force_current_case,
        mode_consent_enabled=mode_consent_enabled,
        proactive_graph_mode=proactive_graph_mode,
    )
    if isinstance(payload, dict):
        normalized = _normalize_dialog_result(
            payload,
            fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            default_status=str(payload.get("status") or "completed"),
        )
    else:
        normalized = _normalize_dialog_result(
            {"status": "failed", "error": f"HTTP {status_code}", "message": str(payload)},
            fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            default_status="failed",
        )
    if progress is not None:
        _workspace_log_turn_done(progress, normalized)
    return status_code, payload, normalized


def _normalize_dialog_result(
    payload: dict[str, Any] | None,
    *,
    fallback_latency_ms: int,
    default_status: str = "completed",
) -> dict[str, Any]:
    data = payload.copy() if isinstance(payload, dict) else {}
    response_text = data.get("response")
    if not isinstance(response_text, str) or not response_text.strip():
        response_text = data.get("message") if isinstance(data.get("message"), str) else ""
    sources = data.get("sources")
    if not isinstance(sources, list):
        sources = []
    try:
        confidence = float(data.get("confidence_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    try:
        latency_ms = int(data.get("latency_ms", 0) or 0)
    except (TypeError, ValueError):
        latency_ms = 0
    if latency_ms <= 0:
        latency_ms = fallback_latency_ms
    status_value = str(data.get("status") or default_status)
    if status_value.lower() == "success":
        status_value = "completed"
    data["status"] = status_value
    data["response"] = response_text
    data["sources"] = sources
    data["confidence_score"] = confidence
    data["latency_ms"] = latency_ms
    data["route_intent"] = str(data.get("route_intent") or data.get("route") or "")
    return data


def run_dialog(
    headers: dict[str, str],
    *,
    conversation_id: int,
    text: str,
    force_current_case: bool = False,
    mode_consent_enabled: bool = False,
    proactive_graph_mode: bool = True,
    auto_confirm_boundary: bool = True,
    progress: WorkspaceTurnProgress | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    if progress is not None:
        _workspace_log_turn_send(progress, text)
    status_code, first = start_dialog(
        headers,
        conversation_id=conversation_id,
        text=text,
        force_current_case=force_current_case,
        mode_consent_enabled=mode_consent_enabled,
        proactive_graph_mode=proactive_graph_mode,
    )
    if status_code != 200:
        result = _normalize_dialog_result(
            {"status": "failed", "error": f"HTTP {status_code}", "message": str(first)},
            fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            default_status="failed",
        )
        if progress is not None:
            _workspace_log_turn_done(progress, result)
        return (first if isinstance(first, dict) else {"status": "failed"}), result

    first_payload = first if isinstance(first, dict) else {"status": "failed", "message": str(first)}
    if (
        first_payload.get("status") == "needs_confirmation"
        and auto_confirm_boundary
        and first_payload.get("action") == "suggest_new_case"
        and not force_current_case
    ):
        if progress is not None:
            interim = _normalize_dialog_result(
                first_payload,
                fallback_latency_ms=int((time.perf_counter() - started) * 1000),
                default_status="needs_confirmation",
            )
            _workspace_log_turn_done(progress, interim)
        return run_dialog(
            headers,
            conversation_id=conversation_id,
            text=text,
            force_current_case=True,
            mode_consent_enabled=mode_consent_enabled,
            proactive_graph_mode=proactive_graph_mode,
            auto_confirm_boundary=False,
            progress=progress,
        )

    if first_payload.get("status") == "needs_confirmation":
        result = _normalize_dialog_result(
            first_payload,
            fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            default_status="needs_confirmation",
        )
        if progress is not None:
            _workspace_log_turn_done(progress, result)
        return first_payload, result

    task_id = first_payload.get("task_id")
    if not task_id:
        result = _normalize_dialog_result(
            first_payload,
            fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            default_status=str(first_payload.get("status") or "completed"),
        )
        if progress is not None:
            _workspace_log_turn_done(progress, result)
        return first_payload, result

    if progress is not None:
        _workspace_log_turn_polling(progress, str(task_id))
    next_progress_log_s = POLL_PROGRESS_INTERVAL
    for _ in range(int(POLL_TIMEOUT / POLL_INTERVAL)):
        time.sleep(POLL_INTERVAL)
        elapsed_s = time.perf_counter() - started
        if progress is not None and elapsed_s >= next_progress_log_s:
            _workspace_log_turn_running(progress, elapsed_s)
            next_progress_log_s += POLL_PROGRESS_INTERVAL
        status_code, payload = request_json(
            "GET",
            f"/dialog/status/{task_id}",
            headers=headers,
            timeout=15,
        )
        if status_code != 200 or not isinstance(payload, dict):
            continue
        if payload.get("status") == "completed":
            result = _normalize_dialog_result(
                payload.get("data", {}) if isinstance(payload.get("data"), dict) else {},
                fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            )
            if progress is not None:
                _workspace_log_turn_done(progress, result)
            return first_payload, result
        if payload.get("status") == "failed":
            result = _normalize_dialog_result(
                {
                    "status": "failed",
                    "error": payload.get("error", "task_failed"),
                    "message": payload.get("message", ""),
                },
                fallback_latency_ms=int((time.perf_counter() - started) * 1000),
                default_status="failed",
            )
            if progress is not None:
                _workspace_log_turn_done(progress, result)
            return first_payload, result

    result = _normalize_dialog_result(
        {"status": "failed", "error": "timeout"},
        fallback_latency_ms=int((time.perf_counter() - started) * 1000),
        default_status="failed",
    )
    if progress is not None:
        _workspace_log_turn_done(progress, result)
    return first_payload, result


def retry_value(fetcher, predicate, *, attempts: int = 4, sleep_s: float = 2.0):
    last_value = None
    for attempt in range(attempts):
        last_value = fetcher()
        if predicate(last_value):
            return last_value
        if attempt < attempts - 1:
            time.sleep(sleep_s)
    return last_value


def is_empty_graph(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return True
    if isinstance(payload.get("graph"), dict):
        graph_payload = payload.get("graph", {})
        nodes = graph_payload.get("nodes", []) if isinstance(graph_payload.get("nodes"), list) else []
        edges = graph_payload.get("edges", []) if isinstance(graph_payload.get("edges"), list) else []
    else:
        nodes = payload.get("nodes", []) if isinstance(payload.get("nodes"), list) else []
        edges = payload.get("edges", []) if isinstance(payload.get("edges"), list) else []
    return not nodes and not edges


def has_structured_case_persisted(record: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict):
        return False
    case_summary_present = bool(str(record.get("case_summary") or "").strip())
    case_type_present = bool(str(record.get("case_type") or "").strip())
    case_state_present = record.get("case_state") not in (None, {}, "")
    return case_summary_present and (case_type_present or case_state_present)


def wait_for_tracked_graph_update(
    headers: dict[str, str],
    conversation_id: int,
    *,
    smoke: bool,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    max_wait_s = (
        WORKSPACE_GRAPH_TIMEOUT_SMOKE
        if smoke
        else WORKSPACE_GRAPH_TIMEOUT_FULL
    )
    started = time.perf_counter()
    last_snapshot = get_conversation_snapshot(headers, conversation_id)
    last_record = get_conversation_record(last_snapshot)

    while (time.perf_counter() - started) < max_wait_s:
        if has_structured_case_persisted(last_record):
            break
        time.sleep(WORKSPACE_GRAPH_POLL_INTERVAL)
        last_snapshot = get_conversation_snapshot(headers, conversation_id)
        last_record = get_conversation_record(last_snapshot)

    structured_case_persisted = has_structured_case_persisted(last_record)
    last_graph = get_conversation_graph(headers, conversation_id)
    if not structured_case_persisted:
        return (
            last_snapshot,
            last_graph,
            f"structured case persistence timeout after {int(max_wait_s)}s",
        )

    while (time.perf_counter() - started) < max_wait_s:
        if not is_empty_graph(last_graph):
            return last_snapshot, last_graph, ""
        time.sleep(WORKSPACE_GRAPH_POLL_INTERVAL)
        last_snapshot = get_conversation_snapshot(headers, conversation_id)
        last_record = get_conversation_record(last_snapshot)
        last_graph = get_conversation_graph(headers, conversation_id)

    return (
        last_snapshot,
        last_graph,
        (
            f"graph async update timeout; structured case had been persisted; "
            f"graph still empty after {int(max_wait_s)} seconds"
        ),
    )


def get_conversation_record(snapshot: dict[str, Any]) -> dict[str, Any]:
    conversation = snapshot.get("conversation")
    return conversation if isinstance(conversation, dict) else {}


def contains_any(text: str, *keywords: str) -> bool:
    lowered = str(text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _workspace_setup(message: str) -> None:
    print(f"[workspace] setup {message}")


def _select_workspace_case_plans(
    *,
    smoke: bool,
    only: str,
) -> list[WorkspaceCasePlan]:
    normalized_only = str(only or "all").strip().lower() or "all"
    if normalized_only == "all":
        return _workspace_case_plans(smoke)
    if normalized_only not in WORKSPACE_ONLY_GROUPS:
        valid = ", ".join(["all", *sorted(WORKSPACE_ONLY_GROUPS)])
        raise SystemExit(f"Unsupported --only value: {normalized_only}. Valid values: {valid}")
    selected_codes = set(WORKSPACE_ONLY_GROUPS[normalized_only])
    return [plan for plan in WORKSPACE_CASE_PLANS if plan.code in selected_codes]


def _workspace_output_name(*, smoke: bool, only: str) -> str:
    normalized_only = str(only or "all").strip().lower() or "all"
    if normalized_only != "all":
        return f"personalized_workspace_benchmark_{normalized_only}.json"
    if smoke:
        return "personalized_workspace_benchmark_smoke.json"
    return "personalized_workspace_benchmark_report.json"


def ensure_tracked_case(
    context: WorkspaceScenarioContext,
    *,
    prompt_progress: WorkspaceTurnProgress | None = None,
    fact_progress: WorkspaceTurnProgress | None = None,
) -> dict[str, Any]:
    if context.tracked_case is not None:
        return context.tracked_case

    if prompt_progress is None and fact_progress is None:
        _workspace_setup("creating tracked conversation prerequisite")

    conversation = create_conversation(context.headers, title="workspace-C-tracked")
    first_status, first_payload, first_result = run_dialog_once(
        context.headers,
        conversation_id=conversation["id"],
        text=context.case_message,
        mode_consent_enabled=True,
        progress=prompt_progress,
    )
    first_response = (
        first_payload if isinstance(first_payload, dict) else {"status": f"HTTP {first_status}"}
    )
    consent = set_conversation_mode_consent(
        context.headers,
        conversation["id"],
        mode="TRACKED_CASE",
        consent="tracked",
        reason="workspace_benchmark_tracked",
    )
    _, result = run_dialog(
        context.headers,
        conversation_id=conversation["id"],
        text=context.case_message,
        mode_consent_enabled=False,
        progress=fact_progress,
    )
    snapshot, graph, graph_timeout_detail = wait_for_tracked_graph_update(
        context.headers,
        conversation["id"],
        smoke=context.smoke,
    )
    record = get_conversation_record(snapshot)
    context.tracked_case = {
        "conversation": conversation,
        "first_response": first_response,
        "first_result": first_result,
        "consent": consent,
        "result": result,
        "snapshot": snapshot,
        "graph": graph,
        "graph_timeout_detail": graph_timeout_detail,
        "record": record,
    }
    return context.tracked_case


def ensure_dossier_setup(context: WorkspaceScenarioContext) -> dict[str, Any]:
    if context.dossier_setup is not None:
        return context.dossier_setup

    tracked = ensure_tracked_case(context)
    _workspace_setup("creating dossier binding prerequisite")
    dossier = create_dossier(
        context.headers,
        title="劳动争议测试案卷",
        case_type="劳动争议",
        summary="workspace benchmark dossier",
    )
    dossier = update_dossier_participant_aliases(
        context.headers,
        dossier["id"],
        primary_label="我",
        primary_role="劳动者",
        opposing_label="A公司",
        opposing_role="用人单位",
    )
    attached_conversation = attach_conversation_to_dossier(
        context.headers,
        tracked["conversation"]["id"],
        dossier["id"],
    )
    dossier_list = list_dossiers(context.headers)
    dossier_detail = get_dossier(context.headers, dossier["id"])
    snapshot = get_conversation_snapshot(context.headers, tracked["conversation"]["id"])
    context.dossier_setup = {
        "dossier": dossier,
        "attached_conversation": attached_conversation,
        "dossier_list": dossier_list,
        "dossier_detail": dossier_detail,
        "snapshot": snapshot,
        "record": get_conversation_record(snapshot),
    }
    return context.dossier_setup


def ensure_dossier_conversation(context: WorkspaceScenarioContext) -> dict[str, Any]:
    if context.dossier_conversation is not None:
        return context.dossier_conversation

    dossier_setup = ensure_dossier_setup(context)
    _workspace_setup("creating dossier-bound conversation prerequisite")
    conversation = create_conversation(
        context.headers,
        title="workspace-E-dossier-bound",
        dossier_id=dossier_setup["dossier"]["id"],
    )
    context.dossier_conversation = conversation
    return conversation


def ensure_participant_resolution(
    context: WorkspaceScenarioContext,
    *,
    fact_progress: WorkspaceTurnProgress | None = None,
) -> dict[str, Any]:
    if context.participant_resolution is not None:
        return context.participant_resolution

    dossier_setup = ensure_dossier_setup(context)
    if fact_progress is None:
        _workspace_setup("creating participant resolution prerequisite")
    conversation = create_conversation(
        context.headers,
        title="workspace-H-participant-resolution",
        dossier_id=dossier_setup["dossier"]["id"],
    )
    _, fact_result = run_dialog(
        context.headers,
        conversation_id=conversation["id"],
        text=context.followup_fact_message,
        mode_consent_enabled=False,
        progress=fact_progress,
    )
    candidates_payload = retry_value(
        lambda: get_dossier_participant_candidates(context.headers, dossier_setup["dossier"]["id"]),
        lambda payload: bool((payload.get("candidates") if isinstance(payload, dict) else []) or []),
        attempts=4,
        sleep_s=2.0,
    )
    candidates = (
        candidates_payload.get("candidates")
        if isinstance(candidates_payload, dict) and isinstance(candidates_payload.get("candidates"), list)
        else []
    )
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, dict) and item.get("entity_type") == "organization"
        ),
        candidates[0] if candidates else None,
    )
    resolve_result: dict[str, Any] | None = None
    if isinstance(candidate, dict):
        resolve_result = resolve_dossier_participant(
            context.headers,
            dossier_setup["dossier"]["id"],
            {
                "participant": {
                    "label": str(candidate.get("suggested_label") or "A公司"),
                    "role": str(candidate.get("suggested_role") or "用人单位") or None,
                    "party_side": str(candidate.get("party_side") or "opponent"),
                    "entity_type": str(candidate.get("entity_type") or "organization"),
                    "description": "workspace benchmark confirmed participant",
                },
                "mentions": candidate.get("mentions") or [],
            },
        )
    participants_payload = get_dossier_participants(
        context.headers,
        dossier_setup["dossier"]["id"],
    )
    dossier_graph = get_dossier_graph(context.headers, dossier_setup["dossier"]["id"])
    participants = (
        participants_payload.get("participants")
        if isinstance(participants_payload.get("participants"), list)
        else []
    )
    mentions = (
        participants_payload.get("mentions")
        if isinstance(participants_payload.get("mentions"), list)
        else []
    )
    graph_nodes = (
        dossier_graph.get("graph", {}).get("nodes", [])
        if isinstance(dossier_graph.get("graph"), dict)
        else []
    )
    resolved_label = (
        str(candidate.get("suggested_label") or "A公司")
        if isinstance(candidate, dict)
        else "A公司"
    )
    context.participant_resolution = {
        "conversation": conversation,
        "fact_result": fact_result,
        "candidates": candidates,
        "candidate": candidate,
        "resolve_result": resolve_result,
        "participants_payload": participants_payload,
        "dossier_graph": dossier_graph,
        "participants": participants,
        "mentions": mentions,
        "graph_nodes": graph_nodes,
        "resolved_label": resolved_label,
    }
    return context.participant_resolution


def run_case_a(
    context: WorkspaceScenarioContext,
    *,
    plan: WorkspaceCasePlan,
    case_index: int,
    case_total: int,
) -> BenchmarkCase:
    conversation = create_conversation(context.headers, title="workspace-A-legal-qa")
    first_response, result = run_dialog(
        context.headers,
        conversation_id=conversation["id"],
        text=context.legal_question,
        mode_consent_enabled=False,
        proactive_graph_mode=True,
        progress=_build_workspace_turn_progress(
            case_index=case_index,
            case_total=case_total,
            case_key=plan.key,
            turn_index=1,
            turn_total=plan.turns,
        ),
    )
    snapshot = get_conversation_snapshot(context.headers, conversation["id"])
    graph = get_conversation_graph(context.headers, conversation["id"])
    record = get_conversation_record(snapshot)
    return BenchmarkCase(
        code="A",
        name="LEGAL_QA pure legal question does not enter structured workspace state",
        notes={
            "route_intent": result.get("route_intent"),
            "sources_count": len(result.get("sources", [])),
            "latency_ms": _latency_sum_ms(result),
        },
        checks=[
            CheckResult("dialog completed", result.get("status") == "completed", str(result.get("status"))),
            CheckResult(
                "no mode-consent prompt",
                first_response.get("status") != "needs_confirmation",
                str(first_response.get("status")),
            ),
            CheckResult(
                "route is not CASE_UPDATE",
                str(result.get("route_intent") or "").upper() != "CASE_UPDATE",
                str(result.get("route_intent") or ""),
            ),
            CheckResult("case_summary stays empty", not (record.get("case_summary") or "").strip()),
            CheckResult("case_type stays empty", not (record.get("case_type") or "").strip()),
            CheckResult("case_state stays empty", record.get("case_state") in (None, {}, "")),
            CheckResult("conversation graph stays empty", is_empty_graph(graph)),
        ],
    )


def run_case_b(
    context: WorkspaceScenarioContext,
    *,
    plan: WorkspaceCasePlan,
    case_index: int,
    case_total: int,
) -> BenchmarkCase:
    conversation = create_conversation(context.headers, title="workspace-B-temporary")
    first_status, first_payload, first_result = run_dialog_once(
        context.headers,
        conversation_id=conversation["id"],
        text=context.case_message,
        mode_consent_enabled=True,
        progress=_build_workspace_turn_progress(
            case_index=case_index,
            case_total=case_total,
            case_key=plan.key,
            turn_index=1,
            turn_total=plan.turns,
        ),
    )
    first_response = (
        first_payload if isinstance(first_payload, dict) else {"status": f"HTTP {first_status}"}
    )
    consent = set_conversation_mode_consent(
        context.headers,
        conversation["id"],
        mode="CASE_ANALYSIS",
        consent="temporary",
        reason="workspace_benchmark_temporary",
    )
    _, result = run_dialog(
        context.headers,
        conversation_id=conversation["id"],
        text=context.case_message,
        mode_consent_enabled=False,
        progress=_build_workspace_turn_progress(
            case_index=case_index,
            case_total=case_total,
            case_key=plan.key,
            turn_index=2,
            turn_total=plan.turns,
        ),
    )
    snapshot = get_conversation_snapshot(context.headers, conversation["id"])
    graph = get_conversation_graph(context.headers, conversation["id"])
    record = get_conversation_record(snapshot)
    return BenchmarkCase(
        code="B",
        name="temporary consent keeps plain messages but skips structured persistence",
        notes={
            "route_intent": result.get("route_intent"),
            "latency_ms": _latency_sum_ms(first_result, result),
        },
        checks=[
            CheckResult("mode consent prompt appears", first_response.get("status") == "needs_confirmation", str(first_response)),
            CheckResult("mode consent action is mode_consent", first_response.get("action") == "mode_consent", str(first_response.get("action"))),
            CheckResult("temporary consent stored", consent.get("persistence_consent") == "temporary", json.dumps(consent, ensure_ascii=False)),
            CheckResult("conversation mode stored as CASE_ANALYSIS", consent.get("conversation_mode") == "CASE_ANALYSIS", json.dumps(consent, ensure_ascii=False)),
            CheckResult("dialog completed", result.get("status") == "completed", str(result.get("status"))),
            CheckResult("case_summary stays empty", not (record.get("case_summary") or "").strip()),
            CheckResult("case_type stays empty", not (record.get("case_type") or "").strip()),
            CheckResult("case_state stays empty", record.get("case_state") in (None, {}, "")),
            CheckResult("conversation graph stays empty", is_empty_graph(graph)),
        ],
    )


def run_case_c(
    context: WorkspaceScenarioContext,
    *,
    plan: WorkspaceCasePlan,
    case_index: int,
    case_total: int,
) -> BenchmarkCase:
    tracked = ensure_tracked_case(
        context,
        prompt_progress=_build_workspace_turn_progress(
            case_index=case_index,
            case_total=case_total,
            case_key=plan.key,
            turn_index=1,
            turn_total=plan.turns,
        ),
        fact_progress=_build_workspace_turn_progress(
            case_index=case_index,
            case_total=case_total,
            case_key=plan.key,
            turn_index=2,
            turn_total=plan.turns,
        ),
    )
    return BenchmarkCase(
        code="C",
        name="tracked consent writes structured case and graph",
        notes={
            "route_intent": tracked["result"].get("route_intent"),
            "graph_stats": tracked["graph"].get("stats") or tracked["graph"].get("graph", {}).get("stats", {}),
            "graph_wait_timeout": tracked["graph_timeout_detail"],
            "latency_ms": _latency_sum_ms(tracked["first_result"], tracked["result"]),
        },
        checks=[
            CheckResult("mode consent prompt appears", tracked["first_response"].get("status") == "needs_confirmation", str(tracked["first_response"])),
            CheckResult("tracked consent stored", tracked["consent"].get("persistence_consent") == "tracked", json.dumps(tracked["consent"], ensure_ascii=False)),
            CheckResult("conversation mode stored as TRACKED_CASE", tracked["consent"].get("conversation_mode") == "TRACKED_CASE", json.dumps(tracked["consent"], ensure_ascii=False)),
            CheckResult("dialog completed", tracked["result"].get("status") == "completed", str(tracked["result"].get("status"))),
            CheckResult("case_summary is populated", bool((tracked["record"].get("case_summary") or "").strip()), tracked["record"].get("case_summary", "")),
            CheckResult(
                "case_type or case_state is populated",
                bool((tracked["record"].get("case_type") or "").strip()) or tracked["record"].get("case_state") not in (None, {}, ""),
                json.dumps(
                    {
                        "case_type": tracked["record"].get("case_type"),
                        "case_state_present": tracked["record"].get("case_state") not in (None, {}, ""),
                    },
                    ensure_ascii=False,
                ),
            ),
            CheckResult(
                "conversation graph is not empty",
                not is_empty_graph(tracked["graph"]),
                tracked["graph_timeout_detail"],
            ),
        ],
    )


def run_case_d(
    context: WorkspaceScenarioContext,
    *,
    plan: WorkspaceCasePlan,
    case_index: int,
    case_total: int,
) -> BenchmarkCase:
    dossier_setup = ensure_dossier_setup(context)
    tracked = ensure_tracked_case(context)
    return BenchmarkCase(
        code="D",
        name="dossier creation and tracked conversation binding",
        notes={"dossier_id": dossier_setup["dossier"]["id"], "latency_ms": 0},
        checks=[
            CheckResult(
                "dossier appears in dossier list",
                any(int(item.get("id") or 0) == dossier_setup["dossier"]["id"] for item in dossier_setup["dossier_list"]),
                f"list_count={len(dossier_setup['dossier_list'])}",
            ),
            CheckResult(
                "attach-dossier returns conversation binding",
                int(dossier_setup["attached_conversation"].get("dossier_id") or 0) == dossier_setup["dossier"]["id"],
                json.dumps(dossier_setup["attached_conversation"], ensure_ascii=False),
            ),
            CheckResult(
                "conversation snapshot shows dossier_id",
                int(dossier_setup["record"].get("dossier_id") or 0) == dossier_setup["dossier"]["id"],
                json.dumps(dossier_setup["record"], ensure_ascii=False),
            ),
            CheckResult(
                "dossier detail shows bound conversation",
                any(int(item.get("id") or 0) == tracked["conversation"]["id"] for item in dossier_setup["dossier_detail"].get("conversations", [])),
                f"conversation_count={dossier_setup['dossier_detail'].get('conversation_count')}",
            ),
            CheckResult(
                "participant aliases saved on dossier",
                str(dossier_setup["dossier_detail"].get("participant_aliases", {}).get("opposing_party", {}).get("label") or "") == "A公司",
                json.dumps(dossier_setup["dossier_detail"].get("participant_aliases", {}), ensure_ascii=False),
            ),
        ],
    )


def run_case_e(
    context: WorkspaceScenarioContext,
    *,
    plan: WorkspaceCasePlan,
    case_index: int,
    case_total: int,
) -> BenchmarkCase:
    dossier_conversation = ensure_dossier_conversation(context)
    dossier_setup = ensure_dossier_setup(context)
    return BenchmarkCase(
        code="E",
        name="creating a conversation from dossier binds tracked defaults immediately",
        notes={"conversation_id": dossier_conversation["id"], "latency_ms": 0},
        checks=[
            CheckResult("conversation is bound to dossier", int(dossier_conversation.get("dossier_id") or 0) == dossier_setup["dossier"]["id"], json.dumps(dossier_conversation, ensure_ascii=False)),
            CheckResult("conversation mode defaults to TRACKED_CASE", dossier_conversation.get("conversation_mode") == "TRACKED_CASE", json.dumps(dossier_conversation, ensure_ascii=False)),
            CheckResult("persistence consent defaults to tracked", dossier_conversation.get("persistence_consent") == "tracked", json.dumps(dossier_conversation, ensure_ascii=False)),
            CheckResult("mode reason is created_from_dossier", dossier_conversation.get("mode_reason") == "created_from_dossier", json.dumps(dossier_conversation, ensure_ascii=False)),
        ],
    )


def run_case_f(
    context: WorkspaceScenarioContext,
    *,
    plan: WorkspaceCasePlan,
    case_index: int,
    case_total: int,
) -> BenchmarkCase:
    dossier_conversation = ensure_dossier_conversation(context)
    _, result = run_dialog(
        context.headers,
        conversation_id=dossier_conversation["id"],
        text=context.memory_question,
        mode_consent_enabled=False,
        progress=_build_workspace_turn_progress(
            case_index=case_index,
            case_total=case_total,
            case_key=plan.key,
            turn_index=1,
            turn_total=plan.turns,
        ),
    )
    response_text = str(result.get("response") or "")
    return BenchmarkCase(
        code="F",
        name="dossier memory query answers from dossier context / graph context",
        notes={
            "route_intent": result.get("route_intent"),
            "sources_count": len(result.get("sources", [])),
            "latency_ms": _latency_sum_ms(result),
        },
        checks=[
            CheckResult("dialog completed", result.get("status") == "completed", str(result.get("status"))),
            CheckResult("response identifies the company", contains_any(response_text, "A公司"), response_text[:240]),
            CheckResult(
                "response avoids unrelated criminal / pollution RAG",
                not contains_any(response_text, "污染环境", "刑法", "危险驾驶"),
                response_text[:240],
            ),
        ],
    )


def run_case_g(
    context: WorkspaceScenarioContext,
    *,
    plan: WorkspaceCasePlan,
    case_index: int,
    case_total: int,
) -> BenchmarkCase:
    dossier_conversation = ensure_dossier_conversation(context)
    _, result = run_dialog(
        context.headers,
        conversation_id=dossier_conversation["id"],
        text=context.legal_question,
        mode_consent_enabled=False,
        progress=_build_workspace_turn_progress(
            case_index=case_index,
            case_total=case_total,
            case_key=plan.key,
            turn_index=1,
            turn_total=plan.turns,
        ),
    )
    response_text = str(result.get("response") or "")
    return BenchmarkCase(
        code="G",
        name="explicit law query in dossier conversation still behaves like legal QA",
        notes={
            "route_intent": result.get("route_intent"),
            "sources_count": len(result.get("sources", [])),
            "latency_ms": _latency_sum_ms(result),
        },
        checks=[
            CheckResult("dialog completed", result.get("status") == "completed", str(result.get("status"))),
            CheckResult("response cites article 87", contains_any(response_text, "第八十七条", "87条"), response_text[:240]),
            CheckResult("response discusses unlawful termination compensation", contains_any(response_text, "赔偿金", "违法解除"), response_text[:240]),
            CheckResult("sources are present", len(result.get("sources", [])) >= 1, f"sources={len(result.get('sources', []))}"),
            CheckResult("response is not polluted by dossier company label", "A公司" not in response_text, response_text[:240]),
        ],
    )


def run_case_h(
    context: WorkspaceScenarioContext,
    *,
    plan: WorkspaceCasePlan,
    case_index: int,
    case_total: int,
) -> BenchmarkCase:
    participant_resolution = ensure_participant_resolution(
        context,
        fact_progress=_build_workspace_turn_progress(
            case_index=case_index,
            case_total=case_total,
            case_key=plan.key,
            turn_index=1,
            turn_total=plan.turns,
        ),
    )
    return BenchmarkCase(
        code="H",
        name="participant candidates and confirmation API flow",
        notes={
            "fact_route_intent": participant_resolution["fact_result"].get("route_intent"),
            "candidate_count": len(participant_resolution["candidates"]),
            "latency_ms": _latency_sum_ms(participant_resolution["fact_result"]),
        },
        checks=[
            CheckResult("fact update dialog completed", participant_resolution["fact_result"].get("status") == "completed", str(participant_resolution["fact_result"].get("status"))),
            CheckResult("participant candidates endpoint returns candidates", len(participant_resolution["candidates"]) >= 1, f"candidate_count={len(participant_resolution['candidates'])}"),
            CheckResult("resolve endpoint succeeded", isinstance(participant_resolution["resolve_result"], dict) and participant_resolution["resolve_result"].get("status") == "success", json.dumps(participant_resolution["resolve_result"] or {}, ensure_ascii=False)),
            CheckResult("confirmed participant is persisted", len(participant_resolution["participants"]) >= 1, f"participants={len(participant_resolution['participants'])}"),
            CheckResult("participant mentions are persisted", len(participant_resolution["mentions"]) >= 1, f"mentions={len(participant_resolution['mentions'])}"),
            CheckResult(
                "dossier graph exposes participant mappings",
                len(participant_resolution["dossier_graph"].get("participant_mappings", []) or []) >= 1,
                f"participant_mappings={len(participant_resolution['dossier_graph'].get('participant_mappings', []) or [])}",
            ),
            CheckResult(
                "dossier graph nodes can show resolved display_label",
                any(str(node.get("display_label") or "") == participant_resolution["resolved_label"] for node in participant_resolution["graph_nodes"] if isinstance(node, dict)),
                f"resolved_label={participant_resolution['resolved_label']}",
            ),
        ],
    )


def run_case_i(
    context: WorkspaceScenarioContext,
    *,
    plan: WorkspaceCasePlan,
    case_index: int,
    case_total: int,
) -> BenchmarkCase:
    participant_resolution = ensure_participant_resolution(context)
    dossier_setup = ensure_dossier_setup(context)
    participants_before_archive = len(participant_resolution["participants"])
    archived = archive_dossier(context.headers, dossier_setup["dossier"]["id"])
    dossier_after_archive = get_dossier(context.headers, dossier_setup["dossier"]["id"])
    participants_after_archive = get_dossier_participants(
        context.headers,
        dossier_setup["dossier"]["id"],
    )
    archived_participant_count = len(
        participants_after_archive.get("participants")
        if isinstance(participants_after_archive.get("participants"), list)
        else []
    )
    archived_write_status, archived_write_payload = request_json(
        "POST",
        f"/dossiers/{dossier_setup['dossier']['id']}/participants",
        headers=context.headers,
        json_body={
            "label": "归档后新增主体",
            "role": "测试",
            "party_side": "unknown",
            "entity_type": "unknown",
            "description": "should fail",
        },
    )
    return BenchmarkCase(
        code="I",
        name="archive dossier keeps historical data and blocks new writes",
        notes={"dossier_id": dossier_setup["dossier"]["id"], "latency_ms": 0},
        checks=[
            CheckResult("archive endpoint returns archived dossier", str(archived.get("status") or "") == "archived", json.dumps(archived, ensure_ascii=False)),
            CheckResult("archived dossier remains readable", str(dossier_after_archive.get("status") or "") == "archived", json.dumps(dossier_after_archive, ensure_ascii=False)),
            CheckResult("participants remain after archive", archived_participant_count == participants_before_archive, f"before={participants_before_archive} after={archived_participant_count}"),
            CheckResult("archived dossier rejects participant writes", archived_write_status == 409, f"status={archived_write_status} payload={archived_write_payload}"),
        ],
    )


WORKSPACE_CASE_RUNNERS = {
    "A": run_case_a,
    "B": run_case_b,
    "C": run_case_c,
    "D": run_case_d,
    "E": run_case_e,
    "F": run_case_f,
    "G": run_case_g,
    "H": run_case_h,
    "I": run_case_i,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Personalized workspace benchmark for H2-H5 regression"
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="run the default reduced regression set (A-G) when --only=all",
    )
    parser.add_argument(
        "--only",
        default="all",
        help=(
            "workspace group to run: all, baseline, consent, temporary, tracked, "
            "dossier, memory, graph, participant, archive, delete"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned benchmark cases and exit without network calls",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=POLL_PROGRESS_INTERVAL,
        help="seconds between polling progress logs; default 10",
    )
    parser.add_argument(
        "--username",
        default="",
        help="optional explicit benchmark username; default is a random throwaway user",
    )
    parser.add_argument(
        "--password",
        default="workspace123",
        help="password for the benchmark user",
    )
    return parser.parse_args()


def planned_case_names(case_plans: list[WorkspaceCasePlan]) -> list[str]:
    return [plan.name for plan in case_plans]


def run_benchmark(
    *,
    smoke: bool,
    only: str,
    progress_interval: float,
    username: str,
    password: str,
) -> dict[str, Any]:
    global POLL_PROGRESS_INTERVAL
    POLL_PROGRESS_INTERVAL = max(1.0, float(progress_interval))
    benchmark_started = time.perf_counter()
    case_plans = _select_workspace_case_plans(smoke=smoke, only=only)
    normalized_only = str(only or "all").strip().lower() or "all"
    run_progress = WorkspaceRunProgress(
        case_total=len(case_plans),
        check_total=sum(plan.checks for plan in case_plans),
    )
    planned_turns = sum(plan.turns for plan in case_plans)
    print("=" * 72)
    print("Personalized workspace benchmark")
    print(f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(
        f"planned: cases={run_progress.case_total} turns={planned_turns} "
        f"checks={run_progress.check_total}"
    )
    print(
        f"mode: smoke={smoke} only={normalized_only} "
        f"progress_interval={POLL_PROGRESS_INTERVAL:.1f}s "
        f"username={username or '<random>'} password={'***' if password else '<empty>'}"
    )
    print("=" * 72)

    if normalized_only == "delete" and not DELETE_GROUP_IMPLEMENTED:
        print("[workspace] warning: only=delete is reserved; delete suite is not implemented yet")

    if not case_plans:
        report = {
            "generated_at": datetime.now().isoformat(),
            "base_url": BASE_URL,
            "username": username or "",
            "smoke": smoke,
            "only": normalized_only,
            "summary": {
                "check_score": "0/0",
                "check_pass_rate": 0.0,
                "case_score": "0/0",
                "case_pass_rate": 0.0,
            },
            "cases": [],
            "warnings": ["delete suite is not implemented yet"] if normalized_only == "delete" else [],
        }
        output_path = RESULTS_DIR / _workspace_output_name(smoke=smoke, only=normalized_only)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved report: {output_path}")
        print("summary: cases_passed=0/0 checks_passed=0/0 elapsed=0.0s")
        return report

    status_code, _ = request_json("GET", "/health", timeout=5)
    if status_code != 200:
        raise AssertionError("Backend health check failed before benchmark run")

    benchmark_username = username or random_username()
    token, user = login_or_register(benchmark_username, password)
    headers = {"Authorization": f"Bearer {token}"}
    scenario = WorkspaceScenarioContext(headers=headers, smoke=smoke)

    cases: list[BenchmarkCase] = []
    for case_index, plan in enumerate(case_plans, start=1):
        case_started = _workspace_case_start(
            case_index=case_index,
            case_total=run_progress.case_total,
            case_key=plan.key,
        )
        case = WORKSPACE_CASE_RUNNERS[plan.code](
            scenario,
            plan=plan,
            case_index=case_index,
            case_total=run_progress.case_total,
        )
        cases.append(case)
        _record_workspace_case(
            case=case,
            case_index=case_index,
            case_total=run_progress.case_total,
            case_key=plan.key,
            started=case_started,
            progress=run_progress,
        )

    total_passed = 0
    total_checks = 0
    case_passed = 0
    for case in cases:
        print_case(case)
        passed, total = case.score
        total_passed += passed
        total_checks += total
        if case.passed:
            case_passed += 1

    report = {
        "generated_at": datetime.now().isoformat(),
        "base_url": BASE_URL,
        "username": benchmark_username,
        "smoke": smoke,
        "only": normalized_only,
        "summary": {
            "check_score": f"{total_passed}/{total_checks}",
            "check_pass_rate": (total_passed / total_checks) if total_checks else 0.0,
            "case_score": f"{case_passed}/{len(cases)}",
            "case_pass_rate": (case_passed / len(cases)) if cases else 0.0,
        },
        "cases": [
            {
                "code": case.code,
                "name": case.name,
                "score": f"{case.score[0]}/{case.score[1]}",
                "passed": case.passed,
                "notes": case.notes,
                "checks": [
                    {
                        "label": check.label,
                        "passed": check.passed,
                        "detail": check.detail,
                    }
                    for check in case.checks
                ],
            }
            for case in cases
        ],
    }
    output_path = RESULTS_DIR / _workspace_output_name(smoke=smoke, only=normalized_only)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved report: {output_path}")
    failed_cases = [f"{case.code}:{case.name}" for case in cases if not case.passed]
    failed_checks = [
        f"{case.code}:{check.label}"
        for case in cases
        for check in case.checks
        if not check.passed
    ]
    print(
        f"summary: cases_passed={case_passed}/{len(cases)} "
        f"checks_passed={total_passed}/{total_checks} "
        f"elapsed={_format_seconds(time.perf_counter() - benchmark_started)}"
    )
    if failed_cases:
        print("failed cases:")
        for item in failed_cases:
            print(f"  - {item}")
    if failed_checks:
        print("failed checks:")
        for item in failed_checks:
            print(f"  - {item}")
    return report


def main() -> None:
    args = parse_args()
    normalized_only = str(args.only or "all").strip().lower() or "all"
    if normalized_only != "all" and bool(args.smoke):
        print("[workspace] warning: --only overrides --smoke")
    smoke = bool(args.smoke) if normalized_only == "all" else False
    case_plans = _select_workspace_case_plans(smoke=smoke, only=normalized_only)
    planned_turns = sum(plan.turns for plan in case_plans)
    planned_checks = sum(plan.checks for plan in case_plans)
    if args.dry_run:
        print("=" * 72)
        print("Personalized workspace benchmark dry-run")
        print(f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(
            f"planned: cases={len(case_plans)} turns={planned_turns} "
            f"checks={planned_checks}"
        )
        print(
            f"mode: smoke={smoke} only={normalized_only} dry_run=True "
            f"progress_interval={max(1.0, float(args.progress_interval)):.1f}s "
            f"username={str(args.username or '').strip() or '<random>'}"
        )
        if normalized_only == "delete" and not DELETE_GROUP_IMPLEMENTED:
            print("[workspace] warning: only=delete is reserved; delete suite is not implemented yet")
        print("=" * 72)
        print("Planned personalized workspace benchmark cases:")
        for item in planned_case_names(case_plans):
            print(f"  - {item}")
        return

    report = run_benchmark(
        smoke=smoke,
        only=normalized_only,
        progress_interval=float(args.progress_interval),
        username=str(args.username or "").strip(),
        password=str(args.password or "workspace123"),
    )
    summary = report["summary"]
    print("\n" + "=" * 72)
    print("Personalized workspace benchmark summary")
    print("=" * 72)
    print(f"Check score: {summary['check_score']} ({summary['check_pass_rate']:.0%})")
    print(f"Case score:  {summary['case_score']} ({summary['case_pass_rate']:.0%})")
    failed_cases = [
        case for case in report.get("cases", [])
        if isinstance(case, dict) and not bool(case.get("passed"))
    ]
    raise SystemExit(1 if failed_cases else 0)


if __name__ == "__main__":
    main()
