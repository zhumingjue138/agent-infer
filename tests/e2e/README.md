# End-to-end tests (`tests/e2e`)

GPU/NPU-oriented E2E tests. Excluded from default CPU CI (`pytest --ignore=tests/e2e`).

Shared helpers for case JSON and vLLM lifecycle: [`helpers/`](helpers/).

## Suites

| Subtree | Purpose | Details |
| ------- | ------- | ------- |
| [`perf/`](perf/README.md) | **Performance** E2E: [`swebench_run`](perf/swebench_run/README.md) (SWE-bench run), [`router-replay`](perf/router-replay/README.md) (Router replay) | [`perf/README.md`](perf/README.md) |
| [`function/`](function/README.md) | **Functional** E2E (trace replay repeatability, future smoke tests) | [`function/README.md`](function/README.md) |

`perf/` and `function/` are independent: different bench commands, pass criteria, and case JSON schemas.
