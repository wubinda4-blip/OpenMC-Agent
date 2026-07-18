# OpenMC-Agent 技术报告与进度总览

维护日期：2026-07-12

维护方式：每完成一个重要工程 Step 后更新本报告的"当前状态""验证结果""风险/边界""下一步建议"和"维护记录"。**维护记录使用精炼风格**：每条 2–4 行（日期 + 主题 + 核心改动 + 测试数），不写冗长根因/实现细节（那些在代码与 git history 里）。

### 2026-07-17

- **P2 Plan Closed-Loop Facts Gate Phase 1B**：Facts Gate contract 升级至 0.3；新增 `review_failed`、完整 EvidencePack+schema retry、JSON-object recovery、structured-mode telemetry、review/validation/budget ledger；Facts revision 改为 clone→re-review→atomic commit，human ambiguity 改为 namespaced LangGraph interrupt/resume 并以 typed confirmed fact 驱动 facts regeneration。历史 VERA4 critic 失败已复盘为非 JSON 输出与无上下文 retry，尚未重跑真实资格 canary。验证：Facts/closed-loop/graph targeted tests。
- **2026-07-17 · P2 Placement Gate Phase 2**：closed-loop contract 升级至 0.4；提取通用 structured review I/O，并新增单/多组件 binding view、contract matrix、静态预检、Placement Critic、issue ownership、受限多 patch clone revision 与通用 plan-gate human routing。验证：placement/facts targeted tests；真实 LLM/代理资格未声明，dependency retry 与 Final Plan Gate 仍未实现。

- **P2 Plan Closed-Loop Phase 0**：新增 reactor-neutral typed protocol、SHA-256 semantic fingerprints、deterministic gate/action/state-machine policy，以及持久化到 `PlanBuildState` 的独立预算和 no-progress ledger。advisory 仅写 JSON artifacts；off 不进入框架；controlled 明确 fail-closed 为未实现。
  验证：新增 closed-loop targeted tests（11 passed）；未新增 Critic/Repair/Supervisor LLM 调用或 human interrupt。边界：Facts Gate 及后续 executable repair loop 尚未实现。

## 1. 项目定位

OpenMC-Agent 的目标是把自然语言反应堆建模需求转成可审查、可校验、尽可能可运行的 OpenMC Python 模型。系统的核心安全边界是：

- LLM 只生成结构化 `SimulationPlan`，不直接写最终运行代码。
- 本地 Pydantic schema、validator、renderer 和 OpenMC 工具链负责校验、渲染、导出和 smoke test。
- 缺失材料密度、composition、核数据库路径、benchmark 常数、真实装载图等事实缺口必须保留 human confirmation，不能由 RAG/GraphRAG 自动补全。

当前仓库已经从"结构化建模 agent"扩展成"带诊断闭环、检索编排、GraphRAG、知识注入、incremental 分层 plan 生成、few-shot 双轨、trace/evaluation 和 benchmark 基础设施"的中大型工程。

## 2. 代码规模

截至本报告维护时：

- `openmc_agent/` Python 文件：62 个，约 35,200 行。
- `tests/` Python 测试文件：53 个，约 23,200 行。
- 文档：本报告 + 10 个活跃策略文档。
- 最近全量测试：`1105 passed, 3 skipped in 86.56s`；OpenMC gate：`356 passed, 2 skipped`。环境分层后当前 base 环境（无 OpenMC）通过 `test-no-openmc` 与 `test-all` collection/skip 验证，OpenMC runtime 测试由 `test-openmc` gate。

## 2a. 重大里程碑（2026-07-09）

**VERA3 3A 和 3B 通过 incremental plan builder 端到端成功运行**（真实 LLM deepseek:deepseek-chat）：

- 两个变体全部 7 个 patch 由 LLM 直接生成（不依赖 reference fixture）。
- pin_map 24 个特殊坐标 → assembler 确定性展开 17×17=289 full lattice。
- CellLayerPatch 几何 → assembler 自动构建 ZCylinder surfaces + regions（同心圆柱 pin cell）。
- 边界条件从实际几何推导（radial=reflective, axial=vacuum）。
- 元素符号（He/Zr/Fe/Ni）自动路由到 add_element。
- coord_overlap 确定性修复（3B 同坐标保留高优先级组）。
- OpenMC smoke test 通过。

| 指标 | 3A | 3B |
|---|---|---|
| keff | ~1.149（偏高，合金近似） | **0.979 ± 0.004** |
| Leakage | 0% | 低 |
| Surfaces | 6 | 10 |
| Regions | 9 | 15 |
| Pin counts | 264F + 24GT + 1IT | base: 264F + 24GT + 1IT; finite Pyrex/plug loading requires component-profile support |

## 3. 架构总览

核心流程：

```text
User requirement
  -> _receive_requirement            # 检测 incremental 触发条件，写 planning_mode_decision
  -> retrieve_openmc_docs            # 本地内省 OpenMC API
  -> select_few_shots                # 抽象提纲 + gold case（结构特征打分）
  -> generate_plan                   # dispatcher:
       monolithic  : LLM 一次输出 SimulationPlan（简单 case）
       incremental : plan_builder 7-patch 分层生成 + 确定性组装（复杂 3D case，默认）
  -> validate_plan / capability
  -> retrieval orchestrator          # grep / graph / GraphRAG planner / GraphRAG / RAG / ranker
  -> reflect_plan / ask_expert / auto_repair
  -> renderer                        # PinCell / RectAssembly / Core / TRISO / Skeleton
  -> export_xml / plot / smoke test
  -> trace / records / evaluation
```

主要模块职责：

| 模块 | 职责 |
| --- | --- |
| `schemas.py` | Pydantic IR：`SimulationPlan`、materials、geometry、lattice、axial overlay、capability、issues。 |
| `graph.py` | LangGraph workflow：plan 生成（monolithic/incremental dispatcher）、validate、capability、repair、ask_expert、render、tools、trace。 |
| `plan_builder/` | incremental 7-patch 分层 plan 生成：mode 判断、PlanBuildState、patch schemas、validators、deterministic assembler、LLM patch generator、per-patch prompts、executor + retry router、LLM adapter、reference-backed patches、evaluation harness。 |
| `few_shots.py` / `few_shot_cases.py` | few-shot 选取（抽象提纲 + gold case，结构特征打分）+ gold case loader（slim IR / patch exemplar / 结构特征，堆型无关）。 |
| `assembly3d_guard.py` | 3D assembly 需求被压扁为 2D 的 requirement/plan 级 guard。 |
| `axial_overlay.py` | Level 1 `homogenized_open_region` overlay 渲染决策（堆型无关，保 pin/tube through-path）。 |
| `source_settings.py` / `geometry_bounds.py` | source/bounds 一致性校验（source z 绑定活性燃料、xy 绑定 full footprint、plot 覆盖）。 |
| `verification.py` | verification digest（不变量检查表 + pin/axial/overlay/material/bounds）+ 3D voxel plot。 |
| `validator.py` / `lattice_validation.py` | 结构校验、pin map / lattice 诊断、错误码生成。 |
| `error_catalog.py` | 稳定 issue taxonomy、route hints、repair/retrieval/human confirmation hints。 |
| `auto_repair.py` | deterministic patch，优先于 LLM reflect。 |
| `grep_search.py` | 受控 grep 检索。 |
| `knowledge_graph.py` / `knowledge_graph_registry.py` | hand-written graph registry + `graph_lookup`。 |
| `rag_search.py` | 本地 lexical RAG（无外部服务）。 |
| `graphrag_retriever.py` / `graphrag_query_planner.py` | Graph-guided RAG + issue-intent query planner。 |
| `knowledge_ingestion.py` / `knowledge_runtime.py` | 本地 docs/examples ingestion + knowledge graph 运行时加载。 |
| `retrieval_orchestrator.py` / `evidence_ranker.py` | 统一检索编排 + evidence scoring/dedup/budget。 |
| `workflow_trace.py` / `evaluation.py` / `benchmark_runner.py` | trace model/recorder/export + evaluation case/result/metrics + benchmark/ablation runner。 |
| `renderers/` | PinCell、RectAssembly、Core、TRISO、Skeleton 渲染器和 registry。 |
| `tools.py` | OpenMC export_xml、plot、smoke test 子进程工具。 |

## 4. 已完成能力

### 4.1 诊断闭环

- runtime / export_xml / hex lattice 错误码化；`ValidationIssue` 结构化；`ToolResult.issues` 接入 workflow。
- `reflect_plan` / `ask_expert` 按 issue route 处理；hex lattice 保持 skeleton，不伪装 runnable。
- workflow 有稳定 issue code、schema path、concept id、route hint、retrieval/human confirmation 标记。

### 4.2 检索层

- grep（精确定位代码/测试/示例/文档）、graph（schema/concept/API/doc/error/repair 关系）、RAG（本地 chunk + lexical scoring，无外部服务）。
- GraphRAG Retriever（issues → graph expansion → RAG request）+ Query Planner（issue intent → start nodes/depth/filters/queries）+ Evidence Ranker（merge/dedup/score/budget）。
- 默认链：`issues → grep → graph → GraphRAG planner → GraphRAG → RAG → merge → ranker → reflect_plan prompt`。manual review / fact gap 也触发文档检索，但 human confirmation 保留。

### 4.3 Knowledge Ingestion + Runtime Loader

- `Input/knowledge_sources.json` manifest；扫描 `docs/`、`examples/`、`openmc_docs/`、`openmc_examples/`、`Input/`；规则式 annotation；chunk → graph nodes/edges；JSON/JSONL 输出。
- **Runtime Loader**（2026-07-07）：orchestrator 在 GraphRAG stage 通过 `RetrievalPolicy.knowledge_graph_path` 或 `OPENMC_AGENT_KNOWLEDGE_DIR` 加载持久化 graph 作 `extra_nodes/extra_edges`；失败只 warning 不中断。详见 `docs/knowledge_runtime_strategy.md`。

### 4.4 Trace / Evaluation / Benchmark

- `TraceRecorder`/`WorkflowTrace`/`TraceEvent`；validation/retrieval/auto-repair/reflect/ask_expert/render 摘要事件。
- `EvaluationCase`/`EvaluationResult`/`EvaluationMetrics`；benchmark/ablation runner（fake runner、JSON/JSONL/MD 输出）。

### 4.5 Renderer 与能力边界

当前 renderer：PinCell、RectAssembly、Core、TRISO、Skeleton fallback。

明确未完成或受限：

- HexAssemblyRenderer 未实现。
- depletion / burnup、pebble_bed renderer 未实现。
- Level 1 spacer-grid overlay 是均质近似（详见 4.8），**非** volume-fraction calibrated；不建模真实格条/混流翼/套筒。
- fact gap 仍必须走 ask_expert / human confirmation。

### 4.6 Incremental Plan Builder（Phase 0–7D）

复杂 3D assembly（多 variant / spacer grid / special pin map / 大 lattice / JSON parse 失败历史）从 monolithic 25K JSON 改为 **7-patch 分层生成**（`facts → materials → universes → pin_map → axial_layers → axial_overlays → settings`）+ 确定性组装：

- 每层独立生成/校验/重试；`PinMapPatch` 只输出特殊坐标（不展开 289 格）；`settings` 确定性。
- graph dispatcher：`should_use_incremental_planning` 触发时路由到 incremental executor，简单 2D 仍走 monolithic；`use_incremental_executor=True` 默认。
- **Input-driven structural patches by default**：复杂模型默认按未见模型处理，由 incremental patch LLM 分层生成 structural patches；reference JSON 仅作为显式策略使用（policy：`off` 默认 / `prefer_reference_for_structural` / `reference_only_for_structural` / `fallback_after_llm_failure`）。
- **Resumable**：`save/load_plan_build_state`，已 valid patch 跳过。
- **Plan-level targeted repair**：assembled `SimulationPlan` 通过 schema 但被 validator 拦截时，graph 将 issue code / schema_path 映射回 patch root，executor 只失效该 patch 及下游依赖后定点重做；无法定位时才退回 fresh regeneration。
- **真实 LLM 加固**：禁止不安全 monolithic fallback、strict patch-output contract（禁 full plan / full lattice 输出）、structured output mode（`json_schema`）、per-patch attempt artifact 可见。

### 4.7 Few-shot 双轨增强（2026-07-08）

- 4 个结构命名的 gold case（`data/few_shot_cases/`：`pin_cell_basic` / `assembly_2d_lattice` / `assembly_3d_with_spacer_grids` / `quarter_core_with_reflector`），全部 **anonymize 掉堆型标识**（VERA/C5G7/CASL → `[reference]`/`EXAMPLE`），数值为 illustrative。
- **Incremental 路径**：`build_patch_prompt` 注入 patch 形态参考段（受 2400 字符/层预算）。
- **Monolithic 路径**：`_augmented_plan_requirement` 注入 slim IR + digest。
- 选择按**结构特征**（kind/lattice_size/axial_overlay/reflector/quarter）打分，绝不按堆型名——通用性自检测试守护。
- 边界：仅 3D assembly case 有 patch few-shot（有现成 fixture）；其余 case 只有 monolithic slim_ir。

### 4.8 3D assembly guard + Level 1 overlay（Step 1–6）

- `assembly3d_guard.py`：requirement 级 + plan 级检测 3D 需求被压扁为 2D（四个 `assembly3d.*` issue code）。
- `AxialOverlaySpec` IR + Level 1 `homogenized_open_region` overlay renderer（保留 pin/tube through-path；导向管多 open cell 保守复用）。
- skeleton overlay 自动提升（数据齐全时从 skeleton 升 Level 1）。
- source/bounds 一致性：source z 绑定活性燃料、xy 绑定 full footprint、plot origin 重定心（修 quarter-plot bug）。
- verification digest + 3D voxel plot（堆型无关结构化验证产物）。


### P0 Evaluation Backbone: case schema, workflow runner, and trace metrics

P0 的目标是建立可量化的真实 workflow 回归基准，使每次 RAG / GraphRAG / incremental / renderer 改动都能定位指标变化和失败 stage。当前实现范围是 plan-only lightweight workflow case runner：默认不跑 OpenMC export、plot、smoke test 或 transport run，只收集 planning / validation / capability / artifact 诊断 trace。

新增指标覆盖 `plan_schema_success`、`incremental_patch_success`、issue precision/recall、retrieval trigger、artifact completeness 与 planning mode accuracy。后续 P0-D/E 再正式接入 benchmark runner 运行入口、CLI、markdown/json report、ablation 与 real LLM opt-in benchmark。

### P0-NEW-1：受控材料 composition policy

**目的**：把 Zircaloy-4 / SS-304 / Inconel-718 从纯元素近似（pure Zr / Fe / Ni）升级到 nominal 合金成分（含 Sn/Cr/Ni/Nb/Mo/...），为 keff 对比建立可量化 baseline。

**为什么纯元素近似不安全**：把合金简化成单一基体元素会丢失真实的吸收体（Sn、Cr、Ni、Nb、Mo），导致 keff 系统性偏高。这会让"能跑"和"跑得准"无法区分，使 keff 对比失去诊断价值。

**实现**：

- 新增 `openmc_agent/material_library.py`：`AlloyComposition` registry + alias resolver。覆盖 `zircaloy4` / `ss304` / `inconel718` 三个 canonical id，每个成分 sum=1.0，`source_note` 明确声明是 nominal engineering approximation（不是 VERA 官方 spec），可整体替换。
- 新增 `openmc_agent/material_policy.py`：`MaterialCompositionPolicy` 枚举（`preserve_plan` / `apply_alloy_library` / `strict_confirmed_only`），默认 `apply_alloy_library`。Policy 仅在 (a) material id/name canonicalize 到已知合金，且 (b) 当前 composition 是该合金的纯元素近似（单元素或空）时才替换；fuel / water / helium / pyrex / unknown alloy 全部保留原样，不 block。
- assembler 接入 policy：`_assemble_materials` 现在返回 `(materials, issues, MaterialCompositionReport)`，每次替换写一条 `materials.alloy_library_applied` info issue；assembled plan 同时产出 `material_composition_report.json`（写入 `incremental/`），记录每个材料的 alloy_id / elements / policy。
- graph + executor 透传 `material_policy` 参数（默认 apply_alloy_library）；workflow case runner 的 `_artifact_keys` 把 `material_composition_report` 视为合法 artifact key。
- 新增 `scripts/compare_material_policies.py`：dry-run / OpenMC 两种模式，输出 `comparison_report.json`（含 `preserve_plan` keff、`apply_alloy_library` keff、`delta_pcm`）。

**安全边界**：composition 来自公开 handbook midpoint，**不是** benchmark-specific 常数；density 仍由 plan/patch 提供；fuel/water/pyrex 不替换。smoke run 不是 benchmark agreement。

## 5. 当前 Retrieval/GraphRAG 状态

### Query Planner

`plan_graphrag_query(...)` 把 issues 分类为 `schema_repair` / `runtime_diagnosis` / `export_xml_repair` / `lattice_map_repair` / `renderer_capability` / `documentation_lookup` / `fact_gap_review` / `benchmark_interpretation` / `unknown`，按优先级输出 `GraphRagQueryIntent` / `GraphExpansionPolicy` / start nodes / preferred queries / required filters / avoided queries / planned paths。

### Evidence Ranker

`rank_and_select_evidence(...)`：dedup same locator/doc_chunk_id/near-duplicate；grep exact > graph relationship > GraphRAG > plain RAG；按 issue/schema/concept/API/graph path/ingested node 加分；fact gap unsafe evidence 降分；控制每类数量和 prompt 总字符数。

### Prompt 输出

默认有 ranking 结果时：`[GraphRAG Query Plan] [Graph Context] [Ranked Evidence] [Evidence Safety Constraints]`。


### Environment validation and OpenMC test gating

Base Python 环境允许不安装 OpenMC；测试分层为 `test-no-openmc`（pure Python / no runtime）、`test-openmc`（需要 OpenMC Python package/runtime）和 `test-all`（完整环境）。OpenMC 相关测试统一用 `openmc` marker 与 `pytest.importorskip("openmc")` gate，缺失 OpenMC 时应 skip 而非 collection error。

新增 `scripts/check_environment.py` 作为环境自检入口：默认只报告 Project/OpenMC/OPENMC_CROSS_SECTIONS/Conda/Micromamba 状态且 OpenMC 缺失仍 exit 0；`--require-openmc` 才在缺失 OpenMC 时返回非 0。

## 6. 验证状态

```bash
conda run -n openmc-env python -m pytest -q
# 841 passed, 3 skipped in 46.74s
```

覆盖：schemas / llm / graph / renderers / executor / validator / assembly3d_guard / axial_overlay / source_settings / geometry_bounds / verification / plan_builder（mode / state / patches / validators / assembler / patch_generator / patch_prompts / executor / retry_router / llm_adapter / reference_patches / evaluation）/ few_shots / few_shot_cases。3 skip 为真实 LLM / integration gated（CI 不跑）。

## 7. 当前已知边界和风险

### 7.1 工作区风险

工作区存在未纳入工程提交的用户脏文件：`Input/VERA1_problem.md`–`Input/VERA5_problem.md`、`Input/CASL-U-2012-0131-004.pdf`。这些是用户维护的输入资料，提交工程代码时应排除。

### 7.2 技术风险

- Incremental 已是复杂 3D 默认路径；VERA3 3B 和未来复杂模型默认都按未见模型走 input-driven structural patch synthesis，reference-only 仅用于显式 gold/reference 回归。真实 LLM 生成 `pin_map` / `axial_layers` / `axial_overlays` 的稳定性仍需持续评估（真实 LLM 测试 opt-in，CI 不跑）。
- Level 1 overlay 是均质近似，非 volume-fraction calibrated；不建模真实格条/混流翼。
- **VERA3 component-profile gap**：canonical contract 已确认燃料端塞/气腔是 fuel-pin internal geometry，Pyrex/套管塞是有限轴向 guide-tube inserts。P0-D5 已统一 validator/renderer structural issue source、提前在 validate_plan 阶段拦截 component-profile material slab、新增 shoulder_gap role、实现确定性 multi-patch repair bundle；真实 VERA3 3A smoke 不再因 shoulder gap 进入 reflect/retry。
- 材料/合金 composition 仍多为 approximate（Zircaloy-4 / SS-304 / Inconel-718 简化为纯元素 + warning）。
- patch few-shot 仅 3D assembly case 有；其余 case 的 incremental 路径用泛型 `_PATCH_RULES`。
- Query planner / evidence ranker 是 heuristic，未真实 benchmark 权重校准。
- Benchmark runner 还没推进到真实 workflow case runner；无 persistent trace store / dashboard。

### 7.3 安全边界

RAG / GraphRAG / ingested docs / ranked evidence / few-shot 都只能作为上下文：不能自动确认 nuclear data path、材料密度/composition、benchmark constants、真实 loading map。few-shot 数值为 illustrative reference，不是事实确认来源。

## 8. 下一步建议（更新于 2026-07-09）

VERA3 3A/3B 端到端成功后，重心从"能不能跑通"转向"跑得准不准"：

1. **P0-NEW keff 精度提升**（最高优先级）：
   - 3B keff=0.979（接近临界，非常好），3A keff≈1.149（偏高 ~15%）。
   - 主要偏差来源：合金近似（纯 Zr/Fe/Ni 去除了吸收截面，导致 keff 偏高）。
   - 下一步：引入受控材料 composition library（Zircaloy-4 Sn/Fe/Cr, SS-304 Cr/Ni, Inconel-718 Cr/Fe/Nb/Mo），替代纯元素近似。这不违反安全边界（composition 来自公开核数据手册，不是 benchmark-specific 常数）。
   - 同时考虑：更高粒子数（当前 5 batches），轴向反射层是否需要额外处理。

2. **边界条件验证**（已加入 TODO P1-5）：
   - FactsPatch 增加显式边界字段，validator 增加边界合理性检查。
   - 渲染后自检 XML boundary_type 与 plan 对照。

3. **真实 evaluation case runner**：从 fake trace 推进到 lightweight workflow，评估 retrieval trigger rate / fact gap preservation / skeleton-runnable 分类 / issue code precision/recall。

4. **Volume-fraction calibrated overlay**：grid 体积/质量标定，提升 spacer grid 物理保真度。

5. **Full VERA3 keff benchmark acceptance**：用更高粒子数跑完整 criticality 计算，与 benchmark 参考值对标。
5. **patch few-shot 补齐**：从 IR 反推 VERA2A / C5G7 / pin_cell 的 patch few-shot。
6. **真实 LLM incremental 稳定性**：多模型 ablation。
7. **VERA3 component-profile production IR**：在不枚举 264 个 fuel 坐标的前提下，扩展 component axial profile 与 per-layer composable lattice loading；renderer 必须从 base guide-tube through-path 派生有限 Pyrex/thimble insert，且不让 nozzle homogenization 与详细 lattice 重叠。

## 9. 维护记录

> 精炼风格：每条 2–4 行（日期 + 主题 + 核心改动 + 测试数）。详细根因/实现见代码与 git history。

### 2026-07-16 (MULTI-ASSEMBLY-DETECTION)

- **多组件堆芯特征检测缺失修复**：VERA4（3×3 九组件）跑完 7 个 patch 后在 assemble 阶段报 `assembly.missing_patch`（缺 `assembly_catalog`/`core_layout`）。根因：`should_use_incremental_planning`（`plan_builder/mode.py`）只有 axial/spacer/special-pin/variants/large-lattice 五类特征，**没有 multi-assembly 检测**，导致 `feature_summary.multi_assembly_core` 永远为空 → task order 走单组件路径（含 pin_map、无 assembly_catalog/core_layout），而 facts patch 声明 `model_scope=multi_assembly_core` → assembler 要这俩 patch → 失败。新增 `_detect_multi_assembly_core`（堆型无关关键词 + "N assemblies"/"九个…组件" 计数正则）+ `TRIGGER_FEATURE_MULTI_ASSEMBLY`，VERA4 现在走多组件路径 `[facts, materials, universes, assembly_catalog, axial_layers, axial_overlays, core_layout, settings]`（去掉顶层 pin_map）。单组件/pincell/VVER 单组件负例不误触。非-openmc `1938 passed`，benchmark `21/21`。

### 2026-07-16 (PATCH-NULL-COLLECTION-COERCION)

- **patch schema 容忍 LLM 写的 null 集合字段**：官方 `deepseek-chat` 越过 facts/materials/universes/pin_map 后卡在 `axial_layers` 的 `schema_error`——每层 `loading_ids: null`，但 pydantic `list[str] = Field(default_factory=list)` 只在字段**缺失**时用默认、显式 `null` 会被拒。在 `_PatchBase` 加 `model_validator(mode="before")`，对所有 list/dict 类型字段把显式 `null` 归一为 `[]`/`{}`（通用、堆型无关，避免逐字段打地鼠；与 `schemas.py:708` 运行期模型既有做法一致）。attempt-1 的 `role` 枚举不匹配是单次问题、retry 已修正，不放宽枚举。非-openmc `1935 passed`，benchmark `21/21`。

### 2026-07-16 (JSON-EXTRACT-COT-RECOVERY)

- **从推理模型 CoT 中恢复 JSON patch**：`ds:deepseek-v4-flash` 无视 `reasoning_effort=low`，仍先吐 ~16K 字符 CoT 再给 JSON，旧贪婪正则 `\{[\s\S]*\}` 因 CoT 里含散落 `{` 而抓不到合法 JSON（facts 阶段 `json_parse_error`）。新增 `_scan_json_objects`（在每个 `{` 处 `json.JSONDecoder().raw_decode`）+ `_pick_best_json_object`（取最后一个 `patch_type` 匹配的完整对象），从 CoT 后正确回收 patch；无 `patch_type` 匹配则回退旧贪婪路径、不误收 CoT 示例。同时把诊断 raw 切片 5000→50000（`executor` patch_attempt_artifacts），便于看清推理模型完整输出。非-openmc `1932 passed`，benchmark `21/21`。

### 2026-07-16 (DS-REASONING-TOKENBUDGET)

- **SenseNova `ds:` 推理模型输出截断修复**：`ds:deepseek-v4-flash` 默认开思考模式，CoT 经 `reasoning_content` 吃光输出 token 预算导致 JSON 写一半即断（facts 阶段 `json_parse_error`/`full_plan_output_forbidden`）。最终落地两处：(1) `DSChatClient.adjust_payload` 注入 `reasoning_effort`（默认 `low`，`SENSENOVA_REASONING_EFFORT` 可覆盖）；(2) 新增 `_looks_truncated` 括号平衡启发，截断时报独立码 `patch_generation.json_truncated`（带"降 reasoning_effort/显式调大 max_tokens"提示）而非笼统 `json_parse_error`。
- **回退 max_tokens 自动 cap（回归修复）**：曾把 `PATCH_MAX_TOKENS` 接进 `executor→generate_patch`，但官方 `deepseek-chat` 的 universes patch 实测 ~15237 字符（≈6000 token），4500 的 cap 直接把它截断 → 回归。改为**不主动设 max_tokens**，用各 provider 默认（DeepSeek ~8192，比任何安全统一 cap 都大）；`PATCH_MAX_TOKENS` 保留为参考预算表（universes 标注 6500），可显式传给 `generate_patch`。`generate_patch` 仍保留 `max_tokens` 形参供按需使用。非-openmc `1930 passed`，benchmark `21/21`。

### 2026-07-16 (PINMAP-COUNT-SCOPE-FIX)

- **pin_map 计数 scope 误报修复（multi-assembly core_total vs per-assembly）**：多组件堆芯（VERA4，9 组件）下 pin_map 校验器拿 facts 的 legacy `expected_*_count`（core_total：2376/216/9/80/112）直接比对单组件 pin_map 实际值（264/24/1/20/28），全部误报 `patch.pin_map.count_mismatch`。新增 `scoped_counts.resolve_expected_counts_for_pin_map`：从 `assembly_type` scoped 条目按角色归一到 per-assembly 叠合 scope（跨型号相同取公共值、型号专属取和），`executor` facts 分支在多组件时用它覆盖 `expected_counts`。非均匀 core 且无 assembly_type 细分的 role 不强制（避免误报）。非-openmc `1928 passed`，benchmark `21/21`；真实 VERA4 pin_map 端到端校验 0 issues。

### 2026-07-16 (PINMAP-FULL-LATTICE-FIX)

- **pin_map 全格阵误报修复（结构化判定）**：`patch_generator._detect_full_lattice` 由原始文本启发式（`len(raw)>3000` 或括号总数 `>80`）改为解析 JSON 后按*单个*坐标列表最大长度相对 `lattice_size` 格数判定（≥50% 才算近满格 dump），新增 `_pin_map_largest_coord_list` 助手。修复 VERA4 多插入件 pin_map（24 guide tube + pyrex/thimble/RCCA 合计 ~97 坐标、raw>3000 字符）被误判为全格阵并导致 plan 阶段中止的 false positive；禁用全格阵字段名（`universe_pattern` 等）仍由 `validate_patch_contract` 兜底，raw 50K backstop 仅作 DoS 防护。非-openmc `1924 passed`，benchmark `21/21`；真实 VERA4 raw 复跑 `generate_patch` ok=True。

### 2026-07-15

### 2026-07-16 (LOGGING-MIGRATION)

- **CLI Logging Migration: Python logging + default-visible progress**：迁移运行时进度消息（`[node:...]`/`[llm] ...`/`[normalize] ...`）从手动 `print(file=sys.stderr)` + 布尔开关到标准 `logging` 模块；新增 `logging_setup.py`（`configure_logging(level)` + `OPENMC_AGENT_LOG_LEVEL` env var），`graph.py` `_progress()` 改用 `_logger.info()`（95 个调用点签名不变），`llm.py` `_llm_log`/`_normalize_log`/`_StderrStatus._enabled`/heartbeat 改用 `_logger.isEnabledFor(INFO)` 门控，`set_llm_progress()` 保留为 backward-compat wrapper；CLI 入口添加 `--log-level`/`--quiet`（`run_model.py` 默认 INFO、benchmark 默认 WARNING），Makefile 添加 `LOG_LEVEL` 变量；conftest.py autouse fixture 设 WARNING 静默测试，`test_inspect.py` `[node:]` 断言迁移到 `caplog`。非-openmc `1923 passed`，OpenMC `388 passed`，benchmark `21/21`。

### 2026-07-16 (GRID-ACCEPTANCE-CLOSURE)

- **P2-FULLCORE-2D-A-GRID-ACCEPTANCE-CLOSURE: Strict Grid Geometry Gate, False-Positive Rejection, Material Probes**：新增通用 `grid_geometry_validation.py`（10-point validator: decorated universe 非空 → lattice 引用 → IR 合并 → frame cell → grid material → region → surface → material 可达性 → grid-on/off digest 差异 → dangling refs），fail-closed 传播到 `PlanAssemblyResult.ok`；`build_grid_geometry_reachability_report()` 层次遍历（axial layer → core lattice → assembly → pin lattice → decorated universe → frame cell → region → material）；VERA4 acceptance 扩展 Level F（12 grid checks: 8 bands, 72 instances, 2 end/6 middle, decorated universes, lattice refs, frame cells/regions, material catalog, assembly gap, validator, reachability）；XML integrity gate 检查 model.py + materials.xml + geometry.xml 中 grid objects 完整性；structured material probes（frame/inner-mod/pin/gap/grid-off position probes）；reactor-neutral synthetic fixture（2×2 core, 3×3 pin lattice, 3 grid bands）；5 negative false-positive fixtures（A-E）；7 new test files（78 tests）。非-openmc `1923 passed`，OpenMC `388 passed`，benchmark `21/21`，VERA4 acceptance `46/46`（含 12 grid checks），grid-on/off digest 不同（9a86... vs 1736...），smoke keff=**1.27150±0.00346** zero lost。**P2_FULLCORE_GRID_GEOMETRY_INJECTED**。**P2_FULLCORE_GRID_REACHABILITY_VALIDATED**。**P2_FULLCORE_GRID_FALSE_POSITIVE_GATE_READY**。**VERA4_DETERMINISTIC_GRID_CLOSURE_PASSED**。

### 2026-07-16 (GRID-CLOSURE)

- **P2-FULLCORE-2D-A-GRID-CLOSURE: Physical Spacer-Grid Universe Injection**：关闭 grid geometry 最后一公里：materializer 主循环在 base path state → localized inserts 之后调用 `_make_grid_decorated_universe()`，对 grid-active segment 中每个 universe ID 生成 grid-decorated variant（square_frame + bounded background），替换 derived lattice pattern 中的 universe ID 引用，grid band z-boundaries 加入 global breakpoints 使 segment 在 grid 边界正确分割；assembler `_assemble_universes()` 处理 grid-decorated UniverseSpecPatch，background region 排除 frame area（`+cyl ~ ( frame_expr )`），surface ID 使用 `lo/hi` 替代 `+/-` 避免 region expression parser 冲突，region expression parser 修复 `~` 前缺少 `&` 问题。VERA4 结果：22 grid-decorated universes（覆盖 fuel/​guide_tube/​inst_tube/​pyrex/​thimble/​rcca 全部 pin-state），4 unique grid geometries（Inconel end + Zircaloy middle），176 frame surfaces，86 universes / 207 cells / 67 lattices / 275 surfaces / 156 regions，model.py 806KB → 5 XML → geometry loads → transport smoke keff=**1.2715±0.00346** → zero lost。cross-process identity **10/10**。非-openmc `1845 passed`，OpenMC `388 passed`，benchmark `21/21`。**P2_FULLCORE_GRID_GEOMETRY_INJECTED**。**VERA4_DETERMINISTIC_GRID_CLOSURE_PASSED**。

### 2026-07-16 (HARDENING)

- **P2-FULLCORE-2D-A-HARDENING: Physical Fuel-Path States, Grid Geometry, Host-Path Validation, Deterministic Identity**：关闭四个已知缺口：(1) Base fuel-path axial-state materialization——新增 `BasePathAxialProfilesPatch`/`BasePathStateBindingPatchItem`，materializer 根据 `segment.base_role` 切换燃料路径 universe（lower_shoulder→water_pin, lower_endplug→fuel_endplug, upper_plenum→fuel_plenum），R1/R2 燃料差异在 active_fuel 保持，guide/instrument tube 在所有状态中连续，materialization order: base state → inserts → grid，keff 从 1.275 降至 1.249（物理正确：endplug/plenum 不再含 UO2）；(2) Exact spacer-grid frame geometry——`inner_side = sqrt(pitch² - A_cell)`，`frame_thickness = (pitch - inner_side)/2`，mass back-calculation 验证 8 bands 全部 < 1e-6 relative error，assembler 支持 `square_frame`/`box` region_kind（8 XPlane/YPlane surfaces + boolean expression），`CellLayerPatch` 扩展 `outer_side_cm`/`inner_side_cm`；(3) Host-path equivalence validation——新增 `host_path_validation.py` 模块，`validate_replacement_host_equivalence()` 检查 replacement universe 保留 host guide-tube wall（outermost wall layer by r_max），VERA4 Pyrex/thimble/RCCA 全部通过；(4) Cross-process deterministic identity certification——新增 `scripts/certify_fullcore_deterministic_identity.py`，PYTHONHASHSEED=1 vs 98765 跨进程 plan digest / object IDs / counts 全部一致（10/10 checks），SHA-256 content hash 不依赖 Python `hash()`，`_compute_pin_state_hash` 纳入 base_role/bpath_ids。Fail-closed 加固：missing grid density/mass 报告 error 而非默认值。非-openmc `1835 passed`，OpenMC `388 passed`，benchmark `21/21`，VERA4 acceptance `38/38`（keff=1.24919±0.00737），cross-process identity `10/10`。**P2_FULLCORE_BASE_PATH_STATE_MATERIALIZATION_READY**。**P2_FULLCORE_HOST_PATH_PRESERVATION_VALIDATED**。**P2_FULLCORE_DETERMINISTIC_IDENTITY_CERTIFIED**。

### 2026-07-16

- **P2-FULLCORE-2D-A 补完：Spacer Grid 物理化、Canonical State Hash、Structured Issues**：materializer 重写完成三项增强：(1) Spacer grid assembly-instance materialization——`AssemblyGridState`/`GridFrameDerivationReport` typed results，mass conservation derivation（`A_cell = grid_mass / (density × 289 × grid_height)`，frame thickness = `A_cell / (4 × pitch)`），grid state 计入 pin state hash，8 bands × 9 assemblies = 72 instances tracking；(2) Canonical state hashing——derived pin lattice/universe/cell ID 使用 SHA-256 content hash（`assembly_lattice__<type>__<hash16>`），不依赖 `seg_counter`；core lattice dedup by content hash（相同 core pattern 复用同一 lattice），VERA4 从 19 lattices 降至 16（3 个重复 core 状态被合并）；`state_reuse_report` 返回 pin/core reuse ratio；(3) Structured fail-closed issues——`MaterializationIssue` typed dataclass 替代 `dict[str,str]`，`ConcreteAxialStateResult.has_errors` property，assembler 传播 materialization issues 到 `PlanAssemblyResult`；validation：`base_lattice_missing`、`whole_plane_fill_missing`、`whole_plane_ref_missing`、`coordinate_out_of_bounds`。非-openmc `1803 passed`，OpenMC `388 passed`，benchmark `21/21`，VERA4 acceptance `38/38`（keff=1.27484±0.00741）。

- **P2-FULLCORE-2D-A VERA4 Deterministic Base-Case Fidelity Closure**：新增 axial segment `fill_mode` 契约（`detailed_core` / `whole_plane_material` / `whole_plane_universe` / `void`），`compile_global_axial_segments` 接受 `base_axial_layers` 并将每个 segment 映射到唯一基础 axial layer、自动分类 fill mode、domain clipping（breakpoints 裁剪到 domain 范围）；materializer 重构支持 whole-plane segment（material/universe/void fill 不生成 derived pin/core lattices）；坐标规范化（materializer 不再假设 insert coordinates 已经是 0-based，通过 `coordinate_convention.index_base` 自动转换）；0.0 truthiness bug 修复（`z_min_cm=0.0` 不再被当作 None）；完整 VERA4 fixture（12 base axial layers: -55→463.937 cm，含 lower/upper moderator buffer、core plate、nozzle、shoulder gap、fuel endplug、active fuel、plenum）；nozzle/core-plate 混合材料（SS304+coolant vol% mixture）；完整 fuel-path universes（active_r1/r2、endplug、plenum、water_pin、guide_tube with wall、instrument_tube with wall）；exact Pyrex coordinates（20 per edge assembly，1-based source convention）；thimble plugs（24 per corner + 4 per edge = 112 total）；formal RCCA multi-segment profile（AIC/B4C/plenum/endplug via `LocalizedInsertProfilesPatch`，anchor=257.9，control_state="base"）；assembly-scoped spacer grid overlays（8 bands，Inconel+Zircaloy）；新增 `campaign_eval/vera4_base_acceptance.py`（5-level acceptance: facts/plan/geometry/XML/runtime，38 checks）；新增 `scripts/vera4_base_fidelity.py`（full pipeline: assemble → render → XML → geometry debug → smoke → acceptance）；VERA4 deterministic base-case acceptance **38/38** checks passed（model.py 142KB → 5 XML → geometry loads → transport smoke keff=1.27484±0.00741 → zero lost）。非-openmc `1783 passed`，OpenMC `388 passed`，benchmark `21/21`。**VERA4_DETERMINISTIC_BASE_CASE_ACCEPTANCE_PASSED**。**VERA4_DETERMINISTIC_BASE_CASE_SMOKE_PASSED**。

- **P2-FULLCORE-2C-B Concrete Insert State Materialization and VERA4 Physical Closure**：新增 `axial_state_materializer.py`（per-segment derived pin lattices with insert overrides、per-segment wrapper universes/cells、per-segment core lattices、`CoreSpec.axial_layers` with segment-specific lattice fills）；production assembler 消费 `LocalizedInsertProfilesPatch`（resolved profiles → global axial segments → concrete materialization → axial layers）；VERA4 fixture 增加真实物理结构（pyrex_rod/rcca_absorber replacement universes with cylinder regions + background coolant、pyrex_glass/rcca_absorber_mat materials with full composition）；修复 core axial root cell 径向 boundary 使用 `core.boundary`（reflective）而非 `assembly.boundary`（transmission）；core lattice outer 保留为 precision safety net（不再清零，不再 block renderer）；renderer 移除 `core.lattice_outer_unreachable` error check。VERA4 deterministic physical closure 21/21 checks passed（patches → plan → CoreRenderer runnable → model.py 64KB → 5 XML files → geometry loads → **transport smoke**）。非-openmc `1708 passed`，OpenMC `388 passed`，benchmark `21/21`。**VERA4_DETERMINISTIC_PHYSICAL_CLOSURE_PASSED**。

- **P2-FULLCORE-2C-A Profile Registry, Anchor Resolver, CoreRenderer Export, and VERA4 Geometry Closure**：新增 `LocalizedInsertProfilesPatch` 正式 patch 容器（profile registry）；新增 `localized_insert_profiles.py` 模块含 anchor 解析器（`resolve_profile_anchor`/`resolve_profile_absolute_segments`，支持 bottom/top/center/absolute 四种 anchor_kind）、typed result（`ResolvedLocalizedInsertProfile`/`ResolvedSegment`）、profile validators（`validate_profile_registry`/`validate_profile_segments`/`validate_profile_references`）；executor 扩展 `_DEFAULT_ORDER`/`_DEPENDENCIES`/`_PATCH_DEPENDENTS` 含 localized_insert_profiles；patch prompt 新增 reactor-neutral profile 规则；`PatchGenerationContext`/`PatchValidationContext` 扩展 `known_insert_profile_ids`/`insert_profile_summaries`/`movable_insert_facts`；`compile_global_axial_segments` 重构接受 resolved profiles 并将 profile segment boundaries 注入 global breakpoints；core lattice outer_universe_id 在 reflective/vacuum boundary 时自动清零（消除 dead geometry renderer flag）。VERA4 deterministic geometry closure 15/15 checks passed（patches → plan → CoreRenderer runnable → model.py 36KB → 5 XML files → geometry loads OK）。非-openmc `1708 passed`，OpenMC `388 passed`，benchmark `21/21`。**VERA4_DETERMINISTIC_GEOMETRY_DEBUG_PASSED**。

### 2026-07-16

- **P2-FULLCORE-2B Moderator Outer Universe + Insert Axial Profile + CoreRenderer Export Readiness**：修复 pin lattice `outer_universe_id` 从使用 `default_universe_id`（燃料 pin）改为专用 `moderator_outer` universe（含 `UniverseSpec`+`CellSpec` fill_type=material fill_id=coolant）；新增 `ensure_moderator_outer_universe()` helper；assembly gap 计算（pin_pitch×lattice_size vs assembly_pitch）；新增 `LocalizedInsertAxialSegmentPatchItem`/`LocalizedInsertAxialProfilePatchItem` schema 支持多段 RCCA axial profile（absorber+plenum+end structure）；`LocalizedInsertIntentPatchItem` 新增 `axial_profile_id`/`anchor_z_cm`/`control_state_id` 字段支持参数化棒位；CoreRenderer export 验证（wrapper cells reference real lattices、core lattice universe_pattern 引用 valid universes、plan serializable）。非-openmc `1679 passed`，OpenMC `388 passed`，benchmark `21/21`。**P2_FULLCORE_AXIAL_PROFILE_SCHEMA_READY**。

- **P2-FULLCORE-2A Production Hierarchical Plan Integration + Full-Core Renderer Readiness**：将 `AssemblyCatalogPatch`/`CoreLayoutPatch` 接入 production `assemble_simulation_plan_from_patches`；multi-assembly 路径生成 `ComplexModelSpec(kind="core")` 含 per-type pin lattices + assembly wrapper universes/cells + core lattice；`_expand_assembly_pin_map` 不再将 localized inserts 写入 base lattice（Pyrex/thimble/RCCA 仅通过 axial loadings 应用）；assembly boundary 改为 `transmission`（内部组件不得 reflective）；core lattice 居中原点 (`center_cm=(0,0)`, `lower_left_cm=(-w/2,-w/2)`）；新增 typed `HierarchicalCoreAssemblyResult`（替代 raw dict）；新增 `compile_global_axial_segments`（从 facts/inserts/spacer grids 编译无 gap 无 overlap 全局轴向分段）；新增 3 个测试文件（production assembler integration / base lattice purity / global axial segments / boundary gap placement）。VERA4 deterministic production render 14/14 checks passed（kind=core、3 pin lattices、3 wrapper universes、core_total_fuel=2376、transmission boundary、centered core lattice）。非-openmc `1659 passed`，OpenMC `388 passed`，benchmark `21/21`。**P2_FULLCORE_PRODUCTION_PLAN_ASSEMBLED**。

- **P2-FULLCORE-1 Scope-Aware Count Contract, Hierarchical Assembly Catalog, Core Layout Patch, and Incremental Full-Core Planning**：新增 `ModelScope`/`CountScope`/`ScopedExpectedCount` schema；FactsPatch 扩展 `model_scope`/`assembly_count`/`core_lattice_size`/`assembly_type_counts`/`scoped_expected_counts` 等字段；新增 `scoped_counts.py`（`normalize_scoped_counts`/`compute_assembly_pin_counts`/`aggregate_core_counts`/`validate_count_scope_compatibility`/`compare_scoped_expected_counts`/`derive_homogeneous_local_counts_if_proven`）；新增 `AssemblyCatalogPatch`/`AssemblyTypePatchItem`/`AssemblyPinMapPatchItem`/`CoreLayoutPatch` 四个 patch schema；新增 catalog/layout validator + cross-validation；新增 `hierarchical_assembler.py`（`lift_single_pin_map_to_catalog`/`assemble_assembly_templates`/`assemble_core_lattice`/`build_hierarchical_core_plan`）；executor `_DEFAULT_ORDER`/`_DEPENDENCIES`/`_PATCH_DEPENDENTS` 扩展 assembly_catalog/core_layout；`required_patch_types_for_state` 区分 single/multi path；retry routing 新增 scoped count error codes；prompts 新增 assembly_catalog/core_layout rules + facts prompt 扩展 scoped counts；PatchGenerationContext/PatchValidationContext 扩展 multi-assembly fields。18 新测试文件（104 tests）覆盖 scope schema/validation/aggregation/homogeneous derivation/heterogeneous no-division/catalog patch/layout patch/prompts/dependencies/validators/cross-validation/hierarchical assembler/template reuse/localized insert scope/single-assembly compat/VERA4 regression。非-openmc `1626 passed`，OpenMC `388 passed`，benchmark `21/21`。VERA4 planning diagnostic：3 assembly types (corner/edge/center_rcca)、3×3 layout、core_total fuel=2376/guide_tube=216/instrument_tube=9、no cross-scope mismatch（264 vs 2376 不再比较）。**P2_FULLCORE_IR_READY_RENDERER_NOT_YET_VALIDATED**。P1 certified baseline SHA=6da53e2；current HEAD P2 development uncircified。

- **P1-RUNTIME-POSTFREEZE-2 Localized Insert Intent Contract + Thimble Closure + Full Requalification**：新增 `LocalizedInsertIntentPatchItem` schema 将有限轴向插入件（Pyrex/thimble/absorber）从基础 lattice 路径语义中分离；PinMapPatch 新增 `localized_insert_intents` 字段；`expand_pin_map` 不再读取 `pyrex_rod_coords`/`thimble_plug_coords` 作为基础 lattice 赋值；新增 `localized_insert_derivation.py` 通用确定性 insert loading 派生管线（替代 pyrex-only `_normalize_axial_insert_pin_map`）；assembler 集成 `derive_localized_insert_loadings` 处理所有 insert kind；validator 不再原地修改 patch（改为仅报告 overlap）；axial_overlays `total_mass_missing` 降级为 warning（renderer 可从 frame geometry 计算）；acceptance helper 的 `collect_loading_override_counts` 同时计数 `coordinate_override` transformation。Qualification N=10: **10/10 FIRST_PASS_SUCCESS, 10/10 full VERA3B acceptance (100%), 10/10 real LLM, 10/10 real OpenMC, 0 lost particles, 0 unsafe**。Transport seed stability: **3/3 PASSED (mean keff=1.00554, max z=1.01, effective batches=20/particles=10000)**。非-openmc `1522 passed`，OpenMC `388 passed`，benchmark `21/21`，Lane A `75 passed`。**thimble_loading_missing CLOSED。P1_RUNTIME_STAGE_COMPLETE_CURRENT_HEAD**。

### 2026-07-15

- **P1-RUNTIME-POSTFREEZE-1 Material Semantics Closure + Current-HEAD Requalification**：新增 `CompositionValueBasis`/`NormalizationStatus` 枚举 + `material_semantics.py`/`material_normalization.py`/`material_validation.py` 三模块；executor 删除 `_reconcile_uo2_oxygen_scale`/`_reconcile_borated_water_boron` 隐藏修正，renderer 忠实渲染 normalized material；assembler 集成 `normalize_material_semantics` 在 assembly 后自动归一化（declared basis → deterministic transform → ambiguous blocked）；material patch prompt 扩展 `composition_basis` 语义说明 + reactor-neutral 示例（UO2 stoichiometric + borated water ppm）；axial overlay prompt 从 `homogenized_open_region` 改为 `mass_conserving_outer_frame` 默认。新增 `certification_identity.py`（CertificationIdentity + PhysicsContractIdentity + `check_certification_stale` SHA/contract hash mismatch detection）；transport seed evaluator 修复 `batches`/`particles` 实际写入 settings.xml + `canonical_settings_hash_excluding_seed` + 删除 `-s` 参数（OMP_NUM_THREADS 控制线程）；acceptance 模块改为 repo-root sys.path + `FullAcceptanceLoadError` 不再静默 fallback；CLI `profile=qualification` 自动强制 full acceptance。14 新测试文件（1522→1522 non-openmc passed）。Qualification N=10: 10/10 FIRST_PASS_SUCCESS, 10/10 real LLM verified, 10/10 real OpenMC verified, 0 lost particles, material normalization 全部正确（fuel stoichiometric_ratio→atom_fraction, coolant ppm_by_weight→atom_fraction）。VERA3 acceptance: 0/10 passed (每 run 仅剩 2 个 `thimble_loading_missing`，spacer grid 已修复)。Transport seed stability: 3/3 PASSED (mean keff=1.0217, max z=1.31, geometry/materials/canonical-settings hashes 全 match, effective batches=20/particles=10000 正确)。

### 2026-07-14

- **keff 材料渲染 bug 修复**：发现两个系统性 production executor bug 导致 LLM 生成模型 keff≈0.65（预期≈0.98）。Bug 1：燃料 O16 用分子比 ao=2.0（2 O/U）与 enrichment ao=2.619（per-100-U）混用，OpenMC 归一化后 O/U=0.02 而非 2.0，燃料实际变为铀金属。Bug 2：硼水 B10 用 ppm 值 ao=0.001066（1066 ppm）直接作为 atom fraction，实际过量 9×。修复：executor 新增 `_reconcile_uo2_oxygen_scale()`（检测 U percents sum≈100 且 O16≈2 → 乘以 100）和 `_reconcile_borated_water_boron()`（检测 B10 frac>5e-4 的水类材料 → 从 ppm 转换为正确 atom fraction）。修复后 keff 从 0.65 提升到 0.985±0.006，达到 VERA3B 预期范围。11 新测试。非-openmc `1455 passed`。

- **P1-RUNTIME-TRUTH-3 Qualification N=10 + Transport Seed Stability + Final Gate**：强化 LLM 证据 9-point gate（`LLMCallRecorder` 统一记录 planning/diag/proposer/supervisor 四角色调用，拦截 `__call__` + `generate_patch_json`）；3-stage OpenMC 证据（export/geometry-debug/smoke 各自独立 backend 标记 + returncode + hash）；实际 reference/few-shot/monolithic provenance 从 workflow state 提取替代硬编码空列表；campaign budget 强制（`LLMBudgetExhausted` → `SAFE_STOP_BUDGET`）；safe resume（config mismatch → `CAMPAIGN_RESUME_CONFIG_MISMATCH`）；扩展 qualification metrics（Wilson 95% CI、autonomous/bounded rates、success-only artifact/verification rates）；transport seed stability evaluator（3 seeds × pairwise z ≤ 5）；P1-RUNTIME final gate（10 gates 全 PASS）。Qualification N=10: 9/10 FIRST_PASS_SUCCESS (90%), real_llm_verified=100%, real_openmc_verified=100%, unsafe=0, artifact=100%。Transport seed stability: 3/3 PASSED (max z=0.61)。非-openmc `1444 passed`，OpenMC `388 passed`，benchmark `21/21`，Lane A `20/20`。**P1_RUNTIME_STAGE_COMPLETE**。

- **P1-RUNTIME-TRUTH-2 Real Lane B Executor + VERA3B Pilot N=3**：实现真实 Lane B campaign executor（`real_campaign.py`），每次 run 创建全新 DeepSeek client，通过 production `build_plan_graph` 完成 incremental planning → validation → render → real OpenMC → bounded runtime recovery。Pilot N=3 全部通过：3/3 FIRST_PASS_SUCCESS，每个 run 真实 OpenMC export/geometry-debug/smoke 成功，zero lost particles，VERA3B acceptance 通过。Safety: unsafe=0, fake_client=false, reference_patches=0, monolithic_fallback=false。修复 run classifier（runtime supervisor finish_success ≠ runtime_iters==0）。非-openmc `1349 passed`，benchmark `21/21`。

- **P1-RUNTIME-TRUTH-1 Runtime Evaluation Truthfulness Closure**：修正 promotion gate 语义（PARTIAL/PASSED/FAILED + new field tracking）；新增 source-strategy rendering contract（4 策略 active_fuel_box/assembly_box/manual/unknown 流入 assembler→renderer→settings.xml 完整链）；source_bounds_for_plan 重构为 strategy-aware；atom-density fuel detection（density_unit=sum 不再需要注入虚构 g/cm3）；删除 fixture 后处理密度补丁；F01 关闭为真实 OpenMC source recovery gate（manual non-fuel bounds → pre-flight → deterministic repair → candidate clone real smoke → commit → rerun success）；F05 重分类为 root-cause precedence injection gate（NO_PROGRESS）；删除 _REAL_OPENMC_CASES 硬编码集合，改为 case.requires_real_openmc 驱动；修复 5 个生产 bug（GraphState 缺 runtime_supervisor_result/runtime_user_cancelled、RuntimeFailureClass import ×3、repair_result_router 缺 runtime_supervisor edge、repaired plan capability re-assessment、ValidationReport 构造）。20/20 fault matrix PASSED，2 real-OpenMC (F00+F01)；非-openmc `1301 passed`，OpenMC `388 passed`，benchmark `21/21`。

- **P1-RUNTIME-R7/R8 VERA3B Fault-Injection Matrix + Runtime Stability Campaign**：将占位 harness 改造为 production graph-backed runner；新增 `runtime_faults.py`（20-case immutable fault registry + 隔离 source-state injectors）、`runtime_metrics.py`（matrix/campaign aggregation + promotion gates）、`runtime_campaign.py`（per-case tool/LLM injection 通过 `build_plan_graph(...)` 参数注入，不另写模拟状态机）；修复 5 个 R5/R6 遗留 production bug（`stable_json_hash`/`RuntimeFailureClass` import 缺失、`plots/` 目录未创建、`retry_same_plan` 不重置 validation_report、`GraphState` 缺 `accepted_plan_build_state` 字段）；Lane B CLI 有 confirmation gate（缺 key → `NOT_RUN_ENV`，有 key 无 `--confirm-real-campaign` → `CONFIRMATION_REQUIRED`）。18/20 evaluated cases 通过（F00 real OpenMC baseline + 17 injected），2 pending real-OpenMC（F01/F05 source rejection injection 需要 renderer source-binding 深入修改）。36 新测试；非-openmc `1254 passed`，OpenMC `388 passed`，benchmark `21/21`。

- **P1-RUNTIME-R5/R6 Post-Execution Runtime Supervisor + Bounded Recovery**：新增独立 `runtime_supervisor.py`/`runtime_supervisor_policy.py`/prompt（不复用 planning RunSupervisor action），包含 finish/deterministic/LLM/transient retry/human/stop 六种 action、RuntimeLoopBudget、Python allowlist/veto、runtime-only fingerprint 和 no-progress 保护；feature-flag graph 将执行结果进入 runtime supervisor，repair commit 后从 source patch 重组 plan 再 reexecute，transient 同计划仅重试一次；新增 runtime_loop iteration manifests 与 VERA3B fault-sequence harness（缺真实密钥时 `REAL_LLM_SKIPPED_ENV`）。复杂 geometry 继续 diagnose-only，不能扩大 provenance allowlist。16 新测试；R4 完整回归非-openmc `1252 passed`，OpenMC `388 passed`，benchmark `21/21`。

- **P1-RUNTIME-R4 LLM Runtime Diagnostician + Constrained Patch Proposer**：在 R2/R3 基础上新增 `runtime_diagnostician.py`（RuntimeDiagnosis + 10 种 RepairKind + evidence 分级 + deterministic validation，LLM 只能收窄权限不能扩大）；新增 `runtime_patch_proposer.py`（LLMRuntimeRepairProposal + 静态 RFC6902 validation，复用 `apply_json_patch_to_clone` + `is_protected_path` 三层安全网）；RuntimeRepairPolicy 扩展 LLM 字段（llm_diagnosis_supported / llm_proposal_supported / allowed_repair_kinds / max_mutating_operations=4）；graph 新增 `llm_runtime_diagnose → llm_runtime_propose` 路由（one-shot budget，fingerprint dedup，allow_fallback=False 不用 Fake 冒充真实 LLM）；新增 prompt builder 和 16 个 trace event。29 新测试覆盖 diagnosis validation、proposal static safety（protected/forbidden/budget/allowlist/root）、fake client safety、graph routing。非-openmc `1236 passed`，OpenMC `388 passed`，benchmark `21/21`。

- **P1-RUNTIME-R2/R3 Deterministic Runtime Repair + One-Shot Recovery**：修复 validate_plan 中 incremental regeneration 清空 plan_build_state 导致 valid patch 丢失的 bug（graph.py:2708）；新增 `runtime_repair_policy.py`（18 个 issue code 的 RuntimeRepairPolicy registry，映射 owner 到真实 patch type）；新增 `runtime_repair.py`（RuntimeRepairRequest/Proposal/Evaluation + source binding oracle + geometry diagnosis + clone-only evaluation + commit）；graph 新增 classify_runtime_feedback → deterministic_runtime_repair 路由（one-shot budget，fingerprint dedup，不消耗 planning retry_count）；新增 12 个 trace event 类型。source oracle 确定性切换 source_strategy→active_fuel_box + fissionable=true，不写入 benchmark 常数。23 新测试；非-openmc `1207 passed`，OpenMC `388 passed`（含此前 pre-existing fail 现已修复），benchmark `21/21`。

- **P1-RUNTIME-R0/R1 Runtime Feedback Contract + Geometry-Debug Stage**：新增 `runtime_feedback.py`（`RuntimeFailure`/`RuntimeFailureClass`/`RuntimeIterationRecord` + `classify_runtime_tool_results`/`normalize_runtime_error`/`compute_runtime_error_fingerprint`，根因优先级 source-rejection > cross-section > geometry > material > crash > unknown；fingerprint 去除时间戳/PID/绝对路径/hex 地址）；新增 `run_geometry_debug` 工具（隔离 `geometry_debug/` 子目录，timeout 不归为 overlap）；重构执行顺序 export→plots→geometry_debug→smoke，每阶段独立 trace event；error catalog 新增 `runtime.openmc_timeout`/`runtime.openmc_process_crash`；修复 `_runtime_issue` message=None bug 和 "No overlaps found" false positive。29 新测试；非-openmc `1187 passed`，OpenMC `384 passed`（1 pre-existing fail），benchmark `21/21`。

- **Incremental ID Canonicalization + Half-Pitch Water Cell**：axial patch generator 对唯一匹配的 universe ID 分隔符变体做保守规范化（如 `fuel_pin_endplug`→`fuel_pin_end_plug`），不为真正未知/歧义 ID 猜测替换；assembly capability 允许 moderator cylinder 半径恰好等于 square lattice half-pitch，仍拒绝超界半径。根因：真实 VERA3B LLM 产生 endplug 拼写变体，且 `water_cell r=0.63 cm` 被错误判为超出 `pitch/2=0.63 cm`，使可渲染 plan 被降为 skeleton。2 新测试；非-openmc `1159 passed`，benchmark `21/21`。

- **Structural Axial-Slab Material Guard**：axial patch validator 新增 reactor-neutral 规则：`lower_nozzle`/`upper_nozzle`/`core_plate` 等全截面结构层不得引用 role=`coolant`/`moderator` 的材料；materials/axial prompts 明确要求将输入给出的钢-冷却剂均匀化结构写为独立 material + `mixture_components`，不能只写 assumption 后填充纯水。根因：真实 VERA3B LLM patch 将 4 个 nozzle/core-plate slab 直接填为 borated water。2 新测试；fixture 重建确认 4 层使用 `lower_nozzle_3b`/`upper_nozzle_3b`/`core_plate_3b`；非-openmc `1157 passed`，benchmark `21/21`。

- **Incremental Axial-Universe Dependency Recovery**：当 `axial_layers` 的 lattice transformation 引用了未定义 replacement universe 时，executor 提取缺失 ID，保留有效 facts/materials，并仅失效/再生 `universes` 与其下游 pin-map/axial patches；下一轮 universe prompt 显式携带所需 profile ID。修复了此前全量 7-patch regeneration 反复消耗 retry budget 的依赖环；不启用 benchmark reference、gold few-shot 或 monolithic fallback。1 新测试；非-openmc `1155 passed`，benchmark `21/21`。

- **Concentric Pin/Tubing Closure（修复 VERA3B lost particles）**：assembler 现在按显式 `r_min_cm>0` 生成 annulus，即使 LLM 将 gap/clad 标成 `cylinder`；对有径向外边界、但未声明 background cell 的 repeated universe，确定性注入输入材料 role=`coolant`/`moderator` 的外部 moderator cell，避免 lattice pitch 内未定义区域。根因：最新 VERA3B LLM plan 将 gap/clad 渲染成嵌套实心圆柱且漏掉外部 coolant，粒子穿越 clad 外径 surface 3 后无法定位。真实 VERA3B 重建输运完成（keff=0.99345±0.00592，0 lost particles）。2 新测试；非-openmc `1154 passed`，benchmark `21/21`。

- **Renderer nuclide-name normalization（修复 B-10 transport abort）**：executor 新增 reactor-neutral `_normalize_nuclide_name()`，将 GND 连字符核素名（`B-10`/`U-235m`/`B-10.71c`）统一改写为 OpenMC HDF5 库约定（`B10`/`U235m`/`B10.71c`），在全部 5 个 `add_nuclide` 出口（2 live 构造 + 3 render emit）应用；纯格式化，不改成分/密度。根因：OpenMC 0.15.x 原样存储核素名，本机 `endfb-vii.1-hdf5` 库仅含无连字符条目，导致 VERA3B 硼水/Pyrex 的 `B-10` 在输运期 `Could not find nuclide` MPI_ABORT。真实 VERA3B 端到端：smoke exit=0、nuclide 错误消除、keff≈0.951。4 新测试；非-openmc `1150 passed`，OpenMC executor `49 passed`，benchmark `21/21`。

- **P0-FINAL + FC-MVP：工程基线闭合 + 全堆 MVP**：VERA3 reference Table P3-3 精确原子数密度（atom/barn-cm）替换全部存根材料；新增 `atom_density_barn_cm` composition_basis + `set_density('sum')` 支持；executor 扩展原子质量表（60+ 核素）用于 outer-frame density 反算。VERA3 3A/3B transport smoke 通过（3A keff=1.17547±0.005 Δ=0.025%，3B keff=0.99487±0.006 Δ=0.53%）。Reusable assembly universe（boundary=transmission, x/y bounds stripped）支持 core lattice 嵌套；3×3 full-core MVP transport smoke 通过（keff=0.99452±0.005, leakage=4.6%, 0 lost particles, assembly tally 9 entries center-peaked P/A=1.35）。非-openmc `1147 passed`，OpenMC `380 passed`，benchmark `21/21`。

- **P0-V4 Variant-Specific Nozzle and Core-Plate Homogenized Mixtures**：新增 `MaterialSpecPatch.mixture_components`/`variant_scope`/`derivation_method` 和 `ComplexMaterialSpec.mixture_component_ids`/`mixture_volume_fractions`/`is_mixture` schema 支持；executor 新增确定性 volume-fraction 展平（`_flatten_volume_mixture` 将 atom_frac/weight_frac 组件统一转为 weight_frac 混合，不依赖 `openmc.Material.mix_materials`）。3A/3B 各新增 3 个 variant-specific mixture（lower_nozzle f_SS304=0.27922 / upper_nozzle f_SS304=0.19147 / core_plate 50-50）；4 层 axial layer fill 从纯 SS304 迁移至 variant mixture；renderer/executor density+composition 校验跳过 mixture 材料。28 新测试；非-openmc `1117 passed`，OpenMC `380 passed`，benchmark `21/21`；3A/3B geometry 0 overlaps / 0 lost particles。

- **P0-V3 Spacer-Grid Mass-Conserving Outer-Frame Geometry**：新增 `mass_conserving_outer_frame` geometry_mode（Level 2 overlay）和 reactor-neutral `outer_frame_overlay.py` planner（`derive_mass_conserving_outer_frame` 确定性计算 frame area/thickness/mass conservation + clearance check）；executor 新增 outer-frame 发射（4 XPlane/YPlane + inner region + frame cell + 保留 background moderator 至 inner square）。3A/3B 8 个 spacer-grid 从 `homogenized_open_region` 迁移至 `mass_conserving_outer_frame`（end 1017g Inconel-718 / mid 875g Zircaloy-4 / 289 cells）；新增 schema 字段 `total_mass_g`, `cell_count`, `pitch_cm`, `material_density_source`, `mass_tolerance_rel`。38 新测试；非-openmc `1117 passed`，OpenMC `380 passed`，benchmark `21/21`；3A/3B geometry 0 overlaps / 0 lost particles。

- **P0-V2 VERA3B Pyrex Upper-Gas Axial Profile**：新增 `pyrex_upper_gas_inner_profile` universe（SS304 内/外管保留、氦气腔替代毒物+间隙、水隙背景保留）和 `pyrex_upper_gas_loading`（16 坐标复用毒物段）；6 层轴向层（376.441–397.510 cm）更新为含 upper-gas 的多 loading 组合；geometry contract v1→v2（双 axial profile + conflict resolved）。修复多 loading 物化时共享 `derived_lattice_id` 导致 lattice ID 碰撞的 reactor-neutral 问题（`materialize_axial_lattice_transformations` 追加唯一后缀）。41 新测试；非-openmc `1079 passed`，OpenMC `380 passed`，benchmark `21/21`。

- **P0-V1 Fuel Helium Gap & Upper Plenum Correction**：新增燃料棒氦气间隙 (0.4096–0.418 cm) 到 fuel_pin universe；修正 plenum 气体 r_max (0.4096→0.418) 和 clad r_min (0.4096→0.418)；新增 reactor-neutral `radial_profile_validation.py` validator（间隙/重叠/背景检测）；集成到 patch validator。修复 7 个 P0-D5B 预存测试失败（frozen 回归 fixture）。44 新测试；非-openmc `1038 passed`，OpenMC `380 passed`，benchmark `21/21`。

### 2026-07-18

- **P2-PLAN-CLOSED-LOOP-PHASE-7A**：Real Controlled Five-Gate Canary 离线 harness（reactor-neutral `RealCampaignCaseSpec`/`builtin_case_registry` 仅在 case preset/CLI/fixture/report 出现，生产代码无 VERA3/VERA4 分支；`RealCampaignClientBundle` 扩展 `plan_reviewer_client`+`plan_repair_client`，由 `_create_client_bundle(plan_reviewer_enabled, plan_repair_enabled)` 构造并通过 `LLMCallRecorder` 记录；`detect_provider_environment(model)` 通过 `_client_for_model` resolver + `OpenAICompatibleChatClient.api_key_env` class attribute 探测 provider key env — 不硬编码 `DEEPSEEK_API_KEY`，未知 provider 返回空 env → `BLOCKED_BY_LLM_ENVIRONMENT`；`make_five_gate_controlled_policy()` 显式五 Gate controlled；`estimate_real_campaign_llm_budget` 估算 patch/manifest/fragment/gate-review/plan-repair/runtime 8 类预算；`CampaignResumeFingerprint` 18 字段绑定 git/input/req/human-answer/model/provider/reasoning/output/policy-hash/gates/modes/universes-mode/fragment-budget/material/runtime/cross-sections fingerprint，不匹配 → `CONFIG_MISMATCH`；`validate_real_canary_truthfulness` 覆盖 fake/fallback/reference/gold/monolithic/render-before-final-gate/partial-fragment/missing-reviewer/gate-auto-accepted/stale-plan/reasoning-persisted；stages `planning|render-compile|openmc-smoke`；artifact writers 含 five_gate_status/timeline/hashes、plan_reviewer_calls、llm_budget、truthfulness_evidence、environment_evidence、human_answer_provenance；CLI `scripts/evaluate_plan_closed_loop_real_canary.py` 含 `--case`/`--input`/`--operating-state`/`--stage`/`--universes-generation-mode`/`--strict-structured-patch-output` 等。复用 runtime_repair/supervisor/diagnostician/proposer 不重设计；不修改 contract 版本（仍 0.8）；不动 `_DEFAULT_EXECUTABLE_PLAN_GATES`。非-openmc `2593 passed`（0 failures），Phase-7A targeted `99 passed`，VERA3/VERA4 offline `BLOCKED_BY_LLM_ENVIRONMENT`/`BLOCKED_BY_OPENMC_ENVIRONMENT` 路径全部通过，fake benchmark `21/21`。

- **P0-LARGE-STRUCTURED-PATCH-GENERATION-CLOSURE**：大型结构化 Patch 分片生成（`PatchLLMResponse` typed telemetry with finish_reason/token usage/reasoning fingerprint、`normalize_patch_llm_response()` 统一 str/PatchLLMResponse、`strict_structured_output` fail-closed policy、`generate_patch_json_with_meta()` 返回 telemetry、`PatchGenerationAttempt` 新增 finish_reason/prompt_tokens/completion_tokens/reasoning_tokens/structured_fallback_reasons、`universe_fragment_generation.py` 含 requirement extraction/manifest/fragment model/deterministic merge/strategy switching/checkpoint session、`universe_patch_pipeline.py` 入口 `generate_universes_patch()` orchestrating auto→monolithic→fragmented truncation-aware strategy switching、executor 对 universes 调用 pipeline、CLI `--universes-generation-mode`/`--universe-fragment-max-tokens`/`--large-patch-safe-output-ratio`/`--strict-structured-patch-output`）。思考模式不关闭；可靠性来自缩小每次结构化输出而非更大 token 预算；截断后自动切 fragmented 不再请求同一个完整 Patch；fragment checkpoint/resume 跳过已完成 fragment；最终输出标准 UniversesPatch。非-openmc `2494 passed`（0 failures），targeted `61 passed`，VERA4 11-universe offline `全部通过`，fake benchmark `21/21`。

- **P2-PLAN-CLOSED-LOOP-PHASE-6**：Final / Assembled Plan Review Gate（contract 0.7→0.8 迁移、`AssembledPlanBindingView` 含 typed object graph/root selection/reachability/renderer capability matrix/static source feasibility/plot coverage/execution check、`AssembledPlanContractMatrix` 七行类型 root_selection/root_reachability/reference_integrity/renderer_capability/static_source_feasibility/plot_coverage/execution_check、`run_assembled_plan_preflight` 确定性 issue codes 覆盖 root/reachability/renderer/source/plot/exec、`AssembledPlanEvidencePack` typed evidence refs G/R/CM/SF/EC/D、独立 Critic `run_assembled_plan_review` + coverage + unknown-ref/owner-action/runtime-claim rejection、`assembled_plan_issue_policy.py` Python owner registry 路由 Facts/Materials/Universes/Placement/Axial-owned、executor `_run_assembled_plan_gate` controlled barrier requires all upstream gates accepted + Phase-3B retry 集成 + input-hash invalidation + reopen、CLI `--assembled-plan-review-mode`）。复用 assemble_simulation_plan_from_patches/validate_simulation_plan/choose_renderer/collect_active_dependencies/assembly3d_guard，不复制底层算法。非-openmc `2433 passed`（0 failures），Phase-6 targeted `63 passed`，VERA3/VERA4 offline `全部通过`，fake benchmark `21/21`。

- **P2-PLAN-CLOSED-LOOP-PHASE-5**：Axial Geometry Review Gate（contract 0.6→0.7 迁移、`AxialGeometryBindingView` + 9-kind `AxialGeometryContractMatrix`、`derive_axial_geometry_segments` 有限分段、`run_axial_geometry_preflight` 确定性 issue codes 覆盖 domain/fill/loading/overlay/through-path/localized-insert、`AxialGeometryEvidencePack` typed evidence refs F/M/P/B/A/L/O/I/T/G/D、独立 Critic `run_axial_geometry_review` + coverage + unknown-ref/owner-action/root-reachability/runtime rejection、`axial_geometry_issue_policy.py` Python owner registry 路由 Facts/Materials/Universes/Placement/Axial-owned、executor `_run_axial_geometry_gate` controlled barrier requires Facts+MU+Placement accepted + Phase-3B retry 集成 + input-hash invalidation + reopen、CLI `--axial-geometry-review-mode`）。复用 validators/axial_overlay/compute_axial_segments/assembly3d_guard/material_execution_readiness，不复制底层算法。非-openmc `2368 passed`（0 failures），Phase-5 targeted `103 passed`，VERA3/VERA4 offline `全部通过`。

- **P2-PLAN-CLOSED-LOOP-PHASE-4**：Material–Universe Review Gate（contract 0.5→0.6 迁移、`MaterialUniverseBindingView`/`MaterialUniverseContractMatrix` 四行类型、`run_material_universe_preflight` 确定性 40+ issue codes、`MaterialUniverseEvidencePack` typed evidence refs F/M/U/C/V/D、独立 Critic `run_material_universe_review` + coverage 完整性校验 + unknown-ref/owner-action/root-reachability rejection、`material_universe_issue_policy.py` Python owner registry、executor `_run_material_universe_gate` controlled barrier + Phase-3B retry 集成 + input-hash invalidation + reopen）。Gate 验证 Facts→Materials→Universes→cell 静态绑定、fuel-variant identity/reachability/collapse、role compatibility（reactor-neutral）、compound species evidence。role 兼容性区分 pass/fail/unresolved（custom role 不直接报 error）。非-openmc `2253 passed`（4 pre-existing failures 不变），Phase-4 targeted `64 passed`，VERA3/VERA4 offline `全部通过`，fake benchmark `21/21`。

- **P2-PLAN-CLOSED-LOOP-PHASE-3B**：typed retry request fidelity（5 builders + idempotent registration + lifecycle states）、scope-aware owner policy（pin_map+assembly_catalog 永不并存、unknown scope fail-closed）、`RetryCandidateProducerRegistry`（Facts/Materials/Universes/Placement/TaskPlan/Axial dedicated producers + `RetryPatchGenerationContext` retry-aware prompt）、real callable `run_owner_acceptance_checks`（25+ checks：schema/consistency/density/required-ids/near-miss/fuel-variant 等）、bounded `execute_plan_retry_loop`（多轮 + post-replay `reclassify_retry_outcome` + terminal removal + budget/cycle/no-progress enforcement）、`resume_incremental_from_patch`（非递归 depth-guarded downstream resume）、gate invalidation/replay 分离（`plan_retry_gate_invalidation_counts` vs `plan_retry_gate_replay_attempt_counts` vs `plan_retry_gate_replay_success_counts`）、Graph 新增 `execute_plan_retry` + `resume_plan_retry` 节点与 retry-aware routers、`retry_human.py`（typed human question + fingerprint-validated answer）、`retry_artifacts.py`。材料 readiness 路径现在同时注册 typed `ExecutablePlanRetryRequest`。非-openmc `2184 passed`（4 pre-existing failures unrelated），targeted Phase-3B `81 passed`，VERA4 offline mutations `6/6 PASS`，fake benchmark `21/21`。

### 2026-07-17

- **P2-FULLCORE-2D-B-MATERIAL-SPECIES-SEMANTIC-CLOSURE**：新增 typed `compound_components`、独立 `material_species` resolver、质量守恒/重复 species 合并、fissile compound fail-closed、legacy formula 审计转换、静态 patch gate、materials-only retry 与运行前 cross_sections 预检；所有普通/complex/mixture material emission 统一通过 canonical resolver。VERA4 Pyrex 现在以 `B2O3 12.5 wt% + SiO2 87.5 wt%` source 表示，确定性解析为自然 `B/Si/O`，并输出 `material_species_resolution_report.json`。验证：VERA4 A–G/geometry/XML/smoke 55/55（keff=1.14309±0.01647、0 lost），VERA3 3B XML 回归 0 error，fake benchmark 21/21。

- **P2-FULLCORE-2D-B-AXIAL-OVERLAY-SEMANTIC-CLOSURE**：axial-overlay `through_path_preserved` 区分字段缺失（确定性派生 True + 审计）与明确 false（`mode_semantic_contradiction` error）；issue-scoped compact retry 携带上次 parsed patch + 允许/锁定字段列表；retry drift gate 检测 z_min/material/total_mass 等锁定字段变化（`patch_retry.unexpected_semantic_drift`）；DS 模型 `patch_type` 自动注入（特征键匹配）；canary status 门控区分 fuel_variant_subcanary 与 full planning canary；post-decoration fuel variant identity 动态检查。VERA4 real-LLM (ds:deepseek-v4-flash) 全流程首次通过：8/8 patches valid、assembly ok、model.py compile + XML export + Geometry.from_xml + smoke test 全部 PASS。Level F (grid 11/11) + Level G (fuel 5/5) 全通。非-openmc `2016 passed`，OpenMC `388 passed`，benchmark `21/21`。

### 2026-07-12

- **P0-D5B Early Lattice-Loading Validation & Deterministic Grid Migration**：新增共享 `lattice_loading_validation.py`（`lattice_loading_structural_issues()` 在 validate_plan 阶段直接发现 `lattice_transform.replacement_universe_missing` / `source_universe_missing` / `loading_ref_missing`，不再推迟到 renderer materialization）；renderer `_axial_assembly_modeling_errors` 同步调用；`_probe_axial_materialization_blockers` 改为 defensive assertion（按 code 去重，不重复注入已发现 issue）。patch-level 新增 transformation cross-reference validation（replacement/source universe 存在性、cell-id-as-universe 误用、spacer_grid_transformation_misuse）；`PatchValidationContext` 扩展 `known_cell_ids` / `cell_owner_universe_ids` / `known_overlay_summaries` / `has_spacer_grids`。新增 `grid_loading_repair.py`（证据层级诊断 + Strategy A-D 确定性修复：优先移除冗余 grid transformation + 清理 layer loading_ids 引用，保留非 grid loadings；不创建 solid grid universe、不修改 materials/facts/pin_map）。graph repair pipeline 新增 lattice-loading/grid migration oracle（shoulder-gap oracle 之后、LLM 之前）。真实 VERA3B DeepSeek smoke：retry_count=0、grid_cell 引用消除、pin counts 264/24/1、capability issues=[]、model.py 生成；VERA3A 回归 retry_count=0、issues=[]。修复 3 个既有测试失败（fixture 缺 water_cell universe、test_resume 用不兼容 minimal universes）。非-openmc `994 passed, 1 skipped`；新增 6 个测试文件 `37 passed`。

- **P0-D5 Early Structural Validation & Deterministic Shoulder-Gap Repair**：统一 validator/renderer 共享 `assembly3d_structural_issues()`，使 `component_profile_as_material_slab` 在 validate_plan 阶段直接产生（不再推迟到 renderer capability）；新增 shoulder_gap role、patch-level early validation、forbidden role-edit ownership policy、确定性 multi-patch repair bundle（universes + axial_layers）+ clone-only acceptance + through-path preservation check。graph repair pipeline 新增 component-profile oracle（在 LLM repair 之前，支持多 layer 一次修复）。真实 VERA3 3A DeepSeek smoke：retry_count=0、pin counts 264/24/1、model.py 生成、capability issues=[]；6 个新测试文件 `36 passed`。

- **P0-D4 Deterministic Pin-Map Repair Oracle**：assembler 现在始终尊重 `PinMapPatch.default_universe_id`，不会被同 kind 的轴向 profile 覆盖，并为缺失显式默认产生结构化 assembly error。新增通用 pin-map usage/delta diagnosis、唯一等量 default 修复 oracle、clone-only preflight、severity-aware issue delta 与 enriched LLM semantic context。真实 VERA3 3A 本地 smoke 直接恢复 `fuel_pin=264, guide_tube=24, instrument_tube=1`，未调用 repair LLM；后续仅剩独立 shoulder-gap 结构问题。验证：新增/相关 targeted `32 passed`，OpenMC gate、full pytest、fake benchmark 均通过。

- **P0-D3 Real-LLM Patch Repair Proposal Contract**：将 DeepSeek repair 输出分为可兼容的 model envelope 与系统绑定的内部 proposal；缺失 rationale/confidence 使用可追溯保守默认，operations 仍严格校验。adapter 现在真实发送 JSON Schema，provider 拒绝后显式回退 json_object 并写入 raw/normalization/normalized artifacts。真实 VERA3 3A 两个候选均完成 clone parse/assembly/full validation 后因无改善拒绝，不再发生 metadata schema rejection。验证：contract targeted `24 passed`，full `1248 passed, 3 skipped`。

### 2026-07-11

- **P0-D2 Real-LLM Validation Repair Smoke**：修复 real-model validation repair 延迟构造 patch adapter、优先使用 adapter JSON mode，并为 schema-invalid proposal 写入 proposal/evaluation artifact。真实 DeepSeek VERA3 3A 两次局部 RFC6902 proposal 均因缺必填元数据被严格拒绝；仅一次 targeted regeneration、global retry=1，未出现三次盲重试。验证：repair targeted `13 passed`。

### 2026-07-10

- **P0-D1 Validation-Driven Incremental Patch Repair**：plan-level issue 现在先进入 patch-relative RFC6902 repair request（稳定 fingerprint、issue ownership policy、protected-path denylist、clone-only assemble/full validation）；accepted repair 不消耗 graph retry，重复/无改善候选立即停止并退回一次 targeted regeneration。VERA3 3A 历史三轮 `lattice.pin_count_mismatch` 已脱敏固化为回归 fixture/diagnosis。验证：新增 repair targeted `9 passed`，incremental/trace 回归 `44 passed`。
- **Incremental plan-level 定点修复**：validate_plan 不再对所有 incremental validation failure 清空 `PlanBuildState`；新增 issue→patch root 路由、patch/downstream invalidation 与 targeted repair prompt，优先重做命中的 patch 集。验证：incremental executor + graph integration `30 passed`。
- **VERA3 Geometry Step 1**：新增 test-only canonical geometry contract，分离 assembly zones、fuel/guide/instrument/Pyrex/thimble component profiles 与 3B finite loading；旧 reference 标为 legacy。升级 acceptance helper（base/loading/active union/continuity）和 deterministic diagnostic oracle，修复 12-layer 与 full-height Pyrex/plug 集成断言。当前 fixture 明确诊断 whole-layer end-plug/plenum、Pyrex helium-gap/radial-stack 和 missing thimble loading，不作为 gold/few-shot。验证：targeted `59 passed, 2 skipped`，OpenMC gate `356 passed, 2 skipped`，full `1105 passed, 3 skipped`。
- **P0-B LLM Patch Repair Proposer**：新增受控 repair proposal schema、issue→path allowlist、protected path policy、JSON Patch clone executor、before/after deterministic validation、accept/reject/unsafe 判定、prompt/fake client/fallback，并接入 workflow trace/artifacts 与 benchmark metrics/CLI。默认关闭/proposal-only，不运行 OpenMC、不修改科学事实；validate-only 只作用于 clone。验证：新增 repair proposal tests `19 passed`，相关旧测试 `27 passed, 9 skipped`，no-OpenMC 全量 `641 passed, 18 skipped, 10 deselected`，repair fake benchmark `16/16 pass_rate=100%`。
- **P0-A LLM Semantic Plan Auditor**：新增只读 semantic audit schema/input builder/prompt/client/fake/fallback，并接入 workflow trace/artifacts 与 benchmark audit metrics；默认关闭，warning-only 不改变 route/pass-fail，strict 仅在 evaluation expectation mismatch 时失败。新增 semantic regression fixture 与 8 个测试文件，覆盖 unknown code normalize、fallback、secret/map compact、warning-only/strict 评估。验证：targeted semantic tests `11 passed`；semantic fake benchmark `13/13 pass_rate=100%`。
- **LLM axial loading 坐标与 plug 剪枝修复**：修复真实 unseen-model run 中 LLM 已生成 `loading_3B` 时 assembler 提前返回，导致 1-based overrides 被 renderer 当成 0-based、且 `thimble_plug` 错误覆盖 active fuel 的问题。已有 loading 现在会按 pin_map 坐标约定归一化，plug-like 非 absorber/control/poison 插入件会从 active-fuel loading 剪枝，base guide tube 保水。验证：real failed patches 复验 `loading_3B` 仅 16 个 0-based Pyrex、`(3,9)/(6,6)/(9,3)` 为 guide_tube；renderer active lattice 16 Pyrex / 0 thimble；`611 passed, 1 skipped, 347 deselected` + compileall + fake workflow 11/11 + regression diff 无回归。
- **空 base loading 覆盖修复**：修复未知模型归一化后 active fuel 仍引用 LLM 生成的空 `base_loading`，导致 Pyrex loading 未应用、3B 退化为 3A-like 水导向管的问题。assembler 现在允许空 loading 被真实 insert loading 替换；真实旧 patches 复验 active fuel `loading_id=pyrex_rod_loading`，renderer 派生 lattice 同时含 `pyrex_rod` 与 `guide_tube`。Targeted tests `16 passed`。
- **未知模型 guide-tube base normalization**：修复真实 incremental/LLM 路径仍会把有限轴向插入件写进 base pin_map 的问题；assembler 现在通用地将 `pyrex_rod_coords`/`thimble_plug_coords` 归一化为 guide-tube base lattice，并为 Pyrex 生成 `lattice_loadings`。真实旧 `data/runs/VERA3_3B` patches 复验：`(3,9)/(6,6)/(9,3)` 组装为 `guide_tube`；targeted tests `55 passed`。
- **VERA3 3B guide-tube water preservation**：修复 3B reference/fixture 将 Pyrex/套管塞坐标直接写入全高 base pin map 的问题；base lattice 改为 24 个水填充 guide_tube，Pyrex 通过 `axial_layers.lattice_loadings` 仅在毒物棒轴向段覆盖，`(3,9)/(6,6)/(9,3)` 等套管塞坐标在 active fuel 中段保持水。新增 axial layer patch 对 `lattice_loadings`/`loading_id` 的通用装配支持。Targeted tests `47 passed`（3B incremental + patch generator）。

### 2026-07-09

- **P0-H Evaluation schema audit + report diff gate**：审计确认 EvaluationCase / EvaluationMetrics / evaluate_trace_against_case / aggregate_evaluation_results / workflow_benchmark report writer 之间字段一致（所有 P0 字段已存在：forbidden_issue_codes、expected_planning_mode、expected_incremental_patch_types、expected_artifact_keys、4 个 rate 指标）。新增 `scripts/diff_evaluation_reports.py`：比较两个 evaluation_report.json，输出 markdown diff（metric delta / case status change / new failure / fixed case），支持 `--fail-on-regression` PR gate（pass_rate / plan_schema / artifact_completeness 下降或新增 failed case → exit 1）。Makefile 新增 `diff-workflow-reports` / `gate-workflow-regression`。新增 9 个 diff 测试。no-OpenMC 全量 `609 passed, 1 skipped`。
- **Requirement resolution bug fix**：requirement 只含文件路径时 feature detection 看不到内容 → pin_map/axial_overlays 任务缺失 → assembly.missing_patch。新增 `requirement_resolver.py`（inline 本地 .md/.txt/.json 内容，不读 URL）；graph `_receive_requirement` 自动解析；executor `required_patch_types_for_state` 增加 benchmark variant 信号。新增 14 个测试。VERA3 3A 真实 LLM 验证：全部 7 patch 生成成功。
- **P0-NEW-1 受控合金 composition library**：新增 `material_library.py`（Zircaloy-4/SS304/Inconel-718 nominal 成分，sum=1.0，alias resolver）+ `material_policy.py`（preserve_plan/apply_alloy_library/strict_confirmed_only 三策略，默认 apply_alloy_library）；assembler 接入 policy 并产出 `material_composition_report.json`；graph/executor 透传 material_policy；新增 `scripts/compare_material_policies.py` (dry-run/OpenMC 两模式)。VERA3 3A/3B fixtures 的三合金 `composition_status` 改为 `needs_library`。新增 70 个测试覆盖 library/policy/CLI；no-OpenMC 全量 `586 passed, 1 skipped`；OpenMC-marked `71 passed`。安全边界：composition 来自公开 handbook midpoint，非 benchmark-specific。
- **Environment validation / OpenMC gating**：新增环境自检脚本、Makefile 测试分层、pytest markers 和 OpenMC importorskip gate；base Python 缺失 OpenMC 时 no-OpenMC/full 测试不再 collection error。`508 passed, 28 skipped`（base full test-all）。
- **P0 Evaluation Backbone**：扩展 EvaluationCase contract、fixture P0 cases、trace evaluator/aggregate metrics，并新增 plan-only `workflow_case_runner` + benchmark-compatible adapter；默认不跑 OpenMC。`compileall` + OpenMC-stub targeted tests。
- **云端环境配置**：新增 `environment.yml`、`Dockerfile`、`.devcontainer/devcontainer.json` 与 `docs/cloud_environment.md`，支持 Conda/Mamba、Docker、Dev Container/Codespaces 三种云端启动方式；README 增加云端环境入口。`git diff --check` + targeted pytest smoke。
- **VERA3 3A/3B 端到端成功**：两个变体均通过 incremental plan builder（真实 LLM deepseek:deepseek-chat）端到端成功运行。全部 7 个 patch 由 LLM 直接生成（不依赖 reference fixture）。3B keff=0.979，3A keff≈1.149。关键修复：assembler 自动构建 ZCylinder surfaces/regions（解决 region=None 几何缺失）；边界条件从实际几何推导（radial=reflective）；元素符号自动路由 add_element；coord_overlap 确定性修复；count_mismatch 对 overlap-repaired 组跳过；零丰度核素跳过；plot 范围从实际几何推导。`841 passed, 3 skipped`。

### 2026-07-08

- **Few-shot 双轨增强**：4 个 anonymized gold case（`data/few_shot_cases/`）接入 monolithic（slim IR+digest）+ incremental（patch 参考段，2400 字符/层预算）路径；`few_shots.py` 按结构特征堆型无关打分合并 gold case；修 `_compact_context` 静默丢弃 `requirement` 的 P0 bug。新增 `few_shot_cases.py` + `scripts/build_few_shot_case.py`。`818 passed`。
- **复杂模型默认按未见处理**：graph 默认 `reference_patch_policy="off"`，VERA3 3B 不再默认走 reference fixture；`prefer_reference_for_structural` 改为 reference 缺失/失败时继续 input-driven synthesis；pin_map count validation 忽略非 pin-map 计数字段（如 spacer grid count）。`48 + 39 targeted passed`，`compileall openmc_agent` 通过。
- **VERA3 3B reference 路由加固**：reference matcher 增加通用 benchmark id normalization（如 `VERA_PROBLEM_3 -> VERA3`）；显式 `reference_only_for_structural` 缺失或验证失败不再落回 LLM；incremental artifacts 写入前清理 stale patch/result 文件。`841 passed, 3 skipped`。
- **VERA3 3B incremental/reference 修复**：区分 partial/complete `expected_counts`，新增 deterministic actual pin counts；`grid_zircaloy4→zircaloy4` alias resolver + overlay canonicalization；VERA3 3B reference structural path 保留为显式策略并禁止 monolithic reflect fallback。`831 passed, 3 skipped`。
- **VERA3 3B expected_counts 二次加固**：assembler 最终写入前 reconcile FactsPatch 计数与 deterministic expanded pin_map；3B pattern 自洽时以 actual counts 覆盖错误 `guide_tube=24` facts，避免 `expected_counts sum 313` 阻断渲染。`832 passed, 3 skipped`。
- **VERA3 3B axial-layer material alias 加固**：material resolver 支持唯一 variant suffix 解析（如 `borated_water -> borated_water_3B`）；assembler canonicalize axial layer fill material，避免 reference structural axial layers 与 LLM material ids 命名不一致导致 skeleton/reflect。`833 passed, 3 skipped`。
- **Incremental Plan Builder Phase 0–7D**：复杂 3D assembly 从 monolithic 25K JSON 改为 7-patch 分层生成 + 确定性组装（`plan_builder/`）；graph dispatcher 路由（`use_incremental_executor=True` 默认）；reference-backed deterministic structural patches + resumable execution；真实 LLM 加固（禁不安全 monolithic fallback、strict patch-output contract、`json_schema` structured mode、attempt artifact 可见）。测试 `453→791 passed`。

### 2026-07-07

- **3D assembly guard + Level 1 overlay（Step 1–6）**：guard 阻断 3D 压扁为 2D（四个 `assembly3d.*` issue code）；`AxialOverlaySpec` IR；Level 1 `homogenized_open_region` renderer（保 pin/tube through-path）；skeleton overlay 自动提升；VERA3 acceptance foundation（reference fixture + benchmark validator + 三层 E2E 测试）。`490→581 passed`。
- **source/bounds 一致性（Step 5–6）**：source z 绑定活性燃料、xy 绑定 full footprint；plot origin 重定心修 quarter-plot bug；新增 `source_settings.py` / `geometry_bounds.py` + 17 个 runtime/bounds issue code。`549→565 passed`。
- **overlay cell 复用 bug 修复**：overlay universe 克隆 solid cell 而非复用，修 base 燃料段被抢走导致的 source rejection（真实根因）。`567 passed`。
- **专家验证工具**：`verification.py` digest（不变量检查表 + pin/axial/overlay/material/bounds）+ 3D voxel plot（ParaView 可读）。`581 passed`。
- **runtime/渲染修复**：surface 轴截距别名（`x→x0`/`y→y0`/`z→z0`）、`rectangular_prism` pitch kwarg、dangling lattice outer 降级、suppress 已配置的 `OPENMC_CROSS_SECTIONS` capability 阶段专家问题。`533→574 passed`。
- **Knowledge Asset Runtime Loader**：orchestrator GraphRAG stage 加载持久化 knowledge graph（path/env fallback，失败只 warning）；trace summary 暴露 node/edge/source/warning 计数。`474 passed`。
- **Knowledge Ingestion + GraphRAG reranker/query planner**：ingestion pipeline、evidence ranker + dedup + prompt budget、query planner + graph path scoring、docs 整理、repo-local `AGENTS.md`/`CLAUDE.md` 自动提交规则。`453 passed`。
- **case3 输入修正**：导向管/裂变室填水（C5G7 未插棒构型，避免高估 Zr 吸收）。

> 更早期的检索/retrieval/graphrag 基础设施搭建记录见 git history；本报告聚焦 2026-07-07 起的工程进展。

### P0-D/E/F Workflow Benchmark Reporting

The P0 evaluation backbone now includes a report-generating workflow benchmark entry point. `openmc_agent.workflow_benchmark.run_workflow_benchmark(...)` loads the evaluation case manifest, runs each case through the fake or opt-in workflow runner, evaluates traces, aggregates metrics, and writes `evaluation_report.json`, `benchmark_summary.md`, per-case traces, and per-case artifact summaries.

The CLI `scripts/run_workflow_benchmark.py` defaults to `model=fake`, `mode=plan-only`, rendering disabled, and OpenMC tools disabled. Non-fake models are refused unless `--allow-real-llm` is passed, keeping the no-OpenMC/no-LLM test boundary intact. The primary local command is `make benchmark-workflow-fake`.

- 2026-07-09 — P0-D/E/F workflow benchmark reporting: added workflow benchmark API, CLI report generation, fake benchmark Make target, and no-OpenMC tests for reports/traces. Validation: targeted workflow benchmark tests plus no-OpenMC suite.
## 2026-07-17 — Phase 1C canonical planning safety boundary

- Added feature-to-Facts reconciliation, one persisted canonical planning scope,
  and canonical task-plan persistence so task selection and assembly share the
  same patch-family decision.
- Added source-critical localized-insert/profile checks and grouped material
  density execution-readiness diagnostics; focused regression tests pass.
- Boundaries remain unchanged: no generic retry controller and no additional
  Placement, Material–Universe, Axial, or Final Plan LLM gate is claimed.
## 2026-07-17 — Phase 3 executable dependency retry foundation

Added protocol v0.5 typed retry requests, owner routing, a single patch
dependency graph, clone/atomic owner transaction, and resumable invalidation
ledger.  Added reactor-neutral protocol and graph tests; no new reviewer,
runtime repair, or monolithic fallback path was introduced.

## 2026-07-17 — Real-model JSON output and targeted patch recovery

- Added opt-in CLI/client controls for JSON-object/schema mode, per-call output
  tokens, and provider reasoning effort; actual structured fallback mode is
  retained in patch/review artifacts. Defaults remain provider-compatible.
- Patch-generation failure now preserves the source requirement and valid
  envelopes, resuming at the failed patch instead of contaminating every prompt
  with a full-plan correction. Hyphenated document identifiers no longer count
  as lattice dimensions. Focused regression tests: 65 passed.

## 2026-07-18 — Placement Gate deferred applicability

- Placement Gate now remains pending while candidate profile/intent patches are
  still being generated, and only becomes `skipped` after task-plan completion.
  A stale `not_applicable` checkpoint is explicitly reopened if its inputs later
  become applicable; this prevents an illegal `skipped → reviewing` transition.
- Validation: placement, incremental, graph, and controller regressions: 41 passed.

## 2026-07-18 — Axial replacement-universe dependency replay

- An axial layer that references a missing replacement universe now invalidates
  the `universes` owner, its dependency-graph descendants, and the failed
  axial consumer before Graph replay. The next pass regenerates that bounded
  path instead of skipping a stale valid `UniversesPatch` three times.
- Validation: incremental executor, retry router, graph replay, and repair
  regressions: 49 passed.

## 2026-07-18 — Axial patch admission and assembly diagnostics

- Incomplete axial intervals and unattached lattice loadings now fail patch
  validation before assembly; executor results retain the assembler's concrete
  issue codes alongside the generic failure wrapper.
- Validation: focused patch-validator and incremental-executor regressions.

## 2026-07-18 — Controlled Facts resume barrier and evidence coverage

- Coalesced line-aligned Markdown evidence paragraphs before applying the Facts
  review chunk limit; a controlled gate failure is terminal for ordinary Graph
  regeneration and cannot be bypassed by reusing a valid FactsPatch.
- Placement dependency requests now retain required universe IDs and gate-input
  hash; focused tests plus the non-OpenMC/non-real-LLM suite and fake workflow
  benchmark passed.

## 2026-07-18 — Axial JSON schema recovery and bounded controlled retry

- Axial prompts now enumerate canonical layer roles and forbid null ordering
  priorities; two exact lexical role aliases and an omitted-default priority
  are normalized with attempt-level audit records only.
- Controlled Graph execution stops after the failed patch's local budget is
  exhausted, retains its checkpoint/candidate hashes, and does not reopen a
  whole-plan retry. Validation: focused generator, executor, and Graph tests.

## 2026-07-18 — Executable closed-loop CLI defaults

- The CLI now defaults to controlled incremental planning with all currently
  executable gates enabled: Facts, Material–Universe, and Placement. Per-gate
  modes inherit the selected loop mode; patch output remains provider `auto`.
- Axial and Assembled Plan gate IDs remain opt-in/unimplemented rather than
  causing a default modeling command to fail. Validation: CLI default tests.
