# Phase 8C Step 3I 完成报告

维护日期：2026-07-24

## 实现状态

原计划在 MU accepted 后提取 Placement/Axial/Assembled production bundles；实际执行时发现当前 checkpoint 只有 MU accepted boundary，没有下游 gate boundary，extractor 正确 fail-closed。继续审计 MU 后的 `assembly_catalog` failure，发现它只是下游症状：真实 Universes patch 中 `u_fuel_region1_2.11wt` 声明 `metadata.fuel_variant_id=region1_2.11wt`，但 fuel cells 引用的 material 是 `source_variant_id=region2_2.619wt`。

修复将该问题前移到 MU deterministic preflight：binding view 现在读取 universe metadata 的 fuel variant；preflight 通过 cell.material_id 反查 material.source_variant_id，并在 fuel universe 声明 variant 与 fuel material variant 不一致时产出 `material_universe.fuel_variant_material_mismatch`，owner route 为 `universes`。同时修复 patch schema 对 LLM structured `assumptions` 的通用 normalization，避免可恢复 audit-note 形状触发大 patch retry 和截断。

## 验证

- Step 3H 真实 `plan_build_state.json` 离线重跑 MU preflight：`ok=False`，blocking 仅 1 个 `material_universe.fuel_variant_material_mismatch`，定位到 `u_fuel_region1_2.11wt/fuel_inner`，expected `region1_2.11wt`、actual `region2_2.619wt`。
- 真实 `assembly_catalog_attempt_1_raw.txt` 在新 schema 下可 parse，`validate_patch` 通过；此前 schema failure 的 structured assumptions 已规范化为字符串。
- Focused tests：`39 passed`（MU preflight、MU issue policy、retry registry parity、MU replay classifier、gate replay fail-closed、assembly catalog schema）。
- Repository validation：非 OpenMC/非 LLM 全量 pytest `3680 passed, 2 skipped, 392 deselected`；`compileall` 通过；fake workflow benchmark `21/21`；baseline regression diff 无回归。
- Step 3I v2 canary 到达 Facts accepted 后阻塞于 MU preflight：`u_fuel_region2_2.619/fuel_inner` 声明 Region2 universe 但引用 Region1 material。离线修复让 deterministic blocker 跳过 reviewer 但继续生成 Universes owner retry request，并修正 failed owner 与 campaign aggregate status。Focused tests：`48 passed`；全量非 OpenMC/非 LLM pytest `3684 passed, 2 skipped, 392 deselected`，`compileall`、fake benchmark `21/21`、baseline diff 均通过。
- Step 3I v3 canary 证明 retry request 已生成，但 MU stage 仍为 blocked；executor resume/final path 未强制 Material-Universe accepted，继续进入 assembly 并暴露 `localized_insert.*` 下游症状。修复后真实 v3 `plan_build_state.json` 离线 replay 停在 `planning.material_universe_gate_not_accepted`，不再进入 assembly。Focused tests：`39 passed`；全量非 OpenMC/非 LLM pytest `3686 passed, 2 skipped, 392 deselected`，`compileall`、fake benchmark `21/21`、baseline diff 均通过。
- Step 3I v3 离线审计进一步确认：Universes retry request 的 owner/target 正确，但 fragment prompt 对 fuel role 暴露了所有 fuel materials，导致 retry 后两个 fuel universes 都混用 Region1/Region2 material。修复为按 manifest fuel variant 过滤 role binding，并在 prompt/repair prompt/fragment qualification 中拒绝 variant mismatch；v3 混用 fragments 现在以 `qualification.fuel_variant_material_mismatch` fail。Focused tests：`63 passed`；全量非 OpenMC/非 LLM pytest `3689 passed, 2 skipped, 392 deselected`，`compileall`、fake benchmark `21/21`、baseline diff 均通过。
- Step 3I v5 未到 MU，Facts revision closure 阻塞于 `facts.control_state_contract_missing`。根因是真实 candidate 使用空字符串表示 source-declared base operating state，deterministic check 将 `""` 视为 missing。修复后空 control state canonicalize 为 `base`，v5 final candidate 离线 consistency issue codes 为空。Focused tests：`39 passed`；全量非 OpenMC/非 LLM pytest `3692 passed, 2 skipped, 392 deselected`，`compileall`、fake benchmark `21/21`、baseline diff 均通过。

## 下一步

下一次不直接重跑完整链路；优先 resume v2 checkpoint，复用 Facts accepted，并验证 MU deterministic blocker 是否产出 Universes retry request。只有 Universes owner retry/regeneration 离线闭合后，才继续 target MU canary；MU accepted 后再提取下游 Placement/Axial/Assembled bundles。
