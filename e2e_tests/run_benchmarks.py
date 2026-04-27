"""
Targeted benchmark orchestrator.

Modern "full" regression is defined as a sequence of smaller independent
benchmark suites instead of one monolithic script.
"""
from __future__ import annotations

import argparse
import builtins
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    return builtins.print(*args, **kwargs)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROGRESS_INTERVAL = 5.0
DELETE_SUITE_IMPLEMENTED = False


@dataclass(frozen=True)
class SuitePlan:
    key: str
    command: list[str]


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
        "--fail-fast",
        action="store_true",
        help="stop after the first failing suite",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned suites and commands without executing them",
    )
    parser.add_argument(
        "--include-pytest",
        action="store_true",
        help="prepend pytest to the planned suites",
    )
    parser.add_argument(
        "--include-legacy-quality",
        action="store_true",
        help="append the legacy quality benchmark to the planned suites",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=DEFAULT_PROGRESS_INTERVAL,
        help="progress interval passed to child benchmark suites when supported; default 5",
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


def _workspace_command(*, only: str, username: str, password: str, progress_interval: float) -> list[str]:
    command = _python_command("e2e_tests/personalized_workspace_benchmark.py", "--only", only, "--progress-interval", str(progress_interval))
    if username:
        command.extend(["--username", username])
    if password:
        command.extend(["--password", password])
    return command


def _smoke_command(*, username: str, password: str, progress_interval: float) -> list[str]:
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


def _legacy_quality_command() -> list[str]:
    return _python_command("e2e_tests/quality_benchmark.py")


def _pytest_command() -> list[str]:
    return _python_command("-m", "pytest", "tests", "-q")


def build_suite_plans(
    *,
    profile: str,
    include_pytest: bool,
    include_legacy_quality: bool,
    progress_interval: float,
    username: str,
    password: str,
) -> list[SuitePlan]:
    suites: list[SuitePlan] = []
    if include_pytest:
        suites.append(SuitePlan("pytest", _pytest_command()))

    if profile == "quick":
        suites.extend(
            [
                SuitePlan("smoke", _smoke_command(username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_baseline", _workspace_command(only="baseline", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_temporary", _workspace_command(only="temporary", username=username, password=password, progress_interval=progress_interval)),
            ]
        )
    elif profile == "full":
        suites.extend(
            [
                SuitePlan("smoke", _smoke_command(username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_baseline", _workspace_command(only="baseline", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_temporary", _workspace_command(only="temporary", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_tracked", _workspace_command(only="tracked", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_dossier", _workspace_command(only="dossier", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_memory", _workspace_command(only="memory", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_participant", _workspace_command(only="participant", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_archive", _workspace_command(only="archive", username=username, password=password, progress_interval=progress_interval)),
            ]
        )
        if DELETE_SUITE_IMPLEMENTED:
            suites.append(
                SuitePlan("workspace_delete", _workspace_command(only="delete", username=username, password=password, progress_interval=progress_interval))
            )
    elif profile == "workspace":
        suites.extend(
            [
                SuitePlan("workspace_baseline", _workspace_command(only="baseline", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_temporary", _workspace_command(only="temporary", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_tracked", _workspace_command(only="tracked", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_dossier", _workspace_command(only="dossier", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_memory", _workspace_command(only="memory", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_participant", _workspace_command(only="participant", username=username, password=password, progress_interval=progress_interval)),
                SuitePlan("workspace_archive", _workspace_command(only="archive", username=username, password=password, progress_interval=progress_interval)),
            ]
        )
        if DELETE_SUITE_IMPLEMENTED:
            suites.append(
                SuitePlan("workspace_delete", _workspace_command(only="delete", username=username, password=password, progress_interval=progress_interval))
            )
    elif profile == "legacy":
        print("[runner] warning: legacy quality benchmark is slow and may take a long time")
        suites.append(SuitePlan("legacy_quality", _legacy_quality_command()))
    else:
        raise AssertionError(f"Unhandled profile: {profile}")

    if include_legacy_quality and profile != "legacy":
        print("[runner] warning: legacy quality benchmark is slow and may take a long time")
        suites.append(SuitePlan("legacy_quality", _legacy_quality_command()))

    return suites


def main() -> None:
    args = parse_args()
    suites = build_suite_plans(
        profile=str(args.profile),
        include_pytest=bool(args.include_pytest),
        include_legacy_quality=bool(args.include_legacy_quality),
        progress_interval=max(1.0, float(args.progress_interval)),
        username=str(args.username or "").strip(),
        password=str(args.password or "workspace123"),
    )
    print("=" * 72)
    print("Benchmark runner")
    print(f"[runner] repo_root={REPO_ROOT}")
    print(
        f"[runner] planned suites={len(suites)} profile={args.profile} "
        f"fail_fast={bool(args.fail_fast)} dry_run={bool(args.dry_run)}"
    )
    print(
        f"[runner] include_pytest={bool(args.include_pytest)} "
        f"include_legacy_quality={bool(args.include_legacy_quality)} "
        f"progress_interval={max(1.0, float(args.progress_interval)):.1f}s"
    )
    print("=" * 72)

    if args.dry_run:
        for index, suite in enumerate(suites, start=1):
            print(
                f"[runner] suite {index}/{len(suites)} {suite.key} DRY-RUN "
                f'cmd="{_format_command(suite.command)}"'
            )
        return

    overall_started = time.perf_counter()
    failures: list[str] = []
    completed_suites = 0
    for index, suite in enumerate(suites, start=1):
        started = time.perf_counter()
        print(
            f"[runner] suite {index}/{len(suites)} {suite.key} START "
            f'cmd="{_format_command(suite.command)}"'
        )
        process = subprocess.Popen(suite.command, cwd=str(REPO_ROOT))
        exit_code = process.wait()
        elapsed_s = time.perf_counter() - started
        print(
            f"[runner] suite {index}/{len(suites)} {suite.key} DONE "
            f"exit_code={exit_code} elapsed={_format_seconds(elapsed_s)}"
        )
        print(
            f"[runner] overall progress {index}/{len(suites)} "
            f"{(100.0 * index / len(suites)):.1f}%"
        )
        completed_suites = index
        if exit_code != 0:
            failures.append(suite.key)
            if args.fail_fast:
                print("[runner] fail-fast enabled; stopping after the first failing suite")
                break

    total_elapsed = time.perf_counter() - overall_started
    print("=" * 72)
    print(
        f"[runner] summary completed_suites={completed_suites}/{len(suites)} "
        f"failures={len(failures)} elapsed={_format_seconds(total_elapsed)}"
    )
    if failures:
        print("[runner] failed suites:")
        for suite_key in failures:
            print(f"  - {suite_key}")
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
