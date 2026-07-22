# Phase 8C Step 3G 完成报告

维护日期：2026-07-23

## 实现状态

Step 3D Facts→MU canary 已越过 Facts、Materials 和 Universes，并在 MU reviewer 返回唯一 blocker：`material_universe.invalid_composition_sum_for_basis`。该材料声明 `atom_frac`，但 H1=2、O16=1 是水的化学计量比，sum=3，不是归一化 fraction。

修复将该问题前移到 deterministic path：MaterialsPatch validator 检查 `atom_frac`/`weight_frac` composition sum；materials fragment qualification 通过 schema validator 在 fragment accept 前拦截；MU preflight 通过 source validator 在 reviewer 前暴露 deterministic error。

## 验证

- 全量非 OpenMC/非 LLM 测试：`3662 passed, 2 skipped, 392 deselected`。
- `compileall`：通过。
- fake workflow benchmark：`21/21`。
- baseline regression diff：通过，`new_failures=0`。
- 覆盖场景：H1=2/O16=1 + `atom_frac` 被拒绝；partial fraction vector `<=1` 保持允许；percent-style sum `≈100` 保持允许；fragment qualification 和 MU preflight 均能 deterministic 捕获；MU finding classifier 将 reviewer code 分类为 `deterministic_preflight_gap`。
- MU target replay 验收：preflight accepted；v13 recorded-review accepted，coverage complete、reviewer_calls=3、schema_retries=0、blocking/rejected finding 均为 0；target-only live-review accepted，live_review_invoked=True、coverage complete、reviewer_calls=3、schema_retries=0、issues/findings/rejected 均为空。
- 下游推进验证：`scripts/qualify_downstream_gates_offline.py --bundle-dir tests/fixtures/gate_replay` 通过，Placement/Axial/Assembled 三个 offline fixture 的 preflight 与 recorded-review 均 accepted、blocking/rejected finding 为 0；focused downstream tests `30 passed`。

## 下一步

短期不再用完整 Facts→MU canary 定位该问题。下一步优先准备真实 campaign 到达 MU accepted 后的下游 target-only 验收：从 production checkpoint 提取 Placement/Axial/Assembled bundle，按 Placement → Axial Geometry → Assembled Plan 顺序运行 target-only live-review；三个下游 live-review 闭合后，再安排一次完整 milestone canary。
