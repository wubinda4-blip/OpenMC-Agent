# OpenMC-Agent 技术报告与进度总览

维护日期：2026-07-12

维护方式：每完成一个重要工程 Step 后更新本报告的"当前状态""验证结果""风险/边界""下一步建议"和"维护记录"。**维护记录使用精炼风格**：每条 2–4 行（日期 + 主题 + 核心改动 + 测试数），不写冗长根因/实现细节（那些在代码与 git history 里）。

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

### 2026-07-13

- **Incremental Axial-Universe Dependency Recovery**：当 `axial_layers` 的 lattice transformation 引用了未定义 replacement universe 时，executor 提取缺失 ID，保留有效 facts/materials，并仅失效/再生 `universes` 与其下游 pin-map/axial patches；下一轮 universe prompt 显式携带所需 profile ID。修复了此前全量 7-patch regeneration 反复消耗 retry budget 的依赖环；不启用 benchmark reference、gold few-shot 或 monolithic fallback。1 新测试；非-openmc `1155 passed`，benchmark `21/21`。

- **Concentric Pin/Tubing Closure（修复 VERA3B lost particles）**：assembler 现在按显式 `r_min_cm>0` 生成 annulus，即使 LLM 将 gap/clad 标成 `cylinder`；对有径向外边界、但未声明 background cell 的 repeated universe，确定性注入输入材料 role=`coolant`/`moderator` 的外部 moderator cell，避免 lattice pitch 内未定义区域。根因：最新 VERA3B LLM plan 将 gap/clad 渲染成嵌套实心圆柱且漏掉外部 coolant，粒子穿越 clad 外径 surface 3 后无法定位。真实 VERA3B 重建输运完成（keff=0.99345±0.00592，0 lost particles）。2 新测试；非-openmc `1154 passed`，benchmark `21/21`。

- **Renderer nuclide-name normalization（修复 B-10 transport abort）**：executor 新增 reactor-neutral `_normalize_nuclide_name()`，将 GND 连字符核素名（`B-10`/`U-235m`/`B-10.71c`）统一改写为 OpenMC HDF5 库约定（`B10`/`U235m`/`B10.71c`），在全部 5 个 `add_nuclide` 出口（2 live 构造 + 3 render emit）应用；纯格式化，不改成分/密度。根因：OpenMC 0.15.x 原样存储核素名，本机 `endfb-vii.1-hdf5` 库仅含无连字符条目，导致 VERA3B 硼水/Pyrex 的 `B-10` 在输运期 `Could not find nuclide` MPI_ABORT。真实 VERA3B 端到端：smoke exit=0、nuclide 错误消除、keff≈0.951。4 新测试；非-openmc `1150 passed`，OpenMC executor `49 passed`，benchmark `21/21`。

- **P0-FINAL + FC-MVP：工程基线闭合 + 全堆 MVP**：VERA3 reference Table P3-3 精确原子数密度（atom/barn-cm）替换全部存根材料；新增 `atom_density_barn_cm` composition_basis + `set_density('sum')` 支持；executor 扩展原子质量表（60+ 核素）用于 outer-frame density 反算。VERA3 3A/3B transport smoke 通过（3A keff=1.17547±0.005 Δ=0.025%，3B keff=0.99487±0.006 Δ=0.53%）。Reusable assembly universe（boundary=transmission, x/y bounds stripped）支持 core lattice 嵌套；3×3 full-core MVP transport smoke 通过（keff=0.99452±0.005, leakage=4.6%, 0 lost particles, assembly tally 9 entries center-peaked P/A=1.35）。非-openmc `1147 passed`，OpenMC `380 passed`，benchmark `21/21`。

- **P0-V4 Variant-Specific Nozzle and Core-Plate Homogenized Mixtures**：新增 `MaterialSpecPatch.mixture_components`/`variant_scope`/`derivation_method` 和 `ComplexMaterialSpec.mixture_component_ids`/`mixture_volume_fractions`/`is_mixture` schema 支持；executor 新增确定性 volume-fraction 展平（`_flatten_volume_mixture` 将 atom_frac/weight_frac 组件统一转为 weight_frac 混合，不依赖 `openmc.Material.mix_materials`）。3A/3B 各新增 3 个 variant-specific mixture（lower_nozzle f_SS304=0.27922 / upper_nozzle f_SS304=0.19147 / core_plate 50-50）；4 层 axial layer fill 从纯 SS304 迁移至 variant mixture；renderer/executor density+composition 校验跳过 mixture 材料。28 新测试；非-openmc `1117 passed`，OpenMC `380 passed`，benchmark `21/21`；3A/3B geometry 0 overlaps / 0 lost particles。

- **P0-V3 Spacer-Grid Mass-Conserving Outer-Frame Geometry**：新增 `mass_conserving_outer_frame` geometry_mode（Level 2 overlay）和 reactor-neutral `outer_frame_overlay.py` planner（`derive_mass_conserving_outer_frame` 确定性计算 frame area/thickness/mass conservation + clearance check）；executor 新增 outer-frame 发射（4 XPlane/YPlane + inner region + frame cell + 保留 background moderator 至 inner square）。3A/3B 8 个 spacer-grid 从 `homogenized_open_region` 迁移至 `mass_conserving_outer_frame`（end 1017g Inconel-718 / mid 875g Zircaloy-4 / 289 cells）；新增 schema 字段 `total_mass_g`, `cell_count`, `pitch_cm`, `material_density_source`, `mass_tolerance_rel`。38 新测试；非-openmc `1117 passed`，OpenMC `380 passed`，benchmark `21/21`；3A/3B geometry 0 overlaps / 0 lost particles。

- **P0-V2 VERA3B Pyrex Upper-Gas Axial Profile**：新增 `pyrex_upper_gas_inner_profile` universe（SS304 内/外管保留、氦气腔替代毒物+间隙、水隙背景保留）和 `pyrex_upper_gas_loading`（16 坐标复用毒物段）；6 层轴向层（376.441–397.510 cm）更新为含 upper-gas 的多 loading 组合；geometry contract v1→v2（双 axial profile + conflict resolved）。修复多 loading 物化时共享 `derived_lattice_id` 导致 lattice ID 碰撞的 reactor-neutral 问题（`materialize_axial_lattice_transformations` 追加唯一后缀）。41 新测试；非-openmc `1079 passed`，OpenMC `380 passed`，benchmark `21/21`。

- **P0-V1 Fuel Helium Gap & Upper Plenum Correction**：新增燃料棒氦气间隙 (0.4096–0.418 cm) 到 fuel_pin universe；修正 plenum 气体 r_max (0.4096→0.418) 和 clad r_min (0.4096→0.418)；新增 reactor-neutral `radial_profile_validation.py` validator（间隙/重叠/背景检测）；集成到 patch validator。修复 7 个 P0-D5B 预存测试失败（frozen 回归 fixture）。44 新测试；非-openmc `1038 passed`，OpenMC `380 passed`，benchmark `21/21`。

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
