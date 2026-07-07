# OpenMC-Agent 技术报告与进度总览

维护日期：2026-07-07  
维护方式：每完成一个重要工程 Step 后更新本报告的“当前状态”“验证结果”“风险/边界”“下一步建议”和“变更日志”。

## 1. 项目定位

OpenMC-Agent 的目标是把自然语言反应堆建模需求转成可审查、可校验、尽可能可运行的 OpenMC Python 模型。系统的核心安全边界是：

- LLM 只生成结构化 `SimulationPlan`，不直接写最终运行代码。
- 本地 Pydantic schema、validator、renderer 和 OpenMC 工具链负责校验、渲染、导出和 smoke test。
- 缺失材料密度、composition、核数据库路径、benchmark 常数、真实装载图等事实缺口必须保留 human confirmation，不能由 RAG/GraphRAG 自动补全。

当前仓库已经从“结构化建模 agent”扩展成“带诊断闭环、检索编排、GraphRAG、知识注入、trace/evaluation 和 benchmark 基础设施”的中大型工程。

## 2. 代码规模

截至本报告维护时：

- `openmc_agent/` Python 文件：46 个，约 24,500 行。
- `tests/` Python 测试文件：29 个，约 14,170 行。
- 文档文件：12 个，包括 `docs/README.md`、本报告和 10 个活跃策略文档，覆盖 grep、knowledge graph、RAG、GraphRAG、ingestion、ranking、query planner、trace/evaluation、benchmark 等。
- 最近全量测试：`453 passed in 43.05s`（本轮先完成 renderer 定向回归：`28 passed in 1.54s`）。

## 3. 架构总览

核心流程：

```text
User requirement
  -> LLM structured SimulationPlan
  -> validation / capability assessment
  -> structured issues
  -> retrieval orchestrator
      -> grep
      -> graph
      -> GraphRAG query planner
      -> GraphRAG retriever
      -> plain RAG
      -> evidence ranker / dedup / budget
  -> reflect_plan / ask_expert / auto_repair
  -> renderer
  -> export_xml / plot / smoke test
  -> trace / records / evaluation
```

主要模块职责：

| 模块 | 职责 |
| --- | --- |
| `schemas.py` | Pydantic IR：`SimulationPlan`、materials、geometry、lattice、capability、issues。 |
| `graph.py` | LangGraph workflow：plan 生成、validate、capability、repair、ask_expert、render、tools、trace。 |
| `validator.py` / `lattice_validation.py` | 结构校验、pin map / lattice 诊断、错误码生成。 |
| `error_catalog.py` | 稳定 issue taxonomy、route hints、repair/retrieval/human confirmation hints。 |
| `auto_repair.py` | deterministic patch，优先于 LLM reflect。 |
| `grep_search.py` | 受控 grep 检索，输出 `RetrievedEvidence(source_type="grep")`。 |
| `knowledge_graph.py` / `knowledge_graph_registry.py` | hand-written graph registry + `graph_lookup` + `GraphContext`。 |
| `rag_search.py` | 本地 lexical RAG：文档 chunk/index/search/evidence。 |
| `graphrag_retriever.py` | Graph-guided RAG：graph expansion -> RAG query -> `graphrag` evidence。 |
| `graphrag_query_planner.py` | 新增：issue intent 分类、expansion policy、graph path scoring、preferred query/filter 生成。 |
| `knowledge_ingestion.py` | 本地 docs/examples/Input ingestion，规则式 annotation，chunk -> graph nodes/edges，JSON/JSONL 输出。 |
| `retrieval_orchestrator.py` | 统一 grep/graph/GraphRAG/RAG/ranking 编排，生成 prompt retrieval context。 |
| `evidence_ranker.py` | 新增：evidence scoring、dedup、per-source budget、prompt total budget、ranked block。 |
| `workflow_trace.py` | trace model、recorder、JSON/JSONL export、workflow summary helpers。 |
| `evaluation.py` / `benchmark_runner.py` | trace-based evaluation case/result/metrics、benchmark 和 ablation runner。 |
| `renderers/` | PinCell、RectAssembly、Core、TRISO、Skeleton 渲染器和 registry。 |
| `tools.py` | OpenMC export_xml、plot、smoke test 子进程工具。 |

## 4. 已完成能力

### 4.1 诊断闭环

已完成：

- runtime / export_xml / hex lattice 错误码化。
- `ValidationIssue` 结构化。
- `ToolResult.issues` 接入 workflow。
- `reflect_plan` / `ask_expert` 按 issue route 处理。
- hex lattice 仍保持 skeleton，不伪装 runnable。

当前意义：workflow 不再只有自然语言错误，而是有稳定 issue code、schema path、concept id、route hint、retrieval/human confirmation 标记。

### 4.2 检索层

已完成：

- grep 搜索层：精确定位代码、测试、示例和文档片段。
- graph 层：维护 schema/concept/API/doc/error/repair 关系。
- RAG 层：本地文档 chunk + lexical scoring，无外部服务。
- GraphRAG Retriever：从 issues 走 graph expansion，再生成 RAG request。
- GraphRAG Query Planner：按 issue intent 规划 start nodes、depth、filters、preferred queries。
- Evidence Ranker：合并、去重、评分和 prompt budget。

当前默认检索链：

```text
issues
  -> grep
  -> graph
  -> GraphRAG query planner
  -> GraphRAG
  -> plain RAG
  -> merge
  -> evidence ranker
  -> reflect_plan prompt
```

默认策略倾向于“先检索、再判断”：manual review 和 fact gap 也会触发文档/GraphRAG 检索，用来解释 API、配置方式和项目上下文；但 human confirmation 标记仍然保留，不能由 evidence 自动补齐材料密度、composition、核数据库路径、benchmark 常数或真实 loading map。

### 4.3 Knowledge Ingestion

已完成：

- `Input/knowledge_sources.json` 默认 manifest。
- 支持扫描 `docs/`、`examples/`、`openmc_docs/`、`openmc_examples/`、`Input/`、`README.md`。
- 复用 RAG chunking。
- 规则式 annotation：materials、geometry、lattice、settings/runtime、benchmark/input concepts。
- chunk -> graph nodes/edges。
- JSON/JSONL 输出：
  - `knowledge_chunks.json`
  - `knowledge_chunks.jsonl`
  - `knowledge_graph_nodes.json`
  - `knowledge_graph_edges.json`
  - `knowledge_summary.json`
- CLI：

```bash
python -m openmc_agent.knowledge_ingestion \
  --manifest Input/knowledge_sources.json \
  --output data/knowledge
```

注意：ingestion graph 现在通过 Knowledge Asset Runtime Loader 自动接入默认 workflow（orchestrator 在 GraphRAG stage 加载 `data/knowledge`，详见 `docs/knowledge_runtime_strategy.md`）。

### 4.4 Trace / Evaluation / Benchmark

已完成：

- `TraceRecorder`、`WorkflowTrace`、`TraceEvent`。
- validation、retrieval、auto-repair、reflect、ask_expert、render/export/smoke 等摘要事件支持。
- `EvaluationCase` / `EvaluationResult` / `EvaluationMetrics`。
- benchmark runner 和 ablation runner 支持 fake runner、JSON/JSONL/Markdown 输出。

当前意义：已经具备后续做 ablation study 和真实 workflow benchmark 的数据结构基础。

### 4.5 Renderer 与能力边界

当前 renderer：

- PinCell
- RectAssembly
- Core
- TRISO
- Skeleton fallback

**3D assembly workflow guard**（`openmc_agent/assembly3d_guard.py`）：requirement 级 detector 扫描通用轴向信号（axial layers / spacer grid / explicit z 范围 / nozzle / plenum / control rod insertion / 中文"定位格架""轴向反射"等），plan 级 validator 在 plan validation 阶段就检查"3D 需求是否被压扁成 2D assembly"。四个 `assembly3d.*` issue code 覆盖：缺 axial_layers、默认 z=-1..1 伪 3D、spacer grid 被建成整层 material slab、grid layer 丢失 pin/tube through-path。触发即降级为 skeleton / human confirmation，不会产出"看似可导出但物理错误"的模型。该模块不含任何 benchmark 专用事实。

明确未完成或受限：

- HexAssemblyRenderer 未实现。
- depletion / burnup 未实现。
- pebble_bed renderer 未实现。
- 对其他复杂 benchmark 的 modeling fidelity 仍不能自动保证。
- `AxialOverlaySpec` 与 Level 1 spacer-grid overlay renderer 未实现：当前 spacer grid 只能作为 derived lattice（loading overlay）安全表达，或被 guard 拦下要求人工确认；尚未支持 volume-fraction calibrated 的 homogenized overlay。
- fact gap 仍必须走 ask_expert / human confirmation。

## 5. 当前 Retrieval/GraphRAG 状态

### Query Planner

`plan_graphrag_query(...)` 会把 issues 分类为：

- `schema_repair`
- `runtime_diagnosis`
- `export_xml_repair`
- `lattice_map_repair`
- `renderer_capability`
- `documentation_lookup`
- `fact_gap_review`
- `benchmark_interpretation`
- `unknown`

优先级：

```text
fact_gap_review
> export_xml_repair
> runtime_diagnosis
> lattice_map_repair
> renderer_capability
> schema_repair
> benchmark_interpretation
> documentation_lookup
> unknown
```

Planner 输出：

- `GraphRagQueryIntent`
- `GraphExpansionPolicy`
- start nodes
- preferred queries
- required filters
- avoided queries
- planned paths

### Evidence Ranker

`rank_and_select_evidence(...)` 会：

- dedup same locator / same doc_chunk_id / near-duplicate text；
- grep exact match 优先；
- graph relationship 次之；
- GraphRAG 优先于 plain RAG；
- 根据 issue/schema/concept/API/graph path/ingested node 加分；
- 对 fact gap unsafe evidence 降分；
- 控制每类 evidence 数量和 prompt 总字符数。

### Prompt 输出

默认有 ranking 结果时：

```text
[GraphRAG Query Plan]
[Graph Context]
[Ranked Evidence]
[Evidence Safety Constraints]
```

这比原来直接 dump grep/graph/GraphRAG/RAG section 更紧凑。

## 6. 验证状态

最近验证：

```bash
conda run -n openmc-env python -m pytest -q
# 490 passed in 46.22s

conda run -n openmc-env python -m compileall -q openmc_agent
# passed
```

本轮新增或重点覆盖：

- 3D assembly guard：detector（`detect_assembly_3d_features`）+ plan validator（`validate_assembly3d_plan`，经 `validate_simulation_plan(plan, requirement=...)` 接入 graph）+ renderer 复用（`assembly3d_grid_layer_issues`）+ 四个 `assembly3d.*` issue code 的六场景测试。
- GraphRAG query planner tests。
- GraphRAG retriever regression。
- retrieval orchestrator integration。
- evidence ranker regression。
- knowledge ingestion regression。
- workflow trace summary regression。

## 7. 当前已知边界和风险

### 7.1 工作区风险

当前工作区存在一些未纳入本次工程提交范围的脏文件：

- `Input/VERA1_problem.md` 到 `Input/VERA5_problem.md`
- `Input/CASL-U-2012-0131-004.pdf`
- `_verify_cli.py`

这些看起来像用户维护的输入资料或临时验证脚本。提交工程代码时应避免误提交，除非明确要把它们纳入 benchmark/input corpus。

### 7.2 技术风险

- Retrieval/GraphRAG 体系已经比较完整，但还没有默认加载 ingestion 输出的持久化 graph。
- Query planner 和 evidence ranker 是 heuristic，未经过真实 benchmark 权重校准。
- Benchmark runner 已有，但还没有真实 workflow case runner。
- Trace 已有，但没有 persistent trace store 或 dashboard。
- Renderer 能力边界仍是建模质量的主要瓶颈，而不是检索能力。
- 3D axial assembly 的 guard 已落地（阻断 3D 需求被压扁为 2D 导出，四个 `assembly3d.*` issue code），但真正的 spacer-grid overlay / volume-fraction homogenization 尚未实现；VERA3 等三维组件仍只能作为后续验收 benchmark，不作为可导出目标。

### 7.3 安全边界

RAG / GraphRAG / ingested docs / ranked evidence 都只能作为上下文：

- 不能自动确认 nuclear data path。
- 不能自动确认材料密度或 composition。
- 不能自动确认 benchmark constants。
- 不能自动补齐真实 loading map。

## 8. 下一步建议

**Knowledge Asset Runtime Loader + Retrieval Config 已完成（2026-07-07，见维护记录）**：orchestrator 在 GraphRAG stage 通过 `RetrievalPolicy.knowledge_graph_path` 或 `OPENMC_AGENT_KNOWLEDGE_DIR` 加载持久化 knowledge graph，作为 `extra_nodes/extra_edges` 注入；加载失败只产生 warning 不中断 workflow；trace summary 暴露 node/edge/source/warning 计数；GraphRAG evidence 带 `knowledge_runtime_loaded` / `knowledge_graph_path` 标记。详见 `docs/knowledge_runtime_strategy.md`。

第一优先级：真实 evaluation case runner。

- 把现有 benchmark runner 从 fake trace 推进到可调用 lightweight workflow。
- 先不跑 OpenMC 大仿真，只跑 plan/validate/retrieval/capability。
- 用 trace 评估：
  - retrieval trigger rate
  - fact gap preservation
  - skeleton/runnable classification
  - issue code precision/recall

第三优先级：Renderer / fidelity。

- 对 VERA/C5G7 类 benchmark，检索已经能提供上下文，但最终可信度取决于 renderer 能否表达结构。
- 建议先做 RectAssembly/Core 的 loading map fidelity checks，而不是马上实现 HexAssemblyRenderer。

**3D assembly / spacer-grid overlay（Step 2 已完成 2026-07-07）**：

- Step 1：通用 3D assembly workflow guard 已落地，3D 需求被 2D assembly 吞掉时在 plan validation 阶段即拦截。
- Step 2（本次）：`AxialOverlaySpec` IR + `core.axial_overlays` 落地；guard 不再把"含 grid 字样的长燃料区 lattice layer"误判为 spacer grid slab；through-path check 不再误伤 lattice 填充层；新增 4 个 overlay issue code；renderer `can_render` 接入 overlay 检查；产物持久化 bug 修复。LLM 现在可以用 `geometry_mode='skeleton'` + `requires_human_confirmation` 诚实表达 VERA3 格架，而不再卡死或产出物理错误的 material slab。
- Step 3（下一步）：实现 Level 1 `homogenized_open_region` overlay renderer，保留 pin/tube through-path。
- Step 4：volume-fraction calibrated overlay。
- Step 5：VERA3 end-to-end benchmark acceptance。
- 在 Step 3 落地前，VERA3 只作为验收 benchmark，overlay 仍降级为 skeleton。

## 9. 维护记录

### 2026-07-07（Step 2：AxialOverlay IR + guard 误报修复 + 产物持久化）

完成并验证：

- **修正 assembly3d guard 对 VERA3 spacer grid 的误报**。原 `_layer_looks_like_grid` 只要 id/name/purpose 含 "grid" 就把整层当 spacer grid slab，导致 VERA3 的 365 cm `layer_fuel_region`（lattice 填充、purpose 注释提到 embedded grids）被误判，触发 `assembly3d.pin_through_path_missing`，LLM 重试 3 轮无法修复。新判定分两层：
  - `layer_mentions_grid(layer)`：仅文本弱信号。
  - `layer_is_spacer_grid_slab_candidate(layer)`：保守判定——id/name 含明确 grid-slab 短语（`spacer grid`/`support grid`/`grid strap`/`grid slab`/`定位格架`…），或薄 z-band（≤5 cm）且 id/name 提到 grid/spacer；**忽略 purpose**（"fuel region with embedded grids" 不算 slab），**tall lattice 填充层永不判为 slab**。
- **修正 through-path check**。`_grid_layer_lacks_through_path` 不再因 `fill.type=='lattice'` + `loading_id is None` 判定缺 through-path——lattice 填充本身保留 pin 穿透；`loading_id` 仅在叠加 grid 材料时需要，且必须 resolve 到已声明的 `LatticeLoadingSpec`。
- **新增 `AxialOverlaySpec` IR**（`schemas.py`）+ `CoreSpec.axial_overlays`。声明式表达 spacer grid / support plate / absorber insert 等"叠加在 lattice 之上的薄层结构"：`overlay_kind` / `z_min_cm` / `z_max_cm` / `target_lattice_id` / `material_id` / `geometry_mode`（`skeleton`|`homogenized_open_region`|`annular_shell`|`explicit_bars`|`volume_fraction_calibrated`）/ `through_path_preserved` / `through_universe_ids` / `volume_fraction` / `effective_density_g_cm3` / `requires_human_confirmation`。schema 校验：non-skeleton 必须有 z_min/z_max；其余 domain/target/through-path 由 guard 检查。
- **新增 4 个 issue code**（`error_catalog.py`）：
  - `assembly3d.spacer_grid_overlay_required`（requirement 有 spacer grid 但 plan 无 overlay 也无安全 slab → `reflect_plan`）。
  - `assembly3d.axial_overlay_invalid_range`（z 缺失/反转/与 axial domain 不相交）。
  - `assembly3d.axial_overlay_missing_target`（non-skeleton overlay 的 `target_lattice_id` 缺失或不 resolve）。
  - `assembly3d.axial_overlay_requires_renderer_support`（overlay 请求 renderer 尚未实现的 geometry_mode，或 skeleton overlay → 审查降级）。
- **guard 拆分 requirement-agnostic / aware**：`assembly3d_overlay_issues(model)`（renderer `can_render` 与 validator 共享）+ `axial_overlay_issues(model, flags)`（额外含 `spacer_grid_overlay_required`）。`renderers/assembly.py` 的 `_axial_assembly_modeling_errors` 现在同时跑 slab 与 overlay 检查，保证 renderer 也对 overlay 降级。
- **更新 `prompts.py`**：明确告诉 LLM spacer grid 必须用 `core.axial_overlays` 表达，禁止用 material slab 或 purpose 注释糊弄。
- **修复产物持久化 bug**（`graph.py`）：`_render_plan_script` / `_render_script` 开头调用新增的 `_clean_stale_render_artifacts(output_dir)` 清理上一轮的 model.py / XML / capability_report.json / TODO.md / statepoint h5 / plots/；当 render 因 plan 无效或无 renderer 而跳过时，调用 `_write_non_executable_marker` 写出诚实的 NOT_EXECUTABLE `capability_report.json` + `TODO.md`，杜绝旧 exportable 产物冒充本轮成功结果。run record（simulation_plan.json / transcript.json / plan_artifacts/ / checkpoints.sqlite / inspect_runs.jsonl）不动。
- 全量测试：`499 passed`（新增 9 个 assembly3d overlay/persistence 场景）。

安全边界保持：renderer 仍不生成假 overlay 几何；任何 overlay（skeleton 或更高 fidelity）都降级为 review-only skeleton；VERA3 facts 未固化进生产代码。

仍未完成（明确留给后续 Step）：

- **Step 3**：Level 1 `homogenized_open_region` overlay renderer（保留 pin/tube through-path，在 coolant/open region 等效填充 grid 材料）。
- **Step 4**：volume-fraction calibrated overlay。
- **Step 5**：VERA3 end-to-end benchmark acceptance（在 Step 3 落地后才作为可导出目标）。

### 2026-07-07（case3 输入修正 + 项目自动提交偏好）

完成：

- **`Input/case3.md` 材料修正**：将导向管 `guide_tube` 与裂变室 `fiss_chamber` 由"实心 Zircaloy-4 圆柱（r=0.54）"改为"实心 water 圆柱（r=0.54）"。经典 C5G7 未插棒构型下导向管 / 中心测量管内部为水，仅有薄壁 Zircaloy-4 管；原建模把整根棒填成 Zircaloy-4 会显著高估 Zr 吸收并损失局部慢化，偏离 C5G7 物理图像。沿用 case3 既有的"单材料均质棒"简化（燃料棒亦不建包壳 / 气隙），故取体积占优的水作为均质填充材料。Zircaloy-4 材料定义保留但标注当前未使用，供未来引入包壳 / 管壁结构时启用。栅元半径、pin 计数与 lattice 结构不变（导向管 96、裂变室 4，活性棒位总数 1156 不变）。
- **项目自动提交偏好显式化**：在 `AGENTS.md` 与 `CLAUDE.md` 顶部新增显式声明——本仓库默认开启自动 commit/push，覆盖全局 `~/.claude/CLAUDE.md` 中"不自动提交除非明确要求"的默认，消除项目级与全局偏好之间的歧义。

验证：

- 本次为文档 / 规则 / 输入规格变更，未改动 Python 代码，按仓库规则运行 `git diff --check -- <paths>` 轻量检查（未跑全量 pytest）。

### 2026-07-07（3D assembly workflow guard — Step 1）

完成并验证：

- **通用 3D assembly workflow guard**（`openmc_agent/assembly3d_guard.py`），阻止 3D axial assembly 需求被错误降维成 2D assembly 并被标记为 exportable。
  - `Assembly3DFeatureFlags` + `detect_assembly_3d_features(requirement)`：requirement 级 detector，接受 `str | dict | 对象`，扫描通用轴向信号（3D assembly / axial layer / axial heterogeneity / spacer grid / grid strap / mixing vane / support grid / nozzle / end plug / plenum / fuel stack height / control rod insertion / explicit z 范围 / `z_min`/`z_max` / `from X cm to Y cm` / 中文"三维""定位格架""轴向反射"等），输出 `has_axial_geometry` / `has_spacer_grid` / `has_explicit_z_ranges` / `has_axial_components` / `matched_terms`。不含任何 benchmark 专用事实。
  - `validate_assembly3d_plan(plan, requirement)` + `assembly3d_grid_layer_issues(model)`：plan 级 validator，在 plan validation 阶段（`validate_simulation_plan(plan, requirement=...)`，graph `_validate_plan` 传 `state["requirement"]`）即检查；renderer `can_render` 通过 `assembly3d_grid_layer_issues` 复用同一套 slab/through-path 判定，单一来源。
  - 四个稳定 issue code：
    - `assembly3d.axial_layers_required`（requirement 有 axial 信号但 plan 缺 `core.axial_layers`，`route_hint=reflect_plan`）。
    - `assembly3d.default_z_extent_for_axial_problem`（有 explicit z 范围但 plan 仍会渲染默认 z=-1..1 unit slab，`capability_downgrade`）。
    - `assembly3d.spacer_grid_material_slab`（grid layer fill 是单一 material，`capability_downgrade`）。
    - `assembly3d.pin_through_path_missing`（grid layer 无法证明保留 fuel/guide/instrument tube through-path，`capability_downgrade`）。
  - `error_catalog.py` 注册以上四个 code 的 severity / knowledge_refs / repair_hints / route_hint / grep_patterns。
  - `renderers/assembly.py` 的 `_axial_assembly_modeling_errors` 委托给 guard。
  - 既有 2D assembly 路径不受影响（六场景测试 + 全量 `490 passed`）。

仍未解决（明确留给后续 Step）：

- 尚未实现 `AxialOverlaySpec`。
- 尚未实现 Level 1 spacer-grid overlay renderer。
- 尚未实现 volume-fraction calibrated overlay。
- VERA3 仍只作为后续验收 benchmark，不是本 Step 的可导出目标。

### 2026-07-07（续）

完成并验证：

- **Knowledge Asset Runtime Loader + Retrieval Config**。
  - 新增 `openmc_agent/knowledge_runtime.py`：`KnowledgeGraphStore` / `KnowledgeGraphLoadConfig` / `load_knowledge_graph_store`，支持显式 path -> `OPENMC_AGENT_KNOWLEDGE_DIR` env -> unloaded 三级 fallback；缺失路径 / 损坏 JSON / 超限节点边均只产生 warning。
  - `RetrievalPolicy` 新增 `enable_knowledge_graph_loading` / `knowledge_graph_path` / `max_knowledge_nodes` / `max_knowledge_edges` / `allow_missing_knowledge_path`。
  - `RetrievalContext` 新增 `knowledge_graph_summary` / `knowledge_graph_warnings`（只存 summary，不存完整 nodes/edges）。
  - Orchestrator 在 GraphRAG stage 加载 store 一次，传入 `extra_nodes/extra_edges`；GraphRAG disabled 时不加载。
  - `GraphRagRequest.runtime_knowledge` + `_annotate_runtime_knowledge` 给来自 ingested doc_chunk 的 evidence 补 `knowledge_runtime_loaded` / `knowledge_graph_path`（不覆盖已有键）。
  - `workflow_trace.summarize_retrieval_context` 暴露 `knowledge_graph_attempted/loaded/node_count/edge_count/source_ids/warning_count`；`retrieval_completed` event metadata 同步。
  - `build_plan_graph(knowledge_graph_path=..., retrieval_policy=...)` + inspect CLI `--knowledge-dir` 接入。
  - 新增 `docs/knowledge_runtime_strategy.md`。
  - 全量测试通过：`474 passed`。

### 2026-07-07

完成并验证：

- Knowledge Ingestion Pipeline。
- GraphRAG Evidence Reranker + Dedup + Prompt Budgeter。
- GraphRAG Query Planner + Graph Path Reranking。
- docs 文件夹整理：删除早期 Phase 0 盘点/草案/单问题计划，新增 `docs/README.md`，并更新 retrieval/RAG/graph/trace/benchmark 文档到当前 GraphRAG + ranking 状态。
- 新增 repo-local agent 维护规则：`AGENTS.md`（Codex）和 `CLAUDE.md`（Claude），要求代码改动测试通过后自动 commit/push，并同步维护 `README.md` 与本技术报告。
- 调整默认检索策略：默认开启 grep/graph/RAG/GraphRAG/query planner/evidence ranking；manual review 和 fact gap 默认也做文档检索，但仍保留 human confirmation。
- 全量测试通过：`453 passed in 43.05s`。

新增核心文档：

- `docs/knowledge_ingestion_strategy.md`
- `docs/evidence_ranking_strategy.md`
- `docs/graphrag_query_planner_strategy.md`
- `docs/project_technical_report.md`

下一步推荐：

1. Knowledge Asset Runtime Loader + Retrieval Config。
2. 真实 workflow evaluation runner。
3. Renderer fidelity / loading map validation 增强。
