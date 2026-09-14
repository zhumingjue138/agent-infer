# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the AgentInfer project
"""Pytest entry for router + Codex JSONL perf benchmarks."""

from __future__ import annotations

from pathlib import Path

import pytest

from .helpers.codex_dataset import dataset_from_pytest
from .helpers.router_case_loader import RouterCodexPerfConfig, load_router_codex_case_file
from .helpers.router_codex_benchmark import run_router_codex_benchmark, validate_router_codex_summary


@pytest.mark.e2e_perf
def test_router_codex_benchmark_completes(pytestconfig: pytest.Config) -> None:
    """Run one router Codex JSONL case selected by ``--test-config-file``."""

    test_config_file = pytestconfig.getoption("--test-config-file")
    if not test_config_file:
        pytest.skip("--test-config-file is required for router Codex perf benchmarks")

    case = load_router_codex_case_file(Path(test_config_file))
    config = RouterCodexPerfConfig.from_case(
        case,
        cli_dataset=dataset_from_pytest(pytestconfig),
    ).with_benchmark_load(
        num_prompts=pytestconfig.getoption("--num-prompts"),
        max_concurrency=pytestconfig.getoption("--max-concurrency"),
    )
    result_dir = run_router_codex_benchmark(config)
    outcome = validate_router_codex_summary(config, result_dir)
    summary = outcome["summary"]
    apc = (summary.get("apc_prefix_cache") or {}).get("hit_rate_pct", 0.0)
    decisions = (summary.get("cache_aware_decisions") or {}).get("total", 0)
    print(
        f"[router-codex:{config.case.test_name}] completed: "
        f"mode={config.mode.value} ok={outcome['successful_requests']} "
        f"apc_hit_rate={apc}% cache_aware_decisions={decisions} dir={result_dir}",
        flush=True,
    )
