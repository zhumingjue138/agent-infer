# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the AgentInfer project
"""Shared Codex eval cut definitions for dataset build and perf tests."""

from __future__ import annotations

from pathlib import Path

# Single parent pool for all E2E cuts. Hosted at:
# https://huggingface.co/herotai214/12t/tree/main
HF_POOL_REPO_ID = "herotai214/12t"
HF_POOL_REPO_TYPE = "model"
HF_POOL_PAGE = "https://huggingface.co/herotai214/12t/tree/main"
POOL_12T_FILE = "01_codex_swebenchpro_128k_filter_12t_pool.jsonl"

# Optional 8-turn convert artifact (gold rebuild from traces, not used by E2E).
FILTER_CHAT_FILE = "01_codex_swebenchpro_128k_filter_chat.jsonl"

# Optional pre-sampled gold cuts (also not vendored). E2E samples from the 12t pool.
TARGET_SPECS: dict[str, dict[str, object]] = {
    "25s4t": {
        "sessions": 25,
        "turns": 4,
        "file": "01_codex_swebenchpro_128k_filter_25s4t_chat.jsonl",
        "pool": POOL_12T_FILE,
        "rows": 100,
        "sample_order": "stratified_size",
    },
    "13s8t": {
        "sessions": 13,
        "turns": 8,
        "file": "01_codex_swebenchpro_128k_filter_13s8t_chat.jsonl",
        "pool": POOL_12T_FILE,
        "rows": 104,
        "sample_order": "stratified_size",
    },
    "605s8t": {
        "sessions": 605,
        "turns": 8,
        "file": "01_codex_swebenchpro_128k_filter_605s8t_chat.jsonl",
        "pool": POOL_12T_FILE,
        "rows": 4840,
        "sample_order": "stratified_size",
    },
}


def target_for_sessions_turns(sessions: int, turns: int) -> str:
    for name, spec in TARGET_SPECS.items():
        if spec["sessions"] == sessions and spec["turns"] == turns:
            return name
    supported = ", ".join(f"{spec['sessions']}×{spec['turns']}" for spec in TARGET_SPECS.values())
    raise ValueError(f"unsupported sessions×turns: {sessions}×{turns}; supported: {supported}")


def output_jsonl_path(data_dir: Path, sessions: int, turns: int) -> Path:
    """Return the pre-sampled gold JSONL path for this cut (optional artifact)."""

    target = target_for_sessions_turns(sessions, turns)
    filename = str(TARGET_SPECS[target]["file"])
    return (data_dir / filename).resolve()


def pool_jsonl_path(data_dir: Path, sessions: int | None = None, turns: int | None = None) -> Path:
    """Return the 12t pool that chat_jsonl_bench.py should take as --input.

    All supported cuts (25×4, 13×8, 605×8) share this file; N×T is sampled at bench.
    """

    if sessions is not None and turns is not None:
        target_for_sessions_turns(sessions, turns)
    return (data_dir / POOL_12T_FILE).resolve()


def default_sample_order(sessions: int, turns: int) -> str:
    target = target_for_sessions_turns(sessions, turns)
    return str(TARGET_SPECS[target].get("sample_order", "stratified_size"))
