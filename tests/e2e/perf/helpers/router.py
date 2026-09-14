# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the AgentInfer project
"""Manage vllm-router subprocesses for router Codex perf cases."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .router_case_loader import RouterCodexPerfConfig, build_router_argv
from .server import (
    _shutdown_process,
    ensure_port_free,
    join_streaming_output,
    start_streaming_output,
    wait_for_port_free,
)

_READY_POLL_INTERVAL_SECONDS = 5
_DEFAULT_ROUTER_READY_TIMEOUT_SECONDS = 300.0


def wait_for_health(
    base_url: str,
    proc: subprocess.Popen[str] | None,
    *,
    log_path: Path | None,
    prefix: str,
    timeout_seconds: float,
    path: str = "/health",
) -> None:
    """Poll an HTTP health endpoint until ready or the process exits."""

    health_url = f"{base_url.rstrip('/')}{path}"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if proc is not None:
            exit_code = proc.poll()
            if exit_code is not None:
                detail = f"; see log {log_path}" if log_path is not None else ""
                raise RuntimeError(f"{prefix}exited with code {exit_code} before becoming ready{detail}")
        try:
            response = httpx.get(health_url, timeout=10, trust_env=False)
            if response.status_code == 200:
                print(f"{prefix}ready: {health_url}", flush=True)
                return
        except httpx.HTTPError:
            pass
        time.sleep(_READY_POLL_INTERVAL_SECONDS)
    detail = f"; see log {log_path}" if log_path is not None else ""
    raise RuntimeError(f"{prefix}not ready within {timeout_seconds}s{detail}")


@dataclass
class RouterServer:
    """Launch and tear down one vllm-router process."""

    config: RouterCodexPerfConfig
    proc: subprocess.Popen[str] | None = None
    log_path: Path | None = None
    _log_thread: threading.Thread | None = field(default=None, repr=False)

    def start(self) -> None:
        router = self.config.case.router
        if router is None:
            raise ValueError("router server requested for dp_baseline case")

        ensure_port_free(router.host, router.port)
        ensure_port_free("127.0.0.1", router.prom_port)

        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.config.log_dir / f"router-{self.config.run_tag}.log"
        cmd = build_router_argv(self.config)
        prefix = f"[router:{self.config.case.test_name}] "
        print(f"{prefix}starting: {' '.join(cmd)}", flush=True)
        print(f"{prefix}log file: {self.log_path}", flush=True)
        self.proc = subprocess.Popen(
            cmd,
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
        if self.config.case.wait_for_router_ready:
            wait_for_health(
                self.config.router_base_url,
                self.proc,
                log_path=self.log_path,
                prefix=prefix,
                timeout_seconds=_DEFAULT_ROUTER_READY_TIMEOUT_SECONDS,
            )

    def stop(self) -> None:
        if self.proc is None:
            return
        router = self.config.case.router
        proc = self.proc
        log_thread = self._log_thread
        prefix = f"[router:{self.config.case.test_name}] "
        try:
            _shutdown_process(proc)
        finally:
            join_streaming_output(proc, log_thread)
            self.proc = None
            self._log_thread = None
            if router is not None:
                try:
                    wait_for_port_free(router.host, router.port, timeout_seconds=30)
                except RuntimeError as exc:
                    print(f"{prefix}warning: {exc}", file=sys.stderr, flush=True)
                try:
                    wait_for_port_free("127.0.0.1", router.prom_port, timeout_seconds=30)
                except RuntimeError as exc:
                    print(f"{prefix}warning: prom port {exc}", file=sys.stderr, flush=True)

    def __enter__(self) -> RouterServer:
        try:
            self.start()
        except Exception:
            self.stop()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def managed_router(config: RouterCodexPerfConfig) -> AbstractContextManager[RouterServer]:
    return RouterServer(config=config)
