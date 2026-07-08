# OpenMC-Agent TODO Roadmap

维护日期：2026-07-08

本文档用于整理项目主线、待完成方向和优先级。它不是单次运行生成的 skeleton `TODO.md`，而是仓库级 roadmap。

优先级约定：

- **P0**：影响主流程可信度、评估闭环或复杂模型可用性的近期必做。
- **P1**：明显提升能力边界或减少人工诊断成本，完成 P0 后推进。
- **P2**：扩展适用范围或工程体验，依赖前两类稳定后推进。
- **P3**：探索性或长期方向，先保持接口与边界清晰。

## 1. P0 主线：真实评估闭环与回归基准

目标：把当前“功能很多但方向发散”的工程，收束到可量化的 case runner。所有 RAG、GraphRAG、incremental、renderer 改动都应能回答“指标是否变好”。

当前状态：

- 已有 `workflow_trace.py`、`evaluation.py`、`benchmark_runner.py`、`tests/fixtures/evaluation_cases.json`。
- 目前 benchmark runner 仍偏 fake/offline，尚未成为真实 workflow 的统一验收入口。
- 全量单测覆盖较好，但真实 LLM / 真实复杂 case 的稳定性仍是 opt-in。

下一步：

1. 建立 lightweight workflow case runner：执行 `plan -> validate -> retrieval/capability`，默认不跑大规模 OpenMC 仿真。
2. 定义核心指标：plan schema success、incremental patch success、retrieval trigger precision、fact-gap preservation、skeleton/runnable 分类准确率、issue code precision/recall、artifact 完整性。
3. 固化首批评估集：pin-cell、2D assembly、3D assembly with overlays、quarter core、fact-gap case、unsupported hex/depletion case。
4. 输出稳定的 `evaluation_report.json` / `benchmark_summary.md`，供 PR 前后对比。

完成标准：

- 一条命令可跑完整评估集。
- 每个主线改动能报告指标变化。
- 失败 case 能定位到 patch type / issue code / renderer / retrieval stage。

## 2. P0 主线：Incremental 分层输出稳定化

目标：复杂模型默认走 input-driven 分层 patch 生成，而不是依赖 monolithic 大 JSON 或 benchmark-specific fixture。

当前状态：

- `plan_builder/` 已实现 `facts -> materials -> universes -> pin_map -> axial_layers -> axial_overlays -> settings` 分层生成、校验、重试、组装。
- reference patch 支持显式策略与 deterministic benchmark id matching；reference 只能作为 gold/reference 回归，不能代替通用建模能力。
- 3D assembly、spacer grid、special pin map 已有 guard、assembler 与 fixture 回归。

下一步：

1. 用真实 LLM 跑多模型 incremental ablation：DeepSeek / GLM / OpenAI 兼容模型，记录每层失败类型。
2. 加强 patch prompt 的最小输出契约：禁止 full plan、禁止 full lattice、要求只输出当前 patch schema。
3. 扩展 patch few-shot：从现有 IR 反推 pin-cell、2D assembly、quarter-core、unsupported skeleton case 的 patch exemplar。
4. 增强 resume / artifact 诊断：失败 run 清晰展示最后有效 patch、失败 patch、LLM raw、validation issue、推荐下一步。
5. 明确 policy 默认值并统一文档：`off`、`prefer_reference_for_structural`、`reference_only_for_structural`、`fallback_after_llm_failure` 的语义必须和 README / 技术报告 / 测试一致。

完成标准：

- VERA3 3B 类复杂 3D assembly 在不使用 reference-only 的情况下能稳定产出可审查 plan。
- 每层 patch 的失败可局部重试，不触发 monolithic reflect。
- policy 行为有单测和 graph integration 测试覆盖。

## 3. P0 主线：事实缺口与安全边界守护

目标：保持“可检索但不编造事实”的边界，尤其是材料密度、composition、核数据库路径、benchmark 常数、真实 loading map。

当前状态：

- RAG / GraphRAG / few-shot / ingested docs 均声明不能自动确认物理事实。
- validator、error catalog、retrieval orchestrator 已能保留 human confirmation route。
- 部分材料仍使用 approximate 纯元素 fallback，并带 warning。

下一步：

1. 建立 fact-gap regression suite：材料密度缺失、composition 缺失、cross section path 缺失、benchmark loading map 缺失都必须保持 human confirmation。
2. 对 retrieval evidence 加安全分类：context-only、repair-hint、fact-source-candidate、unsafe-auto-fill。
3. 在 transcript / evaluation report 中显式统计 fact-gap preservation。
4. 把 approximate composition 与 runnable/exportable 边界进一步收紧，避免“近似可运行”被误读为 benchmark-accurate。

完成标准：

- 所有 fact-gap case 不会被 RAG/GraphRAG/few-shot 自动填实。
- 用户能在报告里看到哪些数据是确认事实，哪些只是 illustrative/approximate。

## 4. P1 主线：检索体系收敛与效果评估

目标：把 grep / graph / GraphRAG / RAG 从“多条能力线”收敛成可解释、可评估、可调权重的 retrieval stack。

当前状态：

- 默认链路：`grep -> graph -> GraphRAG query planner -> GraphRAG -> plain RAG -> evidence ranking`。
- 已有 query planner、evidence ranker、knowledge ingestion/runtime loader。
- 权重仍是 heuristic，缺少真实 benchmark calibrated evidence。

下一步：

1. 在评估 runner 中加入 retrieval ablation：关闭 grep、关闭 graph、关闭 GraphRAG、关闭 RAG、不同 ranker 权重。
2. 标注 evidence 对修复是否有用：helpful / irrelevant / unsafe / redundant。
3. 做 query planner confusion matrix：issue intent 是否选对 start nodes、depth、avoid filters。
4. 将 ingested knowledge graph 与 hand-written registry 的冲突、重复、陈旧节点可视化或导出诊断。
5. 清理策略文档：把 10 个 retrieval/knowledge strategy 文档合并为“当前实现”和“待实验”两层，减少维护分叉。

完成标准：

- 每次 retrieval 改动能给出 ablation 对比。
- ranker 权重调整有指标依据。
- fact-gap 类 issue 的 unsafe evidence 不会进入 auto-repair 路径。

## 5. P1 主线：Renderer 能力与物理 fidelity

目标：在不扩大 LLM 执行权限的前提下，提高可导出/可运行模型的物理表达能力。

当前状态：

- 已有 PinCell、RectAssembly、Core、TRISO、Skeleton。
- 3D assembly axial layers 与 Level 1 `homogenized_open_region` overlay 可表达 spacer/support 类结构，但不是体积分数标定模型。
- HexAssembly、depletion/burnup、pebble-bed renderer 仍未实现。
- **边界条件无验证**：LLM 提取的边界条件（reflective/vacuum）与输入文档之间没有对照检查；renderer 曾因 fallback 逻辑 bug 将径向面设为 vacuum 导致 57% 泄漏率。

下一步：

1. 材料/合金 composition fidelity：建立材料库或受控 resolver，替代 Zircaloy/SS/Inconel 等纯元素近似。
2. Volume-fraction calibrated overlay：为 spacer grid/support plate 加体积/质量标定字段、验证和 renderer。
3. HexAssemblyRenderer：先做 skeleton-to-exportable 的最小闭环，覆盖 OpenMC `HexLattice` rings / pitch / outer universe。
4. Depletion/burnup：先建立 IR 与 settings/operator 边界，不急于跑重计算。
5. Pebble-bed / stochastic geometry：先定义 capability boundary 和 skeleton 输出，避免伪 runnable。
6. **边界条件验证**：
   - FactsPatch 增加显式边界字段（`radial_boundary` / `axial_boundary`），从输入文档提取，不 hardcode 堆型假设。
   - Validator 增加边界合理性检查：泄漏率 >30% 或 keff 远偏离 1 时发出 warning（非 error，因为不同模型可能合理偏离）。
   - Renderer 渲染后自检：扫描导出 XML 的 `boundary_type`，与 plan 中的 `assembly.boundary` / `core.boundary` 对照，不一致时记录诊断。

完成标准：

- 新 renderer 能通过 capability report 明确声明 `skeleton/exportable/runnable`。
- 不支持的 subsystem 保持 skeleton 或 human confirmation，不伪装成功。
- 物理近似都进入报告和 TODO，而不是静默进入 runnable 结论。
- 边界条件在 plan → render → XML 链路中可追溯、可验证。

## 6. P1 主线：复杂 benchmark 验收

目标：从“结构正确”推进到“benchmark workflow 可复现”，但不把 benchmark facts 硬编码进 production code。

当前状态：

- VERA3 3B 有 reference fixture、patch fixture、acceptance helper 和 3D guard 回归。
- full keff benchmark acceptance 尚未完成。
- 输入文档与 PDF 资料在工作区存在用户维护的脏文件，尚未纳入受控数据流程。

下一步：

1. 明确 benchmark data ownership：哪些是测试 fixture，哪些是用户输入资料，哪些可进入 `data/benchmarks/`。
2. 做 VERA3 3B full workflow acceptance：plan、render、XML export、plots、smoke test、可选 low-particle keff sanity。
3. 增加 fixture provenance：每个 reference patch 记录来源、转写边界、不能自动当作事实 source 的说明。
4. 为 VERA2 / C5G7 / pin-cell 建立小型 acceptance case，优先覆盖结构类型而非堆型名称。

完成标准：

- benchmark fixture 的使用路径清楚：测试 reference、显式 reference policy、或用户输入，不混用。
- production 代码仍保持堆型无关，不写死 VERA/C5G7 特例。

## 7. P2 主线：工程化与用户体验

目标：降低使用和诊断成本，让项目从研究原型走向可持续维护。

下一步：

1. CLI 增加 `--eval-case` / `--benchmark-suite` / `--retrieval-ablation` 入口。
2. 统一 run artifact 结构：plan、incremental、retrieval、trace、verification、tool outputs 分目录。
3. 生成面向用户的 run summary：一句话状态、renderability、阻塞原因、需要用户确认的数据。
4. CI 分层：fast unit、integration、real OpenMC、real LLM opt-in。
5. 清理或归档历史 strategy 文档，保留 `docs/project_technical_report.md` + `TODO.md` 作为当前入口。

完成标准：

- 新用户能通过 README + TODO 理解当前推荐路径。
- 失败 run 能在 1-2 个 artifact 中定位原因。
- CI 不依赖远程模型，真实 LLM 有明确 opt-in 标记。

## 8. P2 主线：Prompt、Schema 与 Error Taxonomy 整理

目标：减少隐性规则散落在 prompt、validator、renderer、few-shot、guard 中导致的行为漂移。

下一步：

1. 建立 schema-rule inventory：每个重要字段对应 validator、renderer consumer、retrieval concept、error code。
2. 清理 prompt 中可能带 benchmark-specific bias 的描述，只保留通用机制。
3. 对 error catalog 做 coverage audit：哪些 issue 有 route hint、retrieval hint、human confirmation hint、测试。
4. 将 common repair rules 从 prompt 转移到 deterministic repair / validator hints，减少 LLM 自由发挥。

完成标准：

- 新增 IR 字段必须同时说明 validation、rendering、retrieval、failure mode。
- issue code 的 route 行为稳定可测。

## 9. P3 探索方向：Renderer Authoring 与自动扩展

目标：保留 agent 在线编写 renderer 的接口，但在安全机制成熟前不进入默认主流程。

当前状态：

- `renderer_authoring/` 是受控 stub / sandbox 方向，主流程未启用。

下一步：

1. 只允许生成候选 patch/PR，不允许自动执行未知 renderer。
2. 加入 static checks、sandbox tests、capability contract tests。
3. 在 benchmark runner 中评估新 renderer 是否真的提升 renderability。

完成标准：

- 自动生成代码必须先进入 review/test，不直接进入生产渲染路径。

## 10. 建议推进顺序

近期建议按以下顺序推进：

1. **P0-1 真实评估闭环**：先让项目有统一尺子。
2. **P0-2 Incremental 稳定化**：用评估集压真实 LLM 分层输出。
3. **P0-3 Fact-gap 安全边界**：防止检索和 few-shot 带来错误“自动填事实”。
4. **P1-4 Retrieval ablation**：用指标决定 RAG/GraphRAG 哪些部分值得保留或调权。
5. **P1-5 Renderer fidelity**：材料库、volume-fraction overlay、HexAssembly 按收益推进。
6. **P1-6 Benchmark acceptance**：把 VERA3 3B 从结构验收推进到 workflow/keff sanity。
7. **P2 工程化与文档收敛**：在主线稳定后整理 CLI、artifact、CI、文档入口。

## 11. 当前不建议做的事

- 不要把 VERA/C5G7 等 benchmark facts 写死进 production prompt、validator、renderer 或 guard。
- 不要让 RAG/GraphRAG 自动确认材料密度、composition、核数据库路径、benchmark 常数或真实 loading map。
- 不要为了单个 benchmark 直接扩大 renderer 能力边界；unsupported subsystem 应保持 skeleton/human confirmation。
- 不要继续增加新的 strategy 文档而不合并旧文档；优先维护本 TODO 和技术报告。
- 不要用 monolithic `reflect_plan` 作为复杂模型分层失败的默认兜底。
