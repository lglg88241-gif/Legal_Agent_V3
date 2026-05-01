"""
Targeted benchmark orchestrator.

Modern "full" regression is defined as a sequence of smaller independent
benchmark suites instead of one monolithic script.
"""
from __future__ import annotations

import argparse
import builtins
import json
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    return builtins.print(*args, **kwargs)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROGRESS_INTERVAL = 5.0
DEFAULT_SUITE_TIMEOUT = 0.0
DEFAULT_READINESS_TIMEOUT = 5.0
TERMINATE_TIMEOUT_SECONDS = 5.0
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
REQUIRED_OPENAPI_PATHS: tuple[str, ...] = (
    "/dialog",
    "/conversations",
    "/conversations/search",
    "/conversations/batch",
    "/conversations/{conversation_id}/attach-dossier",
    "/conversations/{conversation_id}/mode-consent",
    "/dossiers",
    "/dossiers/{dossier_id}",
    "/dossiers/{dossier_id}/graph",
    "/dossiers/{dossier_id}/participants",
    "/dossiers/{dossier_id}/participant-candidates",
    "/dossiers/{dossier_id}/generate-summary",
    "/dossiers/{dossier_id}/hard",
    "/dossiers/hard/{dossier_id}",
)
REQUIRED_OPENAPI_METHODS: dict[str, tuple[str, ...]] = {
    "/dialog": ("post",),
    "/dossiers": ("get", "post"),
    "/dossiers/{dossier_id}/hard": ("delete",),
    "/dossiers/hard/{dossier_id}": ("delete",),
}

PROFILE_SUITE_NAMES: dict[str, tuple[str, ...]] = {
    "quick": (
        "smoke",
        "workspace_baseline",
        "workspace_temporary",
    ),
    "full": (
        "smoke",
        "legal_qa",
        "workspace_baseline",
        "workspace_temporary",
        "workspace_tracked",
        "workspace_dossier",
        "workspace_memory",
        "workspace_participant",
        "workspace_archive",
        "workspace_delete",
    ),
    "workspace": (
        "workspace_baseline",
        "workspace_temporary",
        "workspace_tracked",
        "workspace_dossier",
        "workspace_memory",
        "workspace_participant",
        "workspace_archive",
        "workspace_delete",
    ),
    "legacy": ("legacy_quality",),
}

SUITE_ORDER: tuple[str, ...] = (
    "pytest",
    "smoke",
    "legal_qa",
    "workspace_baseline",
    "workspace_temporary",
    "workspace_tracked",
    "workspace_dossier",
    "workspace_memory",
    "workspace_participant",
    "workspace_archive",
    "workspace_delete",
    "legacy_quality",
)


@dataclass(frozen=True)
class SuitePlan:
    key: str
    command: list[str]


@dataclass
class SuiteResult:
    name: str
    command: str
    status: str
    exit_code: int | None
    elapsed_seconds: float


@dataclass
class ReadinessCheckResult:
    label: str
    passed: bool
    detail: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run modern benchmark suites in a targeted, orchestrated order."
    )
    parser.add_argument(
        "--profile",
        default="quick",
        choices=("quick", "full", "workspace", "legacy"),
        help="suite profile to run; default quick",
    )
    parser.add_argument(
        "--only",
        default="",
        help=(
            "run exactly one suite: smoke, legal_qa, workspace_baseline, "
            "workspace_temporary, workspace_tracked, workspace_dossier, "
            "workspace_memory, workspace_participant, workspace_archive, "
            "workspace_delete, legacy_quality"
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print all available suites and commands, then exit",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop after the first failing or timed-out suite",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned suites and commands without executing them",
    )
    parser.add_argument(
        "--include-pytest",
        action="store_true",
        help="prepend pytest to the planned suites when not using --only",
    )
    parser.add_argument(
        "--include-legacy-quality",
        action="store_true",
        help="append the legacy quality benchmark when not using --only",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=DEFAULT_PROGRESS_INTERVAL,
        help="progress interval passed to child benchmark suites when supported; default 5",
    )
    parser.add_argument(
        "--suite-timeout",
        type=float,
        default=DEFAULT_SUITE_TIMEOUT,
        help="maximum seconds allowed for each suite; 0 means no timeout",
    )
    parser.add_argument(
        "--check-readiness",
        action="store_true",
        help="check the live backend OpenAPI and required routes before running suites",
    )
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help="base URL used for readiness checks; default http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--readiness-timeout",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT,
        help="timeout in seconds for readiness HTTP requests; default 5",
    )
    parser.add_argument(
        "--report-json",
        default="",
        help="optional path for a JSON runner report",
    )
    parser.add_argument(
        "--username",
        default="",
        help="optional explicit benchmark username passed to child suites",
    )
    parser.add_argument(
        "--password",
        default="workspace123",
        help="password passed to child suites that support explicit users",
    )
    return parser.parse_args()


def _format_seconds(value: float | int) -> str:
    return f"{float(value):.1f}s"


def _format_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _python_command(*parts: str) -> list[str]:
    return [sys.executable, *parts]


def _workspace_command(
    *,
    only: str,
    username: str,
    password: str,
    progress_interval: float,
) -> list[str]:
    command = _python_command(
        "e2e_tests/personalized_workspace_benchmark.py",
        "--only",
        only,
        "--progress-interval",
        str(progress_interval),
    )
    if username:
        command.extend(["--username", username])
    if password:
        command.extend(["--password", password])
    return command


def _smoke_command(
    *,
    username: str,
    password: str,
    progress_interval: float,
) -> list[str]:
    command = _python_command(
        "e2e_tests/smoke_benchmark.py",
        "--progress-interval",
        str(progress_interval),
    )
    if username:
        command.extend(["--username", username])
    if password:
        command.extend(["--password", password])
    return command


def _legal_qa_command(
    *,
    username: str,
    password: str,
    progress_interval: float,
) -> list[str]:
    command = _python_command(
        "e2e_tests/legal_qa_benchmark.py",
        "--progress-interval",
        str(progress_interval),
    )
    if username:
        command.extend(["--username", username])
    if password:
        command.extend(["--password", password])
    return command


def _legacy_quality_command() -> list[str]:
    return _python_command("e2e_tests/quality_benchmark.py")


def _pytest_command() -> list[str]:
    return _python_command("-m", "pytest", "tests", "-q")


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def build_suite_catalog(
    *,
    progress_interval: float,
    username: str,
    password: str,
) -> dict[str, SuitePlan]:
    return {
        "pytest": SuitePlan("pytest", _pytest_command()),
        "smoke": SuitePlan(
            "smoke",
            _smoke_command(
                username=username,
                password=password,
                progress_interval=progress_interval,
            ),
        ),
        "legal_qa": SuitePlan(
            "legal_qa",
            _legal_qa_command(
                username=username,
                password=password,
                progress_interval=progress_interval,
            ),
        ),
        "workspace_baseline": SuitePlan(
            "workspace_baseline",
            _workspace_command(
                only="baseline",
                username=username,
                password=password,
                progress_interval=progress_interval,
            ),
        ),
        "workspace_temporary": SuitePlan(
            "workspace_temporary",
            _workspace_command(
                only="temporary",
                username=username,
                password=password,
                progress_interval=progress_interval,
            ),
        ),
        "workspace_tracked": SuitePlan(
            "workspace_tracked",
            _workspace_command(
                only="tracked",
                username=username,
                password=password,
                progress_interval=progress_interval,
            ),
        ),
        "workspace_dossier": SuitePlan(
            "workspace_dossier",
            _workspace_command(
                only="dossier",
                username=username,
                password=password,
                progress_interval=progress_interval,
            ),
        ),
        "workspace_memory": SuitePlan(
            "workspace_memory",
            _workspace_command(
                only="memory",
                username=username,
                password=password,
                progress_interval=progress_interval,
            ),
        ),
        "workspace_participant": SuitePlan(
            "workspace_participant",
            _workspace_command(
                only="participant",
                username=username,
                password=password,
                progress_interval=progress_interval,
            ),
        ),
        "workspace_archive": SuitePlan(
            "workspace_archive",
            _workspace_command(
                only="archive",
                username=username,
                password=password,
                progress_interval=progress_interval,
            ),
        ),
        "workspace_delete": SuitePlan(
            "workspace_delete",
            _workspace_command(
                only="delete",
                username=username,
                password=password,
                progress_interval=progress_interval,
            ),
        ),
        "legacy_quality": SuitePlan("legacy_quality", _legacy_quality_command()),
    }


def select_suite_names(
    *,
    profile: str,
    only: str,
    include_pytest: bool,
    include_legacy_quality: bool,
    available_names: set[str],
) -> list[str]:
    normalized_only = str(only or "").strip()
    if normalized_only:
        if normalized_only not in available_names:
            valid = ", ".join(SUITE_ORDER)
            raise SystemExit(f"Unsupported --only value: {normalized_only}. Valid values: {valid}")
        return [normalized_only]

    selected = list(PROFILE_SUITE_NAMES[profile])
    if include_pytest:
        selected.insert(0, "pytest")
    if include_legacy_quality and profile != "legacy":
        selected.append("legacy_quality")
    return _dedupe_preserve_order(selected)


def _write_report(path_str: str, report: dict[str, Any]) -> None:
    target = Path(path_str)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[runner] report_json={target}")


def _build_report(
    *,
    profile: str,
    dry_run: bool,
    fail_fast: bool,
    started_at: str,
    finished_at: str,
    elapsed_seconds: float,
    suite_results: list[SuiteResult],
) -> dict[str, Any]:
    passed_suite_names = [item.name for item in suite_results if item.status == "passed"]
    failed_suite_names = [item.name for item in suite_results if item.status == "failed"]
    timeout_suite_names = [item.name for item in suite_results if item.status == "timeout"]
    skipped_suite_names = [item.name for item in suite_results if item.status == "skipped"]
    return {
        "profile": profile,
        "dry_run": dry_run,
        "fail_fast": fail_fast,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "total_suites": len(suite_results),
        "passed_suites": len(passed_suite_names),
        "failed_suites": len(failed_suite_names),
        "timeout_suites": len(timeout_suite_names),
        "skipped_suites": len(skipped_suite_names),
        "passed_suite_names": passed_suite_names,
        "failed_suite_names": failed_suite_names,
        "timeout_suite_names": timeout_suite_names,
        "skipped_suite_names": skipped_suite_names,
        "suites": [asdict(item) for item in suite_results],
    }


def _print_overall_progress(
    *,
    completed: int,
    total: int,
    overall_started: float,
) -> None:
    elapsed = time.perf_counter() - overall_started
    avg = elapsed / completed if completed else 0.0
    eta = avg * max(total - completed, 0)
    pct = (100.0 * completed / total) if total else 100.0
    print(
        f"[runner] overall {completed}/{total} {pct:.1f}% "
        f"elapsed={_format_seconds(elapsed)} avg={_format_seconds(avg)} "
        f"eta={_format_seconds(eta)}"
    )


def _normalize_api_base_url(api_base_url: str) -> str:
    return str(api_base_url or DEFAULT_API_BASE_URL).strip().rstrip("/") or DEFAULT_API_BASE_URL


def _run_readiness_checks(
    *,
    api_base_url: str,
    readiness_timeout: float,
) -> tuple[bool, list[ReadinessCheckResult]]:
    base_url = _normalize_api_base_url(api_base_url)
    timeout_seconds = max(1.0, float(readiness_timeout))
    checks: list[ReadinessCheckResult] = []
    openapi_url = f"{base_url}/openapi.json"
    paths: dict[str, Any] = {}

    try:
        response = requests.get(openapi_url, timeout=timeout_seconds)
        if response.status_code != 200:
            checks.append(
                ReadinessCheckResult(
                    label="openapi reachable",
                    passed=False,
                    detail=f"status={response.status_code} url={openapi_url}",
                )
            )
        else:
            try:
                payload = response.json()
            except ValueError as exc:
                checks.append(
                    ReadinessCheckResult(
                        label="openapi reachable",
                        passed=False,
                        detail=f"error=invalid_json url={openapi_url} exc={exc}",
                    )
                )
            else:
                if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
                    checks.append(
                        ReadinessCheckResult(
                            label="openapi reachable",
                            passed=False,
                            detail=f"error=missing_paths url={openapi_url}",
                        )
                    )
                else:
                    paths = payload["paths"]
                    checks.append(
                        ReadinessCheckResult(
                            label="openapi reachable",
                            passed=True,
                            detail=f"url={openapi_url}",
                        )
                    )
    except requests.RequestException as exc:
        checks.append(
            ReadinessCheckResult(
                label="openapi reachable",
                passed=False,
                detail=f"error={exc}",
            )
        )

    if not paths:
        return False, checks

    for path in REQUIRED_OPENAPI_PATHS:
        exists = path in paths
        checks.append(
            ReadinessCheckResult(
                label=f"route {path}",
                passed=exists,
                detail="" if exists else "missing from live OpenAPI",
            )
        )

    for path, methods in REQUIRED_OPENAPI_METHODS.items():
        path_item = paths.get(path) if isinstance(paths.get(path), dict) else {}
        for method in methods:
            exists = isinstance(path_item, dict) and method in path_item
            checks.append(
                ReadinessCheckResult(
                    label=f"route-method {method.upper()} {path}",
                    passed=exists,
                    detail="" if exists else "missing from live OpenAPI",
                )
            )

    return all(check.passed for check in checks), checks


def _print_readiness_results(
    *,
    passed: bool,
    checks: list[ReadinessCheckResult],
) -> None:
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        if check.label == "openapi reachable":
            if check.passed:
                print(f"[readiness] {check.label} {status} {check.detail}")
            else:
                print(f"[readiness] {check.label} {status} {check.detail}")
            continue
        if check.detail:
            print(f"[readiness] {check.label} {status} {check.detail}")
        else:
            print(f"[readiness] {check.label} {status}")

    if passed:
        print("[readiness] summary PASS")
        return

    print("[readiness] summary FAIL")
    print(
        "[readiness] hint: if openapi is unreachable, confirm the backend is running "
        "and --api-base-url is correct."
    )
    print(
        "[readiness] hint: if a route is missing from live OpenAPI, restart the "
        "FastAPI backend so the running service loads the latest routes."
    )
    print(
        "[readiness] hint: readiness does not verify Celery; if async suites fail "
        "later, check the Celery worker separately."
    )


def _run_suite(plan: SuitePlan, *, suite_timeout: float) -> SuiteResult:
    started = time.perf_counter()
    print(f'[runner] suite START name={plan.key} cmd="{_format_command(plan.command)}"')
    process = subprocess.Popen(plan.command, cwd=str(REPO_ROOT))
    timeout_seconds = float(suite_timeout)
    status = "passed"
    exit_code: int | None = None

    try:
        if timeout_seconds > 0:
            exit_code = process.wait(timeout=timeout_seconds)
        else:
            exit_code = process.wait()
    except subprocess.TimeoutExpired:
        print(
            f"[runner] suite TIMEOUT name={plan.key} "
            f"timeout={_format_seconds(timeout_seconds)}"
        )
        status = "timeout"
        try:
            process.terminate()
            process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        exit_code = -9
    else:
        status = "passed" if exit_code == 0 else "failed"

    elapsed = time.perf_counter() - started
    print(
        f"[runner] suite DONE name={plan.key} status={status} "
        f"exit_code={exit_code} elapsed={_format_seconds(elapsed)}"
    )
    return SuiteResult(
        name=plan.key,
        command=_format_command(plan.command),
        status=status,
        exit_code=exit_code,
        elapsed_seconds=round(float(elapsed), 3),
    )


def _print_list(
    *,
    progress_interval: float,
    username: str,
    password: str,
) -> None:
    catalog = build_suite_catalog(
        progress_interval=progress_interval,
        username=username,
        password=password,
    )
    print("=" * 72)
    print("Benchmark runner suite list")
    print(f"[runner] available suites={len(catalog)}")
    print("=" * 72)
    for index, suite_name in enumerate(SUITE_ORDER, start=1):
        plan = catalog[suite_name]
        print(
            f"[runner] suite {index}/{len(SUITE_ORDER)} {suite_name} "
            f'cmd="{_format_command(plan.command)}"'
        )


def main() -> None:
    args = parse_args()
    progress_interval = max(1.0, float(args.progress_interval))
    suite_timeout = max(0.0, float(args.suite_timeout))
    readiness_timeout = max(1.0, float(args.readiness_timeout))
    api_base_url = _normalize_api_base_url(str(args.api_base_url or DEFAULT_API_BASE_URL))
    username = str(args.username or "").strip()
    password = str(args.password or "workspace123")
    normalized_profile = str(args.profile)
    normalized_only = str(args.only or "").strip()

    if args.list:
        _print_list(
            progress_interval=progress_interval,
            username=username,
            password=password,
        )
        return

    if normalized_only:
        print("[runner] warning: --only overrides --profile")
        if args.include_pytest:
            print("[runner] warning: --include-pytest is ignored when --only is set")
        if args.include_legacy_quality:
            print("[runner] warning: --include-legacy-quality is ignored when --only is set")
    elif normalized_profile == "legacy":
        print("[runner] warning: legacy quality benchmark is slow and may take a long time")
    elif args.include_legacy_quality:
        print("[runner] warning: legacy quality benchmark is slow and may take a long time")

    suite_catalog = build_suite_catalog(
        progress_interval=progress_interval,
        username=username,
        password=password,
    )
    suite_names = select_suite_names(
        profile=normalized_profile,
        only=normalized_only,
        include_pytest=bool(args.include_pytest) and not normalized_only,
        include_legacy_quality=bool(args.include_legacy_quality) and not normalized_only,
        available_names=set(suite_catalog),
    )
    suites = [suite_catalog[name] for name in suite_names]

    print("=" * 72)
    print("Benchmark runner")
    print(f"[runner] repo_root={REPO_ROOT}")
    print(
        f"[runner] planned suites={len(suites)} profile={normalized_profile} "
        f"only={normalized_only or '<none>'} fail_fast={bool(args.fail_fast)} "
        f"dry_run={bool(args.dry_run)}"
    )
    print(
        f"[runner] include_pytest={bool(args.include_pytest)} "
        f"include_legacy_quality={bool(args.include_legacy_quality)} "
        f"progress_interval={progress_interval:.1f}s "
        f"suite_timeout={_format_seconds(suite_timeout)} "
        f"check_readiness={bool(args.check_readiness)} "
        f"api_base_url={api_base_url} "
        f"readiness_timeout={_format_seconds(readiness_timeout)}"
    )
    print("=" * 72)

    started_at = datetime.now().isoformat()
    runner_started = time.perf_counter()
    results: list[SuiteResult] = []

    if args.check_readiness:
        passed, checks = _run_readiness_checks(
            api_base_url=api_base_url,
            readiness_timeout=readiness_timeout,
        )
        _print_readiness_results(passed=passed, checks=checks)
        if not passed:
            report = _build_report(
                profile=normalized_profile,
                dry_run=bool(args.dry_run),
                fail_fast=bool(args.fail_fast),
                started_at=started_at,
                finished_at=datetime.now().isoformat(),
                elapsed_seconds=time.perf_counter() - runner_started,
                suite_results=[],
            )
            if args.report_json:
                report["readiness"] = {
                    "enabled": True,
                    "passed": False,
                    "checks": [asdict(item) for item in checks],
                }
                _write_report(str(args.report_json), report)
            raise SystemExit(1)

    if args.dry_run:
        for index, suite in enumerate(suites, start=1):
            print(
                f"[runner] suite {index}/{len(suites)} {suite.key} DRY-RUN "
                f'cmd="{_format_command(suite.command)}"'
            )
            results.append(
                SuiteResult(
                    name=suite.key,
                    command=_format_command(suite.command),
                    status="planned",
                    exit_code=None,
                    elapsed_seconds=0.0,
                )
            )
        finished_at = datetime.now().isoformat()
        report = _build_report(
            profile=normalized_profile,
            dry_run=True,
            fail_fast=bool(args.fail_fast),
            started_at=started_at,
            finished_at=finished_at,
            elapsed_seconds=time.perf_counter() - runner_started,
            suite_results=results,
        )
        if args.report_json:
            if args.check_readiness:
                report["readiness"] = {
                    "enabled": True,
                    "passed": True,
                }
            _write_report(str(args.report_json), report)
        return

    completed = 0
    stop_after_current = False
    for index, suite in enumerate(suites, start=1):
        result = _run_suite(suite, suite_timeout=suite_timeout)
        results.append(result)
        completed = index
        _print_overall_progress(
            completed=completed,
            total=len(suites),
            overall_started=runner_started,
        )
        if result.status in {"failed", "timeout"} and args.fail_fast:
            print("[runner] fail-fast enabled; stopping after the current suite")
            stop_after_current = True
            break

    if stop_after_current and completed < len(suites):
        for suite in suites[completed:]:
            results.append(
                SuiteResult(
                    name=suite.key,
                    command=_format_command(suite.command),
                    status="skipped",
                    exit_code=None,
                    elapsed_seconds=0.0,
                )
            )

    finished_at = datetime.now().isoformat()
    total_elapsed = time.perf_counter() - runner_started
    report = _build_report(
        profile=normalized_profile,
        dry_run=False,
        fail_fast=bool(args.fail_fast),
        started_at=started_at,
        finished_at=finished_at,
        elapsed_seconds=total_elapsed,
        suite_results=results,
    )
    if args.report_json:
        if args.check_readiness:
            report["readiness"] = {
                "enabled": True,
                "passed": True,
            }
        _write_report(str(args.report_json), report)

    print("=" * 72)
    print(
        f"[runner] summary completed_suites={completed}/{len(suites)} "
        f"passed={report['passed_suites']} failed={report['failed_suites']} "
        f"timeouts={report['timeout_suites']} skipped={report['skipped_suites']} "
        f"elapsed={_format_seconds(total_elapsed)}"
    )
    if report["failed_suite_names"]:
        print("[runner] failed suites:")
        for suite_name in report["failed_suite_names"]:
            print(f"  - {suite_name}")
    if report["timeout_suite_names"]:
        print("[runner] timeout suites:")
        for suite_name in report["timeout_suite_names"]:
            print(f"  - {suite_name}")
    if report["skipped_suite_names"]:
        print("[runner] skipped suites:")
        for suite_name in report["skipped_suite_names"]:
            print(f"  - {suite_name}")

    if report["failed_suites"] or report["timeout_suites"]:
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
