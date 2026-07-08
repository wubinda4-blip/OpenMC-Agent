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

这些看起来像用户维护的输入资料。提交工程代码时应避免误提交，除非明确要把它们纳入 benchmark/input corpus。

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

**3D assembly / spacer-grid overlay（Step 6 已完成 2026-07-07）**：

- Step 1：通用 3D assembly workflow guard。
- Step 2：`AxialOverlaySpec` IR + guard 误报修复 + 产物持久化。
- Step 3：Level 1 `homogenized_open_region` overlay renderer。
- Step 4：VERA3 benchmark acceptance foundation。
- Step 5：OpenMC source/settings 修复（source z 绑定活性燃料 + runtime parser）。
- Step 6（本次）：Full-assembly geometry/source/plot bounds 一致性——修复 quarter-plot bug（plot origin 重定心到 assembly center）、bounds consistency validator、source xy 绑定 full footprint、geometry metadata、benchmark validator 加 full-assembly/plot/source 检查。
- Step 7（下一步）：材料/合金 composition fidelity。
- Step 8：volume-fraction calibrated overlay。
- Step 9：full VERA3 keff benchmark acceptance。

## 9. 维护记录

### 2026-07-07（Step 4 续：抑制环境已满足的核数据库路径专家问题）

完成并验证：

- **修复 ask_expert 反复询问已配置的 `OPENMC_CROSS_SECTIONS`**。根因：`few_shots`/`prompts` 引导 LLM 把核数据库路径写进 `capability.required_human_confirmations`（典型措辞 "Cross sections library path (OPENMC_CROSS_SECTIONS) must be set by the user."），`_pending_expert_questions` 无条件把它包成专家问题，且 agent 从不检测环境变量——即使环境已配好仍反复打扰用户。
- **修复点（`openmc_agent/graph.py`）**：新增 `_cross_sections_env_available`（`OPENMC_CROSS_SECTIONS` 非空且文件存在）/`_is_cross_sections_confirmation`（文本匹配）/`_cross_sections_question_resolved_by_env`（组合判定）。在 `_pending_expert_questions` 对 **capability 阶段** 确认项（`required_human_confirmations` + `capability.issues`）做环境自检抑制。
- **关键边界**：**不**抑制 `validation_report.issues` 里的 runtime issue（如 `runtime.cross_sections_missing`）——那是 OpenMC 实际运行失败，即使环境变量设了也可能真失败（路径失效/子进程未继承/hdf5 版本不符），必须保留 ask_expert 路由。读环境变量值 ≠ 发明路径，human-confirmation 安全边界不变。
- **测试**：`tests/test_graph.py` 新增文本匹配单测 +「环境已配则抑制」+「环境未配则保留」两个端到端用例；既有 `test_plan_graph_does_not_reflect_cross_sections_missing` 守卫 runtime issue 不被误抑制。
- 全量测试通过：`552 passed, 2 skipped`。

### 2026-07-07（Step 4 续：surface 轴截距别名归一化）

完成并验证：

- **修复 axis-aligned plane 渲染参数别名**。根因：planner 产出的 IR 中 `xplane`/`yplane`/`zplane` 的 `parameters` 常用直觉命名 `x`/`y`/`z`，而 OpenMC 的 `XPlane`/`YPlane`/`ZPlane` 构造签名要求 `x0`/`y0`/`z0`；`SurfaceSpec.parameters` 是无校验的自由 dict，`executor._surface_constructor` 又原样透传 key，导致 `model.py` 出现 `openmc.ZPlane(z=-55.0)`，导出 XML 时抛 `TypeError: unexpected keyword argument 'z'`（VERA3 run 首个 `ZPlane` 即触发）。
- **修复点**：`openmc_agent/executor.py` 新增 `_AXIS_INTERCEPT_ALIASES`（`x→x0`、`y→y0`、`z→z0`），`_surface_constructor` 在拼接 kwargs 前对 plane kind 做归一化；canonical 已存在时不覆盖（幂等、不冲突）。rectangular/hexagonal prism 与 cylinder/sphere 路径不受影响。
- **测试**：`tests/test_executor.py` 新增 parametrize 用例覆盖三种 plane 的 alias 归一化 + canonical 不被重复映射；端到端用真实 openmc 执行渲染产物确认不再抛 `TypeError`。
- 全量测试通过：`533 passed, 2 skipped`。

### 2026-07-07（专家验证工具：verification digest + 3D voxel plot）

VERA3 已能渲染。为了让人类专家**快速判断渲染结果是否正确**（不靠肉眼逐像素看 2D slice），新增两层通用（堆型无关）验证产物：

- **`openmc_agent/verification.py`**：`build_verification_digest(model)` 从 IR 生成结构化 digest：
  - **不变量检查表**（pass/fail/warn，秒级扫描）：lattice universe 是否都已定义、fuel 材料是否在几何中可达、是否有 active-fuel lattice 层、source z 是否与活性燃料相交、是否有 grid material-slab 层、是否 full assembly（非 quarter）、pin counts 是否匹配 `expected_counts`。
  - **pin counts** / **axial layers 表**（z 范围/高度/fill/含燃料）/ **overlays 表**（z/target/material/mode/renderable/渲染段数）/ **materials 名册**（密度/核素/角色/几何可达性）/ **bounds**（geometry/source/active_fuel/symmetry）。
  - 输出 `verification_digest.json` + `verification_digest.md`（markdown 表格，人读）。
- **3D voxel plot**：`PlotSpec.kind` 扩展为 `"slice" | "voxel"`（width/pixels 支持 2 或 3 元）；`_render_plots_block` 对 voxel 发 `type='voxel'`（3D 二进制，ParaView/VisIt 可读）。新增 `_auto_verification_plots`：对 3D axial 模型自动追加一个覆盖全 geometry bounds 的 voxel plot（专家不用手动指定就能拿到 3D 检查包）。
- **集成**：`RectAssemblyRenderer.render` 成功渲染后自动写 `verification_digest.{json,md}`；core 路径自动追加 voxel plot。完全通用，无堆型假设。
- 全量测试：`581 passed, 2 skipped`（新增 `tests/test_verification.py` 7 个：digest 正确性/破损 plan 标红/json+md 写出/render 集成/auto-voxel/voxel PlotSpec/2D slice 不变）。

专家工作流：(1) 打开 `verification_digest.md` 扫不变量检查表（秒级判断结构对不对）；(2) 打开 voxel plot 在 ParaView 里转 3D 看 grid band / 轴向分层 / pin 贯穿；(3) 必要时看 material 名册核对核素。

### 2026-07-07（skeleton overlay 自动提升 + core.lattice_id 解析）

- **根因（skeleton 降级）**：LLM 把 8 个 spacer grid overlay 写成 `geometry_mode='skeleton'`（保守的"请确认"），尽管它们已带齐 Level 1 所需数据（z 范围 + target_lattice + material + through_path）。guard 对所有 skeleton overlay 一律发 `assembly3d.axial_overlay_requires_renderer_support` → 整模型降级 skeleton（NOT EXECUTABLE）。
- **修复（skeleton 提升）**：`axial_overlay.py` 新增 `overlay_is_promotable_to_level1`（grid-like kind + 有效 z + 可解析 rect target + 可解析 material）；`overlay_is_structurally_renderable` 对 `homogenized_open_region`（需 through_path）和**可提升的 skeleton** 都返回 True。`assembly3d_guard.py` skeleton 分支改为：可提升时不发 `requires_renderer_support`（renderer 自动按 Level 1 渲染），缺数据时仍降级。renderer 的 `_emit_overlay_derived_geometry` / `compute_axial_segments` 已用 `overlay_is_structurally_renderable`，所以可提升的 skeleton 会正常产生 overlay 段。
- **修复（core.lattice_id 解析）**：`executor.py` 新增 `_resolve_core_lattice_from_assembly`——当 `core.lattice_id` 为 None 但 `core.assembly_ids` 引用了一个有合法 lattice 的 assembly 时，从该 assembly 解析 lattice_id（LLM 常把 core 指向 assembly 而非 lattice）。在 `_normalize_core_spec_for_rendering` 的早退检查之前调用。
- **验证**：真实 VERA3 overlay（skeleton + 全数据）→ guard 无 blocking issue、可渲染；可提升 skeleton `renderable=True`，缺 material 的 skeleton `renderable=False` 仍降级。
- **回归测试**：`test_skeleton_overlay_with_full_data_is_promoted_not_blocked`、`test_skeleton_overlay_missing_material_still_downgrades`（test_axial_overlay）；`test_dangling_lattice_outer_does_not_block_render` 改为自包含合成 plan（不再依赖易变的 VERA3 disk 文件）。
- 全量测试：`574 passed, 2 skipped`。

**已知边界**：最新一次 LLM plan 出现 `universes: []`（LLM 完全省略 universe 定义）——这是 planner 级缺陷（CellSpec 不携带 universe 归属，无法从 IR 自动恢复），需 prompt 层保证 LLM 必须给出 `universes` 列表；不属于 renderer 能 silent 修复的范围。

### 2026-07-07（dangling lattice outer_universe_id 修复 —— skeleton 降级）

- **根因**：LLM 给 `assembly_lattice` 设了 `outer_universe_id='borated_water_univ'` 但没定义该 universe。`renderers/core.py` 的 `lattice.outer_universe_ref_missing`（error）触发，整模型降级 skeleton（NOT EXECUTABLE），不导出 XML。配套 warning `core.lattice_outer_unreachable` 已指出该 outer 是 dead geometry（root cell == lattice footprint）。
- **修复（`executor.py`）**：新增 `_drop_dangling_lattice_outer`，在 `_normalize_core_spec_for_rendering` 里 `_ensure_core_lattice_outer_universes` 之前调用——把引用了未定义 universe 的 `outer_universe_id` 置 None。安全：outer 是 dead 时无损失；outer 实际需要时后续 `_ensure_core_lattice_outer_universes` 会自动补一个默认水 outer（`__outer_water_universe`）。`core.lattice_outer_unreachable` warning 仍提示用户。
- **验证**：真实 VERA3 `simulation_plan.json` 的 complex_model（`outer='borated_water_univ'` dangling）经 `render_openmc_assembly_script` 成功渲染（151 KB 脚本，`__outer_water_universe` 补入，无 dangling 引用，compile 通过）。
- **回归测试（`tests/test_executor.py`）**：`test_dangling_lattice_outer_universe_id_is_dropped`（直调 helper）；`test_dangling_lattice_outer_does_not_block_render`（真实 VERA3 plan 注入 dangling outer → 渲染不报错、无 dangling、默认 outer 补入）。
- 全量测试：`572 passed, 2 skipped`。

### 2026-07-07（rectangular_prism `pitch` kwarg 修复 —— export_xml TypeError）

- **根因**：LLM 把 pin-cell 盒写成 `rectangular_prism` surface 且参数用 `pitch=[1.26, 1.26]`。`executor.py` `_rectangular_prism_kwargs` 只归一化 `width`/`height` 和 `xmin/xmax/ymin/ymax` 区间（hexagonal_prism 路径有 `pitch→edge_length`，rectangular 没有），`pitch` 原样传给 `openmc.model.RectangularPrism(pitch=...)` → `TypeError: unexpected keyword argument 'pitch'`（OpenMC 的 RectangularPrism 只接受 `width`/`height`）。导致 `export_xml` 在构造 pin_box region 时崩溃，XML 一个都导不出。
- **修复**：`_rectangular_prism_kwargs` 在最前面把 `pitch`（pair 或 scalar）翻译成 `width`/`height`，只在缺 width/height 时填，不覆盖显式值。
- **回归测试（`tests/test_executor.py`）**：parametrize 覆盖 `pitch=[1.26,1.26]` / `pitch=1.26`（scalar）/ `pitch=[1.26,1.4]`（非方形）→ 断言生成 `width=`/`height=` 且无 `pitch=`。原 `width` pair 测试保持绿。
- 全量测试：`570 passed, 2 skipped`。

### 2026-07-07（Step 3 overlay cell 复用 bug 修复 —— VERA3 source rejection 真正根因）

完成并验证：

- **根因定位（source rejection 的真正原因）**：`executor.py` `_emit_overlay_derived_geometry` 在构造 overlay universe 时按引用复用 base 的 solid cell（`cells=[cells['fuel_cell'], cells['clad_cell'], ..., overlay_cell_mod]`）。OpenMC 中一个 Cell 只能归属一个 Universe —— `Universe(cells=[...])` 会改写 cell 的 `.universe`。overlay universe 在 base 之后构造，把 `fuel_cell`/`clad_cell`/`gap_cell` **从 base `fuel_pin_univ` 抢走**。导出的 `geometry.xml` 实锤：base 燃料位（universe 1）只剩 `Pin moderator`（水），燃料 pellet 跑到 overlay universe 6。于是 base（非 overlay）燃料段——占活性区绝大部分——的燃料位全是水，`only_fissionable=True` 把绝大多数源点拒掉 → `Too few source sites`。与 source bounds / 材料 / plot / 确认问题全无关；Step 5/6 的 source/plot 修复都是对的，但都被这个 bug 掩盖。
- **修复（`openmc_agent/executor.py`）**：`_emit_overlay_derived_geometry` 对每个 derived overlay universe，把保留的 solid cell（fuel/clad/gap/tube wall）**克隆成新 `openmc.Cell`**（同 fill material + 同 region + 新 id，temperature 也带过去），overlay universe 引用这些克隆；base universe 保留原 cell。修复后 `geometry.xml`：universe 1 = `[fuel pellet(fuel), coolant(water)]`（有燃料），overlay universe 6 = `[overlay fuel clone(fuel), overlay coolant(grid)]`。
- **回归测试（`tests/test_axial_overlay.py`）**：
  - `test_overlay_universe_does_not_reuse_base_solid_cells`：静态——overlay universe 行不含 `cells['fuel_cell']`，含 `overlay_cell_fuel_cell_*` 克隆；base universe 行仍含 `cells['fuel_cell']`。
  - `test_base_fuel_universe_retains_fuel_after_overlay_render`（openmc-gated）：render → 跑 model.py 导出 → 解析 geometry.xml + materials.xml，断言 base fuel universe 含燃料 material cell。
  - 更新 `test_derived_overlay_universe_preserves_protected_cells`：原断言编码了 bug 行为（`cells['fuel_cell'], cells['clad_cell'], overlay_cell`），改为断言克隆模式。
- **全 17×17 验证**：deterministic VERA3 plan render + export，base universe 1 含燃料，overlay universe 6/10/14/... 含燃料克隆。
- 全量测试：`567 passed, 2 skipped`。

为什么之前没抓到：Step 3 测试只检查"脚本含 overlay lattice + 能 compile"，没检查"base universe 是否仍含燃料"——bug 只在 OpenMC 实跑（cell→universe 归属）时暴露。新 openmc-gated 回归测试锁死这一点。

当前状态：VERA3 base 燃料段恢复燃料（~30% 裂变份额）→ source rejection 应解除。真实 VERA3 smoke 是否完全通过仍取决于截面库可用性 + 合金 composition fidelity（Step 7）。

### 2026-07-07（Step 6：Full-assembly geometry/source/plot bounds 一致性）

完成并验证：

- **复盘根因**：`data/runs/VERA3/plots.xml` 的 xy plot `origin=(0.0,0.0,200.0)` + `width=(21.5,21.5)`。OpenMC slice origin 是绘图区**中心**，而几何位于 `[0,21.42]×[0,21.42]`（lower_left 在原点），所以 origin=(0,0) 的 plot 实际采样 `[-10.75,10.75]×[-10.75,10.75]`，只与几何相交一个象限 → **画出来是四分之一**。geometry/source bounds 本身一致（source xy=0..21.42、z=11.951..377.711，与几何/活性燃料匹配）；不是 quarter 几何，是 plot origin bug。
- **新增 `openmc_agent/geometry_bounds.py`**：`compute_geometry_bounds(model)`（lattice footprint / geometry / active-fuel z）/ `infer_symmetry_policy`（full/quarter）/ `build_geometry_metadata`（diagnostics dict）/ `validate_bounds_consistency`（source xy 超出几何 / source xy 过小 / plot 不覆盖 assembly）。
- **plot 修复（`executor.py` `_reconcile_plot_origins`）**：对所有 basis 把 in-plane origin 重定心到 assembly center（`center_x = x_min + (x_max-x_min)/2`），再叠加原有 boundary-nudge。VERA3 的 xy plot origin 现在是 `(10.71, 10.71)`，覆盖完整 17×17。修复 quarter-plot bug，不改 spacer-grid 物理。
- **source xy 修复（`source_settings.py` `assembly_xy_bounds`）**：改用 `compute_geometry_bounds`，正确处理 lower_left 在原点的 lattice（之前 lower_left=None 时返回 None）。
- **新增 7 个 issue codes**：`geometry.quarter_symmetry_unexpected` / `runtime.source_geometry_bounds_mismatch` / `runtime.source_quarter_full_mismatch` / `runtime.source_xy_outside_geometry` / `runtime.source_xy_too_small_for_full_assembly` / `runtime.plot_bounds_do_not_cover_assembly`(warning) / `runtime.plot_quarter_full_mismatch`(warning)。
- **workflow 集成（`graph.py` `_execute_tools`）**：smoke pre-flight 现在同时跑 source 验证 + bounds consistency（含 plot），blocking issue 时跳过 smoke；新增 `_plot_bounds_metadata` 把 plan.plot_specs 投影成 validator 需要的 dict。
- **benchmark validator 增强（`tests/helpers/vera3_acceptance.py`）**：新增 `vera3.quarter_geometry_unexpected` / `vera3.plot_quarter_assembly` / `vera3.source_bounds_mismatch` / `vera3.source_not_active_fuel`。
- 全量测试：`565 passed, 2 skipped`（新增 `tests/test_geometry_bounds.py` 13 个）。

当前边界：plot 现覆盖完整 assembly；source/geometry/plot bounds 一致；real VERA3 smoke 是否通过仍取决于截面库/材料 fidelity（source rejection 若仍发生，runtime_report 会附 bounds diagnostics：source_z_matches_active_fuel / source_xy_inside_geometry / source_xy_matches_full_assembly / geometry_is_full_assembly / fuel_material_fissionable）。

仍未完成：Step 7 材料/合金 composition fidelity / Step 8 volume-fraction calibrated overlay / Step 9 full VERA3 keff acceptance。

### 2026-07-07（Step 5：OpenMC source/settings 修复 + VERA3 runtime smoke 稳定化）

完成并验证：

- **根因确认**：`data/runs/VERA3/model.py` 的 `settings.source` 用整个 axial 域 `assembly_z_min=-55.0 .. assembly_z_max=463.937` + `only_fissionable=True`。燃料只在活性区 11.951–377.711，全域 box 导致大量源点落在非燃料区被拒 → `Too few source sites satisfied the constraints`，后续 `double free`/`Segmentation fault`/`MPI abort` 是源初始化失败后的连锁崩溃（非首要根因）。燃料本身可裂变（U235 3.1%/U238 96.9%），活性区 lattice 位置正确。
- **新增 `openmc_agent/source_settings.py`**：`active_fuel_z_bounds` / `source_bounds_for_plan`（z 绑定活性燃料，xy 绑定 lattice footprint）/ `validate_source_settings`（6 个 pre-flight runtime.* 检查）/ `alloy_pure_element_issues`。
- **executor.py source 修复**：`_render_source_block` 把 core 路径 source 的 z 从 `assembly_z_min..assembly_z_max` 改为 `source_z_min..source_z_max`（= 活性燃料 z），保留 `only_fissionable=True`。
- **runtime parser 增强**（`tools.py`）：识别 `Too few source sites` → `runtime.openmc_source_rejection_failure` 作为**首要 issue**，segfault/double-free 不再覆盖。
- **workflow 集成**（`graph.py`）：smoke test 前 pre-flight `validate_source_settings`，blocking source issue 时跳过 smoke 并写结构化 report。
- **新增 10 个 issue codes**：`runtime.source_default_z_extent` / `source_not_in_active_fuel_region` / `source_covers_nonfuel_axial_regions` / `source_missing_fissionable_constraint` / `fuel_material_not_fissionable` / `active_fuel_region_missing` / `active_fuel_geometry_missing` / `source_rejection_fraction_lowered` / `openmc_source_rejection_failure` / `materials.alloy_reduced_to_pure_element`。
- **guide tube 壁验证**：`vera3.guide_tube_wall_missing`（导向管无 Zircaloy 壁则告警）。
- 不盲降 threshold、不移除 fissionable 约束、不改 spacer-grid renderer。
- 全量测试：`549 passed, 2 skipped`（新增 `tests/test_source_settings.py` 16 个 + guide-tube 测试）。

当前边界：确定性 VERA3-like plan 的 source 现绑定活性燃料区、pre-flight 无 blocking；真实 VERA3 smoke 是否通过仍取决于 OpenMC/截面库可用性。spacer-grid 仍 Level 1，合金 composition 仍可能需确认。

仍未完成：Step 6 合金 composition fidelity / Step 7 volume-fraction calibrated overlay / Step 8 full VERA3 keff acceptance。

### 2026-07-07（Step 4：VERA3 end-to-end benchmark acceptance foundation）

完成并验证：

- **VERA3 reference fixture**（`tests/fixtures/vera3_reference.json`，测试专用，生产代码不读取）：全部数值逐字转录自 `Input/VERA3_problem.md`——assembly metadata（pin pitch 1.26、assembly pitch 21.50、17×17、axial domain [-55.0, 463.937]、活性燃料 [11.951, 377.711]）、12 个轴向层（含 z 范围/高度/材料）、8 个 spacer grid（中心 z、高度、Inconel/Zircaloy 材料、z_min/z_max）、3A/3B pin map（计数 + 24 导向管 / 1 仪表管 / 16 Pyrex / 8 套管塞 坐标，1-based doc 约定）、期望材料清单、overlay geometry_mode。coordinate_convention 显式声明（1-based、row 1=top、center (9,9)→0-indexed (8,8)），杜绝坐标系歧义导致的假绿。
- **benchmark acceptance helper**（`tests/helpers/vera3_acceptance.py`，新增 `tests/helpers/` 包，pyproject `pythonpath` 加 `tests`）：
  - `BenchmarkIssue` dataclass + `vera3.*` issue taxonomy（与通用 `assembly3d.*` 分离）。
  - `load_vera3_reference` / `to_0_indexed` 坐标转换。
  - `validate_vera3_plan_structure(plan, reference, variant)`：验证 3D assembly 结构（非默认 z、domain 覆盖、活性燃料高度）、spacer grid 为 overlay（计数/mode/target/through-path、无 material slab、无 purpose-only 注释）、pin map（17×17、计数、导向管/仪表管/Pyrex/套管塞坐标）、材料引用解析、renderer 兼容性（无 blocking `assembly3d.*`）。
  - `build_vera3_like_plan(reference, variant, ...)`：纯 reference 驱动的确定性 VERA3-like plan 构造器（无 LLM），支持 drop_overlays / grid_count / use_material_slab_grid / mutate_pin / wrong_pyrex / default_z 等 mutation flag 构造 intentionally-broken plan。
- **三层 E2E 测试设计**（`tests/test_vera3_acceptance.py`，15 个测试）：
  - A. 确定性 fixture 测试（reference 加载、坐标转换、3A/3B 通过验收、6 类失败模式：missing overlay / wrong grid count / material slab / pin count / pyrex coordinate / default z、3A/3B Level 1 渲染、benchmark report 序列化、stale artifact 不掩盖失败）。
  - B. planner-output replay（gated：`tests/fixtures/vera3_plan_candidate.json` 不存在则 skip，存在则 xfail 直到 Step 5）。
  - C. full-workflow integration（`@pytest.mark.integration`，skip on CI）。
- **prompts.py 通用 guidance 强化**（无 VERA3 硬编码）：多工况变体（一变体一 lattice、不合并）、坐标约定（doc 坐标写入 assumptions、内部 0-indexed 归一、不臆造对称坐标）、missing grid data（skeleton overlay + human confirmation，禁止 material slab fallback）。
- 全量测试：`529 passed, 2 skipped`（新增 15 个 VERA3 验收测试；2 skip 为 replay/integration gated）。

当前能力边界（明确）：

- 确定性 VERA3-like plan（3A/3B）能通过 benchmark 验收并渲染为 exportable Level 1 overlay 模型（7 个燃料区 grid 渲染为 overlay 段；第 8 个 grid 在氦气气腔内，IR 声明但不产生 lattice overlay 段——简化模型的已知 gap）。
- 真实 LLM VERA3 workflow 仍可能 xfail（依赖 planner fact extraction 质量），replay 测试 gated 直到有 candidate fixture。
- 仍无 volume-fraction calibration、无 explicit spacer grid / mixing vane 几何。

仍未完成：

- **Step 5**：真实 VERA3 end-to-end pass（LLM 从 `Input/VERA3_problem.md` 抽取 → 通过 `validate_vera3_plan_structure` → 渲染 → 导出）。
- **Step 6**：volume-fraction calibrated overlay。
- explicit spacer grid bars / mixing vane 几何仍未实现。

### 2026-07-07（Step 3：Level 1 homogenized_open_region overlay renderer）

完成并验证：

- **新增 `openmc_agent/axial_overlay.py`**：纯逻辑模块（无 OpenMC 依赖），提供 overlay 渲染的全部决策：
  - `classify_material_role(material)`：按 id/name/formula 把材料分为 `open`（water/coolant/moderator，可被替换为 grid 材料）vs `protected`（fuel/clad/zircaloy/gap/absorber/tube/grid alloy，永不替换）。
  - `universe_open_cell_ids(...)`：识别 universe 的 open-region cell。
  - `derive_overlay_universe_plan(...)`：对 target lattice 的每个 universe 决定——单 open cell → 派生 overlay universe；多 open cell（如导向管内水+外水）→ **保守复用 base universe**（保 through-path，不加 grid 材料）；零 open cell → unresolved。
  - `compute_axial_segments(...)`：把 `core.axial_layers` 边界与 renderable overlay 边界合并，输出带 `(z_min, z_max, layer, covering_overlay)` 的有序分段。
  - `overlay_is_structurally_renderable(...)`：homogenized_open_region + rect target lattice 解析 + material 解析 + through_path_preserved=True。
- **executor.py 实现 Level 1 渲染**：
  - `_emit_overlay_derived_geometry(spec)`：为每个 structurally renderable overlay 派生 overlay cell（open cell 的 region 复用，fill 换成 grid 材料）+ overlay universe（复用其余 protected cells）+ derived overlay lattice（pattern 中每个 universe 映射到派生或复用的 universe）。
  - `_render_axial_core_root` 改为基于 `compute_axial_segments` 渲染：overlay 覆盖的段 fill = derived overlay lattice，其余段保持原 layer fill（loading_id 仍按层缓存派生一次）。未切分的层保留旧 `root_cell_<layer.id>` 命名，被切分的层用 `root_cell_<layer.id>_segN`。
- **pin/tube through-path 保证**：派生 universe 复用 fuel/clad/tube wall 的原 cell，仅替换 open/coolant cell 的 fill；多 open cell 的 universe（导向管）原样复用——任何情况都不截断燃料棒/导向管/测量管。
- **guard 更新（`assembly3d_overlay_issues`）**：homogenized_open_region 在结构上可渲染时**不再**触发 `axial_overlay_requires_renderer_support`；新增三个 issue code：
  - `assembly3d.axial_overlay_open_region_unresolved`（target universe 无可识别 open cell）。
  - `assembly3d.axial_overlay_overlap_unsupported`（不同 material/mode 的 overlay z 区间重叠）。
  - `assembly3d.axial_overlay_target_layer_mismatch`（overlay target lattice 不被任何 axial layer 填充）。
- **capability 行为**：可渲染 overlay 时 `executable_subsystems` 含 `axial_overlays`，warnings 标注 "Level 1 homogenized open-region overlay: pin/tube through-path preserved; grid straps/mixing vanes not modeled; volume fraction not calibrated"。无法安全派生时降级 skeleton，绝不生成假 XML。
- **prompts.py**：planner guidance 把 `homogenized_open_region` 列为 grid 材料+位置已知时的首选 Level 1 模式。
- 全量测试：`514 passed`（新增 `tests/test_axial_overlay.py` 11 个场景：可渲染性、单/多 overlay 切分、非 target 层不受影响、pin map/protected cell/guide tube through-path 保持、overlap 降级、open region unresolved 降级、VERA3-like 多 grid smoke、explicit_bars 仍降级）。

Level 1 近似边界（明确）：

- 不建模真实格条 / 混流翼 / 套筒；grid 材料以均质形式占据每根 pin 的 coolant/open region。
- 不做体积分数标定；导向管（多 open cell）位置保守不加 grid 材料。
- benchmark 验收仍需 VERA3 E2E 测试（依赖更细的 pin-cell 几何与 grid z 位置输入）。

仍未完成：

- **Step 4**：volume-fraction calibrated overlay（需要 grid 体积/质量信息）。
- **Step 5**：VERA3 end-to-end benchmark acceptance。
- explicit spacer grid bars / mixing vane 几何仍未实现。

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

### 2026-07-08

完成并验证：

#### Incremental Plan Builder Phase 0–1

**背景问题：** VERA3 3B 等复杂 3D assembly 场景在 monolithic plan generation 下持续失败。根因链：LLM 一次性输出约 25K 字符 JSON → JSON 语法错误 → `repair_plan_format` 尝试修复 → 修复过程中 `core.axial_layers` / `core.axial_overlays` 丢失 → `assembly3d_guard` 正确拦截 → reflect 多轮仍重复生成破损大 JSON → 最终 skeleton。系统的安全行为是正确的，但架构上不能继续依赖 LLM 一次性输出完整 `SimulationPlan`。

**Phase 0 — 模式判断（`openmc_agent/plan_builder/mode.py`）：**

- 新增 `should_use_incremental_planning(requirement, feature_flags, retry_history, plan_context)` 函数，返回 `PlanningModeDecision`（mode, reasons, triggers, confidence, feature_summary）。
- 触发 incremental mode 的条件：
  - `feature.3d_axial_geometry` — 复用 `detect_assembly_3d_features()` 的轴向信号检测；
  - `feature.spacer_grid` — 定位格架 / grid strap / mixing vane；
  - `feature.special_pin_map` — Pyrex / thimble plug / guide tube / instrument tube / burnable poison / 多 universe 类型；
  - `feature.multiple_variants` — benchmark variant 信号（VERA3, C5G7, 3A/3B）；
  - `feature.large_lattice` — 20×20 或更大 lattice（阈值 20 避免标准 17×17 PWR 误触发）；
  - `history.large_json_parse_error` — retry history 中出现 JSON 语法错误或 raw_output > 12K chars；
  - `history.repair_lost_axial_layers` — retry history 中 repair 后 axial_layers 丢失；
  - `history.repeated_axial_contract_violation` — 多轮 `assembly3d.axial_layers_required`；
  - `override.force_incremental` / `force_monolithic_planning` — 显式覆盖。
- 简单 2D assembly（无 axial、无 spacer、无 special pin、无 large lattice）保持 monolithic，confidence=1.0。

**Phase 1 — PlanBuildState（`openmc_agent/plan_builder/state.py`）：**

- 新增 `PlanBuildState` 模型：state_id, requirement_text, benchmark_id, selected_variant, extracted_facts, confirmed_facts, component_tasks, patches, patch_status, assembled_plan, validation_issues, build_log, metadata。
- 新增 `BuildEvent`, `PlanComponentTask`, `PlanPatchEnvelope` 子模型。
- Helper methods：`add_event`, `add_task`, `add_patch`, `mark_patch_status`, `get_valid_patches`, `to_summary`。
- `initialize_plan_build_state(requirement, decision, ...)` 初始化状态，记录 `planning_mode_selected` / `build_state_initialized` / `component_tasks_initialized` 事件。
- `create_initial_component_tasks(feature_summary)` 根据 feature 生成浅层 task skeleton（facts → materials → universes → pin_map → axial_layers → axial_overlays → settings，带依赖链）。

**Workflow 集成（`openmc_agent/graph.py`）：**

- `GraphState` 新增 `planning_mode_decision` 和 `plan_build_state` 字段。
- `_receive_requirement` 节点调用 `should_use_incremental_planning()`，将决策写入 state。
- 当 mode=incremental 时，初始化 `PlanBuildState` 并记录 `incremental_recommended_but_not_executed` 事件（fallback_reason=`incremental_executor_not_implemented`），然后安全 fallback 到 monolithic 路径。
- 简单 case 完全不受影响。

**测试覆盖（23 tests）：**

- `tests/test_plan_builder_mode.py`：13 个 test case 覆盖简单 2D→monolithic、3D axial→incremental、spacer grid→incremental、VERA3 special pin map→incremental、large JSON parse error history、repair-lost-axial-layers、force override（双向）、large lattice、feature summary、empty requirement、JSON serializable、repeated axial contract。
- `tests/test_plan_builder_state.py`：10 个 test case 覆盖 JSON 序列化、patch lifecycle（add→mark valid→get valid→mark invalid）、component tasks（3D spacer grid full set / empty for simple / skip pin_map without special pins）、transcript summary 含 mode decision、add_event timestamp、add_task replace、fallback event 记录、multiple patches lifecycle。
- 全量测试：`604 passed, 2 skipped in 53.25s`。无回归。

**后续 Phase 路线（未实现）：**

1. Patch schemas（每种 patch_type 的 JSON schema 定义）。
2. Patch validators（per-patch 校验逻辑）。
3. Deterministic assembler（把 valid patches 合并成完整 SimulationPlan）。
4. LLM patch generator（per-task 小粒度 LLM 调用，替代 monolithic 25K JSON 输出）。
5. Local retry router（per-task 重试，而非整体 reflect）。
6. Full workflow replacement（incremental executor 接管 generate_plan 节点）。

#### Incremental Plan Builder Phase 2: Patch schemas and validators

**目标：** 定义 patch-based planning 的核心 patch schema 和独立 validators，使每个 patch 可以被独立 parse / validate，validator 可以精确定位是 materials、universes、pin_map、axial_layers 还是 overlays 出错。

**Patch 类型（`openmc_agent/plan_builder/patches.py`）：**

| Patch type | 模型 | 职责 |
|---|---|---|
| `facts` | `FactsPatch` | benchmark/geometry/variant/missing data facts |
| `materials` | `MaterialsPatch` | 材料 catalog（含 composition_status） |
| `universes` | `UniversesPatch` | universe 定义（fuel pin, guide tube, pyrex rod, ...） |
| `pin_map` | `PinMapPatch` | 坐标 replacement rules（不展开完整 lattice） |
| `axial_layers` | `AxialLayersPatch` | axial layer list（z-segmentation） |
| `axial_overlays` | `AxialOverlaysPatch` | spacer grids / support plates |
| `settings` | `SettingsPatch` | source/plot/execution 策略 |

**关键设计决策：**
- PinMapPatch 只表达坐标 replacement rules（如 16 个 Pyrex 坐标），不输出完整 17×17 lattice pattern。这让 LLM 可以输出 200 字节的 patch 而非 25 KB 的完整 JSON。
- MaterialsPatch 使用 `composition_status` 而非 blocking 结构生成。Zircaloy-4 / SS-304 / Inconel-718 如果被简化为纯元素且标记 `confirmed`，validator 会报 error；标记 `approximate` + warning 则允许通过。
- UniversesPatch 的 guide_tube 要求有内部 water + tube wall，否则 warning。pyrex_rod 要求有 pyrex material cell。

**Validators（`openmc_agent/plan_builder/validators.py`）：**
- `validate_patch(patch, context)` 按 patch_type 路由到 per-type validator。
- `PatchValidationResult` 含 `ok`、`issues`（按 severity: error/warning/info）、`summary`。
- `PatchValidationContext` 提供跨 patch 引用检查（expected_counts, known_universe_ids, benchmark_id, strict_benchmark 等）。
- Issue codes 覆盖：`patch.duplicate_id`, `patch.materials.alloy_reduced_to_pure_element`, `patch.universes.guide_tube_wall_missing`, `patch.pin_map.coord_out_of_bounds`, `patch.pin_map.coord_overlap`, `patch.pin_map.count_mismatch`, `patch.axial_layers.overlap`, `patch.axial_layers.active_fuel_missing`, `patch.axial_layers.default_unit_slab`, `patch.axial_overlays.through_path_not_preserved`, `patch.axial_overlays.volume_fraction_missing` 等。

**PlanBuildState 集成（`state.py`）：**
- `add_validated_patch_to_state(state, envelope, parsed_patch, validation)` 把 validated patch 写入 state：
  - `validation.ok=True` → patch status valid + `planning.patch_validated` event；
  - `validation.ok=False` → patch status invalid + `planning.patch_invalid` event（含 error_codes）。
- 3 个新 event codes：`planning.patch_parsed`, `planning.patch_validated`, `planning.patch_invalid`。

**为什么不在 Phase 2 做 assembler：** assembler 需要 7 种 patch 全部 valid 后才能合成 SimulationPlan。Phase 2 只做 per-patch parse + validate，确保每个 patch 可以独立工作。Phase 3 才做 assembler + VERA3 fixture patches。

**VERA3 3B 将来如何用 patches 避免 25K monolithic JSON：**
1. `FactsPatch`：VERA3 benchmark_id, variant=3B, lattice_size=(17,17), expected_pyrex_count=16, axial_domain_cm, has_special_pin_map=True
2. `MaterialsPatch`：UO2, water, Zircaloy-4 (approximate), Pyrex, air — 各自独立小 patch
3. `UniversesPatch`：fuel_pin, guide_tube, instrument_tube, pyrex_rod, thimble_plug — 每个 universe 的 cells 定义
4. `PinMapPatch`：17×17, default=fuel_pin, pyrex_rod_coords=[16 positions], thimble_plug_coords=[8 positions] — 只列特殊坐标，不展开 289 格
5. `AxialLayersPatch`：12 个层（nozzle → end plug → plenum → active fuel → ...）
6. `AxialOverlaysPatch`：8 个 spacer grid overlays
7. `SettingsPatch`：active_fuel_box source, full_assembly plot

每个 patch 只有几百到几千字符，LLM 不需要一次性输出 25K。

**测试覆盖（53 new tests）：**
- `tests/test_patch_schemas.py`：13 tests 覆盖 parse_patch_content 对 7 种类型的路由、envelope 解析、coordinate convention。
- `tests/test_patch_validators.py`：40 tests 覆盖所有 issue codes 和 PlanBuildState patch lifecycle。
- 全量测试：`657 passed, 2 skipped in 53.36s`。无回归。

**当前未完成事项：**
1. Deterministic assembler（合并 valid patches → SimulationPlan）
2. VERA3 patch fixtures（手写或半自动 fixture patches）
3. LLM patch generator（per-task 小粒度 LLM 调用）
4. Local retry router（per-task 重试）
5. Workflow replacement（incremental executor 接管 generate_plan 节点）

#### Incremental Plan Builder Phase 3: Deterministic assembler and VERA3 patch fixtures

**核心成果：** VERA3 3B 从小 patches 成功组装为完整 3D SimulationPlan，不再需要 LLM 输出 25K JSON。Assembled plan 通过 `assembly3d_guard` 检查。

**Deterministic assembler（`openmc_agent/plan_builder/assembler.py`）：**

- `assemble_simulation_plan_from_patches(patches, strict=True)` → `PlanAssemblyResult{ok, plan, plan_dict, issues, summary}`
- 不调用 LLM；不调用 OpenMC；不渲染 geometry。
- 检查 required patches（3D assembly 需要 facts/materials/universes/pin_map/axial_layers/settings；spacer grids 需要 axial_overlays）。
- 缺少 required patch → `assembly.missing_patch` error。
- 将 7 种 patch 类型适配为现有 `SimulationPlan` schema 的 `ComplexModelSpec`（materials, cells, universes, lattices, core.axial_layers, core.axial_overlays, assemblies）。

**Pin map expansion（`expand_pin_map`）：**

- LLM / fixture 只提供特殊坐标（如 16 个 Pyrex + 8 个 plug 坐标）。
- Python 确定性地展开为完整 17×17 universe_pattern（289 entries）。
- 支持 0-indexed 和 1-indexed 坐标约定。
- 检查坐标越界和 overlap。
- 验证：VERA3 3B 的 289 positions = 264 fuel + 16 pyrex + 8 plug + 1 instrument tube。

**VERA3 patch fixtures（`tests/fixtures/vera3_patches/`）：**

- `vera3_3a_patches.json`：7 个 patches（facts + 7 materials + 3 universes + 24 GT + 12 layers + 8 overlays + settings）。
- `vera3_3b_patches.json`：7 个 patches（facts + 8 materials（含 Pyrex）+ 4 universes（含 pyrex_rod / thimble_plug）+ 16 Pyrex coords + 8 plug coords + 12 layers + 8 overlays + settings）。
- Fixtures 只用于测试；不写入生产 code。
- 所有 fixture patches 通过 Phase 2 validators（strict_benchmark=True, 0 errors）。

**Assembly issue codes：**
`assembly.missing_patch`, `assembly.pin_map_expansion_failed`, `assembly.simulation_plan_schema_invalid`, `assembly.unresolved_universe_reference`, `assembly.completed`

**PlanBuildState assembly helper：**
- `assemble_state_if_ready(state, strict=True)` → 从 `state.patches`（status='valid'）中读取 patches → parse → assemble → `state.assembled_plan = result.plan.model_dump()`。
- 3 个新 event codes：`planning.assembly_started`, `planning.assembly_completed`, `planning.assembly_failed`。

**Assembly3d guard 通过验证：**
- 3A assembled plan：0 个 `assembly3d.axial_layers_required` / `default_z_extent` / `spacer_grid_material_slab`。
- 3B assembled plan：同样 0 个 blocking errors。
- 3B 的 z ranges 完整保留（active_fuel: 11.951–377.711 cm）。
- 8 个 spacer grid overlays 全部 `geometry_mode="homogenized_open_region"` + `through_path_preserved=True`。

**Patch → SimulationPlan 字段映射：**

| Patch | Plan field |
|---|---|
| FactsPatch | assembly.pitch_cm, core.boundary, assumptions |
| MaterialsPatch | complex_model.materials[]（ComplexMaterialSpec + NuclideSpec） |
| UniversesPatch | complex_model.universes[] + cells[]（UniverseSpec + CellSpec） |
| PinMapPatch | complex_model.lattices[0].universe_pattern（expand_pin_map 展开） |
| AxialLayersPatch | core.axial_layers[]（AxialLayerSpec + FillRefSpec） |
| AxialOverlaysPatch | core.axial_overlays[]（AxialOverlaySpec） |
| SettingsPatch | plot_specs, execution_check.settings |

**测试覆盖（33 new tests）：**
- `tests/test_plan_assembler.py`：12 tests（assembly + pin map expansion + state lifecycle）。
- `tests/test_vera3_patch_fixtures.py`：21 tests（3A/3B fixture assembly + counts + assembly3d guard）。
- 全量测试：`690 passed, 2 skipped in 54.51s`。无回归。

**当前仍未完成：**
1. LLM patch generator（per-task 小粒度 LLM 调用，替代 monolithic 25K JSON 输出）
2. Local retry router（per-task 重试，而非整体 reflect）
3. Incremental workflow executor（incremental executor 接管 generate_plan 节点）
4. Renderer/runtime issues（source rejection 修复、plot bounds 修复）
5. Material/alloy fidelity（Zircaloy-4 / SS-304 / Inconel-718 真实 composition）
6. Volume-fraction calibrated spacer grid geometry

#### Incremental Plan Builder Phase 4: LLM patch generator

**核心成果：** 每次 LLM 只生成一个 patch（几百到几千字节），不再输出 25K monolithic JSON。JSON parse 失败或 validation 失败只重试当前 patch，不触碰已 valid patches。

**Prompt builders（`openmc_agent/plan_builder/patch_prompts.py`）：**
- `build_patch_prompt(patch_type, requirement, context)` → per-patch-type prompt
- 每个 prompt 包含全局规则："Do NOT output a full SimulationPlan"、"Do NOT output the full 17x17 lattice"
- PinMapPatch prompt 强调只输出特殊坐标，不输出 289 格
- MaterialsPatch prompt 禁止 confirmed 纯元素合金
- AxialLayersPatch prompt 禁止 default z=-1..1
- AxialOverlaysPatch prompt 强调 overlay 而非 material slab
- `build_retry_prompt(...)` 将 validation errors 反馈给 LLM 以局部修复

**Patch generator（`openmc_agent/plan_builder/patch_generator.py`）：**
- `generate_patch(patch_type, requirement, context, llm_client, max_attempts)` → `PatchGenerationResult`
- 流程：build prompt → call llm_client → parse JSON → parse_patch_content → validate_patch → ok/retry
- JSON parse 支持 markdown fences、preamble text、trailing commas
- Retry 只修改当前 patch，不删除已 valid patches
- `FakePatchLLM` 类用于测试（scripted responses，no real LLM）

**State integration（`state.py`）：**
- `generate_and_add_patch_to_state(state, patch_type, requirement, context, llm_client)` 
- 成功：add valid envelope + `planning.patch_generated` event
- 失败：`planning.patch_generation_failed` event + 保留已 valid patches
- 3 个新 event codes：`planning.patch_generation_started/generated/patch_generation_failed`

**Issue / event codes：**
`patch_generation.json_parse_error`, `patch_generation.schema_error`, `patch_generation.max_attempts_exceeded`, `patch_generation.llm_error`, `patch_generation.no_llm_client`, `planning.patch_generation_started`, `planning.patch_generated`, `planning.patch_generation_failed`

**测试覆盖（36 new tests）：**
- `tests/test_patch_prompts.py`：21 tests（per-type prompt content + context inclusion + retry prompt）
- `tests/test_patch_generator.py`：15 tests（generation success/retry/max-attempts/state-integration/VERA3-3B/JSON-parse）
- 全量测试：`726 passed, 2 skipped in 53.30s`。无回归。

**VERA3 3B fake LLM patch generation summary：**
- pin_map：只输出 16 pyrex + 8 plug + 1 instrument tube 坐标（< 2000 bytes），验证 ok
- axial_overlays：8 个 spacer grid overlays，全部 homogenized_open_region，验证 ok
- facts + pin_map + axial_layers + axial_overlays（4 个 generated patches）+ fixture materials/universes/settings → assembler 成功组装，assembly3d_guard 通过

**当前仍未完成：**
1. Full local retry router（per-task 自动重试 + cross-patch 依赖管理）
2. Incremental workflow executor（incremental executor 接管 generate_plan 节点）
3. Graph replacement（full workflow replacement）
4. Real LLM integration / evaluation（真实 LLM 稳定性评估）
5. Runtime smoke test（source rejection 修复、plot bounds 修复）
6. Material/alloy fidelity（真实 composition library）
7. Volume-fraction calibrated spacer grid geometry

#### Incremental Plan Builder Phase 5: Executor and local retry router

**核心成果：** Incremental executor 从 fake LLM 端到端生成 VERA3 3B 的所有 patches（facts → materials → universes → pin_map → axial_layers → axial_overlays → deterministic settings），按依赖顺序生成，上下文自动传播，失败时只重试当前 patch，最终组装成完整 3D SimulationPlan 并通过 assembly3d guard。

**Executor（`openmc_agent/plan_builder/executor.py`）：**
- `run_incremental_planning(requirement, state, llm_client, max_patch_attempts, task_order)` → `IncrementalExecutionResult{ok, state, assembled_plan, issues, summary}`
- 依赖顺序：facts → materials → universes → pin_map → axial_layers → axial_overlays → settings → assembly
- 跳过已 valid patches（`planning.patch_skipped_already_valid`）
- Settings 使用 deterministic fallback（不调 LLM）
- 所有 required patches valid 后调用 `assemble_state_if_ready`

**Dependency-aware context propagation（`build_generation_context_from_state`）：**
- FactsPatch → benchmark_id, selected_variant, expected_counts, active_fuel_region, axial_domain, feature flags
- MaterialsPatch → known_material_ids
- UniversesPatch → known_universe_ids
- PinMapPatch → expected coordinate counts
- AxialLayersPatch → active fuel z range, axial domain
- AxialOverlaysPatch → known_lattice_ids
- 每个 patch 生成前从已 valid patches 构建上下文（`planning.patch_dependency_context_built`）

**Local retry router（`route_retry`）：**
- JSON parse / schema error → `retry_same_patch`
- Local validation error (pin_map.*, axial_layers.*, etc.) → `retry_same_patch`
- Unresolved reference to missing dependency → `retry_dependency_patch`
- Unresolved reference but dependency valid → `retry_same_patch`（with enriched context）
- Unroutable error → `fail`

**Deterministic settings fallback（`build_deterministic_settings_patch`）：**
- source_strategy=active_fuel_box, plot_strategy=full_assembly
- cross_sections_runtime_required=True, tallies_required_for_smoke_test=False
- 不调用 LLM

**Event logging：**
`planning.incremental_execution_started/completed/failed`, `planning.patch_skipped_already_valid`, `planning.patch_dependency_context_built`, `planning.patch_retry_routed`, `planning.deterministic_settings_patch_created`

**测试覆盖（23 new tests）：**
- `tests/test_incremental_executor.py`：14 tests（dependency order, skip valid, context propagation, deterministic settings, retry, failure stop, VERA3 3B full execution, pin_map size）
- `tests/test_retry_router.py`：9 tests（parse error, unresolved material, count mismatch, coord overlap, schema invalid, axial error, unroutable, warnings only）
- 全量测试：`749 passed, 2 skipped in 50.91s`。无回归。

**VERA3 3B fake incremental execution summary：**
- 生成顺序：facts → materials → universes → pin_map → axial_layers → axial_overlays → settings(deterministic)
- pin_map 只输出 24 个特殊坐标（16 Pyrex + 8 plug），不输出 289 格
- assembled lattice: 17×17 = 289 positions
- Pyrex count: 16, thimble plug count: 8, instrument tube: 1, fuel: 264
- axial_layers: 12, axial_overlays: 8
- assembly3d guard: **0 blocking errors**

**当前仍未完成：**
1. Graph workflow replacement（incremental executor 接管 generate_plan 节点）
2. Real LLM integration / evaluation（真实 LLM 稳定性评估）
3. OpenMC runtime smoke test（source rejection 修复、plot bounds 修复）
4. Material/alloy fidelity（真实 composition library）
5. Volume-fraction calibrated spacer grid geometry
6. keff benchmark acceptance

#### Incremental Plan Builder Phase 6: Graph workflow integration

**核心成果：** 当 `should_use_incremental_planning(...)` 返回 `mode="incremental"` 且 `patch_llm_client` 可用时，graph 自动路由到 incremental patch executor，不再调用 monolithic full-plan LLM。Simple 2D case 仍走原 monolithic planner。VERA3 3B 通过 fake LLM 端到端完成从 patches 到 assembled SimulationPlan 的完整 graph workflow。

**Graph integration point（`graph.py`）：**
- `_make_generate_plan_node` 新增 `patch_llm_client`, `use_incremental_executor`, `allow_monolithic_fallback_for_incremental_failure` 参数。
- `_generate_plan` 在调用 monolithic planner 之前检查 `planning_mode_decision.mode`：
  - `mode="incremental"` + `patch_llm_client` 可用 → 调用 `_run_incremental_plan_generation`
  - `mode="incremental"` + 无 `patch_llm_client` → 静默 fallback 到 monolithic（不报错）
  - `mode="monolithic"` → 原 monolithic planner
- `_run_incremental_plan_generation` 初始化/重建 PlanBuildState → `run_incremental_planning` → 成功则 parse assembled plan dict 成 SimulationPlan model → 注入 graph state → 后续 validate_plan / assess_capability / renderer 流程不变。

**Config flags：**
- `use_incremental_executor: bool = True` — 是否在 incremental mode 时使用 executor
- `allow_monolithic_fallback_for_incremental_failure: bool = False` — executor 失败后是否 fallback 到 monolithic
- `patch_llm_client: Callable[[str], str] | None = None` — patch LLM callable

**Incremental failure behavior：**
- executor 失败 → structured error with patch-level diagnostics
- 不触发 full-plan reflect/repair loop
- 保留 valid patches 在 PlanBuildState
- 默认不 fallback 到 monolithic（除非显式设置 flag）

**Reflection / repair interaction：**
- incremental path 不调用 `repair_plan_format` / `reflect_plan` for patch-level failures
- assembled plan schema invalid → structured issue `incremental.assembled_plan_schema_invalid`
- existing monolithic repair/reflect 路径不受影响

**Transcript fields：**
- `planning_mode_decision.mode`
- `incremental_execution_result.{ok, summary, issues}`
- `plan_build_state.{patches, assembled_plan, build_log}`
- trace event `plan_generated` metadata includes `planning_mode="incremental"`, `patch_order`, `valid_patch_count`

**测试覆盖（9 new tests）：**
- `tests/test_graph_incremental_integration.py`：9 tests（simple 2D monolithic, VERA3 3B incremental, validation, failure no fallback, fallback flag, JSON failure local retry, valid patches preserved, end-to-end, transcript summary）
- 全量测试：`758 passed, 2 skipped in 63.18s`。无回归。

**VERA3 3B fake graph run summary：**
- planning mode: incremental
- patch order: facts → materials → universes → pin_map → axial_layers → axial_overlays → settings(deterministic)
- pin_map: 24 special coords (16 Pyrex + 8 plug), < 2000 bytes
- assembled lattice: 17×17 = 289 positions
- Pyrex: 16, thimble plugs: 8, instrument tube: 1, fuel: 264
- axial layers: 12, overlays: 8
- assembly3d guard: 0 blocking errors
- transcript: `planning_mode="incremental"`, `success=True`

**remaining work：**
1. Real LLM stability evaluation（真实 LLM 替代 fake LLM 的端到端测试）
2. Runtime OpenMC smoke test（source rejection 修复、plot bounds 修复）
3. Material/alloy fidelity（真实 composition library）
4. Volume-fraction calibrated spacer grid geometry
5. keff benchmark acceptance

#### Incremental Plan Builder Phase 7: Real LLM adapter and evaluation harness

**核心成果：** Graph 可以从现有 LLM provider 自动构造 patch client；evaluation harness 可运行 opt-in 真实 LLM 评估并输出结构化 report；PatchGenerationAttempt 增加 raw_chars / full_plan_markers / full_lattice_suspected 诊断。

**Real LLM adapter（`openmc_agent/plan_builder/llm_adapter.py`）：**
- `make_patch_llm_client(llm=None, model_name=None, ...)` → `Callable[[str], str]`
- 复用项目现有 `_client_for_model(model)` 构造 OpenAI-compatible client
- adapter 只做 prompt → raw_text；不 parse / validate / assemble
- 支持 callable 直接传入（FakePatchLLM）/ OpenAI client 包装 / 从 model_name 构造
- per-patch token budgets：`PATCH_MAX_TOKENS` (facts:1200, materials:2500, universes:3500, pin_map:1800, axial_layers:2500, axial_overlays:2500)

**Graph auto-construct patch client（`graph.py`）：**
- incremental mode + 无显式 `patch_llm_client` → 尝试从 `state["model"]` 构造 adapter
- auto-constructed client 失败 → fallback 到 monolithic（best-effort）
- 显式提供的 client 失败 → 受 `allow_monolithic_fallback_for_incremental_failure` 控制

**Evaluation harness（`openmc_agent/plan_builder/evaluation.py`）：**
- `run_incremental_evaluation(requirement, benchmark_id, selected_variant, llm_client, model, max_patch_attempts, output_dir)` → `(EvaluationReport, PlanBuildState)`
- 输出 `evaluation_report.json`, `plan_build_state.json`, `assembled_plan.json`, `patches/*.json`

**CLI（`scripts/evaluate_incremental_planning.py`）：**
- `--benchmark VERA3 --variant 3B --model zhipu:glm-5.2 --out data/evals/...`
- `--dry-run` 打印 patch order 不调 LLM

**Output diagnostics：**
- `PatchGenerationAttempt` 新增：`raw_chars`, `contains_full_plan_markers`, `contains_full_lattice_suspected`
- `patch_generation.full_plan_markers_detected`（warning）
- `patch_generation.pin_map_full_lattice_detected`（warning，当 coord_count > 80 或 raw_chars > 3000）

**Real LLM tests：opt-in only**
- `OPENMC_AGENT_RUN_REAL_LLM_TESTS=1` 环境变量控制
- 默认 skip；CI 不调用真实 LLM

**测试覆盖（13 new tests）：**
- `tests/test_llm_adapter.py`：4 tests（callable wrap / model_name raise / OpenAI client wrap / token budgets）
- `tests/test_incremental_real_llm_eval.py`：6 tests + 1 skipped real LLM（fake eval report / raw_chars / full lattice detection / CLI dry-run / failure report）
- 全量测试：`767 passed, 3 skipped in 62.96s`。无回归。

**VERA3 3B fake eval report summary：**
- ok=True, planning_mode=incremental
- patch_metrics: 7 patches all valid (facts/materials/universes/pin_map/axial_layers/axial_overlays/settings)
- assembly: lattice=[17,17], layers=12, overlays=8, pyrex=16, plugs=8, fuel=264
- guard: blocking=0
- no_monolithic_plan_requested=True

**remaining work：**
1. Real LLM stability evaluation（需真实 API key 手动运行）
2. Runtime OpenMC smoke test（source rejection 修复、plot bounds 修复）
3. Material/alloy fidelity（真实 composition library）
4. Volume-fraction calibrated spacer grid geometry
5. keff benchmark acceptance

#### Phase 7B: Real LLM hardening — no unsafe monolithic fallback and strict patch-output contract

**背景问题：** 真实 LLM (deepseek:deepseek-chat) 在 VERA3 3B 上失败。诊断：incremental executor 被调用但第一个 patch 失败（LLM 不遵守 patch prompt 返回完整 plan）→ `allow_fallback=True` 导致回退 monolithic → 25K JSON 截断 → repair 丢失 axial_layers → guard 拦截。

**核心修改：**

1. **禁止不安全的 monolithic fallback（`graph.py`）：**
   - auto-constructed client 失败时默认 `allow_fallback=False`
   - 例外：LLM 连接错误（`llm_error`）仍允许回退（连接问题，非架构问题）
   - 不再出现：incremental 失败 → 静默回退 monolithic → 25K 截断

2. **禁止 full-plan 输出检测（`patch_generator.py`）：**
   - `patch_generation.full_plan_output_forbidden`（error）— parsed JSON 含 SimulationPlan-only 字段（complex_model, capability_report 等）
   - `patch_generation.pin_map_full_lattice_forbidden`（error）— pin_map >80 coords 或 >3000 chars

3. **强化 patch prompts（`patch_prompts.py`）：**
   - CRITICAL OUTPUT CONTRACT + per-patch minimal examples + 禁止字段列表

4. **Incremental artifact 保存（`graph.py`）：**
   - `<output_dir>/incremental/` 保存 build state + valid/invalid patches（无论成功/失败）

**测试覆盖（6 new tests）：** `773 passed, 3 skipped`。无回归。

**预期行为变化：** 真实 LLM 3B 运行时如果 LLM 不遵守 patch prompt → incremental executor 在 facts patch 失败并停止 → 不回退 monolithic → 不再出现 25K 截断 → 有 patch-level diagnostics。

#### Phase 7C: Structured patch output mode + artifact visibility + contract hardening

**背景问题：** Phase 7B 后真实 LLM 仍不稳定输出合规 patch。失败时 `[9c] Plan artifacts` 显示 `(none)`，无法诊断。

**核心修改：**

1. **Patch contract validation（`patch_generator.py`）：**
   - `validate_patch_contract()` 在 JSON parse 后、model parse 前检查：
     - `patch_type` 字段存在且匹配 → 否则 `patch_type_missing` / `patch_type_mismatch`
     - 禁止 SimulationPlan-only 字段（`complex_model`, `core`, `capability_report` 等）→ `full_plan_output_forbidden`
     - 禁止 pin_map 全展开字段（`universe_pattern` 等）→ `pin_map_full_lattice_forbidden`
     - 允许/禁止字段列表由 `get_patch_allowed_top_level_keys()` / `get_patch_forbidden_top_level_keys()` 提供

2. **Structured output mode（`llm_adapter.py`）：**
   - `StructuredPatchLLMClient` 类：支持 `generate_patch_json()` 方法 + plain `__call__`
   - `output_mode` 参数：`auto`（默认，优先 structured）、`plain_prompt`、`json_object`、`json_schema`
   - `_call_llm_for_patch()` 优先使用 structured client，fallback 到 plain callable

3. **Schema hints（`patches.py`）：**
   - `get_patch_allowed_top_level_keys(patch_type)` → 该 patch 允许的 top-level key 集合
   - `get_patch_forbidden_top_level_keys(patch_type)` → 禁止的 key（含 SimulationPlan-only + pin_map forbidden）
   - `get_patch_json_schema(patch_type)` → Pydantic JSON schema

4. **Prompt hardening（`patch_prompts.py`）：**
   - `build_patch_prompt` 新增 allowed/forbidden keys block
   - retry prompt 针对 `patch_type_missing`/`mismatch`/`parse_error` 有专门消息

5. **Artifact visibility（`graph.py` + `state.py`）：**
   - `_write_incremental_artifacts()` 返回 artifact 路径列表
   - 路径写入 `plan_artifacts`（不再显示 `(none)`）
   - `generate_and_add_patch_to_state` 在失败时保存 attempt raw_text/prompt_text/issues 到 state metadata
   - `patch_attempts/` 目录保存 `<patch>_attempt_<n>_raw.txt` / `_prompt.txt` / `_issues.json`

6. **PatchGenerationAttempt 增强：**
   - 新增 `patch_type`, `prompt_text`, `output_mode_used` 字段

**测试覆盖（5 new tests）：** `778 passed, 3 skipped`。无回归。

#### Phase 7D: Failed patch visibility + reference-backed deterministic patches + resumable execution

**背景：** 真实 LLM (deepseek) 在 VERA3 3B 上成功生成 facts/materials/universes，但在后续 structural patch (pin_map/axial_layers/axial_overlays) 上不稳定。失败摘要没有显示 failed_patch_type，无法诊断。已有 valid patches 无法复用。

**核心修改：**

1. **Reference-backed deterministic patches（`reference_patches.py`）：**
   - `load_benchmark_reference(benchmark_id, variant, reference_path)` — 从 JSON 文件加载
   - `build_reference_patch(patch_type, reference, variant)` — 构建 PinMapPatch/AxialLayersPatch/AxialOverlaysPatch/SettingsPatch
   - 从 `tests/fixtures/vera3_patches/vera3_3a_patches.json` 和 `vera3_3b_patches.json` 加载
   - 不 hardcode 任何 VERA3 数字到代码

2. **Reference patch policy（`executor.py`）：**
   - `reference_only_for_structural`：structural patches (pin_map/axial_layers/axial_overlays) 全部从 reference 来，LLM 只生成 facts/materials/universes
   - `fallback_after_llm_failure`：先让 LLM 生成，失败后用 reference
   - `off`（默认）：纯 LLM

3. **Enhanced failure summary：**
   - `failed_patch_type`, `valid_patch_types`, `invalid_patch_types`, `issue_codes`, `attempt_count`
   - `next_recommended_action: "resume_from_failed_patch"`
   - `monolithic_fallback_attempted: false`
   - `reference_patches_used: [...]`

4. **Resumable incremental execution（`state.py`）：**
   - `save_plan_build_state(state, path)` / `load_plan_build_state(path)`
   - 已 valid patches 自动 skip
   - CLI `--resume-from <dir>` + `--start-at-patch <type>`

5. **CLI 新增 flags：**
   - `--reference-patch-policy off|prefer_reference_for_structural|fallback_after_llm_failure|reference_only_for_structural`
   - `--reference-path <path>`
   - `--resume-from <dir>`
   - `--start-at-patch <type>`

**测试覆盖（13 new tests）：** `791 passed, 3 skipped`。无回归。

**VERA3 3B reference structural execution：**
- facts/materials/universes: LLM 生成
- pin_map/axial_layers/axial_overlays: reference deterministic 生成
- settings: deterministic
- assembly: 17×17 lattice, 16 Pyrex, 8 plugs, 12 layers, 8 overlays
- guard: 0 blocking issues
