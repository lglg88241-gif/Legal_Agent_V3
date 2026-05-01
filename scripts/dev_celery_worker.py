from __future__ import annotations

import argparse
import builtins
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


def print(*args, **kwargs):
    kwargs.setdefault("flush", True)
    return builtins.print(*args, **kwargs)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCH_PATHS = ("app", "main.py", "scripts")
DEFAULT_INTERVAL = 1.0
DEFAULT_KILL_TIMEOUT = 10.0
DEFAULT_DEBOUNCE_SECONDS = 0.75


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Celery worker and auto-restart it when Python files change."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="polling interval in seconds; default 1.0",
    )
    parser.add_argument(
        "--loglevel",
        default="info",
        help="Celery worker log level; default info",
    )
    parser.add_argument(
        "--pool",
        default="threads",
        help="Celery worker pool; default threads",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Celery worker concurrency; default 2",
    )
    parser.add_argument(
        "--watch",
        action="append",
        default=[],
        help="additional file or directory to watch; may be repeated",
    )
    parser.add_argument(
        "--kill-timeout",
        type=float,
        default=DEFAULT_KILL_TIMEOUT,
        help="seconds to wait after terminate() before kill(); default 10",
    )
    return parser.parse_args()


def resolve_watch_targets(extra_watch: Iterable[str]) -> list[Path]:
    targets: list[Path] = []
    for raw_path in [*DEFAULT_WATCH_PATHS, *extra_watch]:
        path = Path(raw_path)
        resolved = path if path.is_absolute() else REPO_ROOT / path
        resolved = resolved.resolve()
        if resolved not in targets:
            targets.append(resolved)
    return targets


def iter_python_files(watch_targets: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()

    for target in watch_targets:
        if not target.exists():
            continue
        if target.is_file():
            if target.suffix == ".py" and target not in seen:
                files.append(target)
                seen.add(target)
            continue
        for path in sorted(target.rglob("*.py")):
            if path.is_file() and path not in seen:
                files.append(path)
                seen.add(path)

    return files


def snapshot_files(watch_targets: Iterable[Path]) -> dict[Path, int]:
    snapshot: dict[Path, int] = {}
    for path in iter_python_files(watch_targets):
        try:
            snapshot[path] = path.stat().st_mtime_ns
        except FileNotFoundError:
            continue
    return snapshot


def diff_snapshots(
    previous: dict[Path, int],
    current: dict[Path, int],
) -> list[Path]:
    changed: list[Path] = []
    all_paths = set(previous) | set(current)
    for path in sorted(all_paths):
        if previous.get(path) != current.get(path):
            changed.append(path)
    return changed


def format_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def build_worker_command(
    *,
    loglevel: str,
    pool: str,
    concurrency: int,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "app.celery_app.celery_app",
        "worker",
        "--loglevel",
        str(loglevel),
        "-P",
        str(pool),
        "--concurrency",
        str(concurrency),
    ]


def start_worker(command: list[str]) -> subprocess.Popen[bytes]:
    print(f"[dev-celery] starting worker: {format_command(command)}")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        creationflags=creationflags,
    )


def stop_worker(
    process: subprocess.Popen[bytes] | None,
    *,
    kill_timeout: float,
    reason: str,
) -> None:
    if process is None:
        return
    if process.poll() is not None:
        print(
            f"[dev-celery] worker already exited with code {process.returncode}"
            f" ({reason})"
        )
        return

    print(f"[dev-celery] stopping worker ({reason})")
    used_ctrl_break = False
    if sys.platform == "win32":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            used_ctrl_break = True
            print("[dev-celery] sent CTRL_BREAK_EVENT to worker process group")
        except (ValueError, OSError) as exc:
            print(f"[dev-celery] CTRL_BREAK_EVENT failed: {exc}; falling back to terminate()")
    if not used_ctrl_break:
        process.terminate()
    try:
        process.wait(timeout=kill_timeout)
        print(f"[dev-celery] worker exited with code {process.returncode}")
    except subprocess.TimeoutExpired:
        print(f"[dev-celery] graceful stop timed out after {kill_timeout:.1f}s")
        if used_ctrl_break:
            print("[dev-celery] trying terminate() before kill()")
            try:
                process.terminate()
                process.wait(timeout=2.0)
                print(f"[dev-celery] worker exited with code {process.returncode}")
                return
            except subprocess.TimeoutExpired:
                pass
        process.kill()
        process.wait()
        print(f"[dev-celery] worker killed with code {process.returncode}")


def settle_changes(
    watch_targets: list[Path],
    previous_snapshot: dict[Path, int],
    *,
    debounce_seconds: float,
) -> tuple[list[Path], dict[Path, int]]:
    changed_paths: set[Path] = set()
    current_snapshot = snapshot_files(watch_targets)
    changed_paths.update(diff_snapshots(previous_snapshot, current_snapshot))
    if debounce_seconds > 0:
        time.sleep(debounce_seconds)
        settled_snapshot = snapshot_files(watch_targets)
        changed_paths.update(diff_snapshots(current_snapshot, settled_snapshot))
        current_snapshot = settled_snapshot
    return sorted(changed_paths), current_snapshot


def main() -> int:
    args = parse_args()
    watch_targets = resolve_watch_targets(args.watch)
    command = build_worker_command(
        loglevel=args.loglevel,
        pool=args.pool,
        concurrency=args.concurrency,
    )

    print("[dev-celery] repo root:", REPO_ROOT)
    print(
        "[dev-celery] watch targets:",
        ", ".join(display_path(path) for path in watch_targets),
    )
    print(
        f"[dev-celery] settings: interval={args.interval:.1f}s "
        f"loglevel={args.loglevel} pool={args.pool} "
        f"concurrency={args.concurrency} kill_timeout={args.kill_timeout:.1f}s"
    )

    missing_targets = [path for path in watch_targets if not path.exists()]
    if missing_targets:
        print(
            "[dev-celery] warning: missing watch targets:",
            ", ".join(display_path(path) for path in missing_targets),
        )

    previous_snapshot = snapshot_files(watch_targets)
    print(f"[dev-celery] initial python files tracked: {len(previous_snapshot)}")

    process: subprocess.Popen[bytes] | None = None

    try:
        process = start_worker(command)
        while True:
            time.sleep(max(args.interval, 0.1))

            if process.poll() is not None:
                print(
                    f"[dev-celery] worker exited unexpectedly with code {process.returncode}; restarting"
                )
                process = start_worker(command)
                previous_snapshot = snapshot_files(watch_targets)
                continue

            current_snapshot = snapshot_files(watch_targets)
            changed_paths = diff_snapshots(previous_snapshot, current_snapshot)
            if not changed_paths:
                previous_snapshot = current_snapshot
                continue

            changed_paths, current_snapshot = settle_changes(
                watch_targets,
                previous_snapshot,
                debounce_seconds=DEFAULT_DEBOUNCE_SECONDS,
            )
            if not changed_paths:
                previous_snapshot = current_snapshot
                continue

            print("[dev-celery] detected Python file changes:")
            for path in changed_paths:
                print(f"  - {display_path(path)}")

            stop_worker(
                process,
                kill_timeout=args.kill_timeout,
                reason="source change detected",
            )
            process = start_worker(command)
            previous_snapshot = current_snapshot
    except KeyboardInterrupt:
        print("[dev-celery] received Ctrl+C; shutting down")
        stop_worker(process, kill_timeout=args.kill_timeout, reason="Ctrl+C")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
