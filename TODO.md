# OpenMC-Agent TODO Roadmap

维护日期：2026-07-10

本文档用于整理项目主线、待完成方向和优先级。它不是单次运行生成的 skeleton `TODO.md`，而是仓库级 roadmap。

## 重大里程碑（更新于 2026-07-10）

VERA3 3A 和 3B 均通过 incremental plan builder 端到端成功运行：

| 指标 | 3A | 3B |
|---|---|---|
| Patch 全部 LLM 生成 | ✓ (6 patches) | ✓ (6 patches + deterministic settings) |
| 17×17 base lattice 展开 | ✓ (264 fuel + 24 GT + 1 IT) | ✓ (264 fuel + 24 GT + 1 IT) |
| 有限轴向插入件 | - | ✓ (16 Pyrex 通过 `lattice_loadings` 覆盖毒物棒轴向段，plug/空导向管中段保持水) |
| 轴向 12 层 | ✓ | ✓ |
| 8 个 spacer grid overlays | ✓ | ✓ |
| ZCylinder 几何自动构建 | ✓ (6 surfaces, 9 regions) | ✓ (10 surfaces, 15 regions) |
| 边界条件 (radial=reflective, axial=vacuum) | ✓ | ✓ |
| OpenMC smoke test | ✓ passed | ✓ passed |
| keff | ~1.149 (偏高，合金近似) | **0.979** ± 0.004 |
| Leakage | 0% | 低 |

关键架构突破：
- 不再依赖 monolithic 25K JSON（incremental patch 生成避免了截断问题）
- 不再依赖 reference fixture（LLM 直接生成所有 patch，coord_overlap 确定性修复）
- assembler 自动从 CellLayerPatch 构建 ZCylinder surfaces + regions
- assembler 通用归一化有限轴向插入件：base lattice 保持水填充导向管，Pyrex/control/insert 类结构进入 `lattice_loadings`
- 空 `base_loading` 不再覆盖真实 insert loading，避免 3B 退化成 3A-like 水导向管模型
- 边界条件从实际几何推导（不 hardcode）
- 元素符号（He/Zr/Fe/Ni）自动路由到 add_element
- 受控合金 composition policy 已接入：Zircaloy-4 / SS-304 / Inconel-718 的纯元素近似可替换为 nominal 合金成分，并产出 `material_composition_report.json`
- workflow benchmark 基础版已可输出 `evaluation_report.json`、`benchmark_summary.md`、per-case trace 和 artifact summary
- **P0-A LLM Semantic Plan Auditor 已完成**（2026-07-10）：只读语义审查接入 workflow，warning-only 不改 pass/fail，strict 仅 evaluation mismatch 失败；semantic fake benchmark 13/13。
- **P0-B LLM Patch Repair Proposer 已完成**（2026-07-10）：受限 JSON Patch + path allowlist + protected path policy + clone executor + before/after validation；默认 proposal-only，不运行 OpenMC、不改科学事实；repair fake benchmark 16/16。

优先级约定：

- **P0**：影响主流程可信度、评估闭环或复杂模型可用性的近期必做。
- **P1**：明显提升能力边界或减少人工诊断成本，完成 P0 后推进。
- **P2**：扩展适用范围或工程体验，依赖前两类稳定后推进。
- **P3**：探索性或长期方向，先保持接口与边界清晰。

## 1. P0 新主线：LLM 智能化闭环

目标：在保持“LLM 只生成结构化计划、不直接执行代码”的安全边界下，把更多目前依赖确定性 Python glue 的决策环节升级为可观测、可回退、可评估的 LLM 协作环节，逐步接近 Claude/Codex 类成熟 agent 的任务理解、工具选择、反思修复和长期改进能力。

设计原则：

- LLM 做语义判断、任务分解、候选方案、审查和修复建议；Python 仍做 schema 校验、硬约束、渲染、OpenMC 执行和回归 gate。
- 每个新增 LLM 环节必须有 deterministic fallback、trace 记录、可离线 fake 测试和评估指标。
- 不把 benchmark facts、材料密度、真实 loading map、核数据库路径交给 LLM 自动确认。
- 先做“只读审查/建议”再做“受控结构化 patch”，最后才考虑受限代码生成。

### P0-A：LLM Semantic Plan Auditor ✅ 已完成（2026-07-10）

目的：在 deterministic validator 之后增加一个只读语义审查环节，检查“输入文档事实、LLM patch、assembled plan、renderer 能力声明”之间是否一致。

> 状态（2026-07-10）：已完成。已实现 `semantic_audit` schema / input builder / prompt / client / fake / fallback，接入 workflow trace/artifacts 与 benchmark audit metrics；warning-only 默认不改变 route/pass-fail，strict 仅在 evaluation expectation mismatch 时失败。新增 semantic regression fixture 与 8 个测试文件；semantic fake benchmark 13/13 pass_rate=100%。下方步骤保留为设计参考。

优先覆盖：

1. 轴向语义：有限插入件是否应进入 `lattice_loadings`，base lattice 是否错误承载了只在部分轴向出现的结构。
2. 材料语义：材料 composition policy 是 nominal approximation、plan-provided 还是缺失确认，报告是否说清。
3. 几何语义：3D/2D、边界条件、spacer overlay、source bounds 是否和输入描述冲突。
4. Reference 路径审计：复杂模型默认必须是 unseen-model path；reference 只能显式 policy 或 regression gold 使用。

实现步骤：

1. 新增 `semantic_audit` schema：输入摘要、引用 evidence、findings、severity、suggested_patch_target、requires_human_confirmation。
2. 在 workflow trace 中记录 audit 输入和输出；默认先 warning-only，不改变 plan。
3. 将 VERA3 3B 导向管/毒物棒 bug 作为首批 semantic regression case：auditor 应能指出 base pin map 和 axial loading 语义不一致。
4. 指标加入 workflow benchmark：audit finding precision、false positive rate、是否提前发现已知结构错误。

完成标准：

- auditor 不直接修改 plan，但能在报告中稳定指出结构语义冲突。
- warning-only 模式不会降低现有 fake benchmark 通过率。
- 至少覆盖 VERA3 3B、2D assembly、3D spacer grid、fact-gap 四类 case。

### P0-B：LLM Patch Repair Proposer ✅ 已完成（2026-07-10）

目的：把当前 deterministic auto-repair 覆盖不到的问题交给 LLM 生成“受限 JSON Patch 候选”，再由本地 validator、schema path allowlist 和回归测试决定是否采纳。

> 状态（2026-07-10）：已完成。新增 repair proposal schema、issue→path allowlist、protected path policy、JSON Patch clone executor、before/after deterministic validation、accept/reject/unsafe 判定、prompt/fake client/fallback，接入 workflow trace/artifacts 与 benchmark metrics/CLI。默认 proposal-only，不运行 OpenMC、不改科学事实；validate-only 只作用于 clone。repair proposal tests 19 passed，repair fake benchmark 16/16 pass_rate=100%。下方步骤保留为设计参考。

实现步骤：

1. 为每个 issue code 定义可修改 schema path allowlist，禁止 LLM patch 触碰 materials facts、benchmark constants、nuclear data path 等高风险字段。
2. LLM 只输出 RFC6902-style patch 或 patch-schema 层级的结构化修复，不输出 Python 代码。
3. 应用前后都跑 deterministic validation；失败则保留为建议，不进入 plan。
4. 对 accepted/rejected patch 记录 rationale，作为后续 few-shot / regression 数据。

完成标准：

- deterministic auto-repair 仍优先；LLM repair 只处理 allowlist 内问题。
- 每次 repair 都能解释“修了什么、为什么允许、验证是否通过”。
- benchmark report 统计 accepted/rejected/unsafe repair 数量。

### P0-C：LLM Run Supervisor ⬜ 待做（下一步）

目的：让 Agent 像成熟编码 agent 一样管理一次建模 run：根据 trace 决定下一步是继续生成 patch、局部重试、请求用户确认、降级 skeleton、运行 smoke test，还是停止并解释阻塞。

实现步骤：

1. 增加 `next_action_decision` schema：候选动作、依据事件、风险、是否需要用户确认。
2. 先在 `scripts/evaluate_incremental_planning.py` / workflow benchmark 中离线复盘历史 trace，不在线改变流程。
3. 通过 replay 评估 supervisor 是否比当前硬编码 router 更早定位失败 patch 和阻塞原因。
4. 达标后以 feature flag 接入真实 workflow，默认 conservative。

完成标准：

- supervisor 的动作建议可复现、可审计，不依赖隐藏上下文。
- 错误定位粒度至少到 patch type / issue code / renderer stage。
- 不会绕过 fact-gap human confirmation。

### P1-D：LLM Evidence Synthesizer ⬜ 待做

目的：把 grep/graph/GraphRAG/RAG 的多路证据压缩成“可用于下一步 prompt 或人工审查”的结构化 evidence brief，减少 prompt 噪声并提升修复质量。

实现步骤：

1. 输入 ranked evidence，输出 claims、supporting locators、contradictions、unsafe-auto-fill flags。
2. 对 retrieval ablation 增加 synthesis quality 指标：useful / irrelevant / unsafe / redundant。
3. 将 evidence brief 接入 semantic auditor 和 patch repair proposer，而不是直接堆原始片段。

完成标准：

- prompt evidence 更短、更可追踪。
- unsafe fact evidence 不进入自动修复路径。

### P1-E：LLM Task Decomposer For Unseen Models ⬜ 待做

目的：替代一部分硬编码 feature detection，让 LLM 根据输入文档提出“需要哪些 patch、哪些 renderer 能力、哪些事实缺口、哪些验证步骤”，再由本地 policy 约束。

实现步骤：

1. 新增 `modeling_task_plan`：patch order、required capabilities、known facts、missing facts、risk tags。
2. 与现有 `should_use_incremental_planning` 并行运行，先只做对比，不改变路由。
3. 评估 LLM decomposer 对复杂模型、unsupported subsystem、fact-gap case 的召回率。
4. 达标后允许它影响 patch order 或追加审查步骤，但不允许跳过 mandatory validation。

完成标准：

- 对未知复杂模型能提出比纯规则更多的结构风险点。
- 对简单 pin-cell 不引入额外复杂度。

### P2-F：Accepted-Run Memory And Few-Shot Mining ⬜ 待做

目的：从通过验证的真实 run 中自动抽取结构模式、失败修复和审查摘要，形成可审计的局部记忆，而不是手工维护越来越多 few-shot。

实现步骤：

1. 定义 accepted-run criteria：schema success、validator pass、renderability、可选 smoke pass、人工确认状态。
2. 从 accepted traces 中抽取 anonymized patch exemplar、failure signature、repair rationale。
3. 写入可版本化的 `data/few_shot_cases/` 候选区，需要人工 review 后晋升为正式 few-shot。

完成标准：

- 自动挖掘不直接污染 production prompts。
- 每个晋升 exemplar 都有 provenance 和适用边界。

### P3-G：Agent-Authored Renderer PR Pipeline ⬜ 待做

目的：保留“LLM 编写新 renderer”的长期能力，但只允许生成候选 PR，不允许在线执行未知 renderer。

实现步骤：

1. 让 LLM 从 unsupported capability report 生成 renderer design note、schema contract 和候选代码 patch。
2. 代码必须通过 AST/static checks、sandbox tests、capability contract tests。
3. 只以 draft PR / review artifact 形式交付，人工合并后才进入 renderer registry。

完成标准：

- 自动生成 renderer 不进入默认运行路径。
- 新 renderer 的收益必须由 benchmark runner 证明。

## 2. P0 主线：真实评估闭环与回归基准

目标：把当前“功能很多但方向发散”的工程，收束到可量化的 case runner。所有 RAG、GraphRAG、incremental、renderer 改动都应能回答“指标是否变好”。

当前状态：

- 已有 `workflow_trace.py`、`evaluation.py`、`benchmark_runner.py`、`tests/fixtures/evaluation_cases.json`。
- 已有 `workflow_case_runner.py` / `workflow_benchmark.py` 和 `scripts/run_workflow_benchmark.py`；默认 fake、plan-only、不调用 OpenMC、不调用真实 LLM。
- benchmark 输出 `evaluation_report.json`、`benchmark_summary.md`、per-case trace 和 artifact summary；fake workflow 已作为本仓库提交前 gate。
- 真实 LLM / 真实复杂 case 的稳定性仍是 opt-in，尚未形成长期 baseline 和 dashboard。

下一步：

1. ✅ semantic audit / LLM repair 指标已接入 workflow benchmark（audit metrics + repair metrics/CLI）；run supervisor 指标待 P0-C 完成后接入。
2. 建立真实 LLM opt-in baseline：deepseek / GLM / OpenAI-compatible 模型分别记录 patch success、audit findings、repair success 和 cost/latency。
3. 增加 retrieval ablation 与 LLM-stage ablation：关闭 semantic audit、repair proposer、evidence synthesizer 后对比指标。
4. 固化首批复杂评估集：pin-cell、2D assembly、3D assembly with overlays、quarter core、fact-gap case、unsupported hex/depletion case、VERA3 3B axial insert regression。
5. 为 benchmark report 增加趋势对比：pass rate、new failures、regressions、unsafe repair count、human confirmation preservation。

完成标准：

- 一条命令可跑完整评估集。
- 每个主线改动能报告指标变化。
- 失败 case 能定位到 patch type / issue code / renderer / retrieval stage。

## 3. P0 主线：Incremental 分层输出稳定化（已达成主要目标，继续防回归）

目标：复杂模型默认走 input-driven 分层 patch 生成，而不是依赖 monolithic 大 JSON 或 benchmark-specific fixture。

当前状态：

- `plan_builder/` 已实现 `facts -> materials -> universes -> pin_map -> axial_layers -> axial_overlays -> settings` 分层生成、校验、重试、组装。
- **VERA3 3A 和 3B 均通过真实 LLM (deepseek) incremental pipeline 端到端成功运行**，不使用 reference fixture。
- coord_overlap 确定性修复（同坐标保留高优先级组）。
- 有限轴向插入件归一化已修复：base lattice 保持 guide-tube 水通道，Pyrex 等 insert 进入 `lattice_loadings`。
- 空 `base_loading` 覆盖真实 insert loading 的问题已修复。
- assembler 自动从 CellLayerPatch 构建 ZCylinder surfaces + regions。
- 边界条件从实际几何推导（不 hardcode）。
- reference patch 支持显式策略与 deterministic benchmark id matching；reference 只能作为 gold/reference 回归，不能代替通用建模能力。
- 3D assembly、spacer grid、special pin map 已有 guard、assembler 与 fixture 回归。

下一步：

1. 扩展真实 LLM 测试到其他模型（GLM / OpenAI 兼容模型），记录每层失败类型。
2. 加强 patch prompt 的最小输出契约：禁止 full plan、禁止 full lattice、要求只输出当前 patch schema。
3. 扩展 patch few-shot：从现有 IR 反推 pin-cell、2D assembly、quarter-core、unsupported skeleton case 的 patch exemplar。
4. 增强 resume / artifact 诊断：失败 run 清晰展示最后有效 patch、失败 patch、LLM raw、validation issue、推荐下一步。
5. 清理文档中仍暗示默认 reference-only 的内容；production 默认应按 unseen model path 处理，reference 仅显式策略启用。
6. 将 3B axial insert regression 加入正式 workflow benchmark，而不只停留在 unit/fixture 测试。

完成标准：

- ✅ VERA3 3B 类复杂 3D assembly 在不使用 reference-only 的情况下能稳定产出可审查 plan。
- ✅ 每层 patch 的失败可局部重试，不触发 monolithic reflect。
- policy 行为有单测和 graph integration 测试覆盖。

## 4. P0 主线：事实缺口与安全边界守护

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
5. 将 LLM semantic auditor / repair proposer 的 unsafe-auto-fill 行为纳入 regression：任何自动填密度、composition、真实 loading map 的建议都必须被拦截。

完成标准：

- 所有 fact-gap case 不会被 RAG/GraphRAG/few-shot 自动填实。
- 用户能在报告里看到哪些数据是确认事实，哪些只是 illustrative/approximate。

## 5. P1 主线：检索体系收敛与效果评估

目标：把 grep / graph / GraphRAG / RAG 从“多条能力线”收敛成可解释、可评估、可调权重的 retrieval stack。

当前状态：

- 默认链路：`grep -> graph -> GraphRAG query planner -> GraphRAG -> plain RAG -> evidence ranking`。
- 已有 query planner、evidence ranker、knowledge ingestion/runtime loader。
- 权重仍是 heuristic，缺少真实 benchmark calibrated evidence。

下一步：

1. 在评估 runner 中加入 retrieval ablation：关闭 grep、关闭 graph、关闭 GraphRAG、关闭 RAG、不同 ranker 权重。
2. 标注 evidence 对修复是否有用：helpful / irrelevant / unsafe / redundant。
3. 做 query planner confusion matrix：issue intent 是否选对 start nodes、depth、avoid filters。
4. 增加 LLM Evidence Synthesizer，将多路 evidence 结构化成 claims / contradictions / unsafe flags。
5. 将 ingested knowledge graph 与 hand-written registry 的冲突、重复、陈旧节点可视化或导出诊断。
6. 清理策略文档：把 10 个 retrieval/knowledge strategy 文档合并为“当前实现”和“待实验”两层，减少维护分叉。

完成标准：

- 每次 retrieval 改动能给出 ablation 对比。
- ranker 权重调整有指标依据。
- fact-gap 类 issue 的 unsafe evidence 不会进入 auto-repair 路径。

## 6. P1 主线：Renderer 能力与物理 fidelity

目标：在不扩大 LLM 执行权限的前提下，提高可导出/可运行模型的物理表达能力。

当前状态：

- 已有 PinCell、RectAssembly、Core、TRISO、Skeleton。
- 3D assembly axial layers 与 Level 1 `homogenized_open_region` overlay 可表达 spacer/support 类结构，但不是体积分数标定模型。
- HexAssembly、depletion/burnup、pebble-bed renderer 仍未实现。
- 受控合金 composition library 已实现，替代了最危险的纯元素结构合金近似；仍需用 material policy comparison 和 benchmark acceptance 验证 keff 改善。
- **边界条件无验证**：LLM 提取的边界条件（reflective/vacuum）与输入文档之间没有对照检查；renderer 曾因 fallback 逻辑 bug 将径向面设为 vacuum 导致 57% 泄漏率。

下一步：

1. Material policy comparison：对 VERA3 3A/3B 分别跑 `preserve_plan` vs `apply_alloy_library`，量化 keff delta 和 material report。
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

## 7. P1 主线：复杂 benchmark 验收

目标：从“结构正确”推进到“benchmark workflow 可复现”，但不把 benchmark facts 硬编码进 production code。

当前状态：

- VERA3 3B 有 reference fixture、patch fixture、acceptance helper 和 3D guard 回归。
- 3B 未见模型路径的 guide-tube/Pyrex axial loading bug 已修复并提交，现有旧 run 需要重新生成 artifacts 才能体现修复。
- full keff benchmark acceptance 尚未完成。
- 输入文档与 PDF 资料在工作区存在用户维护的脏文件，尚未纳入受控数据流程。

下一步：

1. 明确 benchmark data ownership：哪些是测试 fixture，哪些是用户输入资料，哪些可进入 `data/benchmarks/`。
2. 做 VERA3 3B full workflow acceptance：plan、render、XML export、plots、smoke test、可选 low-particle keff sanity。
3. 增加 fixture provenance：每个 reference patch 记录来源、转写边界、不能自动当作事实 source 的说明。
4. 为 VERA2 / C5G7 / pin-cell 建立小型 acceptance case，优先覆盖结构类型而非堆型名称。
5. 把“production 不走 reference path”的 acceptance 做成显式 gate：同一 case 应能在 `reference_policy=off` 下产出可审查结构，reference 只用于对照。

完成标准：

- benchmark fixture 的使用路径清楚：测试 reference、显式 reference policy、或用户输入，不混用。
- production 代码仍保持堆型无关，不写死 VERA/C5G7 特例。

## 8. P2 主线：工程化与用户体验

目标：降低使用和诊断成本，让项目从研究原型走向可持续维护。

下一步：

1. CLI 增加 `--eval-case` / `--benchmark-suite` / `--retrieval-ablation` 入口。
2. 统一 run artifact 结构：plan、incremental、retrieval、trace、verification、tool outputs 分目录。
3. 生成面向用户的 run summary：一句话状态、renderability、阻塞原因、需要用户确认的数据。
4. CI 分层：fast unit、integration、real OpenMC、real LLM opt-in。
5. 清理或归档历史 strategy 文档，保留 `docs/project_technical_report.md` + `TODO.md` 作为当前入口。
6. 增加 LLM supervisor 的用户可读摘要：本轮做了哪些判断、哪些被 deterministic gate 拦截、下一步为什么停止。

完成标准：

- 新用户能通过 README + TODO 理解当前推荐路径。
- 失败 run 能在 1-2 个 artifact 中定位原因。
- CI 不依赖远程模型，真实 LLM 有明确 opt-in 标记。

## 9. P2 主线：Prompt、Schema 与 Error Taxonomy 整理

目标：减少隐性规则散落在 prompt、validator、renderer、few-shot、guard 中导致的行为漂移。

下一步：

1. 建立 schema-rule inventory：每个重要字段对应 validator、renderer consumer、retrieval concept、error code。
2. 清理 prompt 中可能带 benchmark-specific bias 的描述，只保留通用机制。
3. 对 error catalog 做 coverage audit：哪些 issue 有 route hint、retrieval hint、human confirmation hint、测试。
4. 将 common repair rules 从 prompt 转移到 deterministic repair / validator hints，减少 LLM 自由发挥。
5. 为 LLM auditor / repair / supervisor 增加独立 schema 和 issue taxonomy，避免把自然语言自由审查混入核心 SimulationPlan。

完成标准：

- 新增 IR 字段必须同时说明 validation、rendering、retrieval、failure mode。
- issue code 的 route 行为稳定可测。

## 10. P3 探索方向：Renderer Authoring 与自动扩展

目标：保留 agent 在线编写 renderer 的接口，但在安全机制成熟前不进入默认主流程。

当前状态：

- `renderer_authoring/` 是受控 stub / sandbox 方向，主流程未启用。

下一步：

1. 只允许生成候选 patch/PR，不允许自动执行未知 renderer。
2. 加入 static checks、sandbox tests、capability contract tests。
3. 在 benchmark runner 中评估新 renderer 是否真的提升 renderability。
4. 与 P3-G 保持一致：只产出设计说明、候选 patch 或 draft PR，不进入默认 renderer registry。

完成标准：

- 自动生成代码必须先进入 review/test，不直接进入生产渲染路径。

## 11. 建议推进顺序（更新于 2026-07-10）

VERA3 3A/3B 端到端成功后，重心从"能不能跑通"转向"更智能、更可审计、更不容易静默跑错"：

1. ✅ **P0-A Semantic Plan Auditor**（已完成 2026-07-10）：warning-only 语义审查已接入 workflow 与 benchmark。
2. ✅ **P0-B Patch Repair Proposer**（已完成 2026-07-10）：allowlist 内结构化 repair patch + 本地 validator 采纳判定。
3. **P0-C Run Supervisor**（下一步）：先离线 replay trace，再以 feature flag 接入真实 workflow。
4. **P0 评估闭环升级**：auditor/repair 指标已纳入 benchmark；supervisor 指标待 P0-C；下一步建立真实 LLM opt-in baseline 与 regression diff。
5. **P0 Fact-gap 安全边界**：防止检索、few-shot 和新增 LLM 环节带来错误"自动填事实"。
6. **P1 Renderer fidelity**：
   - 边界条件验证（已加入 TODO）
   - Volume-fraction calibrated overlay
   - HexAssembly renderer
7. **P1 Benchmark acceptance**：把 VERA3 3B 从结构验收推进到 keff 对标，同时保证 `reference_policy=off` 可独立验收。
8. **P1 Evidence Synthesizer / Task Decomposer**：在安全边界稳定后提升检索压缩和未知模型任务分解能力。
9. **P2 Accepted-run memory**：从通过验证的 run 中挖掘候选 few-shot，但必须人工晋升。
10. **P2 工程化与文档收敛**。

## 12. 当前不建议做的事

- 不要把 VERA/C5G7 等 benchmark facts 写死进 production prompt、validator、renderer 或 guard。
- 不要让 RAG/GraphRAG 自动确认材料密度、composition、核数据库路径、benchmark 常数或真实 loading map。
- 不要让新增 LLM 环节直接写最终 `model.py`、直接调用 OpenMC、直接改 renderer registry。
- 不要把一次通过的真实 LLM 输出无审查地加入 few-shot；必须有 provenance、anonymization 和适用边界。
- 不要为了单个 benchmark 直接扩大 renderer 能力边界；unsupported subsystem 应保持 skeleton/human confirmation。
- 不要继续增加新的 strategy 文档而不合并旧文档；优先维护本 TODO 和技术报告。
- 不要用 monolithic `reflect_plan` 作为复杂模型分层失败的默认兜底。
