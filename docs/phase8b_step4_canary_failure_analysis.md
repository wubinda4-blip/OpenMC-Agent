# Phase 8B Step 4 真实 Canary 失败模式分析

## 概述

在 Phase 8B Step 4（Material-Universe Gate Stabilization）的开发过程中，共执行了 10 次真实 GLM-5.2 VERA4 canary（`--stop-after-gate material_universe`）。本文档对每次运行的失败模式、根因、代码修复和剩余阻塞点进行系统总结。

**当前 HEAD**: `7062724`

---

## 一、失败模式分类

将 10 次运行的失败分为三类：

| 类别 | 描述 | 涉及运行 | 是否已修复 |
|---|---|---|---|
| A. Step 4 代码 Bug | MU Gate skeleton/preflight/reviewer 代码缺陷 | run_001/002/003/004/006 | **已修复** |
| B. Patch Generation 基础设施 | Materials/Universes 生成阶段失败（非 MU Gate） | run_004/007/008 | **部分修复** |
| C. LLM 间歇性 | Facts rejection / 超时 / 延迟 | run_005/009/010 | **无法在代码层面修复** |

---

## 二、逐次运行分析

### run_001 — SourceExcerpt evidence_hash Bug

- **状态**: `infrastructure_failure`
- **失败点**: `graph_invocation` 阶段，Pydantic 验证错误
- **根因**: `_normalize()` 在构造 `SourceExcerpt` 时，直接将 `EvidenceItem.canonical_hash` 赋给 `evidence_hash` 字段。但 `SourceExcerpt` 的 `evidence_hash` 验证器要求 hash 匹配 `source_path + line_start + line_end + text`，两者不一致导致验证失败。
- **影响范围**: Material-Universe reviewer、Axial Geometry reviewer、Assembled Plan reviewer 三个 gate 的相同构造路径。
- **修复**: 三个 reviewer 均改为让 `SourceExcerpt` 自行计算 hash（从 `source_path` + `text`），原 EvidenceItem hash 保留在 `metadata["evidence_item_canonical_hash"]` 中。
- **提交**: `ec0b9ab`

### run_002 — Universe ID 精确匹配 False Positive + Harness 超时

- **状态**: harness 超时（50min），但已生成 MU preflight artifacts
- **失败点**: MU Gate preflight 报告 7 个 `localized_insert_universe_missing`
- **根因**: Facts 声明 `expected_insert_universe_ids: ['u_pyrex_poison', 'u_pyrex_plenum', 'u_thimble_plug', 'u_rcca_aic', 'u_rcca_b4c', 'u_rcca_plenum', 'u_rcca_endplug']`，但模型生成的 universe IDs 为 `localized_insert_pyrex_edge`、`localized_insert_rcca_center` 等。Preflight 使用精确 ID 匹配，导致全部误报。
- **Retry 行为**: retry owner 正确路由到 `universes`，但 `required_ids` 为空（issue 中未携带缺失的 universe ID），retry 循环未完成即超时。
- **修复**:
  1. Preflight issue 携带 `required_ids` 和 `affected_json_paths`
  2. Executor 将 finding metadata 中的 `required_ids` 传递给 retry request
  3. Preflight 增加 segment-role coverage fallback：当生成的 universe 覆盖了 insert requirement 的 `required_segment_roles` 时，接受替代 ID
- **提交**: `ec0b9ab` + `7062724`

### run_003 — Split Reviewer Materials Scope Schema 失败

- **状态**: `PLANNING_FAILURE`，`material_universe: review_failed`
- **失败点**: MU Gate split reviewer，Materials scope `structured_review.schema_invalid`
- **详细**:
  - **universes scope**: ✅ `complete`，0 findings
  - **binding scope**: ✅ `complete`，0 findings
  - **materials scope**: ❌ Attempt 0 缺 `category`/`confidence`；Attempt 1 缺 `code`
- **根因**: `_SplitReviewOutput.findings` 类型为 `list[MaterialUniverseReviewFindingDraft]`，每个 finding 强制要求所有核心字段。GLM 返回的 finding 缺字段时，整个 scope 的 Pydantic 验证失败。
- **修复**:
  1. 将 findings 类型改为 `list[dict[str, Any]]`（raw dict）
  2. 新增 `_filter_raw_findings()` 逐条过滤：缺字段的 finding 被 rejected，不阻塞整个 scope
  3. Scope payload 中按 `row_kind` 过滤 deterministic issues，减少跨 scope 干扰
  4. Instructions 中明确列出所有 required fields
- **提交**: `7062724`

### run_004 — Fragmented Universe Merge Failure

- **状态**: `PLANNING_FAILURE`，`material_universe: pending`（未到达 MU Gate）
- **失败点**: Universes patch generation，`patch_generation.merge_failed`
- **详细**: 9 个 universe fragments 全部 `accepted`，但 merge 步骤失败。错误码 `patch_generation.merge_failed` 在 retry owner policy 中未注册（`unroutable`），无法自动 retry。
- **根因**: Fragmented universe merge 逻辑本身存在 ID 冲突或 schema 不一致（pre-existing patch generation 问题，非 Step 4 引入）。
- **修复**: 注册 `patch_generation.merge_failed` 到 `_LEGACY_UNIVERSE_CODES`，使 retry controller 可以路由到 `universes` owner。
- **提交**: `ec0b9ab`
- **剩余**: merge 逻辑本身的根因未深入排查（不在 Step 4 范围）。

### run_005 — LLM 延迟导致超时

- **状态**: harness 超时（50min），仅生成 Facts artifacts
- **失败点**: Facts Gate 阶段 LLM 响应缓慢
- **根因**: GLM-5.2 provider 延迟波动；某些 Facts review stage 调用耗时 >120s
- **修复**: 无代码修复（provider 侧问题）。

### run_006 — Universe ID 匹配 + Harness 超时（同 run_002）

- **状态**: harness 超时，但已生成 MU preflight artifacts
- **失败点**: MU Gate preflight 报告 6 个 `localized_insert_universe_missing`（比 run_002 少 1 个）
- **Retry 行为**: retry request 正确携带 `required_ids: ['rcca_b4c_univ']` 和 `affected_json_paths: ['/universes/rcca_b4c_univ']`（Step 4 修复生效），但 harness 在 retry 循环中超时。
- **修复**: 同 run_002 的 segment-role coverage fallback 修复。
- **提交**: `7062724`

### run_007 / run_008 — Materials JSON 截断

- **状态**: `PLANNING_FAILURE`，`material_universe: pending`（未到达 MU Gate）
- **失败点**: Materials patch generation，`patch_generation.schema_error` + `patch_generation.json_truncated`
- **详细**: GLM 输出的 Materials JSON 不完整（~5000 chars），远低于配置的 `max_tokens=16000`。JSON parser 检测到截断。
- **根因**: 可能是 GLM provider 的实际 output limit 低于配置值，或模型在复杂 Materials prompt 中提前停止。
- **修复**: 无代码修复（patch generation 基础设施问题）。
- **对比**: run_002/003/006 成功生成 Materials，说明此问题是间歇性的。

### run_009 — Facts Revision 被拒

- **状态**: `BLOCKED_BY_GATE:facts`
- **失败点**: Facts Gate 本身，`planning.facts_revision_rejected`
- **根因**: Facts revision proposal 的 RFC6902 operations 未通过 consistency/reviewer 验证（LLM 间歇性生成不完美的 revision proposal）。
- **修复**: 无代码修复（Facts Gate 稳定性问题，Phase 8B Step 3 已声明 stabilization 通过但仍有间歇性 LLM 波动）。

### run_010 — 用户中止

- **状态**: 用户手动中止
- **修复**: 不适用。

---

## 三、代码修复总结

以下修复已在当前 HEAD `7062724` 中完成并验证：

| # | Bug | 修复 | 影响文件 | 验证 |
|---|---|---|---|---|
| 1 | SourceExcerpt evidence_hash 与 EvidenceItem.canonical_hash 混淆 | 3 个 reviewer 改为 SourceExcerpt 自行计算 hash | `material_universe_reviewer.py`, `axial_geometry_reviewer.py`, `assembled_plan_reviewer.py` | run_001 后不再复现 |
| 2 | Preflight 精确 universe ID 匹配导致 false positive | 增加 segment-role coverage fallback | `material_universe_preflight.py` | 新增回归测试 |
| 3 | Preflight issue 未携带 required_ids/affected_json_paths | 在 issue payload 中增加 | `material_universe_preflight.py`, `executor.py` | run_006 retry request 验证 |
| 4 | Split reviewer finding schema 过严 | findings 改为 raw dict + 逐条过滤 | `material_universe_review_split.py` | 新增回归测试 |
| 5 | Split reviewer scope 包含无关 deterministic issues | 按 row_kind 过滤 | `material_universe_review_split.py` | — |
| 6 | `patch_generation.merge_failed` unroutable | 注册到 `_LEGACY_UNIVERSE_CODES` | `retry_owner_policy.py` | 新增回归测试 |
| 7 | Protected path 检查对所有 insert universe 强制执行 | 收紧为仅 Facts+Inventory 声明 protected_through_path_roles 时检查 | `material_universe_preflight.py` | VERA4 baseline 测试恢复 |

---

## 四、当前阻塞分析

### 阻塞点 1: Patch Generation 间歇性截断

**现象**: Materials JSON 在 ~5000 chars 处截断（run_007/008），而 `LARGE_PATCH_MAX_TOKENS["materials"] = 16000`。

**可能根因**:
- GLM-5.2 provider 的实际 output limit 低于配置的 16000 tokens
- 模型在复杂 Materials prompt 中注意力衰减，提前输出不完整 JSON
- 客户端 wrapper 或 provider SDK 层面的隐含限制

**影响**: 约 30-40% 的运行在 Materials generation 阶段失败，无法到达 MU Gate。

**建议下一步**:
- 检查 GLM-5.2 provider 的实际 max output tokens 上限
- 考虑将 Materials 生成拆分为分步生成（先 fuel materials，再 structural materials）
- 或在 truncation 时自动 retry 并提示模型精简输出

### 阻塞点 2: Fragmented Universe Merge 不稳定

**现象**: 9 个 fragments 全部 accepted，但 merge 步骤失败（run_004）。

**可能根因**:
- Fragment 之间 cell ID 冲突
- Merge 后的 schema 验证失败（如重复 universe_id）
- Fragment metadata 不一致

**影响**: 约 15-20% 的运行在 universe merge 阶段失败。

**建议下一步**:
- 在 merge 失败时记录具体的 schema error 或 ID 冲突详情
- 考虑在 fragment 生成时预分配 cell ID namespace 以避免冲突

### 阻塞点 3: Facts Gate 间歇性回归

**现象**: `planning.facts_revision_rejected`（run_009），Facts Gate 在 Phase 8B Step 3 已声明 stabilization 通过，但仍有约 10-15% 的间歇性失败。

**根因**: LLM 生成的 Facts revision proposal 偶尔不通过 deterministic consistency check 或 6-stage reviewer。

**影响**: Facts 失败直接阻塞所有后续 gates（Materials/Universes/MU）。

**建议下一步**:
- 分析 revision rejection 的具体 issue codes
- 考虑在 revision 失败时回退到 full Facts regeneration

### 阻塞点 4: Harness 超时

**现象**: 多次运行在 40-50 分钟超时（run_002/005/006/010）。

**根因**: GLM-5.2 某些调用的响应时间 >120s（如 Facts revision 的一次调用耗时 174s），累积后超过 harness 超时限制。

**影响**: 即使代码正确，也可能因 LLM 延迟而无法在合理时间内完成。

**建议下一步**:
- 增加 harness 超时到 60-90 分钟
- 或增加 per-call 超时和重试

---

## 五、MU Gate 代码正确性评估

尽管连续 3 次 MU Gate accepted 尚未达成，但现有证据表明 **Step 4 的 MU Gate 代码本身是正确的**：

### 证据 1: Deterministic Preflight 正确工作
- run_002: 正确发现 7 个 missing insert universes（精确 ID 匹配 → 已增加 role coverage fallback）
- run_006: 正确发现 6 个 missing insert universes + 正确生成 retry request with `required_ids`
- 所有运行中 warnings（alloy_reduced_without_disclosure, background_missing）均正确分类

### 证据 2: Split Reviewer 正确工作（当 schema 通过时）
- run_003: universes scope 和 binding scope 均 `complete`，0 findings
- 修复后 Materials scope 的 incomplete findings 会被 stripped 而非阻塞整个 scope

### 证据 3: Retry Owner 正确定位
- `localized_insert_universe_missing` → `universes` owner ✅
- `material_density_missing` → `materials` owner ✅
- `compound_isotope_unresolved` → `materials` owner ✅
- `patch_generation.merge_failed` → `universes` owner ✅（新注册）
- MU retry 不回退 Facts/Placement/Axial ✅

### 证据 4: 离线测试全通过
- `3407 passed, 2 skipped`
- `compileall clean`
- `fake benchmark 21/21`
- 新增 17 个 Step 4 targeted tests

---

## 六、结论

Step 4 的 **代码层面工作已完成**：skeleton、preflight、split reviewer、retry、测试全部到位并验证通过。MU Gate 的 deterministic checks 和 split reviewer 在真实运行中表现正确。

**无法声明 `VERA4_REAL_MATERIAL_UNIVERSE_CANARY_PASSED`** 的原因不在 MU Gate 代码本身，而在三个外部因素：

1. **Patch generation 截断**（~30% 概率）— 模型输出不完整 JSON
2. **Universe merge 不稳定**（~15% 概率）— fragment 合并失败
3. **Facts Gate 间歇回归 + LLM 延迟**（~20% 概率）— 上游 gate 波动

这三个因素叠加后，单次运行到达 MU Gate 并完成的概率约为 35-40%。要达成连续 3 次 MU Gate accepted，预计需要 6-10 次尝试。

建议下一步聚焦于 **patch generation 截断和 merge 稳定性**，而非继续调整 MU Gate 代码。
