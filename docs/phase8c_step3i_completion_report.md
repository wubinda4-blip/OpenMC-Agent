# Phase 8C Step 3I 完成报告

维护日期：2026-07-23

## 实现状态

原计划在 MU accepted 后提取 Placement/Axial/Assembled production bundles；实际执行时发现当前 checkpoint 只有 MU accepted boundary，没有下游 gate boundary，extractor 正确 fail-closed。继续审计 MU 后的 `assembly_catalog` failure，发现它只是下游症状：真实 Universes patch 中 `u_fuel_region1_2.11wt` 声明 `metadata.fuel_variant_id=region1_2.11wt`，但 fuel cells 引用的 material 是 `source_variant_id=region2_2.619wt`。

修复将该问题前移到 MU deterministic preflight：binding view 现在读取 universe metadata 的 fuel variant；preflight 通过 cell.material_id 反查 material.source_variant_id，并在 fuel universe 声明 variant 与 fuel material variant 不一致时产出 `material_universe.fuel_variant_material_mismatch`，owner route 为 `universes`。同时修复 patch schema 对 LLM structured `assumptions` 的通用 normalization，避免可恢复 audit-note 形状触发大 patch retry 和截断。

## 验证

- Step 3H 真实 `plan_build_state.json` 离线重跑 MU preflight：`ok=False`，blocking 仅 1 个 `material_universe.fuel_variant_material_mismatch`，定位到 `u_fuel_region1_2.11wt/fuel_inner`，expected `region1_2.11wt`、actual `region2_2.619wt`。
- 真实 `assembly_catalog_attempt_1_raw.txt` 在新 schema 下可 parse，`validate_patch` 通过；此前 schema failure 的 structured assumptions 已规范化为字符串。
- Focused tests：`39 passed`（MU preflight、MU issue policy、retry registry parity、MU replay classifier、gate replay fail-closed、assembly catalog schema）。
- Repository validation：非 OpenMC/非 LLM 全量 pytest `3680 passed, 2 skipped, 392 deselected`；`compileall` 通过；fake workflow benchmark `21/21`；baseline regression diff 无回归。

## 下一步

重跑 MU stop-after canary。预期新的 MU preflight 会在 reviewer 前阻塞旧错误，并由 Universes owner retry/regeneration 修复；只有 MU 再次 accepted 后，才重新提取下游 Placement/Axial/Assembled bundles 并进入 target-only live-review。
