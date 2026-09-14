# E2E Tests

End-to-end tests grouped by intent. Each subpackage owns its own pytest entrypoints,
case JSON layout, and helpers.

| Path | Purpose |
| ---- | ------- |
| [`perf/`](perf/) | Performance benchmarks: vLLM + AgentBench; vLLM + router + Codex JSONL |
| [`function/`](function/) | Reserved for functional E2E (correctness, API contracts, routing behavior) |

## Quick start (performance)

See [`perf/README.md`](perf/README.md). Typical invocation:

```bash
pytest -s -v tests/e2e/perf/run_benchmark.py \
  --test-config-file tests/e2e/perf/cases/baseline/glm52_8x4_plan_subagent.json
```

## Adding new suites

- **Perf** (latency/throughput/soak): add JSON under `perf/cases/`, helpers under
  `perf/helpers/`, and a `perf/run_*_benchmark.py` entry if the orchestration differs.
- **Function** (pass/fail behavior): add under `function/` with lightweight fixtures;
  avoid pulling in full benchmark orchestration unless needed.

Shared building blocks (for example `serve_env` / `server_params` → CLI) should live in
`perf/helpers/` first and be imported by other subpackages when they stabilize.
