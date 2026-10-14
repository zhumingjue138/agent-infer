# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the AgentInfer project

"""Unit tests for DSH SWE-bench functional run validation (no GPU / DSH CLI required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.helpers.functional import validate_dsh_swebench_run


def _write_minimal_completed_run(run_dir: Path, *, instance_id: str = "org__repo-1") -> None:
    run_dir.mkdir(parents=True)
    task_dir = run_dir / "tasks" / instance_id
    task_dir.mkdir(parents=True)
    workspace = run_dir / "workspaces" / instance_id
    workspace.mkdir(parents=True)
    (task_dir / "dsh-home").mkdir()
    (task_dir / "transcript.jsonl").write_text('{"role":"assistant","content":"ok"}\n', encoding="utf-8")
    (run_dir / "requests.jsonl").write_text("", encoding="utf-8")
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "lifecycle": {"status": "completed"},
                "tasks": {"completed": 1, "failed": 0},
                "requests": {"requests": 1},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "task_index.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "instance_id": instance_id,
                        "outcome": "completed",
                        "termination_reason": None,
                    }
                ],
                "failure_summary": {"by_termination_reason": {}, "total_failed": 0},
            }
        ),
        encoding="utf-8",
    )


def test_validate_dsh_swebench_run_accepts_minimal_layout(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_minimal_completed_run(run_dir)
    validate_dsh_swebench_run(run_dir)


def test_validate_dsh_swebench_run_rejects_hmr_stderr(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_minimal_completed_run(run_dir)
    stderr = run_dir / "tasks" / "org__repo-1" / "dsh.stderr.log"
    stderr.write_text("--expose-internals is required for HMR service\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="HMR startup error"):
        validate_dsh_swebench_run(run_dir)


def test_validate_dsh_swebench_run_rejects_startup_failure_bucket(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_minimal_completed_run(run_dir)
    task_index = json.loads((run_dir / "task_index.json").read_text(encoding="utf-8"))
    task_index["failure_summary"] = {"by_termination_reason": {"agent_startup_failed": 1}, "total_failed": 1}
    (run_dir / "task_index.json").write_text(json.dumps(task_index), encoding="utf-8")
    with pytest.raises(RuntimeError, match="agent_startup_failed"):
        validate_dsh_swebench_run(run_dir)
