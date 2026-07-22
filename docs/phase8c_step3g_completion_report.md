# Phase 8C Step 3G 完成报告

维护日期：2026-07-22

## 实现状态

Step 3D Facts→MU canary 已越过 Facts、Materials 和 Universes，并在 MU reviewer 返回唯一 blocker：`material_universe.invalid_composition_sum_for_basis`。该材料声明 `atom_frac`，但 H1=2、O16=1 是水的化学计量比，sum=3，不是归一化 fraction。

修复将该问题前移到 deterministic path：MaterialsPatch validator 检查 `atom_frac`/`weight_frac` composition sum；materials fragment qualification 通过 schema validator 在 fragment accept 前拦截；MU preflight 通过 source validator 在 reviewer 前暴露 deterministic error。

## 验证

- 全量非 OpenMC/非 LLM 测试：`3662 passed, 2 skipped, 392 deselected`。
- `compileall`：通过。
- fake workflow benchmark：`21/21`。
- baseline regression diff：通过，`new_failures=0`。
- 覆盖场景：H1=2/O16=1 + `atom_frac` 被拒绝；partial fraction vector `<=1` 保持允许；percent-style sum `≈100` 保持允许；fragment qualification 和 MU preflight 均能 deterministic 捕获；MU finding classifier 将 reviewer code 分类为 `deterministic_preflight_gap`。

## 下一步

重跑 Facts→MU milestone canary。验收仍为 Facts accepted、Materials complete、Universes 5/5、MU preflight 无 unexpected error、MU reviewer 完成、truth violations 0。
