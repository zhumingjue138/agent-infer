# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the AgentInfer project
"""Load router + Codex JSONL perf cases and build runtime argv/env."""

from __future__ import annotations

import json
import os
import socket
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .case_loader import (
    VllmDeployConfig,
    _hardware_slug_from_mark,
    _parse_deploy_case,
    _repo_root,
    _resolve_repo_path,
    ensure_directory,
    serve_args_to_argv,
)
from .codex_dataset import (
    ResolvedCodexDataset,
    ensure_codex_dataset,
    resolve_codex_dataset,
)

DEFAULT_HOST = "127.0.0.1"
_ROUTER_BIN_ENV = "ROUTER_BIN"
_MODEL_PATH_ENV = "ROUTER_CODEX_MODEL_PATH"
_DATA_DIR_ENV = "ROUTER_CODEX_DATA_DIR"
_DATASET_BUILD_HINT = (
    "Codex 12t pool JSONL is not checked in. Set ROUTER_CODEX_DATA_DIR "
    "(auto-downloads herotai214/12t) or pass --router-codex-dataset. "
    "All cuts sample that one file:\n"
    "  python3 tests/e2e/perf/benchmarks/chat_jsonl_bench.py --input "
    "01_codex_swebenchpro_128k_filter_12t_pool.jsonl "
    "--sessions N --turns T --sample-order stratified_size ...\n"
    "https://huggingface.co/herotai214/12t/tree/main\n"
    "See tests/e2e/perf/benchmarks/dataset/EVAL_DATASETS.md"
)


class RouterBenchMode(str, Enum):
    """How the Codex JSONL client reaches inference."""

    DP_BASELINE = "dp_baseline"
    CACHE_AWARE = "cache_aware"


@dataclass(frozen=True)
class RouterDeployConfig:
    """vllm-router deployment parameters."""

    bin: Path
    host: str
    port: int
    prom_port: int
    policy: str
    cache_threshold: float
    balance_abs_threshold: int
    balance_rel_threshold: float
    chat_routing_key_mode: str
    intra_node_data_parallel_size: int
    config_label: str


@dataclass(frozen=True)
class RouterCodexCase:
    """One router + Codex JSONL perf case loaded from JSON."""

    test_name: str
    topology: str
    mode: RouterBenchMode
    description: str
    mark: tuple[Any, ...]
    deploy: VllmDeployConfig
    router: RouterDeployConfig | None
    benchmark_params: dict[str, Any]
    assertions: dict[str, Any]
    wait_for_vllm_ready: bool
    wait_for_router_ready: bool


@dataclass(frozen=True)
class RouterCodexPerfConfig:
    """Runtime state for one router Codex JSONL benchmark."""

    case: RouterCodexCase
    backend_host: str
    backend_port: int
    router_host: str
    router_port: int
    router_prom_port: int
    served_model_name: str
    dataset: Path
    num_prompts: int
    max_concurrency: int
    max_tokens: int | None
    fire_mode: str
    bench_timeout_seconds: float
    trace_requests: bool
    result_root: Path
    run_tag: str
    log_dir: Path
    dataset_plan: ResolvedCodexDataset
    bench_sessions: int | None
    bench_turns: int | None
    sample_order: str
    sample_at_bench: bool

    @property
    def repo_root(self) -> Path:
        return _repo_root()

    @property
    def backend_base_url(self) -> str:
        return f"http://{self.backend_host}:{self.backend_port}"

    @property
    def router_base_url(self) -> str:
        return f"http://{self.router_host}:{self.router_port}"

    @property
    def client_base_url(self) -> str:
        if self.case.mode is RouterBenchMode.DP_BASELINE:
            return self.backend_base_url
        return self.router_base_url

    @property
    def mode(self) -> RouterBenchMode:
        return self.case.mode

    @classmethod
    def from_case(
        cls,
        case: RouterCodexCase,
        *,
        repo: Path | None = None,
        cli_dataset: Path | None = None,
    ) -> RouterCodexPerfConfig:
        repo = repo or _repo_root()
        benchmark = case.benchmark_params
        serve_args = dict(case.deploy.serve_args)

        backend_host = str(serve_args.get("host", DEFAULT_HOST))
        backend_port = _resolve_port(serve_args.get("port", 0))

        router = case.router
        if case.mode is RouterBenchMode.CACHE_AWARE:
            if router is None:
                raise ValueError(f"case {case.test_name!r} requires router_params for cache_aware mode")
            router_host = router.host
            router_port = router.port
            router_prom_port = router.prom_port
        else:
            router_host = DEFAULT_HOST
            router_port = 0
            router_prom_port = 0

        served_model_name = str(serve_args.get("served-model-name", case.deploy.model.name or "model"))
        dataset_plan = resolve_codex_dataset(benchmark, repo, cli_dataset=cli_dataset)

        result_root = _resolve_repo_path(case.deploy.result_root, repo)
        run_tag = _make_run_tag()

        max_tokens_raw = benchmark.get("max-tokens")
        max_tokens = int(max_tokens_raw) if max_tokens_raw is not None else None

        return cls(
            case=case,
            backend_host=backend_host,
            backend_port=backend_port,
            router_host=router_host,
            router_port=router_port,
            router_prom_port=router_prom_port,
            served_model_name=served_model_name,
            dataset=dataset_plan.path,
            num_prompts=int(benchmark.get("num-prompts", benchmark.get("limit", 16))),
            max_concurrency=int(benchmark.get("max-concurrency", 4)),
            max_tokens=max_tokens,
            fire_mode=str(benchmark.get("fire-mode", "session_serial")),
            bench_timeout_seconds=float(benchmark.get("timeout", 3600)),
            trace_requests=bool(benchmark.get("trace-requests", False)),
            result_root=result_root,
            run_tag=run_tag,
            log_dir=result_root / "router-codex-logs" / f"{case.test_name}-{run_tag}",
            dataset_plan=dataset_plan,
            bench_sessions=dataset_plan.sessions,
            bench_turns=dataset_plan.turns,
            sample_order=str(
                benchmark.get("sample-order") or _default_sample_order(dataset_plan.sessions, dataset_plan.turns)
            ),
            sample_at_bench=dataset_plan.sample_at_bench,
        )

    def with_benchmark_load(
        self,
        *,
        num_prompts: int | None = None,
        max_concurrency: int | None = None,
    ) -> RouterCodexPerfConfig:
        updates: dict[str, int] = {}
        if num_prompts is not None:
            updates["num_prompts"] = num_prompts
        if max_concurrency is not None:
            updates["max_concurrency"] = max_concurrency
        return replace(self, **updates) if updates else self

    def ensure_dataset(self) -> RouterCodexPerfConfig:
        """Download the 12t pool when missing (N×T is sampled at bench)."""

        path = ensure_codex_dataset(
            self.dataset_plan,
            repo=self.repo_root,
        )
        if path == self.dataset:
            return self
        plan = replace(self.dataset_plan, path=path)
        return replace(self, dataset=path, dataset_plan=plan)

    def make_result_dir(self, *, hardware_slug: str) -> Path:
        name = (
            f"run-{hardware_slug}-local-{self.case.mode.value}-{self.num_prompts}-{self.max_concurrency}-{self.run_tag}"
        )
        ensure_directory(self.result_root, run_as_user=None)
        return self.result_root / name

    def resolve_run_hardware_slug(self) -> str:
        mark_slug = _hardware_slug_from_mark(self.case.mark)
        if mark_slug:
            return mark_slug
        return "unknown"

    def validate_prerequisites(self) -> list[str]:
        blockers: list[str] = []
        model_path = self.case.deploy.model
        if not model_path.exists():
            blockers.append(f"model path does not exist: {model_path}")
        if not self.dataset.exists():
            blockers.append(
                f"pool JSONL does not exist: {self.dataset}\n"
                "Omit --router-codex-dataset to auto-download herotai214/12t "
                "under ROUTER_CODEX_DATA_DIR, or pass a local pool path.\n"
                f"{_DATASET_BUILD_HINT}"
            )
        if self.case.mode is RouterBenchMode.CACHE_AWARE:
            router_bin = self.case.router.bin if self.case.router else None
            if router_bin is None or not (router_bin.exists() or _is_executable(router_bin)):
                blockers.append(f"router binary is unavailable: {router_bin}")
        chat_jsonl = self.chat_jsonl_script()
        if not chat_jsonl.is_file():
            blockers.append(f"chat_jsonl_bench.py not found: {chat_jsonl}")
        return blockers

    def chat_jsonl_script(self) -> Path:
        return (self.repo_root / "tests" / "e2e" / "perf" / "benchmarks" / "chat_jsonl_bench.py").resolve()

    def metrics_summary_script(self) -> Path:
        return (self.repo_root / "tests" / "e2e" / "perf" / "benchmarks" / "router_metrics_summary.py").resolve()


def _default_sample_order(_sessions: int | None, _turns: int | None) -> str:
    return "stratified_size"


def _resolve_dataset_path(benchmark: dict[str, Any], repo: Path) -> Path:
    """Legacy helper; prefer resolve_codex_dataset()."""

    return resolve_codex_dataset(benchmark, repo).path


def _make_run_tag() -> str:
    return f"{datetime.now().strftime('%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def _find_available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEFAULT_HOST, 0))
        return int(sock.getsockname()[1])


def _resolve_port(raw: Any) -> int:
    if raw in (0, "0", "auto", None, ""):
        return _find_available_port()
    return int(raw)


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _parse_router_config(raw: str) -> tuple[str, float, int, float]:
    parts = raw.split(":")
    if len(parts) != 4:
        raise ValueError(f"router config must be label:cache:abs:rel, got {raw!r}")
    label, cache, abs_th, rel = parts
    return label, float(cache), int(float(abs_th)), float(rel)


def load_router_codex_case_file(case_file: Path) -> RouterCodexCase:
    """Load one router Codex JSONL perf case."""

    path = case_file.expanduser()
    if not path.is_file() or path.suffix != ".json":
        raise ValueError(f"--test-config-file must point to a .json file: {case_file}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"case JSON root must be an object: {case_file}")
    return _parse_router_codex_case(payload, case_path=path)


def _parse_router_codex_case(payload: dict[str, Any], *, case_path: Path) -> RouterCodexCase:
    test_name = payload.get("test_name")
    if not isinstance(test_name, str) or not test_name:
        raise ValueError(f"router codex case missing non-empty test_name: {case_path}")

    topology = str(payload.get("topology", "single_router_dp"))
    mode_raw = payload.get("mode", payload.get("router_params", {}).get("mode", "cache_aware"))
    try:
        mode = RouterBenchMode(str(mode_raw).lower())
    except ValueError as exc:
        raise ValueError(f"invalid mode {mode_raw!r} in {case_path}; expected dp_baseline or cache_aware") from exc

    benchmark_params = payload.get("benchmark_params")
    if not isinstance(benchmark_params, dict):
        raise ValueError(f"case {test_name!r} missing benchmark_params object")

    serve_env_raw = payload.get("serve_env", {})
    if not isinstance(serve_env_raw, dict):
        raise ValueError(f"case {test_name!r} serve_env must be an object")

    mark_raw = payload.get("mark", [])
    if not isinstance(mark_raw, list):
        raise ValueError(f"case {test_name!r} mark must be a list")

    assertions_raw = payload.get("assertions", {})
    if not isinstance(assertions_raw, dict):
        raise ValueError(f"case {test_name!r} assertions must be an object")

    wait_for_vllm_ready = payload.get("wait_for_vllm_ready", True)
    if not isinstance(wait_for_vllm_ready, bool):
        raise ValueError(f"case {test_name!r} wait_for_vllm_ready must be a boolean")

    wait_for_router_ready = payload.get("wait_for_router_ready", True)
    if not isinstance(wait_for_router_ready, bool):
        raise ValueError(f"case {test_name!r} wait_for_router_ready must be a boolean")

    server_params = payload.get("server_params")
    if isinstance(server_params, dict):
        model_override = os.environ.get(_MODEL_PATH_ENV)
        if model_override:
            server_params = dict(server_params)
            server_params["model"] = model_override

    deploy = _parse_deploy_case(
        test_name,
        {
            "server_params": server_params,
            "serve_env": serve_env_raw,
            "result_root": payload.get("result_root", "test-results/router-codex"),
        },
        case_label=f"router codex case {test_name!r}",
    )

    router = None
    if mode is RouterBenchMode.CACHE_AWARE:
        router_params = payload.get("router_params")
        if not isinstance(router_params, dict):
            raise ValueError(f"case {test_name!r} missing router_params for cache_aware mode")
        router = _parse_router_deploy(test_name, router_params, deploy=deploy)

    return RouterCodexCase(
        test_name=test_name,
        topology=topology,
        mode=mode,
        description=str(payload.get("description", "")),
        mark=tuple(mark_raw),
        deploy=deploy,
        router=router,
        benchmark_params=dict(benchmark_params),
        assertions=dict(assertions_raw),
        wait_for_vllm_ready=wait_for_vllm_ready,
        wait_for_router_ready=wait_for_router_ready,
    )


def _parse_router_deploy(
    test_name: str,
    params: dict[str, Any],
    *,
    deploy: VllmDeployConfig,
) -> RouterDeployConfig:
    repo = _repo_root()
    bin_raw = os.environ.get(_ROUTER_BIN_ENV) or params.get("bin", "agentrouter/target/release/vllm-router")
    router_bin = Path(str(bin_raw)).expanduser()
    if not router_bin.is_absolute():
        router_bin = (repo / router_bin).resolve()

    config_raw = params.get("config")
    if isinstance(config_raw, str) and config_raw:
        label, cache, abs_th, rel = _parse_router_config(config_raw)
    else:
        label = str(params.get("label", "lb_mid"))
        cache = float(params.get("cache-threshold", 0.3))
        abs_th = int(float(params.get("balance-abs-threshold", 2)))
        rel = float(params.get("balance-rel-threshold", 1.5))

    dp_size = int(params.get("intra-node-data-parallel-size", deploy.serve_args.get("data-parallel-size", 2)))
    host = str(params.get("host", DEFAULT_HOST))
    port = _resolve_port(params.get("port", 0))
    prom_port = _resolve_port(params.get("prometheus-port", params.get("prom-port", 0)))

    return RouterDeployConfig(
        bin=router_bin,
        host=host,
        port=port,
        prom_port=prom_port,
        policy=str(params.get("policy", "cache_aware")),
        cache_threshold=cache,
        balance_abs_threshold=abs_th,
        balance_rel_threshold=rel,
        chat_routing_key_mode=str(params.get("chat-routing-key-mode", "session-id-full-history-fallback")),
        intra_node_data_parallel_size=dp_size,
        config_label=label,
    )


def build_vllm_backend_argv(config: RouterCodexPerfConfig) -> list[str]:
    serve_args = dict(config.case.deploy.serve_args)
    serve_args["host"] = config.backend_host
    serve_args["port"] = config.backend_port
    return serve_args_to_argv(config.case.deploy.model, serve_args, config.case.deploy.middleware)


def build_vllm_backend_env(config: RouterCodexPerfConfig) -> dict[str, str]:
    env = os.environ.copy()
    env.update(config.case.deploy.serve_env)
    return env


def build_router_argv(config: RouterCodexPerfConfig) -> list[str]:
    router = config.case.router
    if router is None:
        raise ValueError("router argv requested for dp_baseline case")
    return [
        str(router.bin),
        "--host",
        router.host,
        "--port",
        str(router.port),
        "--worker-urls",
        config.backend_base_url,
        "--policy",
        router.policy,
        "--prometheus-port",
        str(router.prom_port),
        "--cache-threshold",
        str(router.cache_threshold),
        "--balance-abs-threshold",
        str(router.balance_abs_threshold),
        "--balance-rel-threshold",
        str(router.balance_rel_threshold),
        "--chat-routing-key-mode",
        router.chat_routing_key_mode,
        "--intra-node-data-parallel-size",
        str(router.intra_node_data_parallel_size),
    ]


def build_chat_jsonl_argv(
    config: RouterCodexPerfConfig,
    *,
    result_dir: Path,
    label: str,
    per_request_jsonl: Path,
) -> list[str]:
    argv = [
        sys.executable,
        str(config.chat_jsonl_script()),
        "--base-url",
        config.client_base_url,
        "--model",
        config.served_model_name,
        "--input",
        str(config.dataset),
    ]
    if config.sample_at_bench:
        if config.bench_sessions is not None:
            argv.extend(["--sessions", str(config.bench_sessions)])
        if config.bench_turns is not None:
            argv.extend(["--turns", str(config.bench_turns)])
        argv.extend(["--sample-order", config.sample_order])
        argv.extend(["--sampled-jsonl", str(result_dir / f"sampled_{label}.jsonl")])
    argv.extend(
        [
            "--fire-mode",
            config.fire_mode,
            "--max-concurrency",
            str(config.max_concurrency),
        ]
    )
    if config.max_tokens is not None:
        argv.extend(["--max-tokens", str(config.max_tokens)])
    argv.extend(
        [
            "--limit",
            str(config.num_prompts),
            "--label",
            label,
            "--timeout",
            str(config.bench_timeout_seconds),
            "--per-request-jsonl",
            str(per_request_jsonl),
        ]
    )
    if config.trace_requests:
        argv.append("--trace-requests")
    benchmark = config.case.benchmark_params
    for key, flag in (
        ("active-sessions", "--active-sessions"),
        ("active-session-mode", "--active-session-mode"),
        ("duration-seconds", "--duration-seconds"),
    ):
        if key not in benchmark:
            continue
        argv.extend([flag, str(benchmark[key])])
    if benchmark.get("wrap-last-wave") is True:
        argv.append("--wrap-last-wave")
    elif benchmark.get("wrap-last-wave") is False:
        argv.append("--no-wrap-last-wave")
    if benchmark.get("repeat-dataset"):
        argv.extend(["--repeat-dataset", str(benchmark["repeat-dataset"])])
    return argv


def build_metrics_summary_argv(
    config: RouterCodexPerfConfig,
    *,
    label: str,
    router_prom: Path | None,
    backend_prom: Path,
    per_request_jsonl: Path,
    out_json: Path,
) -> list[str]:
    argv = [
        "python",
        str(config.metrics_summary_script()),
    ]
    if config.case.mode is RouterBenchMode.CACHE_AWARE and router_prom is not None:
        argv.append(f"http://127.0.0.1:{config.router_prom_port}")
    else:
        argv.append(str(backend_prom))
    argv.extend(
        [
            "--workers",
            str(backend_prom),
            "--per-request-jsonl",
            str(per_request_jsonl),
            "--out",
            str(out_json),
            "--brief-only",
            "--label",
            label,
        ]
    )
    return argv
