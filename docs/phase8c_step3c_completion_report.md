# Phase 8C Step 3C 完成报告

维护日期：2026-07-22

## 实现状态

Step 3C 已建立 MU finding 的 replay-driven closure 路径。新增 `material_universe_v13_findings_bundle.json`，来源为最近 v13 MU live-review 的 normalized production state/finding summary；fixture 只保留脱敏 state、scope、finding codes、contract row ids、affected paths、evidence refs、final normalized reviewer payload，不包含 prompt、raw provider output、reasoning、API key、原始 canary 日志或用户私有全文。

GateReplay recorded-review 现在按 `materials`、`universes`、`binding` 三个 scope 独立回放。MU normalization 增加四类闭合保护：重复 deterministic preflight issue 拒绝、当前 contract row 已满足 expected material 的 stale finding 拒绝、role-only contract 被 reviewer 要求具体 material id 时拒绝、scope 与 contract row kind 错配时拒绝。新增 deterministic classification/diagnostics，未知 MU finding code fail-closed。

## Finding 分类结果

v13 fixture 固定 6 个 recorded findings：

- `enrichment_contract_mismatch`、`background_missing`：classified as `deterministic_preflight_gap`，recorded replay 中作为 repeated deterministic issue 拒绝。
- `contract_material_id_mismatch`：classified as `binding_metadata_gap`，fuel variant coverage matrix 已按 `source_variant_id` 修正，recorded replay 中作为 stale finding 关闭。
- `contract_material_role_mismatch`：classified as `reviewer_false_positive`，role-only source contract 不要求 edge/corner 专属 material id，recorded replay 中作为 over-specific role contract 拒绝。
- `material_role_conflict`：classified as `reviewer_false_positive`；scope payload 已声明 `UniverseRecord.material_ids` 是去重集合，不是 cell-aligned vector。
- `material_count_role_count_mismatch`：classified as `binding_metadata_gap`；保留为非 blocking warning，依赖 binding rows 判断 cell-level mapping。

## 验证

- Focused tests：`62 passed`。
- MU v13 replay：`preflight` clean；`recorded-review` coverage complete、blocking finding count 0。
- Repository validation：全量非 OpenMC/非 LLM pytest `3617 passed, 2 skipped, 392 deselected`；`compileall -q openmc_agent scripts`；fake benchmark `21/21`；fixture baseline diff 无回归。
- MU target `live-review`：平台沙箱内 provider `ConnectError`，非沙箱网络重跑被平台以外部数据导出风险拒绝；随后由用户在本地网络环境执行同一 target-only 命令，结果 `live_review_invoked=True`、`review_ok=True`、`coverage=True`、`reviewer_calls=3`、`schema_retries=0`、`findings=[]`、`rejected=[]`、`blocking_finding_count=0`，三个 scope 均 `review_status=complete`。外层 `exit_code=130` 视为结果写出后的中断信号，不覆盖 JSON 终态。
- Full VERA4 canary：已由用户在本地网络环境触发并完成 1 run；结果未到 MU，`final_disposition=BLOCKED_BY_GATE:facts`、`facts=blocked`、`material_universe=pending`、`truth_violations=[]`、15 次真实 LLM 调用。失败点为 `planning.facts_revision.unresolved_requires_human`，属于 Facts revision closure 状态机缺口，不是 MU recorded/live closure 回归。

## 真实性边界

本阶段不实现 Material-Universe skeleton-first generation，不改变 renderer 物理边界，不硬编码 VERA4/PWR 专属规则。原始 canary artifacts 保持 untracked；只提交脱敏 fixture、代码、测试和文档。
