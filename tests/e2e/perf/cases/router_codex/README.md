# Router Codex perf cases

JSON cases for `tests/e2e/perf/run_router_codex_benchmark.py`.

## Datasets are NOT in the repo

One **12-turn pool** on HuggingFace is the `--input` for every cut:

- Repo: [herotai214/12t](https://huggingface.co/herotai214/12t/tree/main)
- File: `01_codex_swebenchpro_128k_filter_12t_pool.jsonl`

Each case sets **`sessions` × `turns`** and **`max-concurrency`** (`c4`/`c8`/`c12`/`c16`):

```bash
python3 tests/e2e/perf/benchmarks/chat_jsonl_bench.py \
  --base-url http://127.0.0.1:PORT \
  --model SERVED_MODEL_NAME \
  --input "$ROUTER_CODEX_DATA_DIR/01_codex_swebenchpro_128k_filter_12t_pool.jsonl" \
  --sessions N --turns T --sample-order stratified_size \
  --fire-mode session_serial \
  --max-concurrency C --max-tokens 256
```

| JSON `sessions`×`turns` | Rows | Default `--sample-order` |
| ------------------------- | ------ | -------------------------- |
| 25×4 | 100 | `stratified_size` |
| 13×8 | 104 | `stratified_size` |
| 605×8 | 4840 | `stratified_size` |

`max-num-seqs` matches `max-concurrency` (4 / 8 / 12 / 16). With
`session_serial`, in-flight sessions cannot exceed the number of sessions in
the cut (e.g. 13s8t at C16 is still at most 13 live sessions).

At run time:

- **No CLI flag** → `$ROUTER_CODEX_DATA_DIR/..._12t_pool.jsonl`; download from
  `herotai214/12t` when missing.
- **`--router-codex-dataset PATH`** → that file is `--input`. JSON
  `sessions`/`turns` still sample at bench.

Manual download (optional; ~1.2 GB):

```bash
export ROUTER_CODEX_DATA_DIR=/path/to/prefix_cache_datasets
huggingface-cli download herotai214/12t \
  01_codex_swebenchpro_128k_filter_12t_pool.jsonl \
  --local-dir "$ROUTER_CODEX_DATA_DIR"
```

See [`EVAL_DATASETS.md`](../../benchmarks/dataset/EVAL_DATASETS.md).

## Scenario matrix

Filename: `qwen35_{cut}_gpu{055|090}_{dp_baseline|cache_aware}_c{4|8|12|16}.json`
(605s8t soak is **C4 only**.)

| Dataset | GPU mem | Mode | Concurrency |
| --------- | --------- | ------ | ------------- |
| 25s4t | 0.9 | DP baseline, cache-aware | 4, 8, 12, 16 |
| 13s8t | 0.9 | DP baseline, cache-aware | 4, 8, 12, 16 |
| 13s8t | 0.55 | DP baseline, cache-aware | 4, 8, 12, 16 |
| 605s8t (long soak) | 0.9 | cache-aware | **4 only** |

25 JSON files (6 short topologies × 4 conc + 1 soak). Shared defaults: DP=2, mt256,
`lb_mid:0.3:2:1.5`, `session_serial`.

## Run

```bash
export ROUTER_CODEX_DATA_DIR=/path/to/prefix_cache_datasets
export ROUTER_CODEX_MODEL_PATH=/path/to/Qwen3.5-4B
export ROUTER_BIN=/path/to/vllm-router

pytest -s -v tests/e2e/perf/run_router_codex_benchmark.py \
  --test-config-file tests/e2e/perf/cases/router_codex/qwen35_13s8t_gpu055_cache_aware_c8.json
```

Use a specific pool file:

```bash
pytest -s -v tests/e2e/perf/run_router_codex_benchmark.py \
  --test-config-file tests/e2e/perf/cases/router_codex/qwen35_13s8t_gpu055_cache_aware_c8.json \
  --router-codex-dataset /path/to/01_codex_swebenchpro_128k_filter_12t_pool.jsonl
```

## Batch (sequential)

```bash
SCENARIOS=(
  qwen35_25s4t_gpu090_dp_baseline
  qwen35_25s4t_gpu090_cache_aware
  qwen35_13s8t_gpu090_dp_baseline
  qwen35_13s8t_gpu090_cache_aware
  qwen35_13s8t_gpu055_dp_baseline
  qwen35_13s8t_gpu055_cache_aware
)
for s in "${SCENARIOS[@]}"; do
  for c in 4 8 12 16; do
    pytest -s -v tests/e2e/perf/run_router_codex_benchmark.py \
      --test-config-file "tests/e2e/perf/cases/router_codex/${s}_c${c}.json"
  done
done
pytest -s -v tests/e2e/perf/run_router_codex_benchmark.py \
  --test-config-file tests/e2e/perf/cases/router_codex/qwen35_605s8t_gpu090_cache_aware_c4.json
```
