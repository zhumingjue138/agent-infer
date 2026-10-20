#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the AgentInfer project
"""Router-replay L1 prerequisite: start serve, calibrate, write program JSON, stop serve.

Run from the repository root:

    python tests/e2e/perf/router-replay/prepare_l1.py \\
        --serve-config-file tests/e2e/perf/router-replay/configs/l1-serve.json

Stable artifacts (default ``results/router-replay/l1/``):

- ``program-scheduling.json`` — ``cases/program_scheduling/``
- ``program-scheduling-binding-only.json`` — ``cases/binding_only/``

L2 pytest cases only reference these paths; they do not re-run calibration.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_import_paths() -> None:
    suite_dir = Path(__file__).resolve().parent
    repo_root = suite_dir.parents[3]
    for path in (repo_root, suite_dir):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)


def _print_next_steps(output_root: Path) -> None:
    root = output_root.expanduser().resolve()
    program = root / "program-scheduling.json"
    binding = root / "program-scheduling-binding-only.json"
    suite = Path(__file__).resolve().parent
    print("[prepare_l1] L1 complete — run perf cases (each starts its own serve + router):", flush=True)
    print(f"  binding_only arm       → {binding}", flush=True)
    print(f"  program_scheduling arm → {program}", flush=True)
    print("Example:", flush=True)
    print(
        "  pytest -s -v tests/e2e/perf/router-replay/run_benchmark.py \\",
        flush=True,
    )
    print(
        f"    --test-config-file {suite / 'cases/binding_only/glm52_24x12_router_replay.json'}",
        flush=True,
    )


def main() -> None:
    _ensure_import_paths()
    from helpers.l1_prepare import bootstrap, parser as l1_parser

    cli = l1_parser()
    cli.prog = Path(__file__).name
    cli.description = (
        "Start vLLM from a serve config, run prefill/decode calibration, write both program JSON files, "
        "then stop serve."
    )
    args = cli.parse_args()
    bootstrap(args)
    _print_next_steps(Path(args.output_root))


if __name__ == "__main__":
    main()
