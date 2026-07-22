# Phase 8C Step 3A Canary Analysis Report

维护日期：2026-07-22

## 结论摘要

本报告生成时，重配预算后的 canary 仍在后台运行，尚未形成最终 `run_result.json`、gate summary 或 truthfulness artifact。因此当前不能宣称 Facts Gate 已通过，也不能把当前运行视为 MU Gate 失败或成功。

上一轮完整智谱 canary 已明确停在 Facts Gate；本轮的目标是提高 Facts closure budget 并保留 Materials → Universes → MU 路径。当前中间 artifact 表明本轮 Facts patch 已比上一轮更完整，但最后一次 completeness review 仍保留两个 warning，并要求 `revise_current_patch`。

## 1. Bug 分析

### 已确认或高度可信的问题

1. **中断时缺少 campaign finalization**

   前台 canary 达到 7200 秒后被工具终止，run 目录只留下 workflow 中间文件，没有 `run_result.json`、`five_gate_status.json`、`llm_call_manifest.json` 或 truthfulness summary。后台 retry 目前也只生成中间 planning artifact，尚未写入终态文件。

   这意味着当前 checkpoint 实现仍不能覆盖 provider/进程/墙钟中断：checkpoint 写入挂在 run artifact 收尾路径，不能在每个 accepted gate 或每个可恢复 action 完成时实时落盘。若进程在 Facts 或 MU reviewer 中断，恢复无法可靠知道哪些调用已经完成。

2. **Facts candidate acceptance 与 Facts Gate acceptance 语义容易混淆**

   `facts_revision_evaluation_000.json` 中 `accepted=true` 表示候选 patch 通过该轮 revision evaluation，不表示 Facts Gate accepted；同一运行的 `facts_review_decision.json` 仍是 `revise_current_patch`，最后 completeness review 仍是 `complete_with_gaps`。artifact 命名和字段需要明确区分 `candidate_accepted` 与 `gate_accepted`。

3. **Facts 计数 scope 语义仍不稳定**

   当前 retry candidate 同时使用：

   - `expected_spacer_grid_count = 8`，表示每 assembly；
   - `scoped_expected_counts[core_total] = 72`，表示 3×3 总量。

   这可以是合理的双层语义，但字段名没有明确 scope。此前 canary 曾把顶层字段写成 `72`，说明 LLM/repair 之间存在 schema interpretation drift。`expected_pin_count=null` 与 scoped fuel pin core total `2376` 也表现出同类问题。

4. **Facts provenance closure 尚未完成**

   当前最后一次 review 仍指出 `/assumptions` 缺少：

   - radial outer boundary = reflective；
   - axial top/bottom outer boundary = vacuum；
   - end-plug modeling convention。

   `boundary_scope` 已有 `radial_reflective_axial_vacuum`，所以这更像 provenance/assumption coverage gap，而不是边界值本身错误。但在 Facts Gate contract 下，它仍必须被修复或显式归类为 nonblocking warning。

### 尚未能确认的问题

- Materials fragment generation 是否达到预期数量。
- Universes fragment qualification、metadata binding、shared-role/dedup 是否通过。
- v13 修复后的 MU preflight 是否为 0 errors。
- MU reviewer 是否真正被调用。
- Placement/Axial/Assembled 后续流程是否存在回归。

当前这些都没有可靠的终态 evidence，不能从 skeleton 文件或 Facts 中间文件推断。

## 2. 最近提交是否生效

### `4c13f80` 及其 MU binding 修复

这些提交仍然有价值，但本次 canary 尚未执行到可以验证它们的路径。它们针对的是：

- universe metadata stamping；
- supporting/shared material roles；
- localized-insert profile fallback；
- dedup binding remap；
- MU deterministic preflight 3 → 0 error 的目标修复。

由于本次仍处于 Facts 阶段，不能说这些修复失效，也不能说它们已经被真实 canary 证实。

### `a4aa742`

该提交的 timeout 分流和 checkpoint contract 的确定性单元测试已通过，但真实恢复闭环尚未被本次 canary 证明。当前运行没有实时 checkpoint artifact，反而暴露出 checkpoint 写入时机不足的问题：accepted gate/action checkpoint 应在运行过程中持久化，而不是只在最终 artifact writer 中生成。

### 当前未提交的预算 CLI 改动

本次运行使用了新增的 reviewer/repair/additional-call 参数，已通过 focused tests 和 compileall，但尚未通过完整测试套件，也尚未证明它能让 Facts 必然通过。它只是扩大 deterministic closure 的允许预算，不应被表述为跳过 Facts 或保证接受。

## 3. 下一步建议

1. **先等待当前后台 retry 完成**

   当前进程仍在运行，不能并行启动同一 campaign。完成后优先读取：

   - `run_result.json`；
   - `five_gate_status.json`；
   - `five_gate_hashes.json`；
   - `llm_call_manifest.json`；
   - `truthfulness_evidence.json`；
   - `workflow/incremental/plan_closed_loop/` 下的 MU preflight/reviewer artifact。

2. **如果 Facts 仍未通过，优先修复 Facts contract ambiguity，而不是继续无限加预算**

   应确定性明确 `expected_*` 顶层字段的 scope，或废弃含义不明确的顶层字段，统一使用 `scoped_expected_counts`。同时把 boundary/provenance 和 end-plug convention 作为明确的 Facts field/assumption contract。

3. **修复 checkpoint 写入时机**

   每个 accepted gate、每个已完成 Facts action、每个 coverage early-stop 都应立即原子写入 campaign checkpoint。checkpoint 至少需要保存：action status、payload hash、evidence/ledger hash、inventory hash、policy hash、billed call count 和 provider timeout telemetry。

4. **增加中断恢复集成测试**

   测试应模拟：

   - Facts provider timeout 后恢复，只重跑未完成 action；
   - accepted Facts 不重复调用 LLM；
   - MU preflight 前中断后恢复，不重复 Materials/Universes；
   - checkpoint 任一 hash 漂移时，从受影响 gate fail-closed；
   - 进程中断后仍能生成 `CAMPAIGN_INTERRUPTED` 终态摘要。

5. **只有 MU reviewer 真正执行后，才评价 v13 binding 修复**

   需要看到：Facts accepted、Materials/Universes expected count、MU preflight `0 errors`、MU reviewer call count > 0、truth violations `0`。若 MU 被 deterministic contract finding 阻塞，应保留 finding 原文和 hash，不得把它归因于 provider timeout。

## 4. 当前运行证据

当前 retry 目录：

```text
data/runs/phase8c_step3a_vera4_canary_zhipu_budget60_retry/
```

已观察到的中间事实：

- scope 为 `multi_assembly_core`；
- scope、fuel variant、assembly structure review 已通过；
- instrument tube requirement 与 aggregate counts 已写入 candidate；
- spacer grid 同时出现 per-assembly `8` 与 core-total `72`；
- 最后 completeness review 为 `complete_with_gaps`；
- 最后 deterministic action 为 `revise_current_patch`；
- 当前没有正式 Facts accepted 或 MU Gate 结果。

## 最终判定

本次运行目前是 **IN_PROGRESS / partial artifact**，不是成功，也不是 MU failure。已经暴露了 Facts scope/provenance 语义问题和中断 artifact finalization 风险；最近的 MU binding 提交尚未被真实 MU 路径验证。下一决策点是等待本轮终态，然后根据 Facts 是否通过决定是继续观察 MU，还是先修复 Facts contract 与 checkpoint 持久化。
