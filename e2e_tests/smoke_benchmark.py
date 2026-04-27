"""
Fast end-to-end smoke benchmark for the default legal QA path.

This suite intentionally avoids dossier/workspace flows and focuses on
strict ordinary legal questions with visible per-case progress output.
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
RESULTS_DIR = Path("e2e_tests/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_CASE_TIMEOUT = 90.0
DEFAULT_PROGRESS_INTERVAL = 5.0
POLL_INTERVAL = 1.5
WORKSPACE_TERMS = (
    "当前案卷",
    "案卷图谱",
    "主体确认",
    "持续跟踪",
    "保存为案卷",
    "案卷记忆",
)


@dataclass
class CheckResult:
    label: str
    passed: bool
    detail: str = ""


@dataclass
class SmokeCaseResult:
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
        return bool(self.checks) and all(check.passed for check in self.checks)


@dataclass(frozen=True)
class SmokeCasePlan:
    code: str
    key: str
    name: str
    query: str
    checks: int


@dataclass(frozen=True)
class SmokeTurnProgress:
    case_index: int
    case_total: int
    case_key: str
    turn_index: int
    turn_total: int


@dataclass
class SmokeRunProgress:
    case_total: int
    check_total: int
    check_index: int = 0


SMOKE_CASE_PLANS = [
    SmokeCasePlan(
        "S1",
        "S1_article_87",
        "S1 article 87 legal QA stays on ordinary path",
        "劳动合同法第八十七条规定了什么？",
        6,
    ),
    SmokeCasePlan(
        "S2",
        "S2_illegal_dismissal",
        "S2 unlawful dismissal claims are answered directly",
        "如果单位违法解除劳动合同，劳动者可以主张什么？",
        5,
    ),
    SmokeCasePlan(
        "S3",
        "S3_unsigned_contract",
        "S3 unsigned contract question avoids absolute overclaiming",
        "不签劳动合同一定能主张双倍工资吗？",
        5,
    ),
    SmokeCasePlan(
        "S4",
        "S4_probation_dismissal",
        "S4 probation dismissal answer explains the reason requirement",
        "试用期被辞退，公司需要说明理由吗？",
        5,
    ),
    SmokeCasePlan(
        "S5",
        "S5_fake_statute",
        "S5 fake statute prompt should not be fabricated",
        "劳动合同法第三百二十五条规定了什么？",
        5,
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fast smoke benchmark for the default legal QA path"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned smoke cases and exit without network calls",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop after the first failing smoke case",
    )
    parser.add_argument(
        "--case-timeout",
        type=float,
        default=DEFAULT_CASE_TIMEOUT,
        help="maximum seconds to wait for one /dialog case; default 90",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=DEFAULT_PROGRESS_INTERVAL,
        help="seconds between polling progress logs; default 5",
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


def random_username(prefix: str = "smoke_bench_") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}{suffix}"


def request_json(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int = 20,
) -> tuple[int, dict[str, Any] | str]:
    url = path if path.startswith("http") else f"{BASE_URL}{path}"
    response = requests.request(
        method,
        url,
        headers=headers,
        json=json_body,
        timeout=timeout,
    )
    try:
        payload: dict[str, Any] | str = response.json()
    except ValueError:
        payload = response.text
    return response.status_code, payload


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
    raise AssertionError("Failed to register/login smoke benchmark user")


def create_conversation(headers: dict[str, str], *, title: str) -> dict[str, Any]:
    status_code, payload = request_json(
        "POST",
        "/conversations",
        headers=headers,
        json_body={"title": title},
        timeout=15,
    )
    if status_code >= 400 or not isinstance(payload, dict):
        raise AssertionError(f"Create conversation failed: HTTP {status_code} {payload}")
    conversation = payload.get("conversation") if isinstance(payload.get("conversation"), dict) else None
    if not conversation:
        raise AssertionError(f"Conversation payload missing: {payload}")
    return conversation


def _format_seconds(value: float | int) -> str:
    return f"{float(value):.1f}s"


def _preview_text(text: str, limit: int = 80) -> str:
    compact = " ".join(str(text or "").strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _status_label(status: str | None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "success"}:
        return "success"
    return normalized or "unknown"


def _workspace_terms_in(text: str) -> list[str]:
    found: list[str] = []
    for term in WORKSPACE_TERMS:
        if term in text and term not in found:
            found.append(term)
    return found


def _contains_any(text: str, *keywords: str) -> bool:
    lowered = str(text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _case_prefix(progress: SmokeTurnProgress) -> str:
    return (
        f"[smoke] case {progress.case_index}/{progress.case_total} "
        f"{progress.case_key}"
    )


def _build_turn_progress(
    *,
    case_index: int,
    case_total: int,
    case_key: str,
    turn_index: int,
    turn_total: int,
) -> SmokeTurnProgress:
    return SmokeTurnProgress(
        case_index=case_index,
        case_total=case_total,
        case_key=case_key,
        turn_index=turn_index,
        turn_total=turn_total,
    )


def _log_case_start(
    *,
    case_index: int,
    case_total: int,
    case_key: str,
) -> float:
    print(f"[smoke] case {case_index}/{case_total} {case_key} START")
    return time.perf_counter()


def _log_turn_send(progress: SmokeTurnProgress, query: str) -> None:
    print(
        f"{_case_prefix(progress)} turn {progress.turn_index}/{progress.turn_total} "
        f'SEND query="{_preview_text(query)}"'
    )


def _log_turn_polling(progress: SmokeTurnProgress, task_id: str) -> None:
    print(
        f"{_case_prefix(progress)} turn {progress.turn_index}/{progress.turn_total} "
        f"task_id={task_id} POLLING"
    )


def _log_turn_running(progress: SmokeTurnProgress, elapsed_s: float) -> None:
    print(
        f"{_case_prefix(progress)} turn {progress.turn_index}/{progress.turn_total} "
        f"still running elapsed={int(elapsed_s)}s"
    )


def _log_turn_done(progress: SmokeTurnProgress, result: dict[str, Any]) -> None:
    status = _status_label(result.get("status"))
    latency_s = float(result.get("latency_ms", 0) or 0) / 1000.0
    extra = ""
    error = str(result.get("error") or "").strip()
    if error:
        extra += f" error={error}"
    message = str(result.get("message") or "").strip()
    if message:
        extra += f' message="{_preview_text(message, 120)}"'
    print(
        f"{_case_prefix(progress)} turn {progress.turn_index}/{progress.turn_total} "
        f"DONE status={status}{extra} latency={_format_seconds(latency_s)}"
    )


def _check_key(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(label or "").strip().lower()).strip("_")
    return slug or "check"


def _log_check(progress: SmokeRunProgress, check: CheckResult) -> None:
    progress.check_index += 1
    status = "PASS" if check.passed else "FAIL"
    detail = f" reason={check.detail}" if check.detail else ""
    print(
        f"[smoke] check {progress.check_index}/{progress.check_total} "
        f"{_check_key(check.label)} {status}{detail}"
    )


def _log_case_end(
    result: SmokeCaseResult,
    *,
    case_index: int,
    case_total: int,
    case_key: str,
    started: float,
) -> None:
    passed, total = result.score
    elapsed_s = time.perf_counter() - started
    latency_s = float(result.notes.get("latency_ms", 0) or 0) / 1000.0
    if latency_s <= 0:
        latency_s = elapsed_s
    if result.passed:
        print(
            f"[smoke] case {case_index}/{case_total} PASS {case_key} "
            f"score={passed}/{total} latency={_format_seconds(latency_s)} "
            f"elapsed={_format_seconds(elapsed_s)}"
        )
        return
    failed_check = next((check for check in result.checks if not check.passed), None)
    reason = failed_check.label if failed_check else "case_failed"
    if failed_check and failed_check.detail:
        reason = f"{reason} | {failed_check.detail}"
    print(
        f"[smoke] case {case_index}/{case_total} FAIL {case_key} "
        f"score={passed}/{total} latency={_format_seconds(latency_s)} "
        f"elapsed={_format_seconds(elapsed_s)} reason={reason}"
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
    data["route_intent"] = str(data.get("route_intent") or data.get("route") or "")
    data["latency_ms"] = latency_ms
    data["workspace_terms_present"] = _workspace_terms_in(response_text)
    return data


def run_dialog(
    headers: dict[str, str],
    *,
    conversation_id: int,
    query: str,
    case_timeout: float,
    progress_interval: float,
    progress: SmokeTurnProgress,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    _log_turn_send(progress, query)
    status_code, payload = request_json(
        "POST",
        "/dialog",
        headers=headers,
        json_body={
            "user_input": query,
            "conversation_id": conversation_id,
            "force_current_case": False,
            "is_precise_mode": False,
            "proactive_graph_mode": True,
            "mode_consent_enabled": False,
        },
        timeout=20,
    )
    if status_code != 200:
        result = _normalize_dialog_result(
            {"status": "failed", "error": f"HTTP {status_code}", "message": str(payload)},
            fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            default_status="failed",
        )
        _log_turn_done(progress, result)
        return (payload if isinstance(payload, dict) else {"status": "failed"}), result

    first_payload = payload if isinstance(payload, dict) else {"status": "failed", "message": str(payload)}
    if first_payload.get("status") == "needs_confirmation":
        result = _normalize_dialog_result(
            first_payload,
            fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            default_status="needs_confirmation",
        )
        _log_turn_done(progress, result)
        return first_payload, result

    task_id = first_payload.get("task_id")
    if not task_id:
        result = _normalize_dialog_result(
            first_payload,
            fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            default_status=str(first_payload.get("status") or "completed"),
        )
        _log_turn_done(progress, result)
        return first_payload, result

    _log_turn_polling(progress, str(task_id))
    next_progress_log_s = max(1.0, float(progress_interval))
    while (time.perf_counter() - started) < case_timeout:
        time.sleep(POLL_INTERVAL)
        elapsed_s = time.perf_counter() - started
        if elapsed_s >= next_progress_log_s:
            _log_turn_running(progress, elapsed_s)
            next_progress_log_s += max(1.0, float(progress_interval))
        status_code, poll_payload = request_json(
            "GET",
            f"/dialog/status/{task_id}",
            headers=headers,
            timeout=15,
        )
        if status_code != 200 or not isinstance(poll_payload, dict):
            continue
        if poll_payload.get("status") == "completed":
            result = _normalize_dialog_result(
                poll_payload.get("data", {}) if isinstance(poll_payload.get("data"), dict) else {},
                fallback_latency_ms=int((time.perf_counter() - started) * 1000),
            )
            _log_turn_done(progress, result)
            return first_payload, result
        if poll_payload.get("status") == "failed":
            result = _normalize_dialog_result(
                {
                    "status": "failed",
                    "error": poll_payload.get("error", "task_failed"),
                    "message": poll_payload.get("message", ""),
                },
                fallback_latency_ms=int((time.perf_counter() - started) * 1000),
                default_status="failed",
            )
            _log_turn_done(progress, result)
            return first_payload, result

    result = _normalize_dialog_result(
        {"status": "failed", "error": "timeout", "message": f"case timeout after {int(case_timeout)} seconds"},
        fallback_latency_ms=int((time.perf_counter() - started) * 1000),
        default_status="failed",
    )
    _log_turn_done(progress, result)
    return first_payload, result


def _base_checks(first_payload: dict[str, Any], result: dict[str, Any]) -> list[CheckResult]:
    workspace_terms = result.get("workspace_terms_present") or []
    return [
        CheckResult("dialog completed", result.get("status") == "completed", str(result.get("status"))),
        CheckResult("no mode-consent prompt", first_payload.get("status") != "needs_confirmation", str(first_payload.get("status"))),
        CheckResult("no workspace terms", not workspace_terms, ", ".join(workspace_terms)),
    ]


def evaluate_case(plan: SmokeCasePlan, first_payload: dict[str, Any], result: dict[str, Any]) -> SmokeCaseResult:
    response_text = str(result.get("response") or "")
    sources_count = len(result.get("sources", []))
    checks = _base_checks(first_payload, result)

    if plan.code == "S1":
        checks.extend(
            [
                CheckResult("sources are present", sources_count >= 1, f"sources={sources_count}"),
                CheckResult("response cites article 87", _contains_any(response_text, "第八十七条", "87条"), response_text[:240]),
                CheckResult("response mentions compensation", _contains_any(response_text, "赔偿金", "二倍", "经济补偿标准的二倍"), response_text[:240]),
            ]
        )
    elif plan.code == "S2":
        checks.extend(
            [
                CheckResult("sources are present", sources_count >= 1, f"sources={sources_count}"),
                CheckResult("response covers available claims", _contains_any(response_text, "赔偿金", "恢复劳动关系", "继续履行", "经济补偿"), response_text[:240]),
            ]
        )
    elif plan.code == "S3":
        checks.extend(
            [
                CheckResult("response is not absolute", _contains_any(response_text, "不一定", "并非", "要看", "需结合"), response_text[:240]),
                CheckResult("response mentions the double-wage conditions", _contains_any(response_text, "双倍工资", "满一个月", "未满一年", "超过一年"), response_text[:240]),
            ]
        )
    elif plan.code == "S4":
        checks.extend(
            [
                CheckResult("sources are present", sources_count >= 1, f"sources={sources_count}"),
                CheckResult("response explains the reason requirement", _contains_any(response_text, "录用条件", "不符合录用条件", "说明理由", "举证"), response_text[:240]),
            ]
        )
    elif plan.code == "S5":
        checks.extend(
            [
                CheckResult("response rejects the fake statute", _contains_any(response_text, "不存在", "请核对", "现行", "未查询到"), response_text[:240]),
                CheckResult(
                    "response does not fabricate the fake statute content",
                    not _contains_any(response_text, "第三百二十五条明确", "第三百二十五条规定为", "第三百二十五条的内容"),
                    response_text[:240],
                ),
            ]
        )
    else:
        raise AssertionError(f"Unhandled smoke case code: {plan.code}")

    return SmokeCaseResult(
        code=plan.code,
        name=plan.name,
        checks=checks,
        notes={
            "latency_ms": int(result.get("latency_ms", 0) or 0),
            "route_intent": result.get("route_intent"),
            "sources_count": sources_count,
        },
    )


def run_benchmark(
    *,
    fail_fast: bool,
    case_timeout: float,
    progress_interval: float,
    username: str,
    password: str,
) -> tuple[dict[str, Any], int]:
    benchmark_started = time.perf_counter()
    run_progress = SmokeRunProgress(
        case_total=len(SMOKE_CASE_PLANS),
        check_total=sum(plan.checks for plan in SMOKE_CASE_PLANS),
    )
    planned_turns = len(SMOKE_CASE_PLANS)
    print("=" * 72)
    print("Smoke benchmark")
    print(f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(
        f"planned: cases={run_progress.case_total} turns={planned_turns} "
        f"checks={run_progress.check_total}"
    )
    print(
        f"mode: fail_fast={fail_fast} case_timeout={case_timeout:.1f}s "
        f"progress_interval={max(1.0, progress_interval):.1f}s "
        f"username={username or '<random>'} password={'***' if password else '<empty>'}"
    )
    print("=" * 72)

    status_code, _ = request_json("GET", "/health", timeout=5)
    if status_code != 200:
        raise AssertionError("Backend health check failed before smoke benchmark")

    benchmark_username = username or random_username()
    token, user = login_or_register(benchmark_username, password)
    headers = {"Authorization": f"Bearer {token}"}

    cases: list[SmokeCaseResult] = []
    exit_code = 0
    for case_index, plan in enumerate(SMOKE_CASE_PLANS, start=1):
        case_started = _log_case_start(
            case_index=case_index,
            case_total=run_progress.case_total,
            case_key=plan.key,
        )
        conversation = create_conversation(headers, title=f"smoke-{plan.code.lower()}")
        first_payload, result = run_dialog(
            headers,
            conversation_id=conversation["id"],
            query=plan.query,
            case_timeout=max(5.0, float(case_timeout)),
            progress_interval=max(1.0, float(progress_interval)),
            progress=_build_turn_progress(
                case_index=case_index,
                case_total=run_progress.case_total,
                case_key=plan.key,
                turn_index=1,
                turn_total=1,
            ),
        )
        case = evaluate_case(plan, first_payload, result)
        cases.append(case)
        for check in case.checks:
            _log_check(run_progress, check)
        _log_case_end(
            case,
            case_index=case_index,
            case_total=run_progress.case_total,
            case_key=plan.key,
            started=case_started,
        )
        if fail_fast and not case.passed:
            exit_code = 1
            print("[smoke] fail-fast enabled; stopping after the first failing case")
            break

    total_passed = 0
    total_checks = 0
    case_passed = 0
    for case in cases:
        passed, total = case.score
        total_passed += passed
        total_checks += total
        if case.passed:
            case_passed += 1
    if any(not case.passed for case in cases):
        exit_code = 1

    report = {
        "generated_at": datetime.now().isoformat(),
        "base_url": BASE_URL,
        "username": benchmark_username,
        "user": user,
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
    output_path = RESULTS_DIR / "smoke_benchmark_report.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
    return report, exit_code


def main() -> None:
    args = parse_args()
    planned_turns = len(SMOKE_CASE_PLANS)
    planned_checks = sum(plan.checks for plan in SMOKE_CASE_PLANS)
    if args.dry_run:
        print("=" * 72)
        print("Smoke benchmark dry-run")
        print(f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(
            f"planned: cases={len(SMOKE_CASE_PLANS)} turns={planned_turns} "
            f"checks={planned_checks}"
        )
        print(
            f"mode: fail_fast={bool(args.fail_fast)} dry_run=True "
            f"case_timeout={max(5.0, float(args.case_timeout)):.1f}s "
            f"progress_interval={max(1.0, float(args.progress_interval)):.1f}s "
            f"username={str(args.username or '').strip() or '<random>'}"
        )
        print("=" * 72)
        print("Planned smoke benchmark cases:")
        for plan in SMOKE_CASE_PLANS:
            print(f"  - {plan.name}")
        return

    report, exit_code = run_benchmark(
        fail_fast=bool(args.fail_fast),
        case_timeout=max(5.0, float(args.case_timeout)),
        progress_interval=max(1.0, float(args.progress_interval)),
        username=str(args.username or "").strip(),
        password=str(args.password or "workspace123"),
    )
    summary = report["summary"]
    print("\n" + "=" * 72)
    print("Smoke benchmark summary")
    print("=" * 72)
    print(f"Check score: {summary['check_score']} ({summary['check_pass_rate']:.0%})")
    print(f"Case score:  {summary['case_score']} ({summary['case_pass_rate']:.0%})")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
