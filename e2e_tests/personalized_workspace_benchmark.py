"""
API-level regression benchmark for the H2-H5 personalized workspace flow.

This benchmark is intentionally separate from quality_benchmark.py:
  - quality_benchmark.py focuses on the legacy ordinary legal-QA / general consult path
  - personalized_workspace_benchmark.py focuses on mode consent, dossier workflow,
    dossier memory, participant resolution, and archive semantics
"""
from __future__ import annotations

import argparse
import io
import json
import random
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

BASE_URL = "http://127.0.0.1:8000"
POLL_INTERVAL = 1.5
POLL_TIMEOUT = 120
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
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
        return (first if isinstance(first, dict) else {"status": "failed"}), result

    first_payload = first if isinstance(first, dict) else {"status": "failed", "message": str(first)}
    if (
        first_payload.get("status") == "needs_confirmation"
        and auto_confirm_boundary
        and first_payload.get("action") == "suggest_new_case"
        and not force_current_case
    ):
        return run_dialog(
            headers,
            conversation_id=conversation_id,
            text=text,
            force_current_case=True,
            mode_consent_enabled=mode_consent_enabled,
            proactive_graph_mode=proactive_graph_mode,
            auto_confirm_boundary=False,
        )

    if first_payload.get("status") == "needs_confirmation":
        result = _normalize_dialog_result(
            first_payload,
            fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            default_status="needs_confirmation",
        )
        return first_payload, result

    task_id = first_payload.get("task_id")
    if not task_id:
        result = _normalize_dialog_result(
            first_payload,
            fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            default_status=str(first_payload.get("status") or "completed"),
        )
        return first_payload, result

    for _ in range(int(POLL_TIMEOUT / POLL_INTERVAL)):
        time.sleep(POLL_INTERVAL)
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
            return first_payload, result

    result = _normalize_dialog_result(
        {"status": "failed", "error": "timeout"},
        fallback_latency_ms=int((time.perf_counter() - started) * 1000),
        default_status="failed",
    )
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
    nodes = (
        payload.get("graph", {}).get("nodes", [])
        if isinstance(payload.get("graph"), dict)
        else []
    )
    edges = (
        payload.get("graph", {}).get("edges", [])
        if isinstance(payload.get("graph"), dict)
        else []
    )
    return not nodes and not edges


def get_conversation_record(snapshot: dict[str, Any]) -> dict[str, Any]:
    conversation = snapshot.get("conversation")
    return conversation if isinstance(conversation, dict) else {}


def contains_any(text: str, *keywords: str) -> bool:
    lowered = str(text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Personalized workspace benchmark for H2-H5 regression"
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="run a reduced regression set without participant resolution / archive checks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned benchmark cases and exit without network calls",
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


def planned_case_names(smoke: bool) -> list[str]:
    cases = [
        "A LEGAL_QA does not write structured case or graph",
        "B temporary mode keeps plain messages but skips structured persistence",
        "C tracked mode writes structured case and graph",
        "D dossier creation and tracked conversation attachment",
        "E dossier-bound conversation defaults to tracked",
        "F dossier memory query answers from dossier context",
        "G explicit law query in dossier conversation still behaves like legal QA",
    ]
    if not smoke:
        cases.extend(
            [
                "H participant candidates and confirmation API flow",
                "I archive dossier keeps historical data accessible",
            ]
        )
    return cases


def run_benchmark(*, smoke: bool, username: str, password: str) -> dict[str, Any]:
    status_code, _ = request_json("GET", "/health", timeout=5)
    if status_code != 200:
        raise AssertionError("Backend health check failed before benchmark run")

    benchmark_username = username or random_username()
    token, user = login_or_register(benchmark_username, password)
    headers = {"Authorization": f"Bearer {token}"}

    cases: list[BenchmarkCase] = []
    context: dict[str, Any] = {
        "username": benchmark_username,
        "user": user,
    }

    legal_question = "劳动合同法第八十七条规定了什么？"
    case_message = "我补充一下案情：我在A公司工作两年，月薪12000，公司一直没签劳动合同，2024年4月4日公司口头辞退我。"
    followup_fact_message = "我再补充一下案情：后来用人单位的人事今天又通知我尽快办离职，老板还说A公司不会给我任何补偿。"
    memory_question = "这个案卷里之前提到的公司是谁？"

    # A
    conversation_a = create_conversation(headers, title="workspace-A-legal-qa")
    first_a, result_a = run_dialog(
        headers,
        conversation_id=conversation_a["id"],
        text=legal_question,
        mode_consent_enabled=False,
        proactive_graph_mode=True,
    )
    snapshot_a = get_conversation_snapshot(headers, conversation_a["id"])
    graph_a = get_conversation_graph(headers, conversation_a["id"])
    record_a = get_conversation_record(snapshot_a)
    cases.append(
        BenchmarkCase(
            code="A",
            name="LEGAL_QA pure legal question does not enter structured workspace state",
            notes={
                "route_intent": result_a.get("route_intent"),
                "sources_count": len(result_a.get("sources", [])),
            },
            checks=[
                CheckResult("dialog completed", result_a.get("status") == "completed", str(result_a.get("status"))),
                CheckResult(
                    "no mode-consent prompt",
                    first_a.get("status") != "needs_confirmation",
                    str(first_a.get("status")),
                ),
                CheckResult(
                    "route is not CASE_UPDATE",
                    str(result_a.get("route_intent") or "").upper() != "CASE_UPDATE",
                    str(result_a.get("route_intent") or ""),
                ),
                CheckResult("case_summary stays empty", not (record_a.get("case_summary") or "").strip()),
                CheckResult("case_type stays empty", not (record_a.get("case_type") or "").strip()),
                CheckResult("case_state stays empty", record_a.get("case_state") in (None, {}, "")),
                CheckResult("conversation graph stays empty", is_empty_graph(graph_a)),
            ],
        )
    )

    # B
    conversation_b = create_conversation(headers, title="workspace-B-temporary")
    first_b_status, first_b_payload = start_dialog(
        headers,
        conversation_id=conversation_b["id"],
        text=case_message,
        mode_consent_enabled=True,
    )
    first_b = first_b_payload if isinstance(first_b_payload, dict) else {"status": f"HTTP {first_b_status}"}
    consent_b = set_conversation_mode_consent(
        headers,
        conversation_b["id"],
        mode="CASE_ANALYSIS",
        consent="temporary",
        reason="workspace_benchmark_temporary",
    )
    _, result_b = run_dialog(
        headers,
        conversation_id=conversation_b["id"],
        text=case_message,
        mode_consent_enabled=False,
    )
    snapshot_b = get_conversation_snapshot(headers, conversation_b["id"])
    graph_b = get_conversation_graph(headers, conversation_b["id"])
    record_b = get_conversation_record(snapshot_b)
    cases.append(
        BenchmarkCase(
            code="B",
            name="temporary consent keeps plain messages but skips structured persistence",
            notes={"route_intent": result_b.get("route_intent")},
            checks=[
                CheckResult("mode consent prompt appears", first_b.get("status") == "needs_confirmation", str(first_b)),
                CheckResult("mode consent action is mode_consent", first_b.get("action") == "mode_consent", str(first_b.get("action"))),
                CheckResult("temporary consent stored", consent_b.get("persistence_consent") == "temporary", json.dumps(consent_b, ensure_ascii=False)),
                CheckResult("conversation mode stored as CASE_ANALYSIS", consent_b.get("conversation_mode") == "CASE_ANALYSIS", json.dumps(consent_b, ensure_ascii=False)),
                CheckResult("dialog completed", result_b.get("status") == "completed", str(result_b.get("status"))),
                CheckResult("case_summary stays empty", not (record_b.get("case_summary") or "").strip()),
                CheckResult("case_type stays empty", not (record_b.get("case_type") or "").strip()),
                CheckResult("case_state stays empty", record_b.get("case_state") in (None, {}, "")),
                CheckResult("conversation graph stays empty", is_empty_graph(graph_b)),
            ],
        )
    )

    # C
    conversation_c = create_conversation(headers, title="workspace-C-tracked")
    first_c_status, first_c_payload = start_dialog(
        headers,
        conversation_id=conversation_c["id"],
        text=case_message,
        mode_consent_enabled=True,
    )
    first_c = first_c_payload if isinstance(first_c_payload, dict) else {"status": f"HTTP {first_c_status}"}
    consent_c = set_conversation_mode_consent(
        headers,
        conversation_c["id"],
        mode="TRACKED_CASE",
        consent="tracked",
        reason="workspace_benchmark_tracked",
    )
    _, result_c = run_dialog(
        headers,
        conversation_id=conversation_c["id"],
        text=case_message,
        mode_consent_enabled=False,
    )
    snapshot_c = get_conversation_snapshot(headers, conversation_c["id"])
    record_c = get_conversation_record(snapshot_c)
    graph_c = retry_value(
        lambda: get_conversation_graph(headers, conversation_c["id"]),
        lambda payload: not is_empty_graph(payload),
        attempts=4,
        sleep_s=2.0,
    )
    cases.append(
        BenchmarkCase(
            code="C",
            name="tracked consent writes structured case and graph",
            notes={
                "route_intent": result_c.get("route_intent"),
                "graph_stats": graph_c.get("stats") or graph_c.get("graph", {}).get("stats", {}),
            },
            checks=[
                CheckResult("mode consent prompt appears", first_c.get("status") == "needs_confirmation", str(first_c)),
                CheckResult("tracked consent stored", consent_c.get("persistence_consent") == "tracked", json.dumps(consent_c, ensure_ascii=False)),
                CheckResult("conversation mode stored as TRACKED_CASE", consent_c.get("conversation_mode") == "TRACKED_CASE", json.dumps(consent_c, ensure_ascii=False)),
                CheckResult("dialog completed", result_c.get("status") == "completed", str(result_c.get("status"))),
                CheckResult("case_summary is populated", bool((record_c.get("case_summary") or "").strip()), record_c.get("case_summary", "")),
                CheckResult(
                    "case_type or case_state is populated",
                    bool((record_c.get("case_type") or "").strip()) or record_c.get("case_state") not in (None, {}, ""),
                    json.dumps(
                        {
                            "case_type": record_c.get("case_type"),
                            "case_state_present": record_c.get("case_state") not in (None, {}, ""),
                        },
                        ensure_ascii=False,
                    ),
                ),
                CheckResult("conversation graph is not empty", not is_empty_graph(graph_c)),
            ],
        )
    )
    context["tracked_conversation_id"] = conversation_c["id"]

    # D
    dossier = create_dossier(
        headers,
        title="劳动争议测试案卷",
        case_type="劳动争议",
        summary="workspace benchmark dossier",
    )
    dossier = update_dossier_participant_aliases(
        headers,
        dossier["id"],
        primary_label="我",
        primary_role="劳动者",
        opposing_label="A公司",
        opposing_role="用人单位",
    )
    attached_c = attach_conversation_to_dossier(headers, conversation_c["id"], dossier["id"])
    dossier_list = list_dossiers(headers)
    dossier_detail = get_dossier(headers, dossier["id"])
    snapshot_c_after_attach = get_conversation_snapshot(headers, conversation_c["id"])
    record_c_after_attach = get_conversation_record(snapshot_c_after_attach)
    cases.append(
        BenchmarkCase(
            code="D",
            name="dossier creation and tracked conversation binding",
            notes={"dossier_id": dossier["id"]},
            checks=[
                CheckResult(
                    "dossier appears in dossier list",
                    any(int(item.get("id") or 0) == dossier["id"] for item in dossier_list),
                    f"list_count={len(dossier_list)}",
                ),
                CheckResult(
                    "attach-dossier returns conversation binding",
                    int(attached_c.get("dossier_id") or 0) == dossier["id"],
                    json.dumps(attached_c, ensure_ascii=False),
                ),
                CheckResult(
                    "conversation snapshot shows dossier_id",
                    int(record_c_after_attach.get("dossier_id") or 0) == dossier["id"],
                    json.dumps(record_c_after_attach, ensure_ascii=False),
                ),
                CheckResult(
                    "dossier detail shows bound conversation",
                    any(int(item.get("id") or 0) == conversation_c["id"] for item in dossier_detail.get("conversations", [])),
                    f"conversation_count={dossier_detail.get('conversation_count')}",
                ),
                CheckResult(
                    "participant aliases saved on dossier",
                    str(dossier_detail.get("participant_aliases", {}).get("opposing_party", {}).get("label") or "") == "A公司",
                    json.dumps(dossier_detail.get("participant_aliases", {}), ensure_ascii=False),
                ),
            ],
        )
    )
    context["dossier_id"] = dossier["id"]

    # E
    dossier_conversation = create_conversation(
        headers,
        title="workspace-E-dossier-bound",
        dossier_id=dossier["id"],
    )
    cases.append(
        BenchmarkCase(
            code="E",
            name="creating a conversation from dossier binds tracked defaults immediately",
            notes={"conversation_id": dossier_conversation["id"]},
            checks=[
                CheckResult("conversation is bound to dossier", int(dossier_conversation.get("dossier_id") or 0) == dossier["id"], json.dumps(dossier_conversation, ensure_ascii=False)),
                CheckResult("conversation mode defaults to TRACKED_CASE", dossier_conversation.get("conversation_mode") == "TRACKED_CASE", json.dumps(dossier_conversation, ensure_ascii=False)),
                CheckResult("persistence consent defaults to tracked", dossier_conversation.get("persistence_consent") == "tracked", json.dumps(dossier_conversation, ensure_ascii=False)),
                CheckResult("mode reason is created_from_dossier", dossier_conversation.get("mode_reason") == "created_from_dossier", json.dumps(dossier_conversation, ensure_ascii=False)),
            ],
        )
    )
    context["dossier_conversation_id"] = dossier_conversation["id"]

    # F
    _, result_f = run_dialog(
        headers,
        conversation_id=dossier_conversation["id"],
        text=memory_question,
        mode_consent_enabled=False,
    )
    text_f = str(result_f.get("response") or "")
    cases.append(
        BenchmarkCase(
            code="F",
            name="dossier memory query answers from dossier context / graph context",
            notes={
                "route_intent": result_f.get("route_intent"),
                "sources_count": len(result_f.get("sources", [])),
            },
            checks=[
                CheckResult("dialog completed", result_f.get("status") == "completed", str(result_f.get("status"))),
                CheckResult("response identifies the company", contains_any(text_f, "A公司"), text_f[:240]),
                CheckResult(
                    "response avoids unrelated criminal / pollution RAG",
                    not contains_any(text_f, "污染环境", "刑法", "危险驾驶"),
                    text_f[:240],
                ),
            ],
        )
    )

    # G
    _, result_g = run_dialog(
        headers,
        conversation_id=dossier_conversation["id"],
        text=legal_question,
        mode_consent_enabled=False,
    )
    text_g = str(result_g.get("response") or "")
    cases.append(
        BenchmarkCase(
            code="G",
            name="explicit law query in dossier conversation still behaves like legal QA",
            notes={
                "route_intent": result_g.get("route_intent"),
                "sources_count": len(result_g.get("sources", [])),
            },
            checks=[
                CheckResult("dialog completed", result_g.get("status") == "completed", str(result_g.get("status"))),
                CheckResult("response cites article 87", contains_any(text_g, "第八十七条", "87条"), text_g[:240]),
                CheckResult("response discusses unlawful termination compensation", contains_any(text_g, "赔偿金", "违法解除"), text_g[:240]),
                CheckResult("sources are present", len(result_g.get("sources", [])) >= 1, f"sources={len(result_g.get('sources', []))}"),
                CheckResult("response is not polluted by dossier company label", "A公司" not in text_g, text_g[:240]),
            ],
        )
    )

    if not smoke:
        # H
        conversation_h = create_conversation(
            headers,
            title="workspace-H-participant-resolution",
            dossier_id=dossier["id"],
        )
        _, result_h_fact = run_dialog(
            headers,
            conversation_id=conversation_h["id"],
            text=followup_fact_message,
            mode_consent_enabled=False,
        )
        candidates_payload = retry_value(
            lambda: get_dossier_participant_candidates(headers, dossier["id"]),
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
                if isinstance(item, dict)
                and item.get("entity_type") == "organization"
            ),
            candidates[0] if candidates else None,
        )
        resolve_result: dict[str, Any] | None = None
        if isinstance(candidate, dict):
            resolve_result = resolve_dossier_participant(
                headers,
                dossier["id"],
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
        participants_payload = get_dossier_participants(headers, dossier["id"])
        dossier_graph_after_resolve = get_dossier_graph(headers, dossier["id"])
        participants_list = (
            participants_payload.get("participants")
            if isinstance(participants_payload.get("participants"), list)
            else []
        )
        mentions_list = (
            participants_payload.get("mentions")
            if isinstance(participants_payload.get("mentions"), list)
            else []
        )
        graph_nodes = (
            dossier_graph_after_resolve.get("graph", {}).get("nodes", [])
            if isinstance(dossier_graph_after_resolve.get("graph"), dict)
            else []
        )
        resolved_label = (
            str(candidate.get("suggested_label") or "A公司")
            if isinstance(candidate, dict)
            else "A公司"
        )
        cases.append(
            BenchmarkCase(
                code="H",
                name="participant candidates and confirmation API flow",
                notes={
                    "fact_route_intent": result_h_fact.get("route_intent"),
                    "candidate_count": len(candidates),
                },
                checks=[
                    CheckResult("fact update dialog completed", result_h_fact.get("status") == "completed", str(result_h_fact.get("status"))),
                    CheckResult("participant candidates endpoint returns candidates", len(candidates) >= 1, f"candidate_count={len(candidates)}"),
                    CheckResult("resolve endpoint succeeded", isinstance(resolve_result, dict) and resolve_result.get("status") == "success", json.dumps(resolve_result or {}, ensure_ascii=False)),
                    CheckResult("confirmed participant is persisted", len(participants_list) >= 1, f"participants={len(participants_list)}"),
                    CheckResult("participant mentions are persisted", len(mentions_list) >= 1, f"mentions={len(mentions_list)}"),
                    CheckResult(
                        "dossier graph exposes participant mappings",
                        len(dossier_graph_after_resolve.get("participant_mappings", []) or []) >= 1,
                        f"participant_mappings={len(dossier_graph_after_resolve.get('participant_mappings', []) or [])}",
                    ),
                    CheckResult(
                        "dossier graph nodes can show resolved display_label",
                        any(str(node.get("display_label") or "") == resolved_label for node in graph_nodes if isinstance(node, dict)),
                        f"resolved_label={resolved_label}",
                    ),
                ],
            )
        )

        # I
        participants_before_archive = len(participants_list)
        archived = archive_dossier(headers, dossier["id"])
        dossier_after_archive = get_dossier(headers, dossier["id"])
        participants_after_archive = get_dossier_participants(headers, dossier["id"])
        archived_participant_count = len(
            participants_after_archive.get("participants")
            if isinstance(participants_after_archive.get("participants"), list)
            else []
        )
        archived_write_status, archived_write_payload = request_json(
            "POST",
            f"/dossiers/{dossier['id']}/participants",
            headers=headers,
            json_body={
                "label": "归档后新增主体",
                "role": "测试",
                "party_side": "unknown",
                "entity_type": "unknown",
                "description": "should fail",
            },
        )
        cases.append(
            BenchmarkCase(
                code="I",
                name="archive dossier keeps historical data and blocks new writes",
                notes={"dossier_id": dossier["id"]},
                checks=[
                    CheckResult("archive endpoint returns archived dossier", str(archived.get("status") or "") == "archived", json.dumps(archived, ensure_ascii=False)),
                    CheckResult("archived dossier remains readable", str(dossier_after_archive.get("status") or "") == "archived", json.dumps(dossier_after_archive, ensure_ascii=False)),
                    CheckResult("participants remain after archive", archived_participant_count == participants_before_archive, f"before={participants_before_archive} after={archived_participant_count}"),
                    CheckResult("archived dossier rejects participant writes", archived_write_status == 409, f"status={archived_write_status} payload={archived_write_payload}"),
                ],
            )
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
    output_name = (
        "personalized_workspace_benchmark_smoke.json"
        if smoke
        else "personalized_workspace_benchmark_report.json"
    )
    output_path = RESULTS_DIR / output_name
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved report: {output_path}")
    return report


def main() -> None:
    args = parse_args()
    if args.dry_run:
        print("Planned personalized workspace benchmark cases:")
        for item in planned_case_names(args.smoke):
            print(f"  - {item}")
        return

    report = run_benchmark(
        smoke=bool(args.smoke),
        username=str(args.username or "").strip(),
        password=str(args.password or "workspace123"),
    )
    summary = report["summary"]
    print("\n" + "=" * 72)
    print("Personalized workspace benchmark summary")
    print("=" * 72)
    print(f"Check score: {summary['check_score']} ({summary['check_pass_rate']:.0%})")
    print(f"Case score:  {summary['case_score']} ({summary['case_pass_rate']:.0%})")


if __name__ == "__main__":
    main()
