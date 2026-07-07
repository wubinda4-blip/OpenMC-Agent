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

- `openmc_agent/` Python 文件：45 个，约 23,938 行。
- `tests/` Python 测试文件：29 个，约 14,105 行。
- 文档策略文件：14+ 个，覆盖 grep、knowledge graph、RAG、GraphRAG、ingestion、ranking、query planner、trace/evaluation、benchmark 等。
- 全量测试：`453 passed in 43.05s`。

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

注意：目前 ingestion graph 可以传给 GraphRAG，但默认 workflow 尚未自动加载持久化 knowledge graph。

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

明确未完成或受限：

- HexAssemblyRenderer 未实现。
- depletion / burnup 未实现。
- pebble_bed renderer 未实现。
- 对复杂 benchmark 的 modeling fidelity 仍不能自动保证。
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
# 453 passed in 43.05s

conda run -n openmc-env python -m compileall -q openmc_agent
# passed
```

本轮新增或重点覆盖：

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

### 7.3 安全边界

RAG / GraphRAG / ingested docs / ranked evidence 都只能作为上下文：

- 不能自动确认 nuclear data path。
- 不能自动确认材料密度或 composition。
- 不能自动确认 benchmark constants。
- 不能自动补齐真实 loading map。

## 8. 下一步建议

我建议下一步优先做 **Knowledge Asset Runtime Loader + Retrieval Config**。

原因：

1. 现在已经能 ingest docs/examples/Input 生成 graph nodes/edges，但默认 workflow 并不会自动读取 `data/knowledge`。
2. GraphRAG 已有 `extra_nodes/extra_edges` 入口，但 orchestrator 还没有 policy/config 把持久化知识资产接入。
3. 这一步能立刻提升 GraphRAG 的实际覆盖面，并且不会改变 renderer 能力边界。
4. 这比马上做 vector search 更稳，因为当前 lexical + graph + planner + ranker 已经足够支持本地确定性检索闭环。

建议实现内容：

- 新增 `KnowledgeGraphStore` 或轻量 loader：
  - `load_ingested_graph(path_or_dir)`
  - `RetrievalPolicy.knowledge_graph_path`
  - `RetrievalContext.knowledge_graph_summary`
- Orchestrator 在 GraphRAG stage 自动加载 extra graph。
- Trace summary 记录：
  - loaded node count
  - loaded edge count
  - knowledge source ids
  - warnings
- CLI 或 inspect 参数支持：
  - `--knowledge-dir data/knowledge`
  - 或环境变量 `OPENMC_AGENT_KNOWLEDGE_DIR`
- 增加 tests：
  - no knowledge dir fallback；
  - invalid knowledge dir warning；
  - loaded ingested HexLattice / cross_sections chunk 可进入 GraphRAG。

第二优先级：真实 evaluation case runner。

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

## 9. 维护记录

### 2026-07-07

完成并验证：

- Knowledge Ingestion Pipeline。
- GraphRAG Evidence Reranker + Dedup + Prompt Budgeter。
- GraphRAG Query Planner + Graph Path Reranking。
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
