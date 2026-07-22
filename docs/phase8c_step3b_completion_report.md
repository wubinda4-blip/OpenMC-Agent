# Phase 8C Step 3B 修正完成报告

维护日期：2026-07-22

## 实现状态

Step 3B 的 accepted-boundary checkpoint 与 GateReplay 实现已按父审查意见修正。边界顺序固定为 `gate:facts`、`patch:materials`、`patch:universes`、`gate:material_universe`。snapshot 写入和 Facts action 写入不吞异常；resume 只在存在完整、state-hash 正确且 fingerprint 全部匹配的 snapshot 时恢复，否则对漂移、损坏和 schema 不匹配 fail-closed。

`real_campaign_harness` 现在向 production graph 注入 `CampaignCheckpointStore`、boundary callback 和 Facts action callback；resume 使用 requirement/input/policy/git/structured-output hashes 恢复 `accepted_plan_build_state`，最终 audit writer 保留。Gate replay 的 PRELIGHT、RECORDED_REVIEW 和 LIVE_REVIEW 均使用 production state/preflight/review normalization 路径，输出仅保留 sanitized normalized result。

## 验证

- 全量非 OpenMC/非 LLM pytest `3611 passed, 2 skipped, 392 deselected`，`compileall`、fake workflow benchmark `21/21` 与 fixture baseline regression diff 均通过。workflow regression baseline 已补为 tracked fixture：`tests/fixtures/workflow_baseline/evaluation_report.json`。
- Facts 与 MU 各完成一次只调用目标 reviewer 的 `live-review`（最长 1800 s）。Facts 返回一条 source-gap warning；MU 完成 3 个 review scope 并产生可复现 finding 终态。

## 真实性边界

本修正验证 deterministic preflight、review normalization、checkpoint crash/corruption handling、action-level reuse、resume hydration 和 production callback wiring。MU fixture finding 尚未经离线分类/闭合，故未启动完整 VERA4 canary，也不声明 full live canary、全 gate acceptance 或 OpenMC 运行成功。
