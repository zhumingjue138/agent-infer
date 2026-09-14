# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the AgentInfer project
"""Run router + Codex JSONL perf benchmarks and validate summaries."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .router import managed_router, wait_for_health
from .router_case_loader import (
    RouterBenchMode,
    RouterCodexPerfConfig,
    build_chat_jsonl_argv,
    build_metrics_summary_argv,
    build_vllm_backend_argv,
    build_vllm_backend_env,
)
from .server import (
    _shutdown_process,
    ensure_port_free,
    join_streaming_output,
    start_streaming_output,
    wait_for_port_free,
)


@dataclass
class BackendVllmServer:
    """Launch one vLLM DP backend for router Codex perf cases."""

    config: RouterCodexPerfConfig
    proc: subprocess.Popen[str] | None = None
    log_path: Path | None = None
    _log_thread: Any = field(default=None, repr=False)

    def start(self) -> None:
        ensure_port_free(self.config.backend_host, self.config.backend_port)
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.config.log_dir / f"vllm-backend-{self.config.run_tag}.log"
        cmd = build_vllm_backend_argv(self.config)
        env = build_vllm_backend_env(self.config)
        prefix = f"[vllm-backend:{self.config.case.test_name}] "
        print(f"{prefix}starting: {' '.join(cmd)}", flush=True)
        print(f"{prefix}log file: {self.log_path}", flush=True)
        self.proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
            bufsize=1,
        )
        self._log_thread = start_streaming_output(
            self.proc,
            log_path=self.log_path,
            prefix=prefix,
        )
        if self.config.case.wait_for_vllm_ready:
            wait_for_health(
                self.config.backend_base_url,
                self.proc,
                log_path=self.log_path,
                prefix=prefix,
                timeout_seconds=self.config.bench_timeout_seconds,
                path="/v1/models",
            )

    def stop(self) -> None:
        if self.proc is None:
            return
        host, port = self.config.backend_host, self.config.backend_port
        proc = self.proc
        log_thread = self._log_thread
        prefix = f"[vllm-backend:{self.config.case.test_name}] "
        try:
            _shutdown_process(proc)
        finally:
            join_streaming_output(proc, log_thread)
            self.proc = None
            self._log_thread = None
            try:
                wait_for_port_free(host, port, timeout_seconds=30)
            except RuntimeError as exc:
                print(f"{prefix}warning: {exc}", file=sys.stderr, flush=True)

    def __enter__(self) -> BackendVllmServer:
        try:
            self.start()
        except Exception:
            self.stop()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def run_router_codex_benchmark(
    config: RouterCodexPerfConfig,
    *,
    result_dir: Path | None = None,
) -> Path:
    """Execute one router Codex JSONL case and return the artifact directory."""

    config = config.ensure_dataset()
    blockers = config.validate_prerequisites()
    if blockers:
        raise RuntimeError("; ".join(blockers))

    hardware_slug = config.resolve_run_hardware_slug()
    print(f"[router-codex:{config.case.test_name}] hardware slug: {hardware_slug}", flush=True)
    output_dir = result_dir or config.make_result_dir(hardware_slug=hardware_slug)
    if output_dir.exists():
        raise FileExistsError(f"result directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    metrics_dir = output_dir / "metrics"
    metrics_dir.mkdir()

    label = _bench_label(config)
    per_request_jsonl = output_dir / f"per_request_{label}.jsonl"
    summary_json = output_dir / f"summary_{label}.json"
    backend_prom = metrics_dir / f"{label}_backend.prom"
    router_prom = metrics_dir / f"{label}_router.prom"

    with BackendVllmServer(config=config):
        router_ctx = managed_router(config) if config.mode is RouterBenchMode.CACHE_AWARE else _nullcontext()
        with router_ctx:
            _invoke_chat_jsonl(config, output_dir, label=label, per_request_jsonl=per_request_jsonl)
            _scrape_metrics(config.backend_base_url, backend_prom)
            if config.mode is RouterBenchMode.CACHE_AWARE:
                _scrape_metrics(f"http://127.0.0.1:{config.router_prom_port}", router_prom)
            _invoke_metrics_summary(
                config,
                label=label,
                router_prom=router_prom if config.mode is RouterBenchMode.CACHE_AWARE else None,
                backend_prom=backend_prom,
                per_request_jsonl=per_request_jsonl,
                out_json=summary_json,
            )

    driver_log = output_dir / "driver.log"
    driver_log.write_text(
        "\n".join(
            [
                f"test_name={config.case.test_name}",
                f"mode={config.mode.value}",
                f"backend={config.backend_base_url}",
                f"client={config.client_base_url}",
                f"dataset={config.dataset}",
                f"summary={summary_json}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return output_dir


def validate_router_codex_summary(config: RouterCodexPerfConfig, result_dir: Path) -> dict[str, Any]:
    """Validate summary JSON against case assertions."""

    label = _bench_label(config)
    summary_path = result_dir / f"summary_{label}.json"
    if not summary_path.is_file():
        raise RuntimeError(f"summary JSON not found: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    per_request_path = result_dir / f"per_request_{label}.jsonl"
    ok_count = _count_ok_requests(per_request_path)

    assertions = config.case.assertions
    max_failed = assertions.get("max-failed-requests")
    if max_failed is not None:
        failed = _count_failed_requests(per_request_path)
        if failed > int(max_failed):
            raise RuntimeError(f"failed requests {failed} exceed max-failed-requests={max_failed}")

    min_ok = assertions.get("min-successful-requests")
    if min_ok is not None and ok_count < int(min_ok):
        raise RuntimeError(f"successful requests {ok_count} below min-successful-requests={min_ok}")

    if config.mode is RouterBenchMode.CACHE_AWARE:
        min_decisions = assertions.get("min-cache-aware-decisions-total")
        if min_decisions is not None:
            decisions = int((summary.get("cache_aware_decisions") or {}).get("total", 0))
            if decisions < int(min_decisions):
                raise RuntimeError(
                    f"cache_aware_decisions.total={decisions} below min-cache-aware-decisions-total={min_decisions}"
                )

    min_apc = assertions.get("min-apc-hit-rate-pct")
    if min_apc is not None:
        apc = float((summary.get("apc_prefix_cache") or {}).get("hit_rate_pct", 0.0))
        if apc < float(min_apc):
            raise RuntimeError(f"apc hit rate {apc:.2f}% below min-apc-hit-rate-pct={min_apc}")

    return {
        "summary": summary,
        "successful_requests": ok_count,
        "result_dir": result_dir,
    }


def _bench_label(config: RouterCodexPerfConfig) -> str:
    if config.mode is RouterBenchMode.DP_BASELINE:
        return "dp_baseline"
    router = config.case.router
    assert router is not None
    return f"cache_aware_{router.config_label}"


def _invoke_chat_jsonl(
    config: RouterCodexPerfConfig,
    result_dir: Path,
    *,
    label: str,
    per_request_jsonl: Path,
) -> None:
    argv = build_chat_jsonl_argv(
        config,
        result_dir=result_dir,
        label=label,
        per_request_jsonl=per_request_jsonl,
    )
    bench_log = result_dir / f"bench_{label}.log"
    prefix = f"[router-codex:{config.case.test_name}] "
    print(f"{prefix}running chat_jsonl: {' '.join(argv)}", flush=True)
    completed = subprocess.run(
        argv,
        check=False,
        text=True,
        capture_output=True,
    )
    bench_log.write_text(
        (completed.stdout or "") + (completed.stderr or ""),
        encoding="utf-8",
    )
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "")[-2000:]
        raise RuntimeError(f"chat_jsonl_bench exited with code {completed.returncode}: {tail}")


def _invoke_metrics_summary(
    config: RouterCodexPerfConfig,
    *,
    label: str,
    router_prom: Path | None,
    backend_prom: Path,
    per_request_jsonl: Path,
    out_json: Path,
) -> None:
    argv = build_metrics_summary_argv(
        config,
        label=label,
        router_prom=router_prom,
        backend_prom=backend_prom,
        per_request_jsonl=per_request_jsonl,
        out_json=out_json,
    )
    prefix = f"[router-codex:{config.case.test_name}] "
    print(f"{prefix}summarizing metrics", flush=True)
    completed = subprocess.run(argv, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "")[-2000:]
        raise RuntimeError(f"router_metrics_summary exited with code {completed.returncode}: {tail}")
    if not out_json.is_file():
        raise RuntimeError(f"metrics summary did not write {out_json}")


def _scrape_metrics(base_url: str, out_path: Path) -> None:
    url = f"{base_url.rstrip('/')}/metrics"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    response = httpx.get(url, timeout=30, trust_env=False)
    response.raise_for_status()
    out_path.write_text(response.text, encoding="utf-8")


def _count_ok_requests(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("ok"):
            count += 1
    return count


def _count_failed_requests(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("ok"):
            count += 1
    return count


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb) -> None:
        return None
