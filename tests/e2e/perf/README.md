# E2E performance (`tests/e2e/perf`)

GPU/NPU performance E2E suites. Each suite uses **one JSON case per pytest invocation**; suites do
not compare arms inside a single test.

| Suite | Purpose | Runbook |
| ----- | ------- | ------- |
| [`swebench_run/`](swebench_run/README.md) | Cold vLLM + BenchKit SWE-bench **`run`** (baseline vs AgentInfer serve) | [`swebench_run/README.md`](swebench_run/README.md) |
| [`router-replay/`](router-replay/README.md) | Cold vLLM + **Router** + trace **`replay`** (binding-only vs program scheduling) | [`router-replay/README.md`](router-replay/README.md) |

Shared vLLM lifecycle and case loading: [`../helpers/`](../helpers/).

Pytest marker: `e2e_perf` (default CI excludes `tests/e2e`).
