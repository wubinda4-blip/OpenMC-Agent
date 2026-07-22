# Phase 8C Step 3H 完成报告

维护日期：2026-07-23

## 实现状态

真实 VERA4 MU checkpoint run 到达 Facts accepted、Materials/Universes valid，并在 MU deterministic preflight 阻塞：`material_universe.localized_insert_universe_missing`。根因是同一个 thimble-plug radial profile 同时服务 C/E 两个 localized insert requirements，但 inventory→universe requirement 映射使用单值 `profile_id → insert_requirement_id`，后写入覆盖前者；生成 universe 只带 `thimble_plug_E` metadata，旧 preflight 因而误判 `thimble_plug_C` 所需 `u_thimble_plug` 缺失。

修复将该关系改为一对多：InventoryUniverseRequirement、UniverseGenerationRequirement、UniverseManifestItem 和 fragmented Universes metadata 均保留完整 `localized_insert_requirement_ids`，同时保留旧 singular 字段兼容。MU preflight 现在优先读 metadata ID 集合；对旧 checkpoint，则从 `planning_geometry_inventory.localized_insert_profiles` 反查 `requirement_id → geometry_profile_id` 来闭合共享 profile coverage。

后续 retry 证明该 blocker 已闭合：Facts accepted、MU preflight 无 blocking issue、MU reviewer 实际调用并 accepted。顶层命令仍返回 failure 的新根因不是 MU，而是 `--stop-after-gate material_universe` 只启用 gate prefix、没有在 MU accepted 后停止，导致 executor 继续生成 `assembly_catalog` 并因 downstream schema/truncation 失败污染 MU milestone canary。本轮补齐 stop-after 语义：target gate accepted 后立即返回 `stopped_after_gate` 成功结果，checkpoint resume 也先停；graph 直接 `save_record`；harness 将目标 gate accepted run 分类为 `STOP_AFTER_GATE_PASSED:<gate>`。

## 验证

- 真实 run 终态分类：`BLOCKED_BY_GATE:material_universe`，Facts accepted，MU reviewer 未调用；blocking code 为 `material_universe.localized_insert_universe_missing`。
- 对该旧 run 的 `plan_build_state.json` 离线重跑 MU preflight：`ok=True`、blocking issues 清零；剩余 enrichment/background 均为 warning。
- Focused tests：`32 passed`。
- 全量非 OpenMC/非 LLM 测试：`3673 passed, 2 skipped, 392 deselected`。
- `compileall`：通过。
- fake workflow benchmark：`21/21`。
- baseline regression diff：通过，`new_failures=0`。
- Stop-after focused regression：`15 passed`。

## 下一步

重跑同一 MU checkpoint canary。预期命令应在 MU accepted 后直接以 `STOP_AFTER_GATE_PASSED:material_universe` 结束，不再进入 `assembly_catalog`。随后提取 Placement/Axial/Assembled production bundles 并进入 downstream target-only live-review。
