# AgentInfer CI 测试分层（L1–L5）

English: [ci-test-layers.en.md](ci-test-layers.en.md)

> **文档性质**：**RFC（设计提案）** — 梳理 CI 分层、859 UT、单轨 L1、L4 缩时、**流水线改动清单**。
> 用于 SIG / CIE / 维护者 **评审与排期**（格式参考 [router#273](https://github.com/vllm-project/router/issues/273)）。
> **RFC 正文**可以：放在本机 `design/`、fork、或发 **GitCode/GitHub Issue** 链到本文；**是否**将 RFC 合入上游 `main` 由维护者决定（与下文「要上主仓的小 PR」不是同一回事）。
>
> **状态**：Draft RFC · current inventory（2026-10-08）。层级定义与
> [CI 质量看护与版本发布总指南](ci-cd-quality-gate-setup-guide.md) 的 L1–L5 对齐，范围只覆盖 `agent-infer`。
>
> **硬件**：L2/L3 **缺可挂 PR/merge 门禁的 CI 专用 GPU、NPU 机器**（与 L4 用的开发机 nightly 池不是同一套调度/合同），故带卡用例先空。
> 开发 **GPU/NPU** 已在每日跑 `tests/e2e/cases/` 并同步看板（NPU 仓内命令，GPU 开发脚本）；L4 目标让 GPU 与 NPU 入口统一。

## 1. 背景与目标

### Motivation

当前仓库里同时存在**两套 PR 级 CI 看护**，配置来源不同、执行面也不同：

| 维度 | **openJiuwen-bot / CodeArts**（GitCode） | **GitHub Actions**（[`ci.yml`](../.github/workflows/ci.yml)） |
| --- | --- | --- |
| 谁触发 | 镜像 PR 到 GitCode | jiusi项目中提PR触发 |
| 合入是否认 | **是**（`/check-pr`、静态表、UT 报告、`ci-successful`） | **upstream 上未跑**（workflow 已注册，公开 run 数为 0） |
| L1 静态 | 静态检查、禁用词、防投毒、开源合规、ruff | pre-commit（Ruff、typos、markdownlint）、DCO |
| L1 UT | **859** 条（见 CodeArts `unit_test_report.html`） | `ci.yml` **`cpu-tests`** 设计 **824** 条（upstream 未跑；路径见 [§2.2.1](#221-l1-ut-路径与条数现状)） |

但这样带来几个问题：
1、**合入门禁与 GitHub Checks 不相干**（GitHub 上 `ci-successful` 不是 Actions job， 两者的UT覆盖范围不一致， 代码检查工具不一致）；
2、**带卡用例已进仓却未分层**（`agentcache/core`、serve E2E、`tests/e2e`）；
3、**L4 已在开发 GPU/NPU 上每日跑** [`tests/e2e/cases/`](../tests/e2e/cases/) 并**同步看板**，但 **GPU 用开发脚本、NPU 用仓内命令**，两套入口尚未完全一致）；
4、**L2–L5 在总指南里有定义，本仓没有按层落地的清单与调度**（无 **CI 专用 GPU/NPU**）。

下文 **§1** 为总览（动机、现状、目标与后续工作）；**§2 起** 按 L1–L5 展开命令、用例路径等可核对细节。

### 现状

#### 双轨 CI 各管什么

- **CodeArts**：挡住 GitCode 合入；当前实质只覆盖 **L1**（静态/合规 + CPU UT）。UT 样例：2026-10-08、`8988342`、Python 3.11，849 passed / 10 skipped。
- **GitHub `ci.yml`**：描述 L1（pre-commit、`cpu-tests`、DCO）及 Router patch / WASM 路径门；在 `openJiuwen-ai/agent-infer` **尚未执行**。
- **GitHub 其它**：`license/cla`、Copilot review；与 CodeArts 并行，不是 `ci.yml` job。
- **易混点**：`openJiuwen-ai/agent-infer` 的 GitHub workflow `CI` 为 `state=active`，公开 API **run 数 0**；PR 上的 **`ci-successful` 来自 CodeArts 回写**，不是 Actions job。

#### 分层（当前）

| 层级 | 何时 | 硬件 | 合入必过？ | 当前 |
| ---- | ---- | ---- | ---------- | ---- |
| **L1** | 每个 PR | CPU | 是 | CodeArts 在跑；`ci.yml` 未在 upstream 触发 |
| **L2** | PR ready 后 | CI GPU/NPU | 可设计为必过 | **未建**；**缺 CI 专用 GPU、NPU**，无 job（L4 开发机不挂 PR 门禁） |
| **L3** | merge 后 | CI GPU/NPU | 主干红则阻断 | **未建**；同上 |
| **L4** | 每晚 | 开发 GPU/NPU | 否 | **已在跑**：GPU、NPU 每日跑 `tests/e2e/cases/` 性能 JSON → **看板**；现网整包约 **NPU 8h/次 GPU 4h/次**，偏长；NPU 仓内命令，GPU 开发脚本 |
| **L5** | 每周 / 发版前 | 开发 GPU/NPU | 发版证据 | 未定时运行长稳，长稳/故障注入用例不全 |



### 目标

#### 分层（目标态）

| 层级 | 何时 | 硬件 | 必过？ | 目标 |
| ---- | ---- | ---- | ------ | ---- |
| **L1** | 每个 PR | CPU | 合入必过 | **唯一合入门禁**：CodeArts 静态/合规 + UT **859**；**不再**以 GitHub `ci.yml` 为 PR 必过项（合并/删除 workflow） |
| **L2** | PR ready 后 | CI GPU/NPU | 关键 PR 可门禁 | **高优先级特性的功能 + 精度**；**CI 专用 GPU/NPU 接入后启用** |
| **L3** | merge 后 | CI GPU/NPU | 主干红则阻断 | **低优先级特性的功能 + 精度**；与 L2 分工、**不重复**同一门禁集 |
| **L4** | 每晚 | 开发 GPU/NPU | 次日看失败清单 | **性能**用例|
| **L5** | 每周 / 发版前 | 开发 GPU/NPU | 发版前需绿 | 长稳可靠性场景 |


> **高 / 低优先级特性分别指什么、哪些用例归 L2 vs L3**：**本 RFC 暂不结论**；**对齐后回填** §3、§4 用例表与 §7 归层（结论可链 issue 或本文件附录）。

#### L4 每日结果与排查（目标）

- **预期**：每晚跑完生成可对比的 artifact（run 目录、vLLM 日志、BenchKit `Results` 摘要）。
- **不符合预期时**：看板或 nightly 异常时，**找对应模块/近期改动的开发同学一起定位**；需要修复或长期跟踪时在 GitCode/GitHub **提 issue**（附 commit、看板截图或 `result_root` 等）。**L4 红不挡 PR 合入**（L1）；未解释的持续失败发版前应谨慎（L5）。
- **留痕**：结论与复现命令写在 **issue** 里。

#### 10月份迭代（L2 先行 · L3 待排 · L4 缩圈）

- **L1 整合**：合入与 CI 监控**只认** CodeArts / `openJiuwen-bot`；退役 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)。DCO/CLA 继续 signoff + `/check-cla`；**保留** [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) 作本地自测。
- **L2（蓝区 L20 + Jenkins）**：合入前跑 **L2 角色** 的功能 + 精度（**特性清单对齐后回填 §3**）；工程 **首通** [`test_serve_flag_e2e.py`](../tests/agentcache/entrypoints/test_serve_flag_e2e.py)（**不等同**「已定为高优特性」）；`agentcache/core` **不占 L20**。**CodeArts → Jenkins**；第一期 **先报结果**，再定 `/check-pr`。
- **L4**：**本地黄区** GPU/NPU 继续 nightly；统一入口、对齐 **§5.2**「每天跑哪些 JSON」；看板异常 → 找对应开发 + **提 issue**（§5.3）。
- **分层用例**：各层 **增加 / 维护对应用例** 与 **§2.2.1、§3–§6** 同步，见 **§1 · 后续工作 · 分层用例** 与建议顺序表「增加 / 维护对应用例」列。

#### 后续迭代工作：流水线改动与分层用例

本节 = 把 **§1 目标 · 近期可对齐项** 落到 **平台 job** 与 **各层对应用例** 上。

- **平台**：**直接在 CodeArts / Jenkins / 现网 cron 改**；merge PR **不能**改 UT/静态/条件 job（扩 `core/` 进 UT 也是改流水线，**不要**指望 Test PR）。
- **用例**：仓内增改测试与 **对齐后回填** §2.2.1、§3–§6 用例表；job 启用时把实际路径、JSON、shell **写回对应层级小节**，避免「流水线跑了、RFC 表还是空」。

**建议顺序**（可并行，按依赖微调）

| 阶段 | 对齐哪条目标 | 平台上改什么 | **增加 / 维护对应用例** |
| ---- | ------------ | ------------ | ------------------------- |
| **① L1 单轨** | 只认 CodeArts；退役 `ci.yml`；Router patch/WASM **待评估是否丢弃** | CodeArts **静态**（typos/md）；patch/WASM **仅评估结论为保留时** 再配 Jenkins | L1 以现有 **859** 为主；静态无新增 pytest |
| **② L1 UT 补齐** | 机器人 **859** 与 §2.2.1 一致 | CodeArts **UT 路径**（现网已是则核对即可；§2.2.1、§8），把 `agentcache/core` 并进 UT | 核对路径与 §2.2.1 表一致 |
| **②′ L2** | 蓝区 L20；L2 **功能 + 精度** | **Jenkins** + CodeArts 触发（§3） | **首通** `serve_flag_e2e`；清单与特性映射 **对齐后回填 §3** |
| **④ L4** | GPU/NPU 统一入口、 nightly 缩圈 | **cron** 按 **§5.2** | **明确 nightly 跑哪些 JSON**（与 §5 表一致）；新 perf 场景 **PR 增** `tests/e2e/cases/`；可选 job 增 `core`/compare 时更新 §5 |
| **⑤ 远期 L3** | merge 后；L3 **功能 + 精度** | Jenkins / CodeArts job（§4） | 从 §4 候选挂 job；**对齐后回填 §4** |
| **⑥ L5** | 每周 / 发版前长稳 | 黄区 cron **专档**（与 L4 nightly 分开） | **维护 §6**；缺口 **对齐后回填 §6** |

**分层用例（对齐后回填）** — 与上表「增加 / 维护」列同一事项，按层查阅：

| 层级 | 用例从哪来 | 后续要做什么 |
| ---- | ---------- | ------------ |
| **L1** | §2.2.1 | 859 路径合同；可选 **core** 并进 UT |
| **L2** | §3 | Jenkins 跑通后补齐用例；缺口 **对齐后回填 §3** |
| **L3** | §4 | 同上；**对齐后回填 §4** |
| **L4** | §5 | **定 nightly 子集** + 看板；新增 JSON / 可选 core、compare |
| **L5** | §6 | **长稳与高档轮询**；总指南尚未覆盖的 **补用例** |

---

**①–③ · PR 流水线（CodeArts）**

| 改动项 | stage | 做什么 |
| ------ | ----- | ------ |
| **L1 UT（859）** | UT | 路径与 **collect = 859** → **§2.2.1**、**§8** |
| **L1 静态** | 静态 | typos、markdownlint 与 [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) 对齐（机器人 ruff 与本地 pre-commit **本来就不是同一套**，见 §2.1） |
| **L1 UT 扩 core**（可选） | UT | 增加 `tests/agentcache/core`、**859+N** → **§2.2.1** |

**不含在 L1 UT**：见 **§2.2.1** 表末两行及 `tests/e2e/`（§5）。

---

**Router patch / WASM（待评估）**

原 [`ci.yml`](../.github/workflows/ci.yml) 在改动 `agentinfer/agentrouter/patches/`、`agent_hint_affinity/` 等路径时，会跑 Router **`git apply`** 校验与 WASM **`cargo test` + `build.sh`**（**不是** 859 UT）。**Router 组件已分仓**，本仓是否仍保留上述目录与 CI 门控 **需评估**：

- **若丢弃**：L1 单轨退役 `ci.yml` 时 **不必** 在 CodeArts/Jenkins 复刻该门控。
- **若保留**：再按原路径 filter，由 CodeArts 触发 Jenkins（与 L2 同类集成方式）。

评估结论定稿后，更新本节与 §2.2 末「额外检查」表。

---

**L4 nightly**

- **统一**：GPU、NPU 都走 `pytest tests/e2e/run_benchmark.py --test-config-file <json>`（GPU 现网脚本逐步对齐仓内）。
- **缩时**：不要每夜 GPU+NPU 全 JSON（现网约 **8 h/夜**）；按 **§5.2** 跑 **1–2 个关键 JSON**。

## 2. L1（每个 PR，CPU）

L1 含静态/合规和 CPU 单元测试。pytest marker（`cpu_test`、`gpu_test` 等）仅表语义；**CI 按路径收集用例，不用 `-m`**。

### 2.1 静态与合规

| 检查 | 谁在跑 | 内容 |
| ---- | ------ | ---- |
| 静态检查 | CodeArts | 安全与代码质量扫描 |
| 禁用词扫描 | CodeArts | [community blacklist](https://gitcode.com/openJiuwen/community/blob/main/security/blacklist.md) |
| 防投毒检查 | CodeArts | 恶意文件扫描 |
| 开源合规检查 | CodeArts | SCA / 许可证 |
| ruff codecheck | CodeArts | Ruff |
| pre-commit（本地） | 贡献者本地；**非**机器人 job | Ruff check/format、typos、markdownlint-cli2（见 `.pre-commit-config.yaml`） |
| DCO / CLA | GitCode CLA + commit signoff；原 `ci.yml` `dco` job 拟废弃 | 每个 commit 含 `Signed-off-by:`；`openJiuwen-cla/yes` |
| CLA | GitHub `license/cla`；GitCode `openJiuwen-cla/yes` | 贡献者协议 |


`pre-commit` 比机器人的 ruff 项多 **typos** 和 **markdownlint**。

### 2.2 CPU UT

UT 合同 = CodeArts **859** collect（CPU、无 vLLM；路径见下表）。自测见 **§8**。

#### 2.2.1 L1 UT 路径与条数（现状）

样例 commit `8988342`（2026-10-08）：**849 passed、10 skipped**，collect **859**（skip 多为缺 Node/sandbox 等，不算失败）。`ci.yml` **824** 列仅历史对照（upstream **未跑**，拟退役）。**合入只认 CodeArts 859。**

| 范围 | 条数 | CodeArts | `ci.yml` | 备注 |
| ---- | ---- | -------- | -------- | ---- |
| `tests/agentbench`（含 `benchmark_smoke`） | 612 | ✓ | ✓ | L1 |
| `tests/scheduling` | 209 | ✓ | ✓ | L1 |
| `tests/agentcache/test_entrypoints.py` | 3 | ✓ | ✓ | L1 |
| `tests/agentcache/entrypoints/test_serve_profile.py` | 18 | ✓ | **✗** | L1；stub CLI，不真起 serve |
| `tests/tools` | 17 | ✓ | **✗** | L1 |
| **L1 UT 合计** | **859 / 824** | **859** | **824** | |
| [`tests/agentcache/core/`](../tests/agentcache/core/) | —（不计入 859，条数随 vLLM 变） | **✗** | **✗** | `cpu_test`，需 vLLM 包；**L4** 或可选并进 CodeArts UT |
| [`tests/agentcache/entrypoints/test_serve_flag_e2e.py`](../tests/agentcache/entrypoints/test_serve_flag_e2e.py) | 5 | **✗** | **✗** | `gpu_test`，真 `vllm serve`；**L2**（§3），非 `tests/e2e/` JSON |

**未进上表 L1 行的**还有 `tests/e2e/` 性能 JSON 等 → **L4/L5**（§5）。

## 3. L2（PR ready 后的带卡冒烟）

定义：关键 PR 打 ready 后，在 **CI 专用 GPU/NPU** 上跑 **§1 目标表** 中 **高优先级特性** 的 **功能 + 精度**（**合入前拦截**；**不含性能**，性能见 **§5 L4**），预算约 30 分钟；失败可设计为阻断合入（**当前方案：先打通、再定门禁**）。

**哪些特性算「高优先级」、对应哪条用例**：**待对齐**（见 §1）；对齐前下文只列 **工程首通** 与 **仓内可复用载体**，不表示已划分特性优先级。

**角色（与 L3 / L4 分工，不含具体特性结论）**

| 类型 | L2 角色 |  补充方式 |
| ---- | -------  | -------- |
| **功能** | 合入前应拦截的 **带卡功能** 路径 | 当前如 [`test_serve_flag_e2e.py`](../tests/agentcache/entrypoints/test_serve_flag_e2e.py)、[`run-baseline-smoke.sh`](../tests/agentbench/run-baseline-smoke.sh)；后续 **PR** 增用例 |
| **精度** | 合入前应拦截的 **精度** | **PR** |


**10月迭代落地**

| 项 | 说明 |
| --- | --- |
| **算力** | **蓝区 L20**（与 L4 本地黄区 nightly **分开**） |
| **调度** | **Jenkins** job |
| **工程首通** | [`test_serve_flag_e2e.py`](../tests/agentcache/entrypoints/test_serve_flag_e2e.py)（打通 Jenkins；**特性优先级待对齐**） |
| **L2 完整清单** | **对齐后回填** 下表占位行 |
| **触发** | CodeArts 条件调 Jenkins |

**用例清单**

| 路径 | 说明 | 调度 | 备注 |
| ---- | ---- | ---- | ---- |
| [`test_serve_flag_e2e.py`](../tests/agentcache/entrypoints/test_serve_flag_e2e.py) | 5 条，`gpu_test`，真 serve + HTTP | **首通**（蓝区 L20） | 工程接入，**非** 高优特性定论 |
| *（L2 用例 · 对齐后回填）* | 功能 + 精度用例 | — | **对齐后回填** |


## 4. L3（合入 main 后的带卡集成）

定义：merge 到 `main` 之后，在 **CI 专用 GPU/NPU** 上跑 **§1 目标表** 中 **低优先级特性** 的 **功能 + 精度**（**不含性能**；性能见 **§5 L4**，真实权重、时限约 1 小时）。主干失败要告警，并在下一笔合入前恢复。与 **§3 L2** 的分工 **对齐后回填**，L3 **不重复** L2 已选门禁集。

**哪些特性算「低优先级」、对应哪条用例**：**待对齐**（见 §1）。

**角色（与 L2 / L4 分工）**

| 类型 | L3 角色  | 补充方式 |
| ---- | -------  | -------- |
| **功能** | merge 后仍须跟踪的 **带卡功能** 路径  | **PR** |
| **精度** | merge 后门禁所需的 **compare / 回归** | **PR** |



| 用例 | 入口 | 说明 | 调度 |
| ---- | ---- | ---- | ---- |
| [`run-baseline-smoke.sh`](../tests/agentbench/run-baseline-smoke.sh) | shell | baseline 单点 | 未建 |



## 5. L4（每晚，开发 GPU/NPU）

### 5.1 现状：开发 GPU / NPU 每日跑 `tests/e2e/cases/`

- **已在执行**：开发 **GPU** 与 **NPU** 上**每天**都会跑 [`tests/e2e/cases/`](../tests/e2e/cases/) 下的**性能用例**（baseline / agentinfer 等 JSON）。
- **结果看板**：跑完后会把结果**同步到看板**，团队从看板查看 nightly 是否达标。
- **触发方式（双轨）**：
  - **NPU**：使用**仓库里的命令**（`pytest tests/e2e/run_benchmark.py --test-config-file …`，与 [`tests/e2e/README.md`](../tests/e2e/README.md) 一致）。
  - **GPU**：使用**开发侧提供的脚本**（不在本仓；调度、参数、模型路径以现网脚本为准，**不是**上述 pytest 入口）。
- **运行时长（现状）**：开发 **GPU + NPU** 按现网脚本/cron **每晚跑多份** [`tests/e2e/cases/`](../tests/e2e/cases/) JSON（baseline 与 agentinfer、多档并发），整包约 **8 小时/夜**，明显长于 [CI 质量看护总指南](ci-cd-quality-gate-setup-guide.md) 对 L4「**< 3 h**」的 nightly 预算；反馈慢、占机久，也不利于次日排查。
- **风险**：JSON 在仓里改 shape 后，GPU 脚本若未同步升级，会与 NPU / 文档行为不一致；看板异常时仍依赖**维护者人工排查**（见 § 5.3），issue 模板与 runbook 待补全。

**性能与其它带卡用例（仓内清单）**

| 用例 | 路径 / 入口 | 现网 nightly | L4 目标 |
| ---- | ----------- | ------------ | ------- |
| **Baseline plan-subagent** | `tests/e2e/cases/baseline/glm52_{8x4,16x8,24x12,32x16}_plan_subagent.json`（4 个） | GPU、NPU **多档常跑** | 与 agentinfer 成对对比；** nightly 关键子集** 优先 8x4，高档 **轮询 / L5** |
| **AgentInfer plan-subagent** | `tests/e2e/cases/agentinfer/glm52_{8x4,16x8,24x12,32x16}_plan_subagent.json`（4 个） | 同上 | 同上；[`tests/e2e/README.md`](../tests/e2e/README.md) 为 schema 来源 |


### 5.2 目标：用例统一、入口统一、**nightly 只跑关键性能场景**

| 统一项 | 说明 |
| ------ | ---- |
| **Case 定义** | 以仓内 JSON 为唯一来源；新增/改 shape 必须 PR 改 `tests/e2e/cases/` |
| **执行入口** | GPU、NPU nightly **均**调用 `pytest tests/e2e/run_benchmark.py --test-config-file <path>`（GPU 从开发脚本迁出或薄封装同一命令）；看板继续承接结果同步 |
| **时长策略** | **不要**每夜 GPU+NPU 全跑 8 个 JSON（8x4–32x16 × baseline/agentinfer）；**建议 L4 nightly 关键子集**，把单夜压到 **< 3 h**（相对现状 ~8 h） |


仓内共 **9** 个 JSON（`baseline/` 4 + `agentinfer/` 5，含 480x12）；现网「全跑」≈ 8 h；目标态 **L4 每夜 1–2 个关键 JSON**，其余按轮询 / L5 消化。


### 5.3 每日结果不符合预期时的排查

- **谁跟进**：先看板发现异常的同学或 nightly 负责人，**拉对应开发定位**（可按失败 JSON 场景、近期 PR 作者或模块 owner 对口）。
- **看什么**：**看板**上的性能指标是否异常。
- **怎么办**：定位后 **提 issue** 跟踪修复或记录「环境/波动 + 是否重跑」。


## 6. L5（每周，以及发版前，开发 GPU/NPU）

开发 **GPU/NPU** 可每周占用更长窗口，符合 L5 的触发条件；**不**挡 PR 合入（L1），发版前应绿。

**用例清单**

| 用例 | 入口 | 说明 | 调度 |
| ---- | ---- | ---- | ---- |
| [`glm52_480x12_plan_subagent.json`](../tests/e2e/cases/agentinfer/glm52_480x12_plan_subagent.json) | `pytest tests/e2e/run_benchmark.py --test-config-file …` | 仓内 **长稳 soak**（480 task × 12 并发）；从 L4「每晚全量」中 **拆出** | 现网多随全量跑；**目标**：每周 / 发版前 **Dedicated** |
| **高档性能矩阵（轮询）** | `agentinfer/` 与 `baseline/` 的 `16x8`、`24x12`、`32x16` JSON（共 6 个） | L4 nightly **不每晚全跑** 时，由 L5 **消化** 高档与 baseline/agentinfer **成对** 对比 | 建议 **按周轮询** 1–2 档 |
| **其他** | — | 可靠性用例、UT覆盖率等 | **仓内尚无** 对应用例；档期预留，补上后写入本表 |


## 7. 现有 marker 归层


| 现在的标记或路径 | 归层 |
| ---------------- | ---- |
| 静态检查、DCO、CLA | L1 |
| agentbench / scheduling / tools / `test_serve_profile` | L1（机器人 859） |
| `benchmark_smoke` | L1（假 runtime，不占卡） |
| `tests/agentcache/core/*`（`cpu_test`，需 vLLM 包，通常不占卡；**未进 L1 859**，与 serve 扩展同层调度） | L4 |
| `test_serve_flag_e2e.py`（`gpu_test`，serve 功能 E2E） | **L2 工程首通**（§3）；长期归层 **对齐后回填** |
| `tests/e2e/cases/*.json`（除 480x12） | **L4 性能**（nightly/看板）；**不归 L2/L3** |
| `glm52_480x12_plan_subagent.json` | L5 |
| L2 | 高优先级特性 · 功能 + 精度（§3；**对齐后回填**） |
| L3 | 低优先级特性 · 功能 + 精度（§4；**对齐后回填**） |


