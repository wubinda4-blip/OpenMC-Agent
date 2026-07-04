# 错误分类草案（Error Taxonomy Draft）

> Phase 0 现状盘点 · 回答 Q3（validator 能发现什么）/ Q4（schema/validator 可直接修什么）/ Q5（必须查代码/手册的）/ Q6（必须人工确认的）。
> 配套文档：[[current_capability_matrix]]、[[research_roadmap]]。

---

## 1. 错误发现的四道防线（Q3）

系统在四个层面发现错误，由早到晚：

### 防线 1 — Schema 自校验（Pydantic `model_validator`）
进入 IR 前就拦截，**抛异常**而非产出 issue：
- `inactive < batches`（`schemas.py:1099`）
- complex_model 至少含一个子系统（`schemas.py:1217`）
- 装配根 lattice 推断（`schemas.py:1241`）
- `renderability` 与 `is_executable` 一致性（`schemas.py:1377`）

### 防线 2 — IR/Plan/Script 静态校验（`validator.py`）
返回 `ValidationReport`，含稳定 `code`：
- pin-cell 几何：半径/栅距范围、包壳半径单调、material↔radii 配对（`validator.py:35-78`）
- plan 结构：`plan.model.missing` / `plan.complex_model.non_executable` / `plan.executable.unsupported_renderer`（`validator.py:83-105`）
- script 模板：`script.missing_structure`（materials/geometry/settings/tallies/export）/ `script.material_not_referenced`（`validator.py:108-146`）

### 防线 3 — Renderer 能力诊断（`can_render`）
renderer 自检决定 `renderability`，失败时输出结构化 `issues`（带稳定 code）：
- assembly：material/lattice ref、shape/pattern、pin_count、radius（`assembly.py` `_assembly_diagnostics`）
- core：同上 + axial_layer ref、universe 可达性（`core.py`、`executor._core_reachable_universe_ids`）
- triso：material 密度/成分、layer material ref、matrix material、半径关系（`triso.py:106-145`）

### 防线 4 — Runtime 检查（`tools.py`）
导出/运行后才发现：
- `export_xml`：XML 产物完整性、`geometry.xml` 悬空引用（cell fill / lattice universe，`tools.py:194-242`）
- `parse_openmc_output`：5 类文本匹配 —— 截面缺失 / undefined region / overlap / lost particle / traceback（`tools.py:162-181`）
- `run_smoke_test`：particles/batches 安全限值（`tools.py:95-113`）

### 错误码总表（40 个稳定 code，`error_catalog.py`）

| 类别               | code 数 | 代表 code                                                                                                |
| ---------------- | ----- | ----------------------------------------------------------------------------------------------------- |
| pin-cell 几何      | 9     | `geometry.fuel_radius.out_of_range`、`geometry.cladding.outer_not_greater_than_inner`                  |
| settings         | 1     | `settings.inactive.not_less_than_batches`                                                             |
| plan 结构          | 8     | `plan.model.missing`、`plan.assembly.requires_complex_assembly`、`plan.non_executable.renderer_must_be_none` |
| script 模板        | 2     | `script.missing_structure`、`script.material_not_referenced`                                           |
| material 定义     | 3     | `material.definition.missing`、`material.macroscopic.invalid_density_unit`                              |
| 几何结构必需      | 3     | `cell.fill_id.missing`、`lattice.rect.universe_pattern_missing`、`lattice.hex.rings_missing`            |
| 重复几何引用/形状 | 12+   | `lattice.universe_ref_missing`、`lattice.pin_count_mismatch`、`cell.material_ref_missing`、`axial_layer.fill_ref_missing` |
| TRISO / pebble   | 2     | `triso.layers.not_strictly_increasing`、`pebble.fuel_zone_radius.too_large`                            |

> 每个 code 都带 `severity / schema_path / rule_id / concept_id / knowledge_refs / repair_hints`，是后续 RAG / GraphRAG / 自动修复的稳定钩子。

---

## 2. 修复路径分类（Q4 / Q5 / Q6）

按"谁能修"分成四档。`graph.py` 的路由器（`graph.py:914-930`）按档分流到 `reflect_plan` 或 `ask_expert`。

### 档 A — 确定性自修（无需 LLM，Q4）
**唯一可解的 id-reference typo** → `auto_repair.auto_repair_lattice_structure`（`auto_repair.py:94`）产出 RFC 6902 patch：
- `cell.fill_id`（material/universe/lattice）、`cell.region_id`
- `universe.cell_ids`、`region.surface_ids`、`lattice.universe_pattern[r][c]`
- `core.axial_layers[i].fill.id`、`core.axial_layers[i].loading_id`
- `lattice_loadings[i].base_lattice_id`、`lattice_loadings[i].overrides.<universe_id>`

匹配策略（`auto_repair.py:62-91`）：精确 → 唯一前缀/后缀 → 唯一编辑距离（容差 `max(2, len//4)`）。**多解 / 无解一律不碰**，留给 LLM。patch 失败有 `PATCH_FALLBACK_THRESHOLD` 熔断（`graph.py`），超阈改走整 plan 重生成。

### 档 B — LLM 可修（结构 typo，需 reflect，Q4）
**多解的 ref / shape / pin_count / radius** —— 是 plan 写错而非缺事实。`graph.py:SELF_REPAIRABLE_CODES`（`graph.py` `_SELF_REPAIRABLE_CAPABILITY_PATTERNS` / `SELF_REPAIRABLE_CODES`）覆盖重复几何引用/形状/计数/半径类 code，包括 axial layer `fill/loading` 与 lattice loading 引用错误，路由进 `reflect_plan`：
1. 先试 A 档确定性 patch；
2. 再试 investigation patch（retrieval 回路）；
3. 最后整体重生成 `SimulationPlan`（带 id 引用复查提示，`graph.py` 反思节点）。

### 档 C — 必须查代码 / 示例 / 手册（Q5）
不是 typo，是"不知道怎么写"，需要 `KnowledgeRef`（`error_catalog.py:64-95`）+ 检索（`retrieval.py`）：
- 重复几何 cell→universe→lattice 嵌套写法 → `LATTICE_GUIDE`
- TRISO / packing → `GEOMETRY_GUIDE`（TRISO 章节）
- surface 参数、region 布尔表达式 → `GEOMETRY_GUIDE`
- MGXS / C5G7 配置 → `openmc.usersguide.mgxs`

### 档 D — 必须人工确认（ask_expert，Q6）
**缺失的是事实，不是写法**。进 `required_human_confirmation`，经 `ask_expert` interrupt 询问（`graph.py:1038`、`graph.py:1811-1822`）：
- material 缺 density / composition（专家填值）
- 核数据选择（截面库、热散射 S(α,β)、温度、depletable）
- lattice `pin_count` 与 `expected_counts` 不符的软确认
- 模糊假设（`assumptions`）

> 关键判据：material 类缺失（密度/成分）**不**在 `SELF_REPAIRABLE_CODES`，永远走 ask_expert；而 ref/shape/pin_count/radius 走 reflect。这是"事实缺口 vs plan typo"的分界（`graph.py:2449` 区段注释明确）。

---

## 3. 错误 → 修复路径速查

| 错误现象                  | 发现层       | code 示例                             | 修复档      | 去向                |
| ----------------------- | --------- | ----------------------------------- | -------- | ----------------- |
| cell 指向不存在的 material（唯一拼写错） | Renderer  | `cell.material_ref_missing`         | A        | auto_repair       |
| lattice universe_pattern 指向多个近似 id | Renderer  | `lattice.universe_ref_missing`      | B        | reflect_plan      |
| lattice pin_count 与 expected 不符     | Renderer  | `lattice.pin_count_mismatch`        | B        | reflect_plan      |
| material 缺 density                  | Renderer  | `material.density.partial_missing`  | D        | ask_expert        |
| inactive ≥ batches                  | Schema    | `settings.inactive.not_less_than_batches` | A（schema 抛异常） | model_validator   |
| geometry.xml 悬空 universe 引用        | Runtime   | （无 code，文本）                         | C / B    | 检索 + reflect     |
| OpenMC 报 overlap / lost particle     | Runtime   | （无 code，文本）                         | C        | 检索 + 人工         |
| 不知道 hex lattice 怎么写            | —         | —                                   | C        | retrieval + KG    |

---

## 4. 主要缺口

1. **Runtime 错误无稳定 code**：`tools.parse_openmc_output` 仅 5 类正则，未回灌为 `ValidationIssue`，无法进 A/B 档自动修复回路（见 [[research_roadmap]] "runtime error 修复系统化"）。
2. **TRISO packing 错误码缺失**：堆积失败、体积分数越界等无 code。
3. **depletion 相关错误完全空缺**：schema 无字段，自然无 code。
4. **hex lattice 诊断码缺失**：hex 的 rings/指向检查未入 catalog。
