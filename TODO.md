# OpenMC-Agent TODO Roadmap

维护日期：2026-07-12  
当前基线：`main`，最近确认提交 `c983752`（Fix 8 outdated OpenMC test fixtures）  
全量测试：1377 collected — 非-OpenMC `994 passed, 1 skipped`；OpenMC `380 passed, 2 skipped`；零失败

本文档用于维护仓库级主线、已完成能力、近期任务和长期方向。  
它不是单次运行生成的 skeleton `TODO.md`，也不应记录一次性调试日志。

---

## 0. 当前阶段判断

项目已完成从"LLM 一次性生成大计划"到"分层规划、语义审查、受限修复、受控路由、确定性渲染和回归评估"的主要架构升级，并已建立确定性结构修复闭环（pin-map / shoulder-gap / grid-loading 三级 oracle）。

当前重心：

```text
过去：复杂模型能否生成并运行
现在：生成的模型是否具有可信的几何、材料和 benchmark fidelity
```

近期主线不再是继续增加新的 Agent 模块，而是：

1. 把 VERA3 3A/3B 打磨为可信的 geometry gold model；
2. 完成材料和等效结构 fidelity；
3. 建立几何与数值双重 benchmark acceptance；
4. 再进行真实 LLM unseen-path 重建；
5. 最后才把通过验收的知识写入 benchmark memory 和匿名 few-shot。

### 状态总览

| 能力 | 状态 |
|---|---|
| Incremental patch planning | ✅ 已完成并作为复杂模型默认路径 |
| LLM Semantic Plan Auditor | ✅ 已完成 |
| LLM Patch Repair Proposer | ✅ 已完成 |
| LLM Run Supervisor | ✅ 已完成 |
| Invalid incremental plan 诊断后重新生成 | ✅ 已完成 |
| Workflow trace / fake benchmark / evaluation metrics | ✅ 已完成基础闭环 |
| Composable lattice transformations | ✅ 已完成 |
| Nested component override / through-path preservation | ✅ 已完成 |
| Axial loading production materialization | ✅ 已完成 |
| Deterministic pin-map count repair oracle (P0-D4) | ✅ 已完成 |
| Deterministic shoulder-gap repair oracle (P0-D5) | ✅ 已完成 |
| Expert feedback semantics & skeleton blocker routing (P0-D5A) | ✅ 已完成 |
| Early lattice-loading validation & grid migration (P0-D5B) | ✅ 已完成 |
| Real-LLM DeepSeek VERA3A/3B zero-retry smoke | ✅ 已通过 |
| VERA3 geometry contract | ✅ 已建立 |
| VERA3 rendered XML point-probe tests | ✅ 已建立 |
| VERA3 plot provenance / annotated plots | ✅ 已建立 |
| VERA3 benchmark-accurate geometry | 🚧 未完成 |
| VERA3 benchmark-accurate materials | 🚧 未完成 |
| VERA3 keff / power acceptance | ⬜ 未开始正式验收 |
| VERA3 gold memory / few-shot 发布 | ⬜ 禁止在验收前进行 |

---

## 1. 最近完成的关键里程碑

### 1.1 确定性结构修复闭环 ✅

P0-D1 到 P0-D5B 建立了三级确定性修复 oracle，在 LLM 介入之前自动修复已识别的结构缺陷：

```
validate_plan → structural issue detected → deterministic oracle → clone evaluation → accept/reject
     ↓ (if rejected)                                                                    ↓ (if accepted)
targeted LLM patch repair                                                     re-validate → proceed
     ↓ (if rejected)
fresh incremental regeneration
```

#### P0-D1：Validation-Driven Incremental Patch Repair ✅

- plan-level issue 进入 patch-relative RFC6902 repair request（稳定 fingerprint、issue ownership policy、protected-path denylist、clone-only assemble/full validation）；
- accepted repair 不消耗 graph retry；
- 重复/无改善候选立即停止并退回一次 targeted regeneration。

#### P0-D2 / P0-D3：Real-LLM Patch Repair Smoke ✅

- 修复 real-model validation repair 延迟构造 patch adapter；
- 优先使用 adapter JSON mode；
- schema-invalid proposal 写入 proposal/evaluation artifact；
- DeepSeek 真实两次局部 RFC6902 proposal 完成完整 clone evaluation。

#### P0-D4：Deterministic Pin-Map Repair Oracle ✅

- assembler 始终尊重 `PinMapPatch.default_universe_id`；
- 唯一等量 default 修复 oracle、clone-only preflight；
- 真实 VERA3 3A 直接恢复 `fuel_pin=264, guide_tube=24, instrument_tube=1`。

#### P0-D5：Early Structural Validation & Deterministic Shoulder-Gap Repair ✅

- validator/renderer 共享 `assembly3d_structural_issues()`；
- `component_profile_as_material_slab` 在 validate_plan 阶段直接产生；
- 确定性 multi-patch repair bundle（universes + axial_layers）+ clone-only acceptance + through-path preservation；
- graph repair pipeline 新增 component-profile oracle。

#### P0-D5A：Expert Feedback Semantics & Skeleton Blocker Routing ✅

- `capability_blockers.py` 确定性分类 structural/environment/human_fact/fidelity；
- `expert_feedback.py`：ExpertQuestionGroup/Decision/AssumptionAcknowledgement；
- structural blocker 优先于 human confirmation；
- assess 节点 axial materialization dry-run probe。

#### P0-D5B：Early Lattice-Loading Validation & Deterministic Grid Migration ✅

- 共享 `lattice_loading_validation.py`：`lattice_transform.replacement_universe_missing` 在 validate_plan 阶段直接发现；
- patch-level transformation cross-reference validation（replacement/source universe 存在性、cell-id-as-universe 误用、spacer_grid_transformation_misuse）；
- `grid_loading_repair.py`：证据层级诊断 + Strategy A-D（优先移除冗余 grid transformation，不创建 solid grid universe）；
- graph 新增 lattice-loading/grid migration oracle；
- 真实 VERA3B retry_count=0、grid_cell 引用消除、capability runnable。

### 1.2 LLM 智能闭环 ✅

#### P0-A：Semantic Plan Auditor ✅

- structured semantic findings；deterministic fallback；warning-only / strict evaluation 模式；trace 与 artifact；benchmark precision / recall / false-positive 指标。

#### P0-B：Patch Repair Proposer ✅

- RFC6902-style 受限 patch；issue-code path allowlist；protected path denylist；clone apply；before/after deterministic validation；accepted / rejected / unsafe 分类；proposal-only / validate-only / apply-if-safe 模式。

#### P0-C：Run Supervisor ✅

- Python 先计算 allowed actions；deterministic veto；advisory / controlled-route；retry budget；state fingerprint；no-progress / loop detection；deterministic fallback。

### 1.3 Incremental planner 稳定化 ✅

复杂模型默认采用 7-patch 分层生成 + 确定性组装，已具备局部重试、reference patch policy、failed patch 诊断、assembled plan validation 失败后诊断再生成。

### 1.4 VERA3 geometry IR 与 renderer 架构 ✅

支持 `replace_universe_family` / `coordinate_override` / `nested_component_override`，多 loading 组合，legacy 迁移，shoulder through-path 保留，materialization failure 阻止静默回退。

### 1.5 VERA3 验收工具基础 ✅

canonical geometry contract、3A/3B patch fixtures、plan-level acceptance、rendered `geometry.xml` point probes、geometry hash differentiation、XY/XZ plots、annotated plots、SHA256 provenance。

---

## 2. 重要纠正：旧里程碑不能继续作为验收结论

### 2.1 不再检查固定"轴向 12 层"

轴向层数由所有 component profile、finite insert、grid overlap 和 shoulder/nozzle 边界的并集决定。正确验收指标是 axial domain 连续、无 gap/overlap、每个 component profile 的 z coverage 正确、同一 z 段 loading/overlay 组合正确、rendered point probes 正确。

### 2.2 旧 keff 仅是历史 smoke 结果

旧记录 3A ~1.149、3B ~0.979 是在几何与材料仍存在明显近似时得到的 smoke 结果，不能作为 benchmark acceptance。在 fuel gap、plenum 半径、Pyrex upper gas、spacer-grid 质量守恒、nozzle mixture、variant-specific coolant、controlled alloy composition 和正式收敛设置完成前不得据此评价模型准确度。

### 2.3 "能导出 XML"不等于 benchmark-accurate

```text
schema-valid → renderable → XML-exportable → geometry-accepted → physics-material-accepted → numerically benchmark-accepted
```

只有最后三层全部通过，才可晋升为 VERA3 gold model。

---

## 3. P0 当前主线：VERA3 Gold Model

### P0-V0：重新验证最新 production renderer 🚧 立即执行

目标：确认最终 XML 已真实包含 axial transformations。

需要重新生成 `data/evals/vera3_geometry/3A` 和 `3B`，检查 point probes（fuel→UO₂、z=382→helium、clad→Zircaloy-4、3B Pyrex annulus→Pyrex、thimble→SS304、shoulder guide wall→Zircaloy-4），geometry hashes 3A/3B 差异，plot manifest 无错误，geometry debug 无 overlap/lost particle。

### P0-V1：燃料棒径向 gap 与 plenum 修正 🚧

活性燃料棒正确径向结构：`0–0.4096 UO₂ / 0.4096–0.418 helium gap / 0.418–0.475 Zircaloy-4 clad / r≥0.475 coolant`。

上部气腔：`0–0.418 helium / 0.418–0.475 clad / r≥0.475 coolant`，不得用 `0.4096 cm` 作为 plenum/clad 分界。

完成标准：production fixture、geometry contract、acceptance helper 一致；active fuel gap 和 plenum 有 rendered point probes；fuel/gap/clad volume 可计算。

### P0-V2：完成 3B Pyrex upper-gas axial profile 🚧

Pyrex poison segment 15.761–376.441 cm；upper helium region 376.441–397.510 cm。需要新增 `pyrex_upper_plenum_inner_profile` / `pyrex_upper_plenum_loading`，重新切分轴向层（376.441–397.510 区间），在 386.267–390.133 cm 叠加 top spacer-grid overlay。

完成标准：Pyrex 不在 376.441 cm 后错误恢复为水导向管；16 Pyrex path 保留正确上部气腔；8 thimble path 坐标互斥；multi-loading + top grid overlay 同时正确。

### P0-V3：Spacer-grid 质量守恒几何 🚧

实现 mass/volume-conserving outer-frame overlay（`mass_conserving_outer_frame`）。端部格架 Inconel-718 1017 g / 3.866 cm；中间格架 Zircaloy-4 875 g / 3.810 cm；质量平均分配到 289 cells；grid material 只占 pitch cell 最外薄方框。

完成标准：grid material volume 与输入质量容差内一致；不再替换整个 moderator open region；through-path 保留。

### P0-V4：Nozzle / core plate 均匀化材料 🚧

正确 volume fractions：lower nozzle 27.92% SS304 / 72.08% coolant；upper nozzle 19.15% / 80.85%；core plate 50% / 50%。3A/3B 使用各自 coolant。

完成标准：不再用纯 SS304 slab 替代 mixture；mixture provenance 写入 material report；3A/3B mixture 不被错误复用。

### P0-V5：材料组成可信化 🚧

优先完成：UO₂ isotopes (U-234/235/236/238 + O-16 stoichiometry)；Pyrex isotopes (B-10/B-11/O-16/Si)；structural alloys (Zircaloy-4/SS304/Inconel-718 controlled library)；coolant variant-specific temperature/density/boron。

完成标准：所有材料有 `composition_status` 和 provenance；no placeholder 标为 benchmark-confirmed。

### P0-V6：几何 Gold Acceptance 🚧

Plan-level（contract diff、base lattice counts、loading coords、radial radii、axial coverage、overlay ranges、boundary conditions）+ Rendered XML-level（point probes、hashes、geometry debug、no overlaps）+ 人工审查（fuel/Pyrex/thimble/guide/instrument/shoulder/grid paths）。

完成后生成 `data/benchmarks/VERA3/` 下的 provenance、contracts、gold plans、acceptance reports、plots。

### P0-V7：数值 Benchmark Acceptance ⬜

只在 V0–V6 完成后开始。顺序：source/settings smoke → low-particle → convergence → production keff → reference comparison → pin power / axial power → uncertainty report。

---

## 4. P0：真实 LLM 与评估闭环

### 4.1 Real-LLM advisory baseline 🚧

VERA3A/3B DeepSeek zero-retry smoke 已通过。继续测试更多 case：clean exportable、failed patch、fact-gap、unsupported subsystem、axial/profile conflict、unsafe material repair proposal。

记录 semantic precision / false positive、repair accepted/rejected/unsafe、supervisor action accuracy、latency/token/cost。

### 4.2 Workflow benchmark regression gate ✅ 基础闭环完成

report-to-report diff、case status changes、new regressions、fixed cases 已支持。继续完善 unsafe repair count、supervisor unsafe action、fact-gap bypass、real LLM opt-in baseline trends。

### 4.3 Fact-gap regression suite 🚧

覆盖 density missing、composition missing、benchmark constants missing、loading map missing、nuclear data path missing、ambiguous axial bounds、conflicting source documents。

完成标准：retrieval/few-shot/auditor/repair/supervisor 均不能自动确认缺失科学事实。

---

## 5. P1：Unseen-model 智能能力

### P1-A：LLM Evidence Synthesizer ⬜

将 grep / graph / GraphRAG / RAG 证据压缩为结构化 brief（claims、locators、contradictions、unsafe-auto-fill flags、missing evidence）。先离线评估。

### P1-B：LLM Task Decomposer ⬜

对 unseen models 生成 required patch types、order、capabilities、known facts、fact gaps、risk tags、verification plan。与 deterministic feature detection 并行。

### P1-C：Retrieval ablation ⬜

对比 grep/graph/GraphRAG/RAG/ranker/synthesizer 各组件 off 时的 task success delta。

### P1-D：Supervisor-driven multi-round repair ⬜

audit → repair proposal → deterministic validation → targeted patch retry → reassemble → revalidate → re-audit → render/ask human/stop，第一阶段最多 1–2 轮。

---

## 6. P1：Renderer 与物理能力扩展

只有 VERA3 fidelity 主线稳定后推进。

- **6.1 HexAssemblyRenderer** ⬜ — OpenMC HexLattice、ring schema、skeleton → exportable。
- **6.2 Depletion / burnup boundary** ⬜ — IR、operator boundary、material evolution、先定义 capability。
- **6.3 Pebble-bed / stochastic geometry** ⬜ — 明确 unsupported boundary、skeleton 输出。
- **6.4 Boundary-condition rendered verification** 🚧 — radial/axial boundary、XML `boundary_type`、leakage anomaly。

---

## 7. P1/P2：VERA3 Memory 与 Few-shot

### 前置条件

只有满足以下条件才能发布：geometry gold accepted、material fidelity accepted、numerical acceptance documented、provenance complete、reference_policy=off reconstruction evaluated。

- **7.1 Benchmark-specific memory** ⬜ — `data/benchmark_knowledge/VERA3/` 结构。
- **7.2 Reactor-neutral few-shot** ⬜ — 从 gold model 匿名提取 component profiles、family replacement、through-path preservation、spacer overlay 等模式，不保留 VERA3 名称/坐标/常数/keff。
- **7.3 Accepted-run mining** ⬜ — 从 accepted traces 自动生成候选 exemplar，人工 review 后晋升。

---

## 8. P2：工程化与用户体验

- **8.1 CI 分层** ⬜ — fast unit / integration / OpenMC geometry / OpenMC transport / real LLM opt-in。
- **8.2 Artifact 目录统一** ⬜ — plan/incremental/retrieval/semantic_audit/repair/supervisor/render/verification/transport/evaluation。
- **8.3 用户可读 run summary** ⬜ — 当前状态、renderability、blockers、unresolved facts、approximations、supervisor decision、required action、artifact links。
- **8.4 CLI 收敛** ⬜ — `--eval-case` / `--benchmark-suite` / `--retrieval-ablation` / `--geometry-acceptance` / `--material-policy` / `--reference-policy`。
- **8.5 文档收敛** ⬜ — README.md + TODO.md + docs/project_technical_report.md 保持为唯一入口。

---

## 9. 推荐推进顺序

### 当前一条主线

1. ~~重新运行最新 VERA3 3A/3B rendered acceptance~~ ✅ smoke 已通过（runnable, model.py 生成）
2. **修复 fuel helium gap 与 plenum 半径**（P0-V1）
3. **实现 Pyrex upper-gas profile 与完整 axial composition**（P0-V2）
4. **实现 mass-conserving spacer-grid outer frame**（P0-V3）
5. **实现 nozzle/core-plate variant-specific mixtures**（P0-V4）
6. **完成可信材料 composition**（P0-V5）
7. **冻结 geometry gold acceptance**（P0-V6）
8. **运行 keff / power numerical acceptance**（P0-V7）
9. **真实 LLM `reference_policy=off` 重建 3A/3B**
10. **发布 benchmark-specific memory**
11. **提取匿名 reactor-neutral few-shot**
12. 再推进 Evidence Synthesizer、Task Decomposer 和新 renderer

### 近期不应并行扩张的方向

在 VERA3 gold model 完成前，不建议同时大规模开展：
- Hex renderer
- depletion
- agent-authored renderer
- long-term memory 自动晋升
- 多模型真实 controlled-route
- 大规模 keff 参数调优

---

## 10. 当前不建议做的事

- 不要把 VERA/C5G7 benchmark facts 写死进 production prompt、validator、renderer 或 guard。
- 不要让 RAG/GraphRAG/few-shot 自动确认 density、composition、benchmark constants、loading map 或 nuclear-data path。
- 不要把旧 VERA3 smoke keff 当作 benchmark reference。
- 不要继续使用固定 axial layer count 作为验收指标。
- 不要在 geometry/material acceptance 前通过调密度拟合 keff。
- 不要让 LLM 直接执行未知代码、直接修改 renderer registry 或跳过 deterministic gate。
- 不要把一次成功的真实 LLM 输出直接加入正式 few-shot。
- 不要在 gold model 完成前发布 VERA3 benchmark memory。
- 不要把 spacer grid 整个 open moderator region 替换为结构材料并称为质量守恒。
- 不要在 nozzle / core-plate slab 中同时保留 detailed lattice。
- 不要用 monolithic `reflect_plan` 作为 incremental failure 的默认兜底。
- 不要只验证 assembled plan；必须验证最终 XML 和物理点探针。
- 不要把 XML export 成功等同于 benchmark-accurate。
- 不要创建 solid grid universe 来消除 `grid_cell` 引用错误——spacer grids 必须通过 axial_overlays 表达。

---

## 11. Definition of Done：VERA3 Gold

只有全部满足，才能将 VERA3 标为完成：

### Geometry

- [ ] fuel active radial gap 正确
- [ ] fuel upper plenum 半径正确
- [ ] Pyrex poison / upper gas profile 正确
- [ ] thimble finite profile 正确
- [ ] guide / instrument through-path 正确
- [x] shoulder transitions 正确（lattice + replace_universe_family, 通过 deterministic repair）
- [ ] spacer-grid mass/volume 正确
- [ ] nozzle / plate slab 正确
- [ ] axial coverage 无 gap / overlap
- [ ] rendered point probes 全通过
- [ ] geometry debug 无 blocking error
- [ ] annotated plots 人工通过

### Materials

- [ ] UO₂ isotopes 正确
- [ ] coolant variant-specific
- [ ] Pyrex isotopes 正确
- [ ] Zircaloy-4 composition policy 正确
- [ ] SS304 composition policy 正确
- [ ] Inconel-718 composition policy 正确
- [ ] nozzle / plate mixture provenance 完整
- [ ] 无 placeholder 被标为 confirmed

### Numerical

- [ ] settings / source convergence 通过
- [ ] keff statistical uncertainty 达标
- [ ] reference keff 对比完成
- [ ] pin power 对比完成
- [ ] axial power 对比完成
- [ ] bias / uncertainty 报告完成

### Agent

- [x] `reference_policy=off` 可生成可审查结构（DeepSeek VERA3A/3B retry_count=0）
- [ ] semantic audit 能发现关键偏差
- [x] unsafe repair 全部拦截（protected-path denylist + clone-only acceptance）
- [x] supervisor 不绕过 blockers（structural blocker precedes human confirmation）
- [ ] gold-vs-agent structured diff 完成

### Knowledge

- [ ] benchmark memory provenance 完整
- [ ] gold plan 版本化
- [ ] anonymous few-shot 人工 review
- [ ] benchmark constants 未泄漏进通用 prompt
- [ ] accepted-run candidate 与 production few-shot 分离
