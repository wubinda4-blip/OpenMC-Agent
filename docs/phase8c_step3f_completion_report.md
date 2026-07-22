# Phase 8C Step 3F 五 Gate 离线 Campaign 恢复资格报告

维护日期：2026-07-22

## 实现状态

新增版本化脱敏 `CampaignRecoveryScenario` / `CampaignRecoveryQualification`，复用生产 `CampaignCheckpointStore`、`GateReplayBundle`、`run_gate_replay`、`downstream_resume` 和 `DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH`。runner 不调用真实 LLM、provider 或 OpenMC，不读取未跟踪 canary 日志。

`scripts/qualify_campaign_recovery_offline.py` 顺序运行 clean campaign 与故障矩阵，输出 scenario fingerprint、input/policy hash、accepted boundary 复用/失效集合、gate/recovery call count、终态与稳定 issue code。

## 覆盖范围

- Clean campaign：Facts → Materials/Universes → MU → Placement → Axial → Assembled 全部 recorded-review accepted；恢复只复用有效 accepted snapshots，不新增 reviewer 调用。
- 故障注入：input/policy drift、checkpoint/bundle hash corruption、敏感字段、缺失上游、Facts timeout、schema failure、blocking finding。
- 依赖传播：Facts/Materials/Universes/Placement/Axial patch change 使用生产 dependency graph 计算 transitive closure，并验证 accepted boundary 后缀失效、前缀保持复用；同时调用生产 non-recursive downstream resume seam 的 fake runner。

## 验证

- Step 3F focused recovery tests：`13 passed`。
- 离线 matrix：`15` 个场景，clean accepted，其余受控 blocked；所有结果不含 prompt/reasoning/raw provider response/secrets。
- 未运行真实 LLM canary；独立 Step 3D Facts canary 不修改、不重启、不纳入本报告。

## 边界与下一步

该阶段只证明恢复、checkpoint 完整性与失效传播语义，不构成真实 MU 或下游 provider acceptance。下一阶段可基于 accepted offline fixture 做 renderer/XML/geometry smoke 资格；真实 provider 仍需 MU accepted 后按 Placement → Axial Geometry → Assembled target-only live-review 顺序执行。
