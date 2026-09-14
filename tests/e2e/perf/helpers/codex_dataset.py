# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the AgentInfer project
"""Resolve the Codex 12t pool and sample N×T at bench.

All cuts share one HuggingFace file:

  https://huggingface.co/herotai214/12t/tree/main
  01_codex_swebenchpro_128k_filter_12t_pool.jsonl

  python3 chat_jsonl_bench.py --input pool.jsonl --sessions N --turns T ...
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResolvedCodexDataset:
    path: Path
    data_dir: Path | None
    sessions: int | None
    turns: int | None
    explicit_path: bool
    sample_at_bench: bool


def dataset_from_pytest(pytestconfig: Any) -> Path | None:
    """Return ``--router-codex-dataset`` when set on the pytest CLI."""

    raw = pytestconfig.getoption("--router-codex-dataset", default=None)
    if raw is None or raw == "":
        return None
    return Path(str(raw))


def resolve_codex_dataset(
    benchmark: dict[str, Any],
    repo: Path,
    *,
    cli_dataset: Path | str | None = None,
) -> ResolvedCodexDataset:
    """Resolve the 12t pool JSONL that becomes ``chat_jsonl_bench.py --input``.

    Two modes:

    1. **CLI override** — ``--router-codex-dataset /path/to/pool.jsonl``
       Uses that file as ``--input``. JSON ``sessions`` / ``turns`` still
       sample at bench when present.

    2. **JSON-driven** — no CLI dataset flag
       ``$ROUTER_CODEX_DATA_DIR/01_codex_swebenchpro_128k_filter_12t_pool.jsonl``.
       Downloads from ``herotai214/12t`` when missing. ``sessions`` × ``turns``
       only select the in-bench sample (25×4, 13×8, or 605×8).
    """

    sessions = _coerce_int(benchmark.get("sessions"))
    turns = _coerce_int(benchmark.get("turns"))
    sample_at_bench = sessions is not None and turns is not None

    if cli_dataset is not None and str(cli_dataset).strip():
        path = _expand(str(cli_dataset).strip(), repo)
        if sessions is not None and turns is not None:
            _load_targets_module(repo).target_for_sessions_turns(sessions, turns)
        return ResolvedCodexDataset(
            path=path,
            data_dir=None,
            sessions=sessions,
            turns=turns,
            explicit_path=True,
            sample_at_bench=sample_at_bench,
        )

    if sessions is None or turns is None:
        raise ValueError(
            "benchmark_params.sessions and benchmark_params.turns are required when --router-codex-dataset is not set."
        )

    data_dir_raw = os.environ.get("ROUTER_CODEX_DATA_DIR")
    if not isinstance(data_dir_raw, str) or not data_dir_raw.strip():
        raise ValueError(
            "Set ROUTER_CODEX_DATA_DIR when using JSON sessions/turns, or pass "
            "--router-codex-dataset for a pool JSONL file."
        )

    data_dir = _expand(data_dir_raw.strip(), repo)
    targets = _load_targets_module(repo)
    try:
        path = targets.pool_jsonl_path(data_dir, sessions, turns)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    return ResolvedCodexDataset(
        path=path,
        data_dir=data_dir,
        sessions=sessions,
        turns=turns,
        explicit_path=False,
        sample_at_bench=True,
    )


def ensure_codex_dataset(
    resolved: ResolvedCodexDataset,
    *,
    repo: Path,
    tokenizer_model: Path | None = None,
    python: str | None = None,
) -> Path:
    """Return the 12t pool path, downloading from HuggingFace when missing."""

    del tokenizer_model, python

    if resolved.path.is_file():
        print(f"[router-codex] pool ready: {resolved.path}", flush=True)
        return resolved.path

    if resolved.explicit_path:
        raise FileNotFoundError(
            f"pool JSONL does not exist: {resolved.path}\n"
            "Pass an existing 12t pool file, or omit --router-codex-dataset to "
            "download herotai214/12t under ROUTER_CODEX_DATA_DIR."
        )

    if resolved.data_dir is None:
        raise FileNotFoundError("internal error: data_dir required for HuggingFace pool download")

    targets = _load_targets_module(repo)
    path = _download_hf_12t_pool(resolved.data_dir, targets)
    if not path.is_file():
        raise FileNotFoundError(f"pool download finished but file missing: {path}")
    print(f"[router-codex] pool ready: {path}", flush=True)
    return path


def _download_hf_12t_pool(data_dir: Path, targets: Any) -> Path:
    dest = (data_dir / str(targets.POOL_12T_FILE)).resolve()
    if dest.is_file():
        return dest

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise FileNotFoundError(
            "huggingface_hub is required to download the 12t pool. "
            "Install it, or place "
            f"{targets.POOL_12T_FILE} under ROUTER_CODEX_DATA_DIR, "
            f"or pass --router-codex-dataset. Source: {targets.HF_POOL_PAGE}"
        ) from exc

    data_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"[router-codex] downloading {targets.HF_POOL_REPO_ID}/{targets.POOL_12T_FILE} "
        f"-> {data_dir} ({targets.HF_POOL_PAGE})",
        flush=True,
    )
    downloaded = Path(
        hf_hub_download(
            repo_id=str(targets.HF_POOL_REPO_ID),
            filename=str(targets.POOL_12T_FILE),
            repo_type=str(targets.HF_POOL_REPO_TYPE),
            local_dir=str(data_dir),
        )
    )
    if downloaded.resolve() != dest and downloaded.is_file() and not dest.is_file():
        shutil.copy2(downloaded, dest)
    return dest


def _load_targets_module(repo: Path) -> Any:
    module_path = (repo / "tests" / "e2e" / "perf" / "benchmarks" / "dataset" / "codex_eval_targets.py").resolve()
    spec = importlib.util.spec_from_file_location("codex_eval_targets", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _coerce_int(raw: Any) -> int | None:
    if raw is None:
        return None
    return int(raw)


def _expand(path_raw: str, repo: Path) -> Path:
    path = Path(path_raw).expanduser()
    return path if path.is_absolute() else (repo / path).resolve()
