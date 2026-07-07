# OpenMC-Agent Docs Index


## 推荐阅读顺序

1. `project_technical_report.md`  
   当前项目进度、架构总览、测试状态、风险边界和下一步建议。以后每个重要工程 Step 都应更新这里。

2. 检索与知识层：
   - `grep_search_strategy.md`
   - `knowledge_graph_strategy.md`
   - `rag_search_strategy.md`
   - `graphrag_retriever_strategy.md`
   - `graphrag_query_planner_strategy.md`
   - `knowledge_ingestion_strategy.md`
   - `evidence_ranking_strategy.md`
   - `retrieval_orchestrator_strategy.md`

3. 可观测与评估：
   - `trace_and_evaluation_strategy.md`
   - `benchmark_and_ablation_strategy.md`

## 维护规则

- 新增大功能时，先更新对应 strategy 文档，再更新 `project_technical_report.md`。
- 如果某个文档只记录历史计划且实现已落地，应合并到报告或 strategy 文档后删除。
- 文档中的能力边界必须与代码一致，尤其是 renderer 能力、fact gap human confirmation、GraphRAG/RAG 不确认物理事实这些约束。
- 文档不应保留“future GraphRAG / no RAG”这类与当前实现冲突的描述。
