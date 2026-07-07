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

明确未完成或受限：

- HexAssemblyRenderer 未实现。
- depletion / burnup 未实现。
- pebble_bed renderer 未实现。
- 对其他复杂 benchmark 的 modeling fidelity 仍不能自动保证。
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

## 9. 维护记录

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
