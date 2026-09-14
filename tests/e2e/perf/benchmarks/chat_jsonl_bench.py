#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the AgentInfer project
"""Replay OpenAI chat JSONL against /v1/chat/completions.

This is the **quoted** cache-aware performance client (Codex SWE-bench Pro
JSONL). Official ``vllm bench serve`` cannot do this: it fires independent
ShareGPT/random/completions requests with no ``session_params.session_id``,
no growing per-session history, and no session-serial fire mode. See
``tests/e2e/perf/README.md``.

Prints duration, RPS, TTFT, TPOT, and E2E. Uses streaming by default so TTFT works.

Default fire mode is ``session_serial``: global concurrency is honored, but at
most one turn per ``session_params.session_id`` is in flight, and turns within a
session run in ``_trace_turn`` order. That avoids launching all turns of one
session together (common when the JSONL is session-blocked and concurrency>=turns).

Optional ``--sessions`` / ``--turns`` sample an N×T cut from a full pool
(same recipe as ``dataset/sample_codex_sessions.py``). ``--active-sessions``
caps how many session_ids are live at once. Default ``--active-session-mode
wave`` runs them in disjoint waves (next wave waits for the previous to
finish). ``sliding`` admits the next file session as soon as one live
session completes all turns. ``--repeat-dataset`` / ``--duration-seconds``
can loop. A short last **wave** stays short unless ``--wrap-last-wave``
fills it from the start of the session list (default off; ignored in
sliding).

Per-request traces (``--per-request-jsonl``) record:

* Client: HTTP start / first-token / finish, client TTFT/TPOT/E2E
* Server (needs ``vllm serve --enable-per-request-metrics``): response
  ``metrics`` with ``queue_time_ms``, ``time_to_first_token_ms`` (scheduled→first
  token ≈ prefill), ``mean_itl_ms`` (TPOT), ``generation_time_ms`` — same
  internals as the Prometheus histogram means, but one sample per request.

Absolute server timestamps are not in the API; we approximate
``queued/scheduled`` unix times by anchoring on the client's first-token time.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, max(0, int(round((pct / 100.0) * (len(values) - 1)))))
    return values[idx]


def mean_or_zero(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


ROUTER_TRACE_HEADERS = {
    "routed_worker": "x-vllm-router-worker",
    "routed_base_worker": "x-vllm-router-base-worker",
    "routed_dp_rank": "x-vllm-router-dp-rank",
    "router_decision": "x-vllm-router-decision",
}


def extract_router_trace_headers(resp: Any) -> dict[str, str | int | None]:
    values: dict[str, str | int | None] = {}
    for field, header in ROUTER_TRACE_HEADERS.items():
        value = resp.headers.get(header)
        if field == "routed_dp_rank" and value is not None:
            try:
                values[field] = int(value)
            except ValueError:
                values[field] = value
        else:
            values[field] = value
    return values


def cached_tokens_from_usage(usage: dict[str, Any]) -> tuple[int | None, bool]:
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict) or "cached_tokens" not in details:
        return None, False
    value = details.get("cached_tokens")
    if value is None:
        return None, True
    return int(value), True


def parse_streaming_response(resp, start_perf: float) -> dict[str, Any]:
    first_token_perf = None
    first_token_unix = None
    usage: dict[str, Any] = {}
    server_metrics: dict[str, Any] | None = None
    for raw_line in resp:
        line = raw_line.decode("utf-8", errors="ignore").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            break
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            continue
        if parsed.get("usage"):
            usage = parsed["usage"]
        # Final usage chunk carries metrics when --enable-per-request-metrics.
        if parsed.get("metrics"):
            server_metrics = parsed["metrics"]
        for choice in parsed.get("choices") or []:
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if first_token_perf is None and content:
                first_token_perf = time.perf_counter()
                first_token_unix = time.time()
    finish_perf = time.perf_counter()
    finish_unix = time.time()
    e2e_s = finish_perf - start_perf
    ttft_s = (first_token_perf - start_perf) if first_token_perf is not None else None
    cached_tokens, cached_tokens_present = cached_tokens_from_usage(usage)
    return {
        "ok": True,
        "e2e_s": e2e_s,
        "ttft_s": ttft_s,
        "first_token_unix": first_token_unix,
        "finish_unix": finish_unix,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "cached_tokens": cached_tokens,
        "cached_tokens_present": cached_tokens_present,
        "server_metrics": server_metrics,
    }


def post_chat(base_url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    http_start_perf = time.perf_counter()
    http_start_unix = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            router_trace = extract_router_trace_headers(resp)
            if payload.get("stream"):
                out = parse_streaming_response(resp, http_start_perf)
            else:
                body = resp.read()
                finish_perf = time.perf_counter()
                finish_unix = time.time()
                parsed = json.loads(body)
                usage = parsed.get("usage") or {}
                cached_tokens, cached_tokens_present = cached_tokens_from_usage(usage)
                out = {
                    "ok": True,
                    "e2e_s": finish_perf - http_start_perf,
                    "ttft_s": None,
                    "first_token_unix": None,
                    "finish_unix": finish_unix,
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "cached_tokens": cached_tokens,
                    "cached_tokens_present": cached_tokens_present,
                    "server_metrics": parsed.get("metrics"),
                }
            out.update(router_trace)
            out["http_start_unix"] = http_start_unix
            out["http_start_perf"] = http_start_perf
            return out
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")[:500]
        finish_unix = time.time()
        return {
            "ok": False,
            "e2e_s": time.perf_counter() - http_start_perf,
            "ttft_s": None,
            "http_start_unix": http_start_unix,
            "http_start_perf": http_start_perf,
            "first_token_unix": None,
            "finish_unix": finish_unix,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": None,
            "cached_tokens_present": False,
            "server_metrics": None,
            "error": f"HTTP {exc.code}: {body}",
        }
    except Exception as exc:  # noqa: BLE001 - benchmark must keep going
        finish_unix = time.time()
        return {
            "ok": False,
            "e2e_s": time.perf_counter() - http_start_perf,
            "ttft_s": None,
            "http_start_unix": http_start_unix,
            "http_start_perf": http_start_perf,
            "first_token_unix": None,
            "finish_unix": finish_unix,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": None,
            "cached_tokens_present": False,
            "server_metrics": None,
            "error": repr(exc),
        }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def enrich_record(meta: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Build one per-request trace row (client + optional server metrics)."""
    e2e_s = float(raw.get("e2e_s") or 0.0)
    ttft_s = raw.get("ttft_s")
    completion = int(raw.get("completion_tokens") or 0)
    decode_s = None
    tpot_s = None
    if ttft_s is not None:
        decode_s = max(0.0, e2e_s - float(ttft_s))
        # TPOT over tokens after the first; if only 1 token, TPOT = decode_s.
        denom = max(completion - 1, 1) if completion > 0 else 0
        if denom > 0:
            tpot_s = decode_s / denom

    http_start_unix = raw.get("http_start_unix")
    first_token_unix = raw.get("first_token_unix")
    finish_unix = raw.get("finish_unix")
    eligible_unix = meta.get("eligible_unix")

    sm = raw.get("server_metrics") or {}
    server_queue_ms = _as_float(sm.get("queue_time_ms"))
    # vLLM: scheduled_ts -> first_token_ts (same as request_prefill_time_seconds)
    server_prefill_ms = _as_float(sm.get("time_to_first_token_ms"))
    server_generation_ms = _as_float(sm.get("generation_time_ms"))
    server_mean_itl_ms = _as_float(sm.get("mean_itl_ms"))  # ≈ TPOT
    server_tps = _as_float(sm.get("tokens_per_second"))

    # Approximate absolute server timestamps by walking back from client first-token.
    # API only returns durations, not engine wall clocks.
    approx_scheduled_unix = None
    approx_queued_unix = None
    approx_prefill_start_unix = None
    if first_token_unix is not None and server_prefill_ms is not None:
        approx_scheduled_unix = float(first_token_unix) - server_prefill_ms / 1000.0
        approx_prefill_start_unix = approx_scheduled_unix
        if server_queue_ms is not None:
            approx_queued_unix = approx_scheduled_unix - server_queue_ms / 1000.0

    prompt_tokens = int(raw.get("prompt_tokens") or 0)
    cached_tokens = raw.get("cached_tokens")
    prompt_cache_hit_pct = (
        (float(cached_tokens) / float(prompt_tokens) * 100.0)
        if cached_tokens is not None and prompt_tokens > 0
        else None
    )

    return {
        "req_index": meta["req_index"],
        "session_id": meta["session_id"],
        "trace_turn": meta.get("trace_turn"),
        "ok": bool(raw.get("ok")),
        "error": raw.get("error"),
        # Client wall-clock (unix epoch)
        "eligible_unix": eligible_unix,
        "submit_unix": meta.get("submit_unix"),
        "http_start_unix": http_start_unix,
        "first_token_unix": first_token_unix,
        "finish_unix": finish_unix,
        "eligible_rel_s": meta.get("eligible_rel_s"),
        "submit_rel_s": meta.get("submit_rel_s"),
        "http_start_rel_s": (
            (float(raw["http_start_perf"]) - float(meta["bench_t0_perf"]))
            if raw.get("http_start_perf") is not None
            else None
        ),
        # Client-observed latency
        "client_ttft_ms": None if ttft_s is None else float(ttft_s) * 1000.0,
        "client_decode_ms": None if decode_s is None else decode_s * 1000.0,
        "client_tpot_ms": None if tpot_s is None else tpot_s * 1000.0,
        "client_e2e_ms": e2e_s * 1000.0,
        # Compat aliases used by aggregate printers
        "ttft_ms": None if ttft_s is None else float(ttft_s) * 1000.0,
        "tpot_ms": None if tpot_s is None else tpot_s * 1000.0,
        "e2e_ms": e2e_s * 1000.0,
        "ttft_s": ttft_s,
        "tpot_s": tpot_s,
        "e2e_s": e2e_s,
        "decode_s": decode_s,
        "decode_ms": None if decode_s is None else decode_s * 1000.0,
        # Server per-request metrics (vllm --enable-per-request-metrics)
        # Same source as Prometheus queue/prefill/ITL histogram observations.
        "server_queue_ms": server_queue_ms,
        "server_prefill_ms": server_prefill_ms,
        "server_generation_ms": server_generation_ms,
        "server_mean_itl_ms": server_mean_itl_ms,
        "server_tokens_per_second": server_tps,
        "server_metrics_raw": sm or None,
        # Approx absolute times (derived; not engine clocks)
        "approx_queued_unix": approx_queued_unix,
        "approx_scheduled_unix": approx_scheduled_unix,
        "approx_prefill_start_unix": approx_prefill_start_unix,
        "approx_timestamp_note": (
            "approx_*_unix derived from client first_token_unix - server durations; "
            "enable with: vllm serve --enable-per-request-metrics"
        ),
        # Router trace headers (requires router support; absent for direct DP baseline).
        "routed_worker": raw.get("routed_worker"),
        "routed_base_worker": raw.get("routed_base_worker"),
        "routed_dp_rank": raw.get("routed_dp_rank"),
        "router_decision": raw.get("router_decision"),
        # Tokens
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion,
        "cached_tokens": cached_tokens,
        "cached_tokens_present": bool(raw.get("cached_tokens_present")),
        "prompt_cache_hit_pct": prompt_cache_hit_pct,
        "source_req_index": meta.get("source_req_index", meta["req_index"]),
        "loop_index": meta.get("loop_index", 0),
        "wave_index": meta.get("wave_index", 0),
        "active_sessions": meta.get("active_sessions"),
    }


def load_requests(
    path: Path,
    model: str | None,
    max_tokens: int | None,
    force_stream: bool,
    limit: int,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if model:
                payload["model"] = model
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if force_stream:
                payload["stream"] = True
                payload.setdefault("stream_options", {"include_usage": True})
            requests.append(payload)
            if limit > 0 and len(requests) >= limit:
                break
    if not requests:
        raise SystemExit(f"no requests loaded from {path}")
    return requests


def session_groups(requests: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    by_sid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for idx, payload in enumerate(requests):
        sid = session_id_of(payload, idx)
        if sid not in by_sid:
            order.append(sid)
        by_sid[sid].append(payload)
    for sid in by_sid:
        by_sid[sid].sort(key=lambda r: int(r.get("_trace_turn") or 0))
    return by_sid, order


def sample_sessions_turns(
    requests: list[dict[str, Any]],
    sessions: int,
    turns: int,
    order: str,
) -> list[dict[str, Any]]:
    """Same cut as dataset/sample_codex_sessions.py (stratified_size default)."""
    by_sid, file_order = session_groups(requests)
    min_turns = turns if turns > 0 else 1
    rank_turn = (turns - 1) if turns > 0 else -1
    eligible_items = [(sid, rows) for sid, rows in by_sid.items() if len(rows) >= min_turns]
    if not eligible_items:
        raise SystemExit(f"no sessions have >= {min_turns} turns")

    def rank_size(item: tuple[str, list[dict[str, Any]]]) -> int:
        rows = item[1]
        return int(rows[rank_turn].get("_prompt_tokens") or 0)

    if sessions <= 0:
        if order == "sorted_sid":
            picked = sorted(sid for sid, _ in eligible_items)
        elif order == "first":
            eligible_set = {sid for sid, _ in eligible_items}
            picked = [sid for sid in file_order if sid in eligible_set]
        else:
            eligible_items.sort(key=rank_size)
            picked = [sid for sid, _ in eligible_items]
    elif order == "stratified_size":
        eligible_items.sort(key=rank_size)
        if len(eligible_items) < sessions:
            raise SystemExit(f"only {len(eligible_items)} sessions have >= {min_turns} turns; need {sessions}")
        if sessions == 1:
            idxs = [0]
        else:
            idxs = [round(i * (len(eligible_items) - 1) / (sessions - 1)) for i in range(sessions)]
        picked = [eligible_items[i][0] for i in idxs]
    else:
        sid_iter = file_order if order == "first" else sorted(by_sid)
        picked = []
        for sid in sid_iter:
            if len(by_sid[sid]) >= min_turns:
                picked.append(sid)
            if len(picked) >= sessions:
                break
        if len(picked) < sessions:
            raise SystemExit(f"only {len(picked)} sessions have >= {min_turns} turns; need {sessions}")

    out: list[dict[str, Any]] = []
    last_toks: list[int] = []
    for sid in picked:
        rows = by_sid[sid] if turns <= 0 else by_sid[sid][:turns]
        out.extend(rows)
        last_toks.append(int(rows[-1].get("_prompt_tokens") or 0))
    print(
        "CHAT_JSONL_SAMPLE "
        f"sessions={len(picked)} turns={turns if turns > 0 else 'all'} "
        f"order={order} reqs={len(out)} W={sum(last_toks)} "
        f"W_per_session={sum(last_toks) / len(picked) if picked else 0:.0f}",
        flush=True,
    )
    return out


def apply_limit(requests: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit > 0:
        return requests[:limit]
    return requests


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _wave_session_ids(wave: list[dict[str, Any]]) -> list[str]:
    sids: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(wave):
        sid = session_id_of(row, i)
        if sid not in seen:
            seen.add(sid)
            sids.append(sid)
    return sids


def describe_workload(
    requests: list[dict[str, Any]],
    active_sessions: int,
    kv_tokens: int,
    wrap_last_wave: bool = False,
    active_session_mode: str = "wave",
) -> None:
    """Print W / ceiling / wave or sliding-window shape without sending HTTP."""
    by_sid, order = session_groups(requests)
    last_toks = [int(by_sid[sid][-1].get("_prompt_tokens") or 0) for sid in order]
    all_toks = [int(r.get("_prompt_tokens") or 0) for sid in order for r in by_sid[sid]]
    turns = [len(by_sid[sid]) for sid in order]
    w = sum(last_toks)
    total = sum(all_toks)
    print("============ Chat JSONL Preview ============")
    print(f"Sessions:                                {len(order)}")
    print(f"Requests:                                {len(requests)}")
    print(f"Turns/session:                           {dict(Counter(turns))}")
    print(f"W (sum last-turn prompt tokens):         {w:,}")
    print(
        f"W per session:                           {w / len(order):,.0f}"
        if order
        else "W per session:                           n/a"
    )
    print(f"All prompt tokens:                       {total:,}")
    print(
        f"Sticky ceiling 1-W/all_prompt:           {100.0 * (1.0 - w / total):.1f}%"
        if total
        else "Sticky ceiling:                         n/a"
    )
    if last_toks:
        print(
            f"Last-turn prompt min/med/max:            "
            f"{min(last_toks):,} / {int(statistics.median(last_toks)):,} / {max(last_toks):,}"
        )
    if active_sessions > 0 and order:
        print(f"Active session mode:                     {active_session_mode}")
        print(f"Active sessions / window:                {active_sessions}")
        first = order[: min(active_sessions, len(order))]
        w0 = sum(int(by_sid[sid][-1].get("_prompt_tokens") or 0) for sid in first)
        print(
            f"Steady-window W (first {len(first)}):           {w0:,}"
            + (f"  ({w0 / w * 100:.1f}% of file W)" if w else "")
        )
        if kv_tokens > 0:
            print(
                f"Steady-window KV/W:                      {kv_tokens / w0:.3f}x"
                if w0
                else "Steady-window KV/W:                      n/a"
            )
        if active_session_mode == "sliding":
            print(
                "Sliding:                                  admit next file "
                "session when one live session finishes all turns"
            )
            if wrap_last_wave:
                print("Wrap last wave:                          ignored (sliding has no waves)")
            if len(order) <= active_sessions:
                last_n = len(order)
            else:
                last_n = len(order) % active_sessions
            print(f"File-end drain sessions:                 {last_n} (only after the last session is admitted)")
            if last_n:
                last_sids = order[-last_n:]
                last_w = sum(int(by_sid[sid][-1].get("_prompt_tokens") or 0) for sid in last_sids)
                print(f"File-end drain W (if no overlap):        {last_w:,}")
                if kv_tokens > 0 and last_w:
                    print(f"File-end drain KV/W:                     {kv_tokens / last_w:.3f}x")
        else:
            waves = build_session_waves(requests, active_sessions, wrap_last_wave)
            last_sids = _wave_session_ids(waves[-1]) if waves else []
            rem = len(order) % active_sessions
            wrapping = bool(wrap_last_wave and rem)
            print(
                f"Waves:                                   {len(waves)} "
                f"(last wave {len(last_sids)} sessions, "
                f"{'wrap' if wrapping else 'no wrap'})"
            )
            print(
                f"First-wave W:                            {w0:,}" + (f"  ({w0 / w * 100:.1f}% of file W)" if w else "")
            )
            if kv_tokens > 0 and w0:
                print(f"First-wave KV/W:                         {kv_tokens / w0:.3f}x")
            last_w = sum(int(by_sid[sid][-1].get("_prompt_tokens") or 0) for sid in last_sids)
            print(f"Last-wave W:                             {last_w:,}")
            if kv_tokens > 0 and last_w:
                print(f"Last-wave KV/W:                          {kv_tokens / last_w:.3f}x")
    if kv_tokens > 0 and w > 0:
        print(f"KV tokens/rank (given):                  {kv_tokens:,}")
        print(f"KV/W (whole loaded set):                 {kv_tokens / w:.3f}x")
        print(f"KV / (W/2)  [CA-ish per-rank]:           {kv_tokens / (w / 2):.3f}x")
    print("============================================")


def session_id_of(payload: dict[str, Any], fallback: int) -> str:
    sid = (payload.get("session_params") or {}).get("session_id")
    if sid is None or sid == "":
        return f"__anon_{fallback}"
    return str(sid)


def run_one(
    base_url: str,
    payload: dict[str, Any],
    timeout: float,
    meta: dict[str, Any],
) -> dict[str, Any]:
    meta = dict(meta)
    meta["submit_unix"] = time.time()
    meta["submit_rel_s"] = time.perf_counter() - float(meta["bench_t0_perf"])
    raw = post_chat(base_url, payload, timeout)
    return enrich_record(meta, raw)


def run_jsonl_order(
    base_url: str,
    requests: list[dict[str, Any]],
    max_concurrency: int,
    timeout: float,
    bench_t0_perf: float,
) -> list[dict[str, Any]]:
    # File-order map: mark eligible at submit time (when a worker picks it up).
    def _task(item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        idx, payload = item
        now_unix = time.time()
        now_perf = time.perf_counter()
        meta = {
            "req_index": idx,
            "session_id": session_id_of(payload, idx),
            "trace_turn": payload.get("_trace_turn"),
            "bench_t0_perf": bench_t0_perf,
            "eligible_unix": now_unix,
            "eligible_rel_s": now_perf - bench_t0_perf,
        }
        return run_one(base_url, payload, timeout, meta)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        return list(pool.map(_task, enumerate(requests)))


def run_session_serial(
    base_url: str,
    requests: list[dict[str, Any]],
    max_concurrency: int,
    timeout: float,
    bench_t0_perf: float,
    extra_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Global concurrency with <=1 in-flight turn per session_id."""
    by_sid: dict[str, deque[tuple[int, dict[str, Any]]]] = defaultdict(deque)
    session_order: list[str] = []
    for idx, payload in enumerate(requests):
        sid = session_id_of(payload, idx)
        if sid not in by_sid:
            session_order.append(sid)
        by_sid[sid].append((idx, payload))

    for sid in by_sid:
        by_sid[sid] = deque(
            sorted(
                by_sid[sid],
                key=lambda item: (
                    int(item[1].get("_trace_turn") or 0),
                    item[0],
                ),
            )
        )

    t0_unix = time.time()
    # First turn of every session is eligible at bench start.
    eligible_unix: dict[str, float] = dict.fromkeys(session_order, t0_unix)
    eligible_rel: dict[str, float] = dict.fromkeys(session_order, 0.0)

    ready: deque[str] = deque(sid for sid in session_order if by_sid[sid])
    results: list[dict[str, Any] | None] = [None] * len(requests)
    inflight_sid: dict[concurrent.futures.Future, str] = {}
    inflight_idx: dict[concurrent.futures.Future, int] = {}
    workers = max(1, max_concurrency)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:

        def submit_sid(sid: str) -> None:
            idx, payload = by_sid[sid].popleft()
            meta = {
                "req_index": idx,
                "session_id": sid,
                "trace_turn": payload.get("_trace_turn"),
                "bench_t0_perf": bench_t0_perf,
                "eligible_unix": eligible_unix[sid],
                "eligible_rel_s": eligible_rel[sid],
                "source_req_index": idx,
            }
            if extra_meta:
                meta.update(extra_meta)
            fut = pool.submit(run_one, base_url, payload, timeout, meta)
            inflight_sid[fut] = sid
            inflight_idx[fut] = idx

        while ready and len(inflight_sid) < workers:
            submit_sid(ready.popleft())

        while inflight_sid:
            done, _ = concurrent.futures.wait(
                inflight_sid,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for fut in done:
                sid = inflight_sid.pop(fut)
                idx = inflight_idx.pop(fut)
                row = fut.result()
                results[idx] = row
                # Next turn of this session becomes eligible when this turn finishes.
                fin = row.get("finish_unix")
                if fin is None:
                    fin = time.time()
                eligible_unix[sid] = float(fin)
                eligible_rel[sid] = time.perf_counter() - bench_t0_perf
                if by_sid[sid]:
                    ready.append(sid)
            while ready and len(inflight_sid) < workers:
                submit_sid(ready.popleft())

    assert all(r is not None for r in results)
    return [r for r in results if r is not None]


def build_session_waves(
    requests: list[dict[str, Any]],
    active_sessions: int,
    wrap_last_wave: bool = False,
) -> list[list[dict[str, Any]]]:
    """Group requests into session waves, preserving per-session turn order.

    Default leaves a short remainder wave as-is. ``wrap_last_wave`` fills it
    to ``active_sessions`` by reusing sessions from the start of the file
    (disjoint from the remainder).
    """
    by_sid, session_order = session_groups(requests)
    if active_sessions <= 0 or active_sessions >= len(session_order):
        return [requests]
    waves: list[list[dict[str, Any]]] = []
    for i in range(0, len(session_order), active_sessions):
        chunk = list(session_order[i : i + active_sessions])
        if wrap_last_wave and 0 < len(chunk) < active_sessions:
            need = active_sessions - len(chunk)
            fill = [sid for sid in session_order if sid not in chunk][:need]
            chunk.extend(fill)
        wave: list[dict[str, Any]] = []
        for sid in chunk:
            wave.extend(by_sid[sid])
        waves.append(wave)
    return waves


def run_session_serial_waves(
    base_url: str,
    requests: list[dict[str, Any]],
    max_concurrency: int,
    timeout: float,
    bench_t0_perf: float,
    active_sessions: int,
    repeat_dataset: int,
    duration_seconds: float,
    wrap_last_wave: bool = False,
) -> list[dict[str, Any]]:
    """Run session-serial replay in bounded active-session waves.

    ``repeat_dataset=0`` means repeat until ``duration_seconds`` expires.
    Duration is checked between waves so an in-flight wave can finish cleanly.
    """
    waves = build_session_waves(requests, active_sessions, wrap_last_wave)
    results: list[dict[str, Any]] = []
    global_idx = 0
    loop_index = 0
    deadline = time.perf_counter() + duration_seconds if duration_seconds > 0 else None

    def stop_for_duration() -> bool:
        return deadline is not None and time.perf_counter() >= deadline

    while True:
        if repeat_dataset > 0 and loop_index >= repeat_dataset:
            break
        if loop_index > 0 and stop_for_duration():
            break
        for wave_index, wave_requests in enumerate(waves):
            if loop_index > 0 or wave_index > 0:
                if stop_for_duration():
                    return results
            extra = {
                "loop_index": loop_index,
                "wave_index": wave_index,
                "active_sessions": active_sessions
                if active_sessions > 0
                else len({session_id_of(r, i) for i, r in enumerate(wave_requests)}),
            }
            wave_results = run_session_serial(
                base_url,
                wave_requests,
                max_concurrency,
                timeout,
                bench_t0_perf,
                extra_meta=extra,
            )
            for row in wave_results:
                row["req_index"] = global_idx
                global_idx += 1
            results.extend(wave_results)
        loop_index += 1
        if repeat_dataset == 0 and deadline is None:
            break
    return results


def _sliding_try_admit(
    session_order: list[str],
    next_idx: int,
    loop_index: int,
    active_sids: set[str],
    repeat_dataset: int,
    duration_seconds: float,
    duration_expired: bool,
) -> tuple[str | None, int, int]:
    """Pick the next session for a sliding window, or None to stop admitting.

    ``repeat_dataset==1`` and no duration: single pass through ``session_order``.
    ``repeat_dataset==0`` means loop until duration expires (caller must pass
    ``duration_expired``). Skips sids still in ``active_sids`` so a replayed
    pass cannot collide with a still-live session.
    """
    n = len(session_order)
    if n == 0 or duration_expired:
        return None, next_idx, loop_index
    scanned = 0
    while scanned < n:
        if next_idx >= n:
            if repeat_dataset == 1 and duration_seconds <= 0:
                return None, next_idx, loop_index
            next_loop = loop_index + 1
            if repeat_dataset > 0 and next_loop >= repeat_dataset:
                return None, next_idx, loop_index
            if next_loop > 0 and duration_expired:
                return None, next_idx, loop_index
            loop_index = next_loop
            next_idx = 0
        sid = session_order[next_idx]
        next_idx += 1
        scanned += 1
        if sid not in active_sids:
            return sid, next_idx, loop_index
    return None, next_idx, loop_index


def run_session_serial_sliding(
    base_url: str,
    requests: list[dict[str, Any]],
    max_concurrency: int,
    timeout: float,
    bench_t0_perf: float,
    active_sessions: int,
    repeat_dataset: int,
    duration_seconds: float,
) -> list[dict[str, Any]]:
    """Session-serial with a sliding live-session cap.

    Seed the first ``active_sessions`` session_ids. When a live session
    finishes all turns, admit the next file session into that slot. Duration
    is checked when admitting (in-flight sessions still finish). Repeat wraps
    the session list after a full pass.
    """
    by_sid_src: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    session_order: list[str] = []
    for idx, payload in enumerate(requests):
        sid = session_id_of(payload, idx)
        if sid not in by_sid_src:
            session_order.append(sid)
        by_sid_src[sid].append((idx, payload))
    for sid in by_sid_src:
        by_sid_src[sid] = sorted(
            by_sid_src[sid],
            key=lambda item: (int(item[1].get("_trace_turn") or 0), item[0]),
        )

    if active_sessions <= 0 or active_sessions >= len(session_order):
        cap = len(session_order)
    else:
        cap = active_sessions

    t0_unix = time.time()
    deadline = time.perf_counter() + duration_seconds if duration_seconds > 0 else None

    def duration_expired() -> bool:
        return deadline is not None and time.perf_counter() >= deadline

    active_sids: set[str] = set()
    ready: deque[str] = deque()
    work: dict[str, deque[tuple[int, dict[str, Any]]]] = {}
    admit_loop: dict[str, int] = {}
    admit_index_of: dict[str, int] = {}
    next_idx = 0
    loop_index = 0
    admit_seq = 0
    results: list[dict[str, Any]] = []
    inflight_sid: dict[concurrent.futures.Future, str] = {}
    workers = max(1, max_concurrency)

    def admit(sid: str) -> None:
        nonlocal admit_seq
        active_sids.add(sid)
        work[sid] = deque(by_sid_src[sid])
        admit_loop[sid] = loop_index
        admit_index_of[sid] = admit_seq
        admit_seq += 1
        ready.append(sid)

    def try_admit() -> str | None:
        nonlocal next_idx, loop_index
        sid, next_idx, loop_index = _sliding_try_admit(
            session_order,
            next_idx,
            loop_index,
            active_sids,
            repeat_dataset,
            duration_seconds,
            duration_expired(),
        )
        return sid

    while len(active_sids) < cap:
        sid = try_admit()
        if sid is None:
            break
        admit(sid)

    eligible_unix: dict[str, float] = dict.fromkeys(active_sids, t0_unix)
    eligible_rel: dict[str, float] = dict.fromkeys(active_sids, 0.0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:

        def submit_sid(sid: str) -> None:
            idx, payload = work[sid].popleft()
            meta = {
                "req_index": len(results) + len(inflight_sid),
                "session_id": sid,
                "trace_turn": payload.get("_trace_turn"),
                "bench_t0_perf": bench_t0_perf,
                "eligible_unix": eligible_unix[sid],
                "eligible_rel_s": eligible_rel[sid],
                "source_req_index": idx,
                "loop_index": admit_loop[sid],
                "admit_index": admit_index_of[sid],
                "active_sessions": cap,
                "active_session_mode": "sliding",
            }
            fut = pool.submit(run_one, base_url, payload, timeout, meta)
            inflight_sid[fut] = sid

        while ready and len(inflight_sid) < workers:
            submit_sid(ready.popleft())

        while inflight_sid:
            done, _ = concurrent.futures.wait(
                inflight_sid,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for fut in done:
                sid = inflight_sid.pop(fut)
                row = fut.result()
                results.append(row)
                fin = row.get("finish_unix")
                if fin is None:
                    fin = time.time()
                eligible_unix[sid] = float(fin)
                eligible_rel[sid] = time.perf_counter() - bench_t0_perf
                if work[sid]:
                    ready.append(sid)
                else:
                    fin_rel = eligible_rel.get(sid, 0.0)
                    active_sids.discard(sid)
                    work.pop(sid, None)
                    admit_loop.pop(sid, None)
                    admit_index_of.pop(sid, None)
                    eligible_unix.pop(sid, None)
                    eligible_rel.pop(sid, None)
                    new_sid = try_admit()
                    if new_sid is not None:
                        eligible_unix[new_sid] = float(fin)
                        eligible_rel[new_sid] = fin_rel
                        admit(new_sid)
            while ready and len(inflight_sid) < workers:
                submit_sid(ready.popleft())

    for i, row in enumerate(results):
        row["req_index"] = i
    return results


def write_per_request_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def print_trace_lines(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        print(
            "REQ_TRACE "
            f"idx={row['req_index']} sid={row['session_id']} turn={row.get('trace_turn')} "
            f"ok={int(row['ok'])} "
            f"http_start_unix={row.get('http_start_unix')} "
            f"approx_queued_unix={row.get('approx_queued_unix')} "
            f"approx_prefill_start_unix={row.get('approx_prefill_start_unix')} "
            f"first_token_unix={row.get('first_token_unix')} "
            f"finish_unix={row.get('finish_unix')} "
            f"worker={row.get('routed_worker')} "
            f"dp_rank={row.get('routed_dp_rank')} "
            f"decision={row.get('router_decision')} "
            f"server_queue_ms={row.get('server_queue_ms')} "
            f"server_prefill_ms={row.get('server_prefill_ms')} "
            f"server_mean_itl_ms={row.get('server_mean_itl_ms')} "
            f"client_ttft_ms={row.get('client_ttft_ms')} "
            f"client_tpot_ms={row.get('client_tpot_ms')} "
            f"client_e2e_ms={row.get('client_e2e_ms')} "
            f"prompt_tok={row.get('prompt_tokens')} "
            f"completion_tok={row.get('completion_tokens')} "
            f"cached_tok={row.get('cached_tokens')} "
            f"prompt_cache_hit_pct={row.get('prompt_cache_hit_pct')}",
            flush=True,
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:18180"))
    p.add_argument("--model", default=os.environ.get("MODEL"))
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--max-concurrency", type=int, default=int(os.environ.get("MAX_CONCURRENCY", "4")))
    p.add_argument("--limit", type=int, default=int(os.environ.get("NUM_PROMPTS", "0")))
    p.add_argument("--timeout", type=float, default=1800.0)
    p.add_argument("--no-stream", action="store_true")
    p.add_argument("--label", default="jsonl")
    p.add_argument(
        "--fire-mode",
        choices=("session_serial", "jsonl"),
        default=os.environ.get("CHAT_JSONL_FIRE_MODE", "session_serial"),
        help=(
            "session_serial (default): <=1 in-flight turn per session_id, "
            "turns in _trace_turn order, global concurrency still applies. "
            "jsonl: legacy file-order pool.map."
        ),
    )
    p.add_argument(
        "--per-request-jsonl",
        type=Path,
        default=(Path(os.environ["PER_REQUEST_JSONL"]) if os.environ.get("PER_REQUEST_JSONL") else None),
        help="Write one JSON object per request with arrival/TTFT/TPOT/E2E fields.",
    )
    p.add_argument(
        "--trace-requests",
        action="store_true",
        default=os.environ.get("CHAT_JSONL_TRACE_REQUESTS", "").lower() in ("1", "true", "yes"),
        help="Also print REQ_TRACE lines for each request to stdout.",
    )
    p.add_argument(
        "--sessions",
        type=int,
        default=int(os.environ.get("CHAT_JSONL_SESSIONS", "0")),
        help="Sample this many sessions from --input (0 = keep file as-is).",
    )
    p.add_argument(
        "--turns",
        type=int,
        default=int(os.environ.get("CHAT_JSONL_TURNS", "0")),
        help="Keep the first T turns of each sampled session (0 = no turn cut).",
    )
    p.add_argument(
        "--sample-order",
        choices=("first", "sorted_sid", "stratified_size"),
        default=os.environ.get("CHAT_JSONL_SAMPLE_ORDER", "stratified_size"),
        help="How to pick N sessions. Same as dataset/sample_codex_sessions.py.",
    )
    p.add_argument(
        "--sampled-jsonl",
        type=Path,
        default=(Path(os.environ["CHAT_JSONL_SAMPLED_JSONL"]) if os.environ.get("CHAT_JSONL_SAMPLED_JSONL") else None),
        help="If sampling, write the N×T cut here (temp cut for this run).",
    )
    p.add_argument(
        "--active-sessions",
        type=int,
        default=int(os.environ.get("CHAT_JSONL_ACTIVE_SESSIONS", "0")),
        help=(
            "For session_serial, at most this many session_ids live at once. "
            "0 means all loaded sessions are active together (default)."
        ),
    )
    p.add_argument(
        "--active-session-mode",
        choices=("wave", "sliding"),
        default=os.environ.get("CHAT_JSONL_ACTIVE_SESSION_MODE", "wave"),
        help=(
            "wave (default): disjoint batches; next wave waits until the "
            "previous finishes. sliding: when one live session completes all "
            "turns, admit the next file session into that slot."
        ),
    )
    p.add_argument(
        "--wrap-last-wave",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("CHAT_JSONL_WRAP_LAST_WAVE", False),
        help=(
            "Fill a short last wave to --active-sessions by reusing sessions from the start of the file. Default off."
        ),
    )
    p.add_argument(
        "--repeat-dataset",
        type=int,
        default=None,
        help=(
            "Number of full dataset passes. Default is 1, or repeat until "
            "--duration-seconds when only duration is set. 0 means repeat until "
            "--duration-seconds expires."
        ),
    )
    p.add_argument(
        "--duration-seconds",
        type=float,
        default=float(os.environ.get("CHAT_JSONL_DURATION_SECONDS", "0") or 0),
        help="Optional wall-clock duration for looped stability runs.",
    )
    p.add_argument(
        "--preview",
        action="store_true",
        default=os.environ.get("CHAT_JSONL_PREVIEW", "").lower() in ("1", "true", "yes"),
        help="Load/sample the JSONL, print W / waves / optional KV/W, then exit (no HTTP).",
    )
    p.add_argument(
        "--kv-tokens",
        type=int,
        default=int(os.environ.get("CHAT_JSONL_KV_TOKENS", "0") or 0),
        help="Optional GPU KV cache tokens/rank for preview KV/W (e.g. 1383103).",
    )
    args = p.parse_args()

    if args.repeat_dataset is None:
        env_repeat = os.environ.get("CHAT_JSONL_REPEAT_DATASET")
        if env_repeat is not None and env_repeat != "":
            args.repeat_dataset = int(env_repeat)
        elif args.duration_seconds > 0:
            args.repeat_dataset = 0
        else:
            args.repeat_dataset = 1
    if args.repeat_dataset < 0:
        raise SystemExit("--repeat-dataset must be >= 0")
    if args.repeat_dataset == 0 and args.duration_seconds <= 0:
        raise SystemExit("--repeat-dataset 0 requires --duration-seconds > 0")
    use_waves = args.active_sessions > 0 or args.repeat_dataset != 1 or args.duration_seconds > 0
    if use_waves and args.fire_mode != "session_serial":
        raise SystemExit("active-session waves / sliding / repeat require --fire-mode session_serial")
    if args.active_session_mode == "sliding" and args.wrap_last_wave:
        print(
            "CHAT_JSONL_WARN wrap-last-wave is ignored with --active-session-mode sliding",
            flush=True,
        )

    base_url = args.base_url.rstrip("/")
    requests = load_requests(
        args.input,
        model=args.model,
        max_tokens=args.max_tokens,
        force_stream=not args.no_stream,
        limit=0,
    )
    if args.sessions > 0 or args.turns > 0:
        requests = sample_sessions_turns(
            requests,
            sessions=args.sessions,
            turns=args.turns,
            order=args.sample_order,
        )
        if args.sampled_jsonl is not None:
            args.sampled_jsonl.parent.mkdir(parents=True, exist_ok=True)
            with args.sampled_jsonl.open("w", encoding="utf-8") as f:
                for row in requests:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"CHAT_JSONL_SAMPLED_JSONL={args.sampled_jsonl}", flush=True)
    requests = apply_limit(requests, args.limit)

    print(
        "CHAT_JSONL_BENCH_CONFIG "
        f"label={args.label} base={base_url} model={args.model or 'from_jsonl'} "
        f"n={len(requests)} concurrency={args.max_concurrency} "
        f"fire_mode={args.fire_mode} "
        f"sessions={args.sessions} turns={args.turns} sample_order={args.sample_order} "
        f"active_sessions={args.active_sessions} "
        f"active_session_mode={args.active_session_mode} "
        f"wrap_last_wave={int(args.wrap_last_wave)} "
        f"repeat_dataset={args.repeat_dataset} "
        f"duration_seconds={args.duration_seconds} "
        f"stream={int(not args.no_stream)} "
        f"max_tokens={args.max_tokens if args.max_tokens is not None else 'from_jsonl'} "
        f"per_request_jsonl={args.per_request_jsonl or ''} "
        f"input={args.input}",
        flush=True,
    )
    describe_workload(
        requests,
        args.active_sessions,
        args.kv_tokens,
        args.wrap_last_wave,
        args.active_session_mode,
    )
    if args.preview:
        return

    bench_t0_perf = time.perf_counter()
    if args.fire_mode == "session_serial" and use_waves:
        if args.active_session_mode == "sliding":
            results = run_session_serial_sliding(
                base_url,
                requests,
                args.max_concurrency,
                args.timeout,
                bench_t0_perf,
                active_sessions=args.active_sessions,
                repeat_dataset=args.repeat_dataset,
                duration_seconds=args.duration_seconds,
            )
        else:
            results = run_session_serial_waves(
                base_url,
                requests,
                args.max_concurrency,
                args.timeout,
                bench_t0_perf,
                active_sessions=args.active_sessions,
                repeat_dataset=args.repeat_dataset,
                duration_seconds=args.duration_seconds,
                wrap_last_wave=args.wrap_last_wave,
            )
    elif args.fire_mode == "session_serial":
        results = run_session_serial(base_url, requests, args.max_concurrency, args.timeout, bench_t0_perf)
    else:
        results = run_jsonl_order(base_url, requests, args.max_concurrency, args.timeout, bench_t0_perf)
    duration = time.perf_counter() - bench_t0_perf

    if args.trace_requests:
        print_trace_lines(results)
    if args.per_request_jsonl is not None:
        write_per_request_jsonl(args.per_request_jsonl, results)
        print(f"PER_REQUEST_JSONL={args.per_request_jsonl}", flush=True)

    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    latencies = [float(r["e2e_ms"]) for r in ok]
    ttfts = [float(r["ttft_ms"]) for r in ok if r.get("ttft_ms") is not None]
    tpots = [float(r["tpot_ms"]) for r in ok if r.get("tpot_ms") is not None]
    server_queues = [float(r["server_queue_ms"]) for r in ok if r.get("server_queue_ms") is not None]
    server_prefills = [float(r["server_prefill_ms"]) for r in ok if r.get("server_prefill_ms") is not None]
    server_itls = [float(r["server_mean_itl_ms"]) for r in ok if r.get("server_mean_itl_ms") is not None]
    prompt_tokens = sum(int(r["prompt_tokens"]) for r in ok)
    completion_tokens = sum(int(r["completion_tokens"]) for r in ok)

    print("============ Chat JSONL Benchmark Result ============")
    print(f"Label:                                   {args.label}")
    print(f"Successful requests:                     {len(ok)}")
    print(f"Failed requests:                         {len(failed)}")
    print(f"Maximum request concurrency:             {args.max_concurrency}")
    print(f"Fire mode:                               {args.fire_mode}")
    print(f"Active sessions per wave:                {args.active_sessions}")
    print(f"Active session mode:                     {args.active_session_mode}")
    print(f"Wrap last wave:                          {int(args.wrap_last_wave)}")
    print(f"Dataset repeat:                          {args.repeat_dataset}")
    print(f"Duration target (s):                     {args.duration_seconds}")
    print(f"Benchmark duration (s):                  {duration:.2f}")
    print(f"Total input tokens:                      {prompt_tokens}")
    print(f"Total generated tokens:                  {completion_tokens}")
    print(f"Request throughput (req/s):              {len(ok) / duration if duration else 0:.2f}")
    print(f"Output token throughput (tok/s):         {completion_tokens / duration if duration else 0:.2f}")
    if server_queues:
        print(f"Mean server_queue (ms):                  {mean_or_zero(server_queues):.2f}")
        print(f"P50 server_queue (ms):                   {percentile(server_queues, 50):.2f}")
        print(f"P90 server_queue (ms):                   {percentile(server_queues, 90):.2f}")
    else:
        print("Mean server_queue (ms):                  n/a (need --enable-per-request-metrics)")
    if server_prefills:
        print(f"Mean server_prefill (ms):                {mean_or_zero(server_prefills):.2f}")
        print(f"P50 server_prefill (ms):                 {percentile(server_prefills, 50):.2f}")
        print(f"P90 server_prefill (ms):                 {percentile(server_prefills, 90):.2f}")
    if server_itls:
        print(f"Mean server_ITL/TPOT (ms):               {mean_or_zero(server_itls):.2f}")
        print(f"P50 server_ITL/TPOT (ms):                {percentile(server_itls, 50):.2f}")
        print(f"P90 server_ITL/TPOT (ms):                {percentile(server_itls, 90):.2f}")
    if ttfts:
        print(f"Mean client TTFT (ms):                   {mean_or_zero(ttfts):.2f}")
        print(f"P50 client TTFT (ms):                    {percentile(ttfts, 50):.2f}")
        print(f"P90 client TTFT (ms):                    {percentile(ttfts, 90):.2f}")
    else:
        print("Mean client TTFT (ms):                   n/a")
    if tpots:
        print(f"Mean client TPOT (ms):                   {mean_or_zero(tpots):.2f}")
        print(f"P50 client TPOT (ms):                    {percentile(tpots, 50):.2f}")
        print(f"P90 client TPOT (ms):                    {percentile(tpots, 90):.2f}")
    else:
        print("Mean client TPOT (ms):                   n/a")
    print(f"Mean client E2EL (ms):                   {mean_or_zero(latencies):.2f}")
    print(f"P50 client E2EL (ms):                    {percentile(latencies, 50):.2f}")
    print(f"P90 client E2EL (ms):                    {percentile(latencies, 90):.2f}")
    print("======================================================")
    if failed:
        print("FAILED_SAMPLE", failed[0])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
