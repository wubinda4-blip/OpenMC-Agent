# Phase 8C Step 3D 完成报告

维护日期：2026-07-22

## 实现状态

Step 3D 针对 T3 full VERA4 canary 暴露的 Facts blocker 做最小闭合。该 run 停在 Facts Gate：`BLOCKED_BY_GATE:facts`，MU Gate 未到达，truth violations 为 0。核心修复是 Facts revision rereview 后的 unresolved finding 状态机：blocked metadata 和 finding registry 现在使用最新 rereview finding set，不再把已被 candidate 修复的初审旧 count finding 报为 unresolved。

Facts reviewer normalization 新增一类保守降级：当 error-severity finding 的 message 明确同时声明 `coverage confirmation` 和 `not an error` 时，转为 warning 并保留 `classification_override` provenance；其它 invalid/error finding 仍 fail-closed。

## 回归靶子

新增最小脱敏 fixture `phase8c_step3d_facts_stale_closure.json`，记录本次 T3 的稳定靶子：旧 count findings（Pyrex 80、guide tube 216、thimble plug 112、instrument tube 9）、revision candidate values、最新 rereview finding codes。fixture 不包含 prompt、raw provider output、reasoning、API key 或原始 canary 日志。

## 验证

- Focused Facts/GateReplay tests：`58 passed`。
- 覆盖场景：confirmation-not-error finding 降级为 warning；blocked closure metadata 使用最新 rereview finding；fixture 敏感字段扫描；现有 Facts revision multi-round closure 不回归。
- 全量验证：非 OpenMC/非 LLM pytest `3620 passed, 2 skipped, 392 deselected`；`compileall -q openmc_agent scripts`；fake benchmark `21/21`；fixture baseline diff 无回归。

## 下一步

先跑 Facts target-only `live-review`。Facts accepted 后，再重跑 `--stop-after-gate material_universe` T3 canary；若到达 MU，则复用 Step 3C recorded/live closure 判定。
