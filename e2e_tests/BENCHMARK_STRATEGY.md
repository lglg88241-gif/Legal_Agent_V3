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

### `legal_qa_benchmark.py`

- Purpose: fuller but still targeted benchmark for the default legal QA path.
- Scope:
  - explicit `mode_consent_enabled=false` on every `/dialog` request
  - no dossier binding
  - no `dossier_id`
  - no workspace / graph / participant flows
  - checks for workspace-term leakage in responses
- Strength: provides a more representative ordinary legal QA regression than `smoke_benchmark.py` without falling back to the legacy monolithic benchmark.

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
  - `legal_qa`
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
- `--only legal_qa`
  - run the targeted default legal-QA suite
- `--suite-timeout 300`
  - enforce a per-suite timeout in seconds
- `--report-json e2e_tests/results/latest_runner_report.json`
  - optionally persist an orchestration report
- `--dry-run`
  - print suite order and commands without executing them
- `--check-readiness`
  - validate the live backend OpenAPI and required routes before running suites
- `--api-base-url http://127.0.0.1:8000`
  - override the backend base URL used by readiness checks
- `--readiness-timeout 5`
  - set the HTTP timeout for readiness checks

Example commands:

```powershell
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --list
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile quick --dry-run
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --only legal_qa --dry-run
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile full --dry-run
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile full --check-readiness
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile full --check-readiness --api-base-url http://127.0.0.1:8000
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --only workspace_delete --dry-run
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile quick --suite-timeout 300
.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile quick --dry-run --report-json e2e_tests/results/latest_runner_report.json
```

### Readiness checks

- `run_benchmarks.py --check-readiness` validates the live backend OpenAPI before any suite runs.
- The runner checks `GET {api_base_url}/openapi.json`, verifies the response is JSON with a `paths` object, and confirms critical benchmark routes are present.
- This check targets the running backend service, not `import main` from local code.
- It is designed to catch problems such as:
  - code already defines a route
  - but the running FastAPI service was not restarted and the live OpenAPI still misses that route
- Readiness checks do not replace `pytest`; they only confirm the e2e environment is pointed at a usable live backend.
- Readiness checks also do not validate Celery workers. If suites fail later during async processing, verify the Celery worker separately.

### `quality_benchmark.py`

- Purpose: legacy quality regression for the older broad legal QA / general consult benchmark.
- Status: low-frequency manual regression.
- Recommended use:
  - release checks
  - defense/demo prep
  - historical comparison against the old 80-point quality baseline
- Important:
  - it stays outside the default modern `full` profile
  - it is not a daily development requirement

## Recommended Usage

### During development

1. Run `pytest`.
2. Run `smoke_benchmark.py` when touching ordinary legal QA behavior.
3. Run `legal_qa_benchmark.py` when you need broader ordinary legal QA coverage.
4. Run `personalized_workspace_benchmark.py --only <group>` for the specific workspace feature you changed.

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
- `legal_qa_benchmark.py` is the default targeted legal-QA regression suite.
- `personalized_workspace_benchmark.py --only <group>` is the targeted workspace regression tool.
- `run_benchmarks.py --profile full` is the modern combined regression entry point: `smoke + legal_qa + workspace suites`.
- `quality_benchmark.py` remains a legacy full-quality comparison benchmark, not the default modern full.

## Modern Full Validation

Current modern full suite order:

1. `smoke`
2. `legal_qa`
3. `workspace_baseline`
4. `workspace_temporary`
5. `workspace_tracked`
6. `workspace_dossier`
7. `workspace_memory`
8. `workspace_participant`
9. `workspace_archive`
10. `workspace_delete`

Modern full benchmark validated:

- date: `2026-04-27`
- profile: `full`
- recommended command:
  - `.venv\Scripts\python.exe e2e_tests\run_benchmarks.py --profile full --check-readiness --fail-fast --suite-timeout 360`
- result:
  - `10/10 suites passed`
- total elapsed:
  - `1058.5s`
- skipped:
  - `0`
- timeouts:
  - `0`

Suite results:

- `smoke`: `5/5 cases`, `26/26 checks`
- `legal_qa`: `6/6 cases`, `38/38 checks`, `1 latency warning`
- `workspace_baseline`: `1/1 cases`, `7/7 checks`
- `workspace_temporary`: `1/1 cases`, `9/9 checks`
- `workspace_tracked`: `1/1 cases`, `7/7 checks`
- `workspace_dossier`: `2/2 cases`, `9/9 checks`
- `workspace_memory`: `2/2 cases`, `8/8 checks`
- `workspace_participant`: `1/1 cases`, `7/7 checks`
- `workspace_archive`: `1/1 cases`, `4/4 checks`
- `workspace_delete`: `1/1 cases`, `6/6 checks`

## Runtime Notes

- Before any live benchmark run, confirm both the FastAPI backend and Celery worker are running the latest code.
- If backend API routes or response schemas changed, restart the backend and the worker before trusting e2e results.
- Prefer a clean single-process backend for benchmark runs. Auto-reload or stale worker processes can produce false failures against old code.
- `workspace_participant` is the most sensitive suite for live backend state because it depends on candidate generation and follow-up participant resolution.
- `legal_qa` keeps latency warnings for observation. A latency warning does not mean the suite failed if the legal QA assertions passed.
- Do not run `quality_benchmark.py` by default during daily development. Keep it as a legacy full regression for release or defense preparation.

## Recent Fixes

- commit: `847d01f fix(workspace): refresh participant mentions before serialization`
- issue: `POST /dossiers/{dossier_id}/participants/resolve` returned HTTP 500 during `workspace_participant`
- root cause: async SQLAlchemy attempted to lazy-load `mention.created_at` after commit during response serialization, which triggered `MissingGreenlet`
- fix: refresh the participant and resolved mentions before serialization so timestamp fields are already loaded
- regression: `tests/test_h4g_dossier_participants.py`

## Rationale

- Smaller suites reduce diagnosis time.
- Modern workspace features should not contaminate default ordinary legal QA regression.
- A modern full regression should be composable from smaller suites instead of being trapped in one large script.
