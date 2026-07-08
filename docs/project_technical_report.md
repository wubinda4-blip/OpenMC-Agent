# OpenMC-Agent 技术报告与进度总览

维护日期：2026-07-08

维护方式：每完成一个重要工程 Step 后更新本报告的"当前状态""验证结果""风险/边界""下一步建议"和"维护记录"。**维护记录使用精炼风格**：每条 2–4 行（日期 + 主题 + 核心改动 + 测试数），不写冗长根因/实现细节（那些在代码与 git history 里）。

## 1. 项目定位

OpenMC-Agent 的目标是把自然语言反应堆建模需求转成可审查、可校验、尽可能可运行的 OpenMC Python 模型。系统的核心安全边界是：

- LLM 只生成结构化 `SimulationPlan`，不直接写最终运行代码。
- 本地 Pydantic schema、validator、renderer 和 OpenMC 工具链负责校验、渲染、导出和 smoke test。
- 缺失材料密度、composition、核数据库路径、benchmark 常数、真实装载图等事实缺口必须保留 human confirmation，不能由 RAG/GraphRAG 自动补全。

当前仓库已经从"结构化建模 agent"扩展成"带诊断闭环、检索编排、GraphRAG、知识注入、incremental 分层 plan 生成、few-shot 双轨、trace/evaluation 和 benchmark 基础设施"的中大型工程。

## 2. 代码规模

截至本报告维护时：

- `openmc_agent/` Python 文件：62 个，约 34,800 行。
- `tests/` Python 测试文件：53 个，约 22,600 行。
- 文档：本报告 + 10 个活跃策略文档（grep/knowledge graph/RAG/GraphRAG/ingestion/ranking/query planner/trace-evaluation/benchmark/knowledge runtime）。
- 最近全量测试：`818 passed, 3 skipped in ~60s`。

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

## 5. 当前 Retrieval/GraphRAG 状态

### Query Planner

`plan_graphrag_query(...)` 把 issues 分类为 `schema_repair` / `runtime_diagnosis` / `export_xml_repair` / `lattice_map_repair` / `renderer_capability` / `documentation_lookup` / `fact_gap_review` / `benchmark_interpretation` / `unknown`，按优先级输出 `GraphRagQueryIntent` / `GraphExpansionPolicy` / start nodes / preferred queries / required filters / avoided queries / planned paths。

### Evidence Ranker

`rank_and_select_evidence(...)`：dedup same locator/doc_chunk_id/near-duplicate；grep exact > graph relationship > GraphRAG > plain RAG；按 issue/schema/concept/API/graph path/ingested node 加分；fact gap unsafe evidence 降分；控制每类数量和 prompt 总字符数。

### Prompt 输出

默认有 ranking 结果时：`[GraphRAG Query Plan] [Graph Context] [Ranked Evidence] [Evidence Safety Constraints]`。

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
- 材料/合金 composition 仍多为 approximate（Zircaloy-4 / SS-304 / Inconel-718 简化为纯元素 + warning）。
- patch few-shot 仅 3D assembly case 有；其余 case 的 incremental 路径用泛型 `_PATCH_RULES`。
- Query planner / evidence ranker 是 heuristic，未真实 benchmark 权重校准。
- Benchmark runner 还没推进到真实 workflow case runner；无 persistent trace store / dashboard。

### 7.3 安全边界

RAG / GraphRAG / ingested docs / ranked evidence / few-shot 都只能作为上下文：不能自动确认 nuclear data path、材料密度/composition、benchmark constants、真实 loading map。few-shot 数值为 illustrative reference，不是事实确认来源。

## 8. 下一步建议

1. **真实 evaluation case runner**：从 fake trace 推进到 lightweight workflow（plan/validate/retrieval/capability，不跑大仿真），评估 retrieval trigger rate / fact gap preservation / skeleton-runnable 分类 / issue code precision/recall。
2. **材料/合金 composition fidelity**（Step 7）：真实 composition library 替代 approximate 纯元素。
3. **Volume-fraction calibrated overlay**（Step 8）：grid 体积/质量标定。
4. **Full VERA3 keff benchmark acceptance**（Step 9）：端到端 keff 验收。
5. **patch few-shot 补齐**：从 IR 反推 VERA2A / C5G7 / pin_cell 的 patch few-shot。
6. **真实 LLM incremental 稳定性**：多模型 ablation。

## 9. 维护记录

> 精炼风格：每条 2–4 行（日期 + 主题 + 核心改动 + 测试数）。详细根因/实现见代码与 git history。

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
