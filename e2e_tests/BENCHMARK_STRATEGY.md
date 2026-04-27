# Benchmark Strategy

## Overview

The benchmark system is split into smaller, independently runnable suites.
The goal is to make regression results easier to interpret and to keep modern
workspace validation separate from legacy ordinary legal QA regression.

## Layers

### `pytest`

- Purpose: fast code-level regression for services, routes, policies, and data handling.
- Usage: run frequently during development.
- Strength: quickest signal when a code change breaks expected logic.

### `smoke_benchmark.py`

- Purpose: fastest end-to-end smoke benchmark for the default ordinary legal QA path.
- Scope:
  - strict legal QA questions only
  - explicit `mode_consent_enabled=false`
  - no dossier binding
  - no workspace / graph / participant flows
- Strength: catches accidental workspace pollution in the ordinary `/dialog` path.

### `personalized_workspace_benchmark.py --only <group>`

- Purpose: targeted validation for independent workspace capabilities.
- Supported groups:
  - `baseline`
  - `consent`
  - `temporary`
  - `tracked`
  - `dossier`
  - `memory`
  - `graph`
  - `participant`
  - `archive`
  - `delete`
  - `all`
- Strength: each workspace capability can be isolated and rerun without paying the cost of a full, mixed benchmark.

### `run_benchmarks.py`

- Purpose: modern orchestrator entry point.
- Behavior:
  - `quick` runs a minimal sequence of independent suites
  - `workspace` runs the workspace-focused suite sequence
  - `full` runs the modern end-to-end suite sequence
  - `legacy` explicitly runs the old quality benchmark
- Important:
  - modern `full` is defined as sequential orchestration of independent suites
  - modern `full` does not automatically run the old monolithic quality benchmark
  - `legacy` stays manual and low-frequency

### `run_benchmarks.py` modern profiles

- `quick`
  - `smoke`
  - `workspace_baseline`
  - `workspace_temporary`
- `workspace`
  - `workspace_baseline`
  - `workspace_temporary`
  - `workspace_tracked`
  - `workspace_dossier`
  - `workspace_memory`
  - `workspace_participant`
  - `workspace_archive`
  - `workspace_delete`
- `full`
  - `smoke`
  - `workspace_baseline`
  - `workspace_temporary`
  - `workspace_tracked`
  - `workspace_dossier`
  - `workspace_memory`
  - `workspace_participant`
  - `workspace_archive`
  - `workspace_delete`
- `legacy`
  - `legacy_quality`

### `run_benchmarks.py` useful flags

- `--list`
  - print all available suite names and their commands, then exit
- `--only workspace_delete`
  - run exactly one modern suite
- `--suite-timeout 300`
  - enforce a per-suite timeout in seconds
- `--report-json e2e_tests/results/latest_runner_report.json`
  - optionally persist an orchestration report
- `--dry-run`
  - print suite order and commands without executing them

Example commands:

```powershell
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --list
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile quick --dry-run
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile full --dry-run
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --only workspace_delete --dry-run
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile quick --suite-timeout 300
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile quick --dry-run --report-json e2e_tests/results/latest_runner_report.json
```

### `quality_benchmark.py`

- Purpose: legacy quality regression for the older broad legal QA / general consult benchmark.
- Status: low-frequency manual regression.
- Recommended use:
  - release checks
  - defense/demo prep
  - historical comparison against the old 80-point quality baseline

## Recommended Usage

### During development

1. Run `pytest`.
2. Run `smoke_benchmark.py` when touching ordinary legal QA behavior.
3. Run `personalized_workspace_benchmark.py --only <group>` for the specific workspace feature you changed.

### Before merging larger changes

1. Run `pytest`.
2. Run `run_benchmarks.py --profile quick`.
3. If the change touched workspace logic, run `run_benchmarks.py --profile workspace`.

### Before release / defense

1. Run `pytest`.
2. Run `run_benchmarks.py --profile full`.
3. Optionally run `quality_benchmark.py` as a legacy comparison baseline.

## Practical Interpretation

- `smoke_benchmark.py` is the fastest ordinary legal-QA end-to-end check.
- `personalized_workspace_benchmark.py --only <group>` is the targeted workspace regression tool.
- `run_benchmarks.py --profile full` is the modern combined regression entry point.
- `quality_benchmark.py` remains a legacy full-quality comparison benchmark, not the default modern full.

## Rationale

- Smaller suites reduce diagnosis time.
- Modern workspace features should not contaminate default ordinary legal QA regression.
- A modern full regression should be composable from smaller suites instead of being trapped in one large script.
