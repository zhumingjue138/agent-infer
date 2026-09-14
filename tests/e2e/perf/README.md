# E2E Performance Tests

Baseline and AgentInfer cases are **fully independent** JSON files. Each file contains its own
environment variables, vLLM serve parameters, and benchmark parameters.

Run benchmarks with pytest (one JSON per invocation):

```bash
pytest -s -v tests/e2e/perf/run_benchmark.py \
  --test-config-file tests/e2e/perf/cases/baseline/glm52_8x4_plan_subagent.json
```

## Case file layout

| Path | Meaning |
| ---- | ------- |
| `tests/e2e/perf/cases/baseline/glm52_{8x4,16x8,24x12,32x16}_plan_subagent.json` | Baseline vLLM + plan-subagent |
| `tests/e2e/perf/cases/agentinfer/glm52_{8x4,16x8,24x12,32x16,480x12}_plan_subagent.json` | AgentInfer variant (480x12 = soak) |
| `tests/e2e/perf/cases/router_codex/qwen35_{25s4t,13s8t}_gpu*_c{4,8,12,16}.json` | short Codex JSONL sweeps |
| `tests/e2e/perf/cases/router_codex/qwen35_605s8t_gpu090_cache_aware_c4.json` | Codex soak (C4 only) |

Dataset notes (25×4, 13×8, 605×8):
[`benchmarks/dataset/EVAL_DATASETS.md`](benchmarks/dataset/EVAL_DATASETS.md).

## Router + Codex JSONL (vLLM + router + chat_jsonl)

Topology: one vLLM DP backend; optional cache-aware router in front. Bench client is
`tests/e2e/perf/benchmarks/chat_jsonl_bench.py` (not BenchKit). The router binary is **not**
vendored; set `ROUTER_BIN` to a built `vllm-router`.

```bash
export ROUTER_CODEX_DATA_DIR=/path/to/prefix_cache_datasets   # 12t pool download dir
export ROUTER_CODEX_MODEL_PATH=/path/to/Qwen3.5-4B
export ROUTER_BIN=/path/to/vllm-router

# Optional: prefetch the 12t pool (~1.2 GB) from HuggingFace
# huggingface-cli download herotai214/12t \
#   01_codex_swebenchpro_128k_filter_12t_pool.jsonl \
#   --local-dir "$ROUTER_CODEX_DATA_DIR"

pytest -s -v tests/e2e/perf/run_router_codex_benchmark.py \
  --test-config-file tests/e2e/perf/cases/router_codex/qwen35_13s8t_gpu055_cache_aware_c8.json

# DP baseline (no router)
pytest -s -v tests/e2e/perf/run_router_codex_benchmark.py \
  --test-config-file tests/e2e/perf/cases/router_codex/qwen35_13s8t_gpu055_dp_baseline_c8.json

# Override load without editing JSON
pytest -s -v tests/e2e/perf/run_router_codex_benchmark.py \
  --test-config-file tests/e2e/perf/cases/router_codex/qwen35_13s8t_gpu055_cache_aware_c8.json \
  --num-prompts 104 --max-concurrency 8
```

Case JSON uses the same `serve_env` / `server_params` pattern as agentbench perf cases.
Additional blocks:

| Field | Purpose |
| ----- | ------- |
| `mode` | `cache_aware` (vLLM + router) or `dp_baseline` (vLLM only) |
| `router_params` | Required for `cache_aware`: router binary, policy knobs, ports (`0` = auto) |
| `benchmark_params` | `sessions`/`turns`/`sample-order` (in-bench sample from pool), concurrency |
| `assertions` | Post-run checks on per-request JSONL and `summary_*.json` |

Environment overrides (optional):

| Variable | Purpose |
| -------- | --------- |
| `ROUTER_CODEX_DATA_DIR` | Local dir for the 12t pool JSONL (auto-download from [herotai214/12t](https://huggingface.co/herotai214/12t/tree/main)) |
| `ROUTER_CODEX_MODEL_PATH` | Overrides `server_params.model` |
| `ROUTER_BIN` | Overrides `router_params.bin` |

CLI: `--router-codex-dataset PATH` is the pool JSONL (`--input`). JSON `sessions`/`turns`
still sample at bench.

Artifacts land under `{result_root}/run-<hardware>-local-<mode>-<prompts>-<conc>-<tag>/`.

## AgentBench schema (vLLM + agentbench)

```json
{
  "test_name": "glm52_8x4_plan_subagent",
  "scenario": "agentinfer",
  "description": "…",
  "serve_env": { "…": "…" },
  "server_params": {
    "model": "/home/models/GLM-5.2-w4a8c8",
    "middleware": ["…"],
    "serve_args": { "tensor-parallel-size": 16 }
  },
  "result_root": "test-results/agentinfer",
  "benchmark_params": {
    "config": "agentinfer/agentbench/configs/swebench_vllm.yaml",
    "prepare_dataset": "swebench",
    "prepare_output_dir": "agentinfer/agentbench/data/swebench",
    "host": "127.0.0.1",
    "port": 8000,
    "task-num": 8,
    "max-concurrency": 4
  }
}
```

| Field | Purpose |
| ----- | ------- |
| `scenario` | `baseline` or `agentinfer` (legacy JSON key `arm` still accepted) |
| `mark` | Optional metadata (e.g. hardware notes for CI job selection) |
| `wait_for_vllm_ready` | Optional. Poll `/v1/models` after vLLM start (default: `true`) |
| `serve_env` | vLLM serve subprocess environment variables |
| `server_params` | Model path, middleware, and `vllm serve` CLI flags |
| `benchmark_params` | BenchKit `run` workload and dataset prepare settings |
| `benchmark_params.benchmark-run-as-user` | E2E-only. For `plan-subagent`, wrap bench with `sudo -u USER` when pytest is root |

## Usage

```bash
export ANTHROPIC_AUTH_TOKEN=agentinfer-local-smoke

# Baseline
pytest -s -v tests/e2e/perf/run_benchmark.py \
  --test-config-file tests/e2e/perf/cases/baseline/glm52_8x4_plan_subagent.json

# AgentInfer
pytest -s -v tests/e2e/perf/run_benchmark.py \
  --test-config-file tests/e2e/perf/cases/agentinfer/glm52_8x4_plan_subagent.json

# Override workload without editing JSON
pytest -s -v tests/e2e/perf/run_benchmark.py \
  --test-config-file tests/e2e/perf/cases/agentinfer/glm52_8x4_plan_subagent.json \
  --task-num 16 --max-concurrency 8
```

Logs stream to the terminal; vLLM output is also tee'd under `{result_root}/vllm-logs/`.
BenchKit stdout (including `Results:` and the tqdm bar) is suppressed during the run; the pytest
line `[benchmark:<scenario>] completed: ... dir=...` reports where artifacts were written.

Compare two finished runs manually with BenchKit:

```bash
vllm bench serve --agentinfer compare \
  --baseline test-results/vllm/run-... \
  --candidate test-results/agentinfer/run-...
```

## Module layout

| Module | Role |
| ------ | ---- |
| `run_benchmark.py` | Pytest entry: vLLM + AgentBench |
| `run_router_codex_benchmark.py` | Pytest entry: vLLM (+ router) + Codex JSONL |
| `helpers/case_loader.py` | AgentBench JSON parsing, `E2EPerfConfig`, argv/env builders |
| `helpers/router_case_loader.py` | Router Codex JSON parsing, `RouterCodexPerfConfig` |
| `helpers/server.py` | vLLM lifecycle, ready wait, port cleanup, log streaming |
| `helpers/router.py` | vllm-router lifecycle |
| `helpers/benchmark.py` | Dataset prepare, BenchKit run orchestration, run validation |
| `helpers/router_codex_benchmark.py` | Codex JSONL orchestration, metrics summary, assertions |
| `benchmarks/chat_jsonl_bench.py` | Session-serial Codex JSONL client |
| `benchmarks/router_metrics_summary.py` | Prometheus + per-request JSONL summary |
