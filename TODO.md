# OpenMC-Agent TODO Roadmap

维护日期：2026-07-10  
当前基线：`main`，最近确认提交 `ff0db716`（Regenerate invalid incremental plans with diagnostics）

本文档用于维护仓库级主线、已完成能力、近期任务和长期方向。  
它不是单次运行生成的 skeleton `TODO.md`，也不应记录一次性调试日志。

---

## 0. 当前阶段判断

项目已完成从“LLM 一次性生成大计划”到“分层规划、语义审查、受限修复、受控路由、确定性渲染和回归评估”的主要架构升级。

当前重心已经发生变化：

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
| VERA3 geometry contract | ✅ 已建立 |
| VERA3 rendered XML point-probe tests | ✅ 已建立 |
| VERA3 plot provenance / annotated plots | ✅ 已建立 |
| VERA3 benchmark-accurate geometry | 🚧 未完成 |
| VERA3 benchmark-accurate materials | 🚧 未完成 |
| VERA3 keff / power acceptance | ⬜ 未开始正式验收 |
| VERA3 gold memory / few-shot 发布 | ⬜ 禁止在验收前进行 |

---

## 1. 最近完成的关键里程碑

### 1.1 LLM 智能闭环

#### P0-A：Semantic Plan Auditor ✅

已具备：

- structured semantic findings；
- deterministic fallback；
- warning-only / strict evaluation 模式；
- trace 与 artifact；
- benchmark precision / recall / false-positive 指标；
- 轴向、材料、reference policy、capability 等语义审查。

#### P0-B：Patch Repair Proposer ✅

已具备：

- RFC6902-style 受限 patch；
- issue-code path allowlist；
- protected path denylist；
- clone apply；
- before/after deterministic validation；
- accepted / rejected / unsafe 分类；
- proposal-only / validate-only / apply-if-safe 模式；
- repair metrics 和 artifacts。

#### P0-C：Run Supervisor ✅

支持动作：

```text
continue_to_render
continue_patch_generation
retry_patch
request_human_confirmation
downgrade_to_skeleton
stop
```

已具备：

- Python 先计算 allowed actions；
- deterministic veto；
- advisory / controlled-route；
- retry budget；
- state fingerprint；
- no-progress / loop detection；
- deterministic fallback；
- benchmark action accuracy / unsafe rate 指标。

> 旧 TODO 中“P0-C 下一步待做”的描述已失效。

### 1.2 Incremental planner 稳定化 ✅

复杂模型默认采用：

```text
facts
→ materials
→ universes
→ pin_map
→ axial_layers
→ axial_overlays
→ settings
→ assemble
→ validate
```

已完成：

- 分层 patch schema；
- 局部重试；
- 禁止 full-plan 输出；
- legacy monolithic fallback 默认关闭；
- coord overlap 确定性修复；
- reference patch 显式 policy；
- failed patch / raw response / artifact 诊断；
- assembled plan validation 失败后，将 validator diagnostics 返回 patch planner 并启动 fresh incremental regeneration；
- 不允许只修改 `expected_counts` 掩盖真实 lattice 错误。

最新相关提交：

- `ff0db716`：Regenerate invalid incremental plans with diagnostics

### 1.3 VERA3 geometry IR 与 renderer 架构 ✅

已完成的提交链：

- `0ce30acf`：Establish VERA geometry contracts
- `71f7af3`：Add composable lattice transformation schemas
- `e3e301b`：Add deterministic lattice transformation engine
- `613f351`：Add nested component replacement and through-path guards
- `0ae4716`：Integrate multi-loading transformations into renderer
- `e848250`：Migrate VERA3 fixtures to component axial profiles
- `a8d1722`：Complete VERA3 Step 3
- `cbda264`：Materialize axial lattice loadings in production renderer

当前支持：

```text
replace_universe_family
coordinate_override
nested_component_override
```

并支持：

- 一个 axial layer 引用多个 ordered loadings；
- legacy `loading_id` / `overrides` 迁移；
- bounded nested-universe fill；
- guide-tube wall / outer moderator 保留；
- stable derived-lattice cache key；
- spacer overlay 与 materialized loading 共存；
- shoulder 区域保留 guide/instrument through-path；
- materialization failure 阻止静默回退 base lattice。

### 1.4 VERA3 验收工具基础 ✅

已具备：

- canonical geometry contract；
- 3A/3B patch fixtures；
- plan-level acceptance；
- rendered `geometry.xml` point probes；
- 3A/3B geometry hash differentiation；
- XML export；
- geometry debug；
- XY / XZ raw plots；
- 带坐标轴 annotated plots；
- `plot_manifest.json` 和 SHA256 provenance；
- plot 强制在目标 output directory 读取本次 XML；
- plot 异常不再静默吞掉。

---

## 2. 重要纠正：旧里程碑不能继续作为验收结论

旧 TODO 中以下描述已经不再适合作为当前 KPI。

### 2.1 不再检查固定“轴向 12 层”

轴向层数由所有 component profile、finite insert、grid overlap 和 shoulder/nozzle 边界的并集决定。

正确验收指标是：

- axial domain 连续；
- 无 gap / overlap；
- 每个 component profile 的 z coverage 正确；
- 同一 z 段中的 loading / overlay 组合正确；
- rendered point probes 正确。

固定层数 12、14 或其他数字都不应成为产品断言。

### 2.2 旧 keff 仅是历史 smoke 结果

旧记录：

```text
3A ~1.149
3B ~0.979 ± 0.004
```

是在几何与材料仍存在明显近似或错误时得到的 smoke 结果，不能作为 benchmark acceptance，也不能写入 gold memory。

在以下事项完成前不得据此评价模型准确度：

- fuel pellet-clad helium gap；
- fuel upper plenum 半径；
- Pyrex upper gas profile；
- spacer-grid 质量守恒；
- nozzle / core-plate homogenized mixture；
- variant-specific coolant；
- controlled alloy / Pyrex compositions；
- 正式收敛设置和 reference comparison。

### 2.3 “能导出 XML”不等于 benchmark-accurate

当前需要区分：

```text
schema-valid
renderable
XML-exportable
geometry-accepted
physics-material-accepted
numerically benchmark-accepted
```

只有最后三层全部通过，才可晋升为 VERA3 gold model。

---

## 3. P0 当前主线：VERA3 Gold Model

### P0-V0：重新验证最新 production renderer 🚨 立即执行

目标：确认 `cbda264` 之后，最终 XML 已真实包含 axial transformations，而不是只在 assembled plan 中存在。

需要重新生成：

```text
data/evals/vera3_geometry/3A
data/evals/vera3_geometry/3B
```

必须检查：

- ordinary fuel center, `z=100` → UO₂；
- ordinary fuel center, `z=382` → helium；
- fuel clad radial point → Zircaloy-4；
- 3B Pyrex annulus, `z=100` → Pyrex；
- Pyrex guide wall → Zircaloy-4；
- thimble center, `z=384` → SS304；
- same thimble coordinate at `z=382` / `394.5` → water；
- lower / upper shoulder guide wall → Zircaloy-4；
- 3A and 3B `geometry.xml` hashes differ；
- `plot_manifest.json.errors` 为空；
- geometry debug 无真实 overlap / lost particle / undefined region。

完成标准：

- rendered-geometry acceptance 无 blocking issue；
- annotated XY/XZ 图与 point probes 一致；
- 结果记录为新的 geometry baseline。

---

### P0-V1：燃料棒径向 gap 与 plenum 修正 🚧 下一项正式开发任务

当前仍需修正：

#### 活性燃料棒

正确径向结构：

```text
0.0000–0.4096 cm  UO₂
0.4096–0.4180 cm  helium gap
0.4180–0.4750 cm  Zircaloy-4 clad
r ≥ 0.4750 cm     coolant / grid outer-frame region
```

当前 fixture 仍需要确认是否显式存在 `0.4096–0.418 cm` helium gap。

#### 上部气腔

正确结构：

```text
0.000–0.418 cm  helium
0.418–0.475 cm  Zircaloy-4 clad
r ≥ 0.475 cm    coolant / grid outer-frame region
```

不得继续使用 `0.4096 cm` 作为 plenum/clad 分界。

完成标准：

- production fixture、geometry contract、acceptance helper 一致；
- active fuel gap 和 plenum 均有 rendered point probes；
- XML geometry 无 overlap；
- fuel volume、gap volume、clad volume 可计算并进入 acceptance report。

---

### P0-V2：完成 3B Pyrex upper-gas axial profile 🚧

输入文件已经给出：

```text
Pyrex poison segment:
15.761–376.441 cm

Pyrex upper helium region inside detailed lattice:
376.441–397.510 cm
```

需要新增或完善：

```text
pyrex_upper_plenum_inner_profile
pyrex_upper_plenum_loading
```

径向语义：

- SS304 inner tube 保留；
- SS304 outer clad 保留；
- poison / inner empty regions切换为 helium；
- `0.484–0.561 cm` water gap 保留；
- `0.561–0.602 cm` guide-tube wall 保留；
- outer moderator 保留。

需要重新切分并组合 3B 轴向层：

```text
376.441–377.711
fuel active + Pyrex upper gas

377.711–379.381
fuel end plug + Pyrex upper gas

379.381–383.310
fuel plenum + Pyrex upper gas

383.310–394.310
fuel plenum + Pyrex upper gas + thimble

394.310–395.381
fuel plenum + Pyrex upper gas

395.381–397.510
fuel positions water + Pyrex upper gas
```

在 `386.267–390.133 cm` 还必须叠加 top spacer-grid overlay。

完成标准：

- Pyrex 不在 `376.441 cm` 后错误恢复为普通水导向管；
- 16 个 Pyrex path 在 detailed-lattice upper region 保留正确上部气腔；
- 8 个 thimble path 与 Pyrex path 坐标互斥；
- multi-loading + top grid overlay 同时正确渲染；
- XZ plot 和 point probes 覆盖 poison → gas transition。

---

### P0-V3：Spacer-grid 质量守恒几何 🚧

当前 `homogenized_open_region` 仍可能把整个 open moderator cell 替换成 grid material，材料体积偏大。

目标：实现质量/体积守恒的 outer-frame overlay，例如：

```text
mass_conserving_outer_frame
```

输入依据：

- 端部格架：Inconel-718，质量 1017 g，高度 3.866 cm；
- 中间格架：Zircaloy-4，质量 875 g，高度 3.810 cm；
- 质量平均分配到 289 个 pitch cells；
- grid material 只占 pitch cell 最外侧薄方框；
- fuel、gap、clad、tube wall、Pyrex、thimble 继续贯穿。

需要增加：

- overlay cross-sectional area / frame thickness；
- mass/volume consistency validator；
- renderer outer-frame CSG；
- overlap checks；
- per-grid volume acceptance；
- overlay + transformed lattice combination tests。

完成标准：

- grid material volume 与输入质量在容差内一致；
- 不再替换整个 moderator open region；
- 所有 through-path 和 localized insert 保留；
- geometry debug 和 point probes 通过。

---

### P0-V4：Nozzle / core plate 均匀化材料 🚧

正确 volume fractions：

```text
lower nozzle:
27.9217 vol% SS304
72.0783 vol% current-variant coolant

upper nozzle:
19.1470 vol% SS304
80.8530 vol% current-variant coolant

lower / upper core plate:
50 vol% SS304
50 vol% current-variant coolant
```

要求：

- 3A 和 3B 使用各自 coolant 温度、硼浓度和 composition；
- nozzle / plate 是 whole-cross-section homogenized slab；
- detailed lattice 在 `z=397.510 cm` 截断；
- 不和 guide tube / Pyrex / grid 双重填充；
- mixture provenance 与 volume fractions 写入 material report。

完成标准：

- 不再用纯 SS304 slab 替代 mixture；
- material volume fraction 可确定性验证；
- 3A/3B mixture materials 不被错误复用。

---

### P0-V5：材料组成可信化 🚧

优先完成：

1. UO₂：
   - 3A / 3B 对应 U-234/U-235/U-236/U-238；
   - O-16 化学计量；
   - 正确 enrichment 与 density。

2. Pyrex：
   - B-10 0.712 wt%；
   - B-11 3.170 wt%；
   - O-16 55.217 wt%；
   - Si 40.901 wt%；
   - density 2.25 g/cc。

3. Structural alloys：
   - Zircaloy-4；
   - SS304；
   - Inconel-718；
   - 使用 controlled material library；
   - 禁止 pure-Zr / pure-Fe 静默进入 benchmark-accurate 结论。

4. Coolant：
   - variant-specific temperature；
   - density；
   - boron ppm；
   - H/O/B composition；
   - nozzle/plate mixture 使用同一工况 coolant。

完成标准：

- 所有材料有 `composition_status` 和 provenance；
- no placeholder / approximate material 被标记为 benchmark-confirmed；
- material report 能区分 confirmed / nominal / approximate / unresolved。

---

### P0-V6：几何 Gold Acceptance 🚧

几何冻结前必须同时通过：

#### Plan-level

- canonical contract diff；
- base lattice counts；
- loading coordinates；
- radial layer radii；
- axial profile coverage；
- overlay ranges；
- boundary conditions；
- no unresolved blocking fact。

#### Rendered XML-level

- point probes；
- geometry hashes；
- geometry debug；
- no overlaps；
- no lost particles；
- no undefined cells/regions；
- volume consistency；
- XY/XZ manual inspection。

#### 人工审查

至少检查：

- ordinary fuel path；
- Pyrex path；
- thimble path；
- ordinary guide path；
- instrument path；
- lower / upper shoulder；
- all grid overlap intervals；
- transition surfaces。

完成后生成：

```text
data/benchmarks/VERA3/
  provenance.json
  geometry_contract.json
  variant_3a_gold_plan.json
  variant_3b_gold_plan.json
  geometry_acceptance_report.json
  material_acceptance_report.json
  rendered_hashes.json
  plots/
```

---

### P0-V7：数值 Benchmark Acceptance ⬜

只在 V0–V6 完成后开始。

顺序：

1. source / settings smoke；
2. low-particle run；
3. convergence study；
4. production keff run；
5. reference keff comparison；
6. pin power / axial power comparison；
7. uncertainty and bias report。

必须区分误差来源：

- geometry；
- material composition；
- homogenization；
- nuclear data；
- temperature / boron；
- statistics；
- source convergence。

禁止：

- 通过随意调密度拟合 keff；
- 使用旧 smoke keff 作为目标；
- 忽略 uncertainty；
- 在 geometry/material acceptance 前开始数值调参。

---

## 4. P0：真实 LLM 与评估闭环

### 4.1 Real-LLM advisory baseline 🚧

P0-A/B/C 的 fake benchmark 已证明连接和 policy 正确，但仍需真实模型 baseline。

至少测试：

- clean exportable case；
- failed patch；
- fact-gap；
- unsupported subsystem；
- VERA3 axial/profile conflict；
- unsafe material repair proposal。

记录：

- semantic precision / false positive；
- repair accepted / rejected / unsafe；
- supervisor action accuracy；
- target-patch accuracy；
- veto / fallback；
- latency / token / cost；
- human-confirmation preservation。

真实模型第一轮只允许：

```text
semantic audit = warning-only
repair = proposal-only
supervisor = advisory
```

达标后才小范围开启 controlled-route。

### 4.2 Workflow benchmark regression gate 🚧

继续完善：

- report-to-report diff；
- case status changes；
- new regressions；
- fixed cases；
- unsafe repair count；
- supervisor unsafe action；
- fact-gap bypass；
- artifact completeness；
- real LLM opt-in baseline trends。

### 4.3 Fact-gap regression suite 🚧

必须覆盖：

- density missing；
- composition missing；
- benchmark constants missing；
- loading map missing；
- nuclear data path missing；
- ambiguous axial bounds；
- conflicting source documents。

完成标准：

- retrieval / few-shot / auditor / repair / supervisor 均不能自动确认缺失科学事实；
- 报告明确显示 confirmed / approximate / unresolved。

---

## 5. P1：Unseen-model 智能能力

### P1-A：LLM Evidence Synthesizer ⬜

目标：将 grep / graph / GraphRAG / RAG 证据压缩为结构化 brief：

```text
claims
supporting locators
contradictions
unsafe-auto-fill flags
missing evidence
```

先离线评估，不直接改变 planner facts。

### P1-B：LLM Task Decomposer ⬜

目标：对 unseen models 生成：

```text
required patch types
patch order
required capabilities
known facts
fact gaps
risk tags
verification plan
```

先与 deterministic feature detection 并行比较，不允许跳过 mandatory validation。

### P1-C：Retrieval ablation ⬜

对比：

- grep off；
- graph off；
- GraphRAG off；
- RAG off；
- ranker weights；
- synthesizer off。

指标：

- useful；
- irrelevant；
- unsafe；
- redundant；
- task success delta。

### P1-D：Supervisor-driven multi-round repair ⬜

在现有 retry budget / loop detection 基础上实现受控闭环：

```text
audit
→ repair proposal
→ deterministic validation
→ targeted patch retry
→ reassemble
→ revalidate
→ re-audit
→ render / ask human / stop
```

第一阶段最多 1–2 轮。

---

## 6. P1：Renderer 与物理能力扩展

只有 VERA3 fidelity 主线稳定后推进。

### 6.1 HexAssemblyRenderer ⬜

- OpenMC HexLattice；
- ring schema；
- pitch；
- outer universe；
- skeleton → exportable 最小闭环；
- capability contract tests。

### 6.2 Depletion / burnup boundary ⬜

- IR；
- settings/operator boundary；
- material evolution；
- statepoint / depletion result artifacts；
- 先定义 capability，不急于大计算。

### 6.3 Pebble-bed / stochastic geometry ⬜

- 明确 unsupported boundary；
- skeleton 输出；
- 不伪装 runnable。

### 6.4 Boundary-condition rendered verification 🚧

继续完善 plan → model.py → XML 对照：

- radial boundary；
- axial boundary；
- XML `boundary_type`；
- mismatch blocking diagnostics；
- leakage anomaly warning。

---

## 7. P1/P2：VERA3 Memory 与 Few-shot

### 前置条件

只有满足以下条件才能发布：

```text
geometry gold accepted
material fidelity accepted
numerical acceptance documented
provenance complete
reference_policy=off reconstruction evaluated
```

### 7.1 Benchmark-specific memory ⬜

建议结构：

```text
data/benchmark_knowledge/VERA3/
  provenance.json
  geometry_contract.json
  material_contract.json
  variant_3a_facts.json
  variant_3b_facts.json
  validated_patches/
  gold_plans/
  acceptance_reports/
  reference_results/
  plots/
```

只在明确识别为 VERA3 时检索。

### 7.2 Reactor-neutral few-shot ⬜

从 gold model 匿名提取：

- component axial profiles；
- fuel plenum modeling；
- family-wide universe replacement；
- finite insert inside guide tube；
- through-path preservation；
- multiple loading composition；
- mass-conserving spacer overlay；
- homogenized nozzle / plate；
- rendered point-probe acceptance。

不得保留：

- VERA3 名称；
- exact benchmark coordinates；
- exact z constants；
- enrichment；
- material densities；
- reference keff。

### 7.3 Accepted-run mining ⬜

从 accepted traces 自动生成候选 exemplar，但必须：

- 有 provenance；
- 有适用边界；
- anonymized；
- 进入 candidate 区；
- 人工 review 后晋升；
- 不自动污染 production prompts。

---

## 8. P2：工程化与用户体验

### 8.1 CI 分层 ⬜

```text
fast unit
integration
OpenMC geometry
OpenMC transport
real LLM opt-in
```

### 8.2 Artifact 目录统一 ⬜

统一：

```text
plan/
incremental/
retrieval/
semantic_audit/
repair/
supervisor/
render/
verification/
transport/
evaluation/
```

### 8.3 用户可读 run summary ⬜

输出：

- 当前状态；
- renderability；
- blockers；
- unresolved facts；
- approximations；
- supervisor decision；
- required user action；
- artifact links。

### 8.4 CLI 收敛 ⬜

增加或统一：

```text
--eval-case
--benchmark-suite
--retrieval-ablation
--geometry-acceptance
--material-policy
--reference-policy
```

### 8.5 文档收敛 ⬜

当前入口应保持：

```text
README.md
TODO.md
docs/project_technical_report.md
```

历史 strategy 文档归档，不继续产生重复路线文档。

---

## 9. 推荐推进顺序

### 当前一条主线

1. **重新运行最新 VERA3 3A/3B rendered acceptance**；
2. **修复 fuel helium gap 与 plenum 半径**；
3. **实现 Pyrex upper-gas profile 与完整 axial composition**；
4. **实现 mass-conserving spacer-grid outer frame**；
5. **实现 nozzle/core-plate variant-specific mixtures**；
6. **完成可信材料 composition**；
7. **冻结 geometry gold acceptance**；
8. **运行 keff / power numerical acceptance**；
9. **真实 LLM `reference_policy=off` 重建 3A/3B**；
10. **发布 benchmark-specific memory**；
11. **提取匿名 reactor-neutral few-shot**；
12. 再推进 Evidence Synthesizer、Task Decomposer 和新 renderer。

### 近期不应并行扩张的方向

在 VERA3 gold model 完成前，不建议同时大规模开展：

- Hex renderer；
- depletion；
- agent-authored renderer；
- long-term memory 自动晋升；
- 多模型真实 controlled-route；
- 大规模 keff 参数调优。

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

---

## 11. Definition of Done：VERA3 Gold

只有全部满足，才能将 VERA3 标为完成：

### Geometry

- [ ] fuel active radial gap 正确；
- [ ] fuel upper plenum 半径正确；
- [ ] Pyrex poison / upper gas profile 正确；
- [ ] thimble finite profile 正确；
- [ ] guide / instrument through-path 正确；
- [ ] shoulder transitions 正确；
- [ ] spacer-grid mass/volume 正确；
- [ ] nozzle / plate slab 正确；
- [ ] axial coverage 无 gap / overlap；
- [ ] rendered point probes 全通过；
- [ ] geometry debug 无 blocking error；
- [ ] annotated plots 人工通过。

### Materials

- [ ] UO₂ isotopes 正确；
- [ ] coolant variant-specific；
- [ ] Pyrex isotopes 正确；
- [ ] Zircaloy-4 composition policy 正确；
- [ ] SS304 composition policy 正确；
- [ ] Inconel-718 composition policy 正确；
- [ ] nozzle / plate mixture provenance 完整；
- [ ] 无 placeholder 被标为 confirmed。

### Numerical

- [ ] settings / source convergence 通过；
- [ ] keff statistical uncertainty 达标；
- [ ] reference keff 对比完成；
- [ ] pin power 对比完成；
- [ ] axial power 对比完成；
- [ ] bias / uncertainty 报告完成。

### Agent

- [ ] `reference_policy=off` 可生成可审查结构；
- [ ] semantic audit 能发现关键偏差；
- [ ] unsafe repair 全部拦截；
- [ ] supervisor 不绕过 blockers；
- [ ] gold-vs-agent structured diff 完成。

### Knowledge

- [ ] benchmark memory provenance 完整；
- [ ] gold plan 版本化；
- [ ] anonymous few-shot 人工 review；
- [ ] benchmark constants 未泄漏进通用 prompt；
- [ ] accepted-run candidate 与 production few-shot 分离。
