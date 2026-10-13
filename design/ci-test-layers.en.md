# AgentInfer CI test layers (L1–L5)

中文：[ci-test-layers.md](ci-test-layers.md)

> **Document type**: **RFC (design proposal)** for SIG/CIE review (see [router#273](https://github.com/vllm-project/router/issues/273)).
> RFC body may live in `design/`, a fork, or a linked Issue; merging the RFC into upstream `main` is a separate decision from the small implementation PRs below.
>
> **Status**: Draft RFC · current inventory (2026-10-08). Layer names match L1–L5 in the
> [CI quality-gate and release guide](ci-cd-quality-gate-setup-guide.md). Scope is `agent-infer` only.
>
> **Hardware**: L2/L3 are empty because there is **no CI-dedicated GPU/NPU pool** wired to PR/merge gates (separate from L4 dev nightly). **Dev GPU and NPU** already run
> [`tests/e2e/cases/`](../tests/e2e/cases/) perf JSON nightly and **sync results to a dashboard**;
> NPU uses in-repo commands, GPU uses a dev-provided script. L4 target: unify GPU with `run_benchmark.py`.

## 1. Background and goals

### Motivation

This repository currently has **two independent PR-level CI systems** with different configs and runners:

| Dimension | **openJiuwen-bot / CodeArts** (GitCode) | **GitHub Actions** ([`ci.yml`](../.github/workflows/ci.yml)) |
| --- | --- | --- |
| Trigger | Mirrored PR on GitCode | `pull_request` / push to `main` |
| Blocks merge? | **Yes** (`/check-pr`, static table, UT report, `ci-successful`) | **Not on upstream** (workflow registered, public run count is 0) |
| L1 static | Static analysis, blacklist, anti-poisoning, compliance, ruff | pre-commit (Ruff, typos, markdownlint), DCO |
| L1 UT | **859** cases (CodeArts `unit_test_report.html`) | `ci.yml` **`cpu-tests`** **824** (0 runs upstream; paths in [§2.2.1](#221-l1-ut-paths-and-counts-today)) |

That split causes real problems: **merge gates are disconnected from GitHub Checks** (`ci-successful` is not an Actions job);
**L1 UT has no single contract** (859 vs 824; typos and markdownlint are not in the bot ruff job);
**accelerator tests live in the tree but are not layered** (core, serve E2E, `tests/e2e`, compare shells are outside PR UT);
**L4 already runs nightly on dev GPU and NPU** ([`tests/e2e/cases/`](../tests/e2e/cases/) + **dashboard sync**),
but **GPU uses a dev script while NPU uses in-repo commands**—two entrypoints not fully aligned);
**L2–L5 are defined in the umbrella guide but not fully scheduled here** (and with no **CI-dedicated GPU/NPU** we must not bolt accelerators onto every PR).

**This document** (in-repo RFC / inventory, not platform config) will:

1. Use **L1–L5** names aligned with the [CI quality-gate guide](ci-cd-quality-gate-setup-guide.md) for when, which hardware, and what runs.
2. **State the current picture**: which layers run today, which are empty, and what each CI plane covers.
3. **State the target**: L1 **only through CodeArts / the bot** for PR monitoring and merge gates; **retire** the dual-track [`.github/workflows/ci.yml`](../.github/workflows/ci.yml);
   L2/L3 **stay empty until CI-dedicated GPU/NPU capacity exists**; L4/L5 unify nightly entry and dashboard triage (see **Near-term alignment**).
4. Leave a path to **promote L4 cases into L2/L3** after CI GPU/NPU exists, instead of ad-hoc accelerator jobs on every PR.

**§1** is the overview (motivation, current state, target, follow-up); **§2 onward** is checkable detail per L1–L5 (commands, paths).

### Current state

#### What each CI plane does

- **CodeArts**: blocks GitCode merge; in practice only **L1** (static/compliance + CPU UT). Sample UT: 2026-10-08, `8988342`, Python 3.11, 849 passed / 10 skipped.
- **GitHub `ci.yml`**: describes L1 (pre-commit, `cpu-tests`, DCO) plus Router patch / WASM path gates; **not executed** on `openJiuwen-ai/agent-infer` upstream.
- **Other GitHub checks**: `license/cla`, Copilot review; parallel to CodeArts, not `ci.yml` jobs.
- **Common confusion**: GitHub workflow `CI` on `openJiuwen-ai/agent-infer` is `state=active` with **0 public runs**; **`ci-successful` on a PR is written back from CodeArts**, not from Actions.

#### Layers (today)

| Layer | When | Hardware | Blocks merge? | Today |
| ----- | ---- | -------- | ------------- | ----- |
| **L1** | Every PR | CPU | Yes | CodeArts runs; `ci.yml` not triggered on upstream |
| **L2** | After PR ready | CI GPU/NPU | Could gate | **Not built**; **no CI-dedicated GPU/NPU** (L4 dev machines are not PR gates) |
| **L3** | After merge | CI GPU/NPU | Red main blocks next merge | **Not built**; same blocker |
| **L4** | Nightly | Dev GPU/NPU | No | **Running**: daily `tests/e2e/cases/` perf JSON → **dashboard**; full prod sweep **~8 h/night** (too long); NPU in-repo cmd, GPU dev script |
| **L5** | Weekly / pre-release | Dev GPU/NPU | Release evidence | Soak partial; scheduling to align with L4 |

```text
Every PR                 L1   ← CodeArts (+ ci.yml not running upstream)
Ready-PR accelerators    L2   ← empty (no CI GPU/NPU)
Post-merge accelerators  L3   ← empty (no CI GPU/NPU)
Nightly                  L4   ← ~8h full JSON today → dashboard; target: critical subset + run_benchmark.py
Weekly / pre-release     L5   ← dev GPU/NPU; align scheduling and artifacts
```

#### L4 nightly accelerators: in-repo entry vs production (today)

Dev **GPU** and **NPU** run [`tests/e2e/cases/`](../tests/e2e/cases/) perf JSON **every day** and **sync results to a dashboard**.
They do **not** use the same trigger today:

| | **In-repo (documented)** | **Production · dev NPU** | **Production · dev GPU** |
| --- | --- | --- | --- |
| Case files | Same JSON tree | Same JSON tree | Same JSON tree |
| How it runs | `pytest tests/e2e/run_benchmark.py --test-config-file <json>` | **In-repo command** (matches [`tests/e2e/README.md`](../tests/e2e/README.md)) | **Dev-provided script** (out of repo) |
| Results | pytest log + `{result_root}` | **Dashboard** + artifacts | **Dashboard** + script output dirs |
| vs PR CI | Contributor self-test | Not L1 CodeArts; maintainers triage anomalies | Same |

**Should they unify?** **Yes, recommended.** JSON stays the single scenario source; **focus on GPU** migrating to
`run_benchmark.py` so it matches **NPU already on in-repo commands**. Keep dashboard sync as the L4 results surface.

### Target state

#### Layers (target)

| Layer | When | Hardware | Required? | Target |
| ----- | ---- | -------- | --------- | ------ |
| **L1** | Every PR | CPU | Blocks merge | **Only merge gate**: CodeArts static/compliance + UT **859**; **no** GitHub `ci.yml` as a required PR check (remove/archive workflow) |
| **L2** | After PR ready | CI GPU/NPU | May gate key PRs | **High-priority feature function + accuracy** (**no performance** — §3); **after CI GPU/NPU** |
| **L3** | After merge | CI GPU/NPU | Red main blocks merge | **Low-priority feature function + accuracy** (**no performance** — §4); do not duplicate L2 gate set |
| **L4** | Nightly | Dev GPU/NPU | Review failures next day | **Performance** (§5); **function + accuracy** only on **L2/L3**; soak on L5 |
| **L5** | Weekly / pre-release | Dev GPU/NPU | Green before release | `480x12` soak; add 72h, fault injection, coverage over time |

**Principle**: **intercept as early as possible** — run function + accuracy on **L2** before merge when feasible; **L3** covers post-merge subset that should not/all fit L2.

> **Which features are high vs low priority, and which cases map to L2 vs L3**: **no conclusion in this RFC yet**; **backfill §3, §4, and §7 after team alignment** (link an issue or appendix if helpful).

#### L4 daily results and triage (target)

- **Expected**: each night produces comparable artifacts (run dir, vLLM logs, BenchKit `Results` summary) vs prior green run or documented thresholds.
- **When results are unexpected**: on dashboard or nightly anomalies, **pair with the developer who owns the module or recent change** to triage (regression vs environment vs model path vs noise); **open an issue** on GitCode/GitHub when a fix or follow-up is needed (attach commit, dashboard snapshot, or `result_root`). **L4 red does not block PR merge (L1)**; unexplained sustained red should gate release cautiously (L5).
- **Paper trail**: put conclusions and repro commands in the **issue**; a future `docs/en/how-to/` nightly runbook may link here.

#### Near-term alignment (L2 first · L3 TBD · L4 subset)

- **L1 single track**: merge gates **only** on CodeArts / `openJiuwen-bot`; retire [`ci.yml`](../.github/workflows/ci.yml). DCO/CLA unchanged; keep [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) for local self-test.
- **L2 (blue-zone L20 + Jenkins)**: **L2 role** function + accuracy (**backfill §3 after alignment**); engineering first wire-up `test_serve_flag_e2e.py` (**not** a priority conclusion); `agentcache/core` not on L20. CodeArts → Jenkins; phase 1 report only.
- **L4**: **local/yellow-zone** dev GPU/NPU nightly; unify entry + **§5.2** daily JSON matrix; dashboard triage → **issues** (§5.3).

#### Follow-up work: pipeline changes and per-layer cases

Turn **Near-term alignment** into **platform jobs** (CodeArts / Jenkins / cron) **and** **cases per layer** (in-repo tests + RFC tables §2.2.1, §3–§6).

- **Platform**: edit jobs on CodeArts / Jenkins / cron; merge PRs **cannot** change UT/static/conditional jobs.
- **Cases**: **backfill §2.2.1, §3–§6** after alignment when jobs run new paths/JSON/shells; keep RFC tables in sync with production.

Full phased table (platform + **add/maintain cases** column), L5 row, and layer-by-layer case checklist: **[ci-test-layers.md](ci-test-layers.md)** §1「后续工作：流水线改动与分层用例」.

## 2. L1 (every PR, CPU)

L1 is static/compliance checks plus CPU unit tests. Pytest markers are semantic only; **CI collects by path, not `-m`**.

### 2.1 Static checks and compliance

| Check | Who runs it | What it does |
| ----- | ----------- | ------------ |
| Static analysis | CodeArts | Security and code-quality scan |
| Blacklist scan | CodeArts | [community blacklist](https://gitcode.com/openJiuwen/community/blob/main/security/blacklist.md) |
| Anti-poisoning scan | CodeArts | Malicious-file scan |
| Open-source compliance | CodeArts | SCA / licenses |
| ruff codecheck | CodeArts | Ruff |
| pre-commit (local) | Contributor machine; **not** a bot job | Ruff check/format, typos, markdownlint-cli2 (`.pre-commit-config.yaml`) |
| DCO / CLA | GitCode CLA + commit signoff; `ci.yml` `dco` job to retire | Every commit contains `Signed-off-by:`; `openJiuwen-cla/yes` |
| CLA | GitHub `license/cla`; GitCode `openJiuwen-cla/yes` | Contributor agreement |

Locally:

```bash
pre-commit run --all-files --hook-stage manual
git commit --signoff
```

`pre-commit` adds **typos** and **markdownlint** on top of the bot's ruff item.

### 2.2 CPU unit tests

UT contract = CodeArts **859** collect (CPU, no vLLM; table below). Self-test: **§8**.

#### 2.2.1 L1 UT paths and counts (today)

Sample `8988342` (2026-10-08): **849 passed, 10 skipped**, collect **859**. **`ci.yml` 824** column is historical (upstream **does not run** it). **Merge gate = CodeArts 859 only.**

| Scope | Count | CodeArts | `ci.yml` | Notes |
| ----- | ----- | -------- | -------- | ----- |
| `tests/agentbench` (incl. `benchmark_smoke`) | 612 | ✓ | ✓ | L1 |
| `tests/scheduling` | 209 | ✓ | ✓ | L1 |
| `tests/agentcache/test_entrypoints.py` | 3 | ✓ | ✓ | L1 |
| `tests/agentcache/entrypoints/test_serve_profile.py` | 18 | ✓ | **✗** | L1; stub CLI |
| `tests/tools` | 17 | ✓ | **✗** | L1 |
| **L1 UT total** | **859 / 824** | **859** | **824** | |
| [`tests/agentcache/core/`](../tests/agentcache/core/) | — (not in 859) | **✗** | **✗** | `cpu_test`, needs vLLM; L4 or optional UT |
| [`test_serve_flag_e2e.py`](../tests/agentcache/entrypoints/test_serve_flag_e2e.py) | 5 | **✗** | **✗** | `gpu_test`; **L2** (§3), not e2e JSON |

`tests/e2e/` perf JSON and the rest → **L4/L5** (§5).

## 3. L2 (accelerator smoke after a PR is ready)

Definition: after PR ready, on **CI-dedicated GPU/NPU**, run **§1** **high-priority feature** **function + accuracy** (**before merge**; **no performance** — see **§5 L4**), ~30 min. Failure may block merge (**phase 1: wire up first, gate later**).

**Which features are high-priority and which cases map here**: **TBD — align first** (see §1). Until then: **engineering first wire-up** and the **case inventory** below — not a priority ruling.

**Role (vs L3 / L4)** — **function** and **accuracy** are both tracked as **cases** in the inventory below (backfill after alignment); **no performance** (→ §5 L4).

| Type | L2 cases (role) |
| ---- | --------------- |
| **Function** | Pre-merge **function** cases |
| **Accuracy** | Pre-merge **accuracy** cases |

**Near-term rollout (team alignment)**

| Item | Detail |
| ---- | ------ |
| **Capacity** | **Blue-zone L20** (separate from L4 yellow-zone dev nightly) |
| **Scheduler** | **Jenkins** job (e.g. agent label `gpu-l20-blue`) |
| **Engineering first wire-up** | [`test_serve_flag_e2e.py`](../tests/agentcache/entrypoints/test_serve_flag_e2e.py) (Jenkins path; **priority TBD**) |
| **Full L2 list** | **Backfill** placeholder row after alignment |
| **Trigger** | CodeArts → Jenkins, PR label, or manual (**not** merged into the 859 UT stage) |
| **L1 boundary** | Bot **unchanged** on UT/static; L2 writes checks/comments separately |

**Case inventory**

| Path | Notes | Scheduling | Remark |
| ---- | ----- | ---------- | ------ |
| [`test_serve_flag_e2e.py`](../tests/agentcache/entrypoints/test_serve_flag_e2e.py) | 5× `gpu_test`, real serve + HTTP | **First wire-up** (blue L20) | Not a high-priority feature decision |
| *L2 cases · backfill after alignment* | function + accuracy | — | **Backfill after alignment** |

[`tests/agentcache/core/`](../tests/agentcache/core/) **does not use L20**; optional nightly → **§5**.

L4 dev nightly **cannot** replace L2 (no PR queue, timeout, or check contract). A dedicated NPU L2 queue can follow later without blocking L20.

## 4. L3 (accelerator integration after merge to main)

Definition: after merge to `main`, run **§1** **low-priority feature** **function + accuracy** on **CI-dedicated GPU/NPU** (**no performance** — see **§5 L4**; ~1 h). Split vs **§3 L2** **backfilled after alignment**; L3 must **not** duplicate the L2 gate set.

**Which features are low-priority and which cases map here**: **TBD — align first** (see §1).

**Role (vs L2 / L4)** — **function** and **accuracy** are both **cases** in the inventory below (backfill after alignment); **no performance** ([`tests/e2e/cases/`](../tests/e2e/cases/) → §5 L4 / §6 L5).

| Type | L3 cases (role) |
| ---- | --------------- |
| **Function** | Post-merge **function** cases |
| **Accuracy** | Post-merge **accuracy** cases |

**Current blocker: same as L2 — no post-merge job on CI-dedicated GPU/NPU, not missing in-repo cases.**

**Case inventory** (perf JSON not listed here — see §5)

| Case | Entry | Notes | Scheduling |
| ---- | ----- | ----- | ---------- |
| [`run-baseline-smoke.sh`](../tests/agentbench/run-baseline-smoke.sh) | shell | baseline single-point (no compare) | Not wired |
| [`run-scheduler-e2e-compare.sh`](../tests/agentbench/run-scheduler-e2e-compare.sh), [`run-router-e2e-compare.sh`](../tests/agentbench/run-router-e2e-compare.sh) | shell | **baseline ↔ agentinfer** dual-arm `compare` (see below) | Not wired |
| *L3 cases · backfill after alignment* | — | alignment-selected subset | — |

- **Vs `tests/e2e`**: the two compare shells use the same **baseline arm + agentinfer arm → BenchKit `compare`** pattern as [`tests/e2e/cases/`](../tests/e2e/cases/), not a different comparison model. **Carrier and layer differ**: e2e uses **JSON + pytest** ([`run_benchmark.py`](../tests/e2e/run_benchmark.py)), one file per arm, paired runs then dashboard/manual compare → **§5 L4/L5 performance** (plan-subagent, concurrency tiers). The shells orchestrate **cold start both arms in one script** for **scheduler swap** vs **router path**, usually smaller scale → **L3 function/accuracy** candidates here.

- L4 dev nightly does not replace merge-gate contracts or **L2 pre-merge** interception.
- **Backfill §4 and §7 after alignment**; wire **L3 job** on platform.

## 5. L4 (nightly, development GPU/NPU)

### 5.1 Today: dev GPU and NPU run `tests/e2e/cases/` daily

- **Running**: development **GPU** and **NPU** both run [`tests/e2e/cases/`](../tests/e2e/cases/) **perf JSON every day**.
- **Dashboard**: results are **synced to a dashboard** for the team; this is **not** an L1 PR gate and not CodeArts / GitHub Checks.
- **Two triggers**:
  - **NPU**: **in-repo command** — `pytest tests/e2e/run_benchmark.py --test-config-file …` (see [`tests/e2e/README.md`](../tests/e2e/README.md)).
  - **GPU**: **dev-provided script** (out of repo; scheduling and params = production script, not the pytest entry above).
- **Runtime (today)**: dev **GPU + NPU** cron runs **many** JSON files under [`tests/e2e/cases/`](../tests/e2e/cases/) each night; end-to-end about **8 hours/night**, well over the [CI quality-gate guide](ci-cd-quality-gate-setup-guide.md) L4 **< 3 h** nightly budget—slow feedback and long machine hold.
- **Risk**: JSON changes in git may diverge from the GPU script; dashboard anomalies still need **maintainer triage** (§ 5.3); runbook / issue template TBD.

**Perf and other accelerator cases (in-repo inventory)**

| Case | Path / entry | Prod nightly today | L4 target |
| ---- | ------------ | ------------------ | --------- |
| **Baseline plan-subagent** | `tests/e2e/cases/baseline/glm52_{8x4,16x8,24x12,32x16}_plan_subagent.json` (4) | Often run on **both** GPU and NPU | Pair with agentinfer; **nightly subset** favors 8x4; higher tiers → rotate / L5 |
| **AgentInfer plan-subagent** | `tests/e2e/cases/agentinfer/glm52_{8x4,16x8,24x12,32x16}_plan_subagent.json` (4) | same | same; schema → [`tests/e2e/README.md`](../tests/e2e/README.md) |
| **Long soak** | `agentinfer/glm52_480x12_plan_subagent.json` | may run in full matrix | **not L4 nightly** → **§6 L5** |
| **agentcache unit** | [`tests/agentcache/core/`](../tests/agentcache/core/) (`cpu_test`, many files) | usually not nightly | **optional separate job** (§ follow-up) |
| **AgentBench env compare** | [`run-scheduler-e2e-compare.sh`](../tests/agentbench/run-scheduler-e2e-compare.sh), [`run-router-e2e-compare.sh`](../tests/agentbench/run-router-e2e-compare.sh), [`run-baseline-smoke.sh`](../tests/agentbench/run-baseline-smoke.sh) | ad hoc | Real vLLM/Router + `MODEL`; **optional L4** or promote baseline smoke to **L3** |
| **Manual compare** | `vllm bench serve --agentinfer compare --baseline … --candidate …` | manual on anomalies | Not a pytest job; see [`tests/e2e/README.md`](../tests/e2e/README.md) |

### 5.2 Target: one entrypoint, **nightly critical scenarios only**

| Unify | Detail |
| ----- | ------ |
| **Case definition** | JSON in-repo only; scenario changes require a PR under `tests/e2e/cases/` |
| **Runner** | **Both** GPU and NPU nightly call `pytest tests/e2e/run_benchmark.py --test-config-file <path>` (GPU migrates off dev script or wraps the same command); keep dashboard sync |
| **Duration policy** | Do **not** run all **8** JSONs (8x4–32x16 × baseline/agentinfer) on **both** GPU and NPU every night; **subset for L4 nightly**, target **< 3 h/night** (from ~8 h) |
| **Recommended · nightly (critical)** | Minimum concurrency **smoke**: `agentinfer/glm52_8x4_plan_subagent.json`; optional **control** `baseline/glm52_8x4_plan_subagent.json` (SIG defines one dashboard contract for GPU vs NPU split) |
| **Recommended · not every night** | `16x8` / `24x12` / `32x16`: **rotate** (e.g. one agentinfer + matching baseline per weekday), or a **weekly** full matrix pass—not bound to every nightly |
| **L5 only** | `agentinfer/glm52_480x12_plan_subagent.json` **excluded** from L4 nightly (long soak, §6) |
| **Optional add-ons** | `agentcache/core`, `test_serve_flag_e2e.py`, three `run-*-compare.sh` (**separate** jobs so nightly does not creep back to 8 h) |

Nine JSON files exist in-repo (`baseline/` 4 + `agentinfer/` 5 including 480x12). Prod “run everything” ≈ 8 h; target L4 **1–2 critical JSONs per night**, rest via rotation / L5.

Target nightly benchmark (in-repo command):

```bash
pytest -s tests/e2e/run_benchmark.py \
  --test-config-file tests/e2e/cases/agentinfer/glm52_8x4_plan_subagent.json
```

Optional serve tests (same layer):

```bash
pytest tests/agentcache/core \
  tests/agentcache/entrypoints/test_serve_flag_e2e.py -q
```

### 5.3 When daily results are not as expected

- **Who**: whoever spots the dashboard anomaly or owns nightly first pass—**pull in the matching developer** (by failing JSON area, recent PR author, or module owner).
- **What to inspect**: **dashboard** metrics first; then `result_root`, vLLM logs, BenchKit `Results:` / completed tasks vs last green run or methodology thresholds.
- **What to do**: after triage, **open an issue** for fixes or document env/noise and re-run; **L4 red does not block PR merge (L1)**; unexplained sustained red should gate release cautiously (L5).
- **TBD**: runbook URL, alert channel, artifact retention—update § target when landed.

Splitting JSON across nights and rotating large tiers stays L4, not L2/L3; **shorter nightly by design** does not drop 16x8–32x16—it avoids an 8 h full sweep every night.

## 6. L5 (weekly, and before release, development GPU/NPU)

Dev **GPU/NPU** can hold a longer weekly window (L5 trigger); **does not** block PR merge (L1); should be green before release.

**Case inventory**

| Case | Entry | Notes | Scheduling |
| ---- | ----- | ----- | ---------- |
| [`glm52_480x12_plan_subagent.json`](../tests/e2e/cases/agentinfer/glm52_480x12_plan_subagent.json) | `pytest tests/e2e/run_benchmark.py --test-config-file …` | In-repo **long soak** (480 tasks × concurrency 12); **split out** from L4 “run everything nightly” | Often in full matrix today; **target**: dedicated weekly / pre-release |
| **High-tier perf matrix (rotation)** | `16x8`, `24x12`, `32x16` under `agentinfer/` and `baseline/` (6 JSONs) | When L4 nightly **does not** run all tiers, L5 **absorbs** paired baseline/agentinfer runs | Suggest **1–2 tiers per week** |
| **Scheduler / Router compare** | `run-scheduler-e2e-compare.sh`, `run-router-e2e-compare.sh` | Cold compare upstream vs AgentInfer paths; long, full stack | **Optional** pre-release (separate from JSON nightly) |
| **Umbrella extensions** | — | 72 h run, kill-and-recover, coverage gates | **No in-repo cases yet**; reserve window, add rows when landed |

L5 does not re-run the 859 L1 unit tests. Release evidence still goes in the PR template:
command, raw logs, parsed summary, dashboard or `result_root`.

## 7. Where current markers and paths sit

> **How to read this table**: layers follow **when tests run, merge-gate status, and environment contract** (L1 = every PR, CPU, bot job without vLLM)—not GPU/NPU alone. A `cpu_test` can still sit on L4 if it needs vLLM or ships with nightly/serve add-ons.

| Marker or path | Layer |
| -------------- | ----- |
| Static checks, DCO, CLA | L1 |
| agentbench / scheduling / tools / `test_serve_profile` | L1 (bot 859) |
| `benchmark_smoke` | L1 (fake runtime, no accelerator) |
| `tests/agentcache/core/*` (`cpu_test`, vLLM package; usually no card; **not in L1 859**; same band as serve add-ons) | L4 |
| `test_serve_flag_e2e.py` (`gpu_test`, serve function E2E) | **L2 engineering first wire-up** (§3); long-term layer **backfill after alignment** |
| `tests/e2e/cases/*.json` (except 480x12) | **L4 performance** (nightly/dashboard); **not L2/L3** |
| compare shells, other accelerator cases | Possible **L2/L3 function + accuracy** carriers; **map after alignment** |
| `glm52_480x12_plan_subagent.json` | L5 |
| L2 | High-priority feature · function + accuracy (§3; **backfill after alignment**) |
| L3 | Low-priority feature · function + accuracy (§4; **backfill after alignment**) |

**Notes**: L1 **859** and agentcache rows **not in L1** → **§2.2.1**. **L4 JSON** → **§5**; **480x12** → L5. **patch/WASM** → §1 follow-up (Router split, TBD). **High/low feature ↔ cases** → **backfill §7 after alignment**.

## 8. Local equivalents

```bash
# L1 static
pre-commit run --all-files --hook-stage manual

# L1 UT (bot 859; §2.2.1 — no core / serve_flag_e2e)
pytest tests/agentbench tests/scheduling \
  tests/agentcache/test_entrypoints.py \
  tests/agentcache/entrypoints/test_serve_profile.py \
  tests/tools -q

# L4 · agentcache not in L1 (§2.2.1 table)
pytest tests/agentcache/core -q
pytest tests/agentcache/entrypoints/test_serve_flag_e2e.py -v

# L4 benchmark (dev GPU, target nightly entry)
pytest -s tests/e2e/run_benchmark.py \
  --test-config-file tests/e2e/cases/agentinfer/glm52_8x4_plan_subagent.json

```
