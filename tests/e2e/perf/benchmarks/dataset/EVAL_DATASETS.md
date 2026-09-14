# Codex eval JSONL: 25×4, 13×8, 605×8

Three standard cuts for router cache-aware perf. JSONL is **not** checked in.

E2E uses **one** parent pool on HuggingFace, then samples N×T at bench:

- [herotai214/12t](https://huggingface.co/herotai214/12t/tree/main)
- `01_codex_swebenchpro_128k_filter_12t_pool.jsonl` (~1.2 GB)

| Cut | Rows | `--sessions` | `--turns` | `--sample-order` |
| ----- | ------ | -------------- | ----------- | ------------------ |
| **25s4t** | 100 | 25 | 4 | `stratified_size` |
| **13s8t** | 104 | 13 | 8 | `stratified_size` |
| **605s8t** | 4840 | 605 | 8 | `stratified_size` |

## Download the pool

```bash
export DATA=/path/to/prefix_cache_datasets

huggingface-cli download herotai214/12t \
  01_codex_swebenchpro_128k_filter_12t_pool.jsonl \
  --local-dir "$DATA"
```

Needs `huggingface_hub`. E2E does the same download into `$ROUTER_CODEX_DATA_DIR`
when the file is missing.

## Bench (same `--input`, change N×T)

```bash
python3 tests/e2e/perf/benchmarks/chat_jsonl_bench.py \
  --base-url http://127.0.0.1:18180 \
  --model qwen35-4b-dp \
  --input "$DATA/01_codex_swebenchpro_128k_filter_12t_pool.jsonl" \
  --sessions 25 --turns 4 --sample-order stratified_size \
  --fire-mode session_serial \
  --max-concurrency 4 --max-tokens 256
```

Swap to `--sessions 13 --turns 8` or `--sessions 605 --turns 8`.

## Use with pytest

```bash
export ROUTER_CODEX_DATA_DIR="$DATA"
export ROUTER_BIN=/path/to/vllm-router

pytest -s -v tests/e2e/perf/run_router_codex_benchmark.py \
  --test-config-file tests/e2e/perf/cases/router_codex/qwen35_13s8t_gpu055_cache_aware_c8.json
```

E2E case JSON: `tests/e2e/perf/cases/router_codex/README.md`
(25s4t/13s8t × C4/8/12/16; 605s8t soak is C4 only).
