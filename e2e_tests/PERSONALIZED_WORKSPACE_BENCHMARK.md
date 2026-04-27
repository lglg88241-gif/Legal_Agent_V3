# Personalized Workspace Benchmark

## Purpose

- `quality_benchmark.py` remains the regression benchmark for the legacy ordinary legal-QA / general consult path.
- `personalized_workspace_benchmark.py` is the separate regression benchmark for H2-H5 personalized workspace capabilities.

Do not mix dossier, participant, graph-memory, or mode-consent scenarios into the legacy `quality_benchmark.py` scoring baseline.

## Coverage

`personalized_workspace_benchmark.py` focuses on API-level checks for:

- LEGAL_QA pure legal question does not write structured case or graph
- temporary mode keeps plain messages but skips structured persistence
- tracked mode writes structured case and graph
- dossier creation and conversation binding
- creating a new conversation from dossier defaults to tracked
- dossier memory queries
- explicit law queries inside dossier-bound conversations
- participant candidate / confirmation API flow
- dossier archive semantics

## Usage

Dry run:

```bash
D:/Dev/python310/python.exe e2e_tests/personalized_workspace_benchmark.py --dry-run
```

Quick smoke:

```bash
D:/Dev/python310/python.exe e2e_tests/personalized_workspace_benchmark.py --smoke
```

Full workspace regression:

```bash
D:/Dev/python310/python.exe e2e_tests/personalized_workspace_benchmark.py
```

## Output

Reports are written to `e2e_tests/results/`:

- `personalized_workspace_benchmark_smoke.json`
- `personalized_workspace_benchmark_report.json`
