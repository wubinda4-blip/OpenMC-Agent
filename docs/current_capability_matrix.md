# 当前能力矩阵（Current Capability Matrix）

> Phase 0 现状盘点 · 回答 Q1（SimulationPlan 能表达什么）/ Q2（renderer 支持哪些）/ Q7（哪些子系统只有 skeleton）。
> 所有结论以代码为准，引用形如 `path:line`，可点击跳转。
> 配套文档：[[error_taxonomy_draft]]、[[research_roadmap]]。

---

## 1. SimulationPlan 能表达的 OpenMC 模型（Q1）

`SimulationPlan` 有两条表达路径：

### 1.1 简化路径 `SimulationSpec`（仅 pin-cell）

`SimulationSpec.kind` 固定为 `"pin_cell"`（`schemas.py:1400`），由 `PinCellSpec` 描述：燃料芯块、包壳、慢化剂、几何半径/栅距。这是第一版实现留下的快路径，表达力受限。

### 1.2 结构化路径 `ComplexModelSpec`（重复几何主力）

`ComplexModelSpec.kind` 取 9 个值（`schemas.py:1112-1121`）：

| kind             | 含义                      | 重复几何层级                                        |
| ---------------- | ----------------------- | ----------------------------------------------- |
| `pin_cell`       | 单栅元                     | cell                                            |
| `assembly`       | 组件（pin-by-pin lattice） | cell → universe → lattice → assembly            |
| `core`           | 全堆                      | cell → universe → lattice → assembly → core     |
| `reflector`      | 反射层部件                  | （仅作 core 子部件，无独立渲染）                              |
| `control_rod`    | 控制棒部件                  | （仅作 core 子部件，通过 `CoreSpec.axial_layers` + `lattice_loadings` 表达） |
| `triso_compact`  | TRISO 密实基体              | TRISO 颗粒 + matrix                               |
| `pebble`         | 单个球床元件                  | TRISO + 球壳 + fuel zone                          |
| `pebble_bed`     | 球床堆                     | pebble → 容器（**无独立 renderer，仅 skeleton**）       |
| `mixed`          | 兜底                      | 无明确结构                                           |

子系统（`schemas.py:1136-1215`）：
`materials / surfaces / regions / cells / universes / lattices / assemblies / core / reflectors / control_rods / trisos / packed_spheres / pebbles / settings`，外加 `mg_cross_sections_file`、`standard_mgxs_library ∈ {"c5g7", null}`。

**重复几何能力（刚强化的核心）**：
- `LatticeSpec.kind ∈ {"rect","hex"}`（`schemas.py:435`），含 `universe_pattern`、`rings`、`outer_universe_id`、`overrides`。
- `CoreSpec`：`lattice_id`、6 面边界 `boundary_conditions`、`axial_layers`。
- `AxialLayerSpec`：逐层 `fill: {type, id}` 表达最终填入 root layer cell 的 OpenMC 对象；局部控制棒/可燃毒物插入由 `loading_id` 指向 `ComplexModelSpec.lattice_loadings`。
- `LatticeLoadingSpec`：用 `base_lattice_id + overrides` 记录相对基础 lattice 的装载差异，renderer 生成派生 lattice，不要求重复整张轴向装载图。

**关键缺口（表达力上限）**：
- **`run_mode` 仅 `"eigenvalue"`**（`schemas.py:1038`），`RunSettingsSpec` 无 `burnup` 字段 → **depletion / 燃耗完全无法表达**。
- `energy_mode ∈ {"continuous-energy","multi-group"}`（`schemas.py:1075`），MG 仅靠 `mg_cross_sections_file` / `standard_mgxs_library="c5g7"`，无通用 MGXS 库构造器。
- TRISO 仅支持 `pack_spheres` / `explicit_centers` / `homogenized` 占位（`schemas.py:771`），无随机堆积求解器。

---

## 2. Renderer 支持矩阵（Q2）

`supported_renderer` 取 6 值（`schemas.py:1325-1327`）：`pin_cell / assembly / triso / core / skeleton / none`。
`renderability` 取 4 级（`schemas.py:6`）：`none / skeleton / exportable / runnable`，是能力边界的**核心标识**。

| Renderer（文件）                        | `name`     | 接受的 `kind`                              | 最高 renderability | 触发条件                                   |
| ----------------------------------- | ---------- | --------------------------------------- | ------------------ | ---------------------------------------- |
| `PinCellRenderer`（`pin_cell.py:17`） | `pin_cell` | `pin_cell`                              | `runnable`         | 有完整 `model_spec`                          |
| `RectAssemblyRenderer`（`assembly.py:54`） | `assembly` | `assembly`                              | `runnable`         | 通过 `_assembly_diagnostics` 全部检查          |
| `CoreRenderer`（`core.py:27`）        | `core`     | `core`                                  | `runnable`         | 通过 core 几何/引用检查                          |
| `TrisoRenderer`（`triso.py:25`）      | `triso`    | `triso_compact`, `pebble`               | `runnable`         | 通过 `_triso_renderability_errors`         |
| `SkeletonRenderer`（`skeleton.py:68`） | `skeleton` | 全部 9 种（兜底）                             | `skeleton`         | 永远最后匹配；输出 review-only `model.py`，**不调 `export_to_xml()`** |

每个 renderer 的 `can_render` 在不满足条件时降级：`none`（无 IR / kind 不匹配）→ `skeleton`（IR 存在但诊断失败）→ `exportable/runnable`。

---

## 3. 仅生成 skeleton 的子系统（Q7）

下列内容**没有专用 renderer，最多产出 review-only skeleton**（`SkeletonRenderer` / 各 renderer 的 `emit_skeleton` 分支）：

| 子系统 / kind                        | 现状                                                                                   | 是否假装 runnable |
| --------------------------------- | ------------------------------------------------------------------------------------ | --------------- |
| `reflector`                       | `ReflectorSpec` 存在，但无 `ReflectorRenderer`；仅作为 core 边界/轴向层填充被间接渲染                          | 否（skeleton）     |
| `control_rod`                     | `ControlRodSpec` 存在，但无独立 renderer；只能通过 `CoreSpec.axial_layers.loading_id` + `lattice_loadings` 表达 | 否（skeleton）     |
| `pebble_bed`                      | 仅有 `pebble` 单球；`pebble_bed` kind 无 renderer                                          | 否（skeleton）     |
| `mixed`                           | 兜底 kind，无结构化渲染                                                                       | 否（skeleton）     |
| hex lattice 重复几何                 | `HexLattice` 在 skeleton 中可画出（`skeleton.py:294`），但 `assembly`/`core` renderer 只处理 `rect` | 否（skeleton）     |
| 任意 renderer 诊断失败的 plan             | 例如缺密度/缺 composition 的 material、ref 悬空 → `renderability="skeleton"`                    | 否（skeleton）     |
| **depletion / 燃耗**               | schema 无 `burnup` 字段、无 renderer                                                        | 否（无法表达）         |

`skeleton` 的安全语义（`skeleton.py:26`、`skeleton.py:364-371`）：脚本可 import 但**故意省略 `model.export_to_xml()`**，标注 `NOT EXECUTABLE`，输出 `capability_report.json` 与 `TODO.md` 等待人工补全。这是系统不假装 runnable 的关键机制。

---

## 4. 能力总览矩阵

> 列含义：**grep** = 是否需要 grep 代码/示例定位写法；**KG** = 是否需要知识图谱（concept_id / KnowledgeRef）；**RAG** = 是否需要检索手册/示例；**人工** = 是否必须人工确认。
> 低 / 中 / 高 表示该能力对辅助手段的依赖程度。

| 能力                      | 当前状态        | grep | KG   | RAG  | 人工    | 依据                                                                 |
| ----------------------- | ----------- | ---- | ---- | ---- | ----- | ------------------------------------------------------------------ |
| pin-cell 几何             | **已支持**（runnable） | 低    | 低    | 低    | 低     | `PinCellRenderer`、`validator.validate_simulation_spec`              |
| assembly lattice（rect）  | **已支持**（runnable） | 低    | 中    | 低    | 视情况   | `RectAssemblyRenderer`、auto_repair + 重复几何码                          |
| core 重复几何（rect + 轴向层）   | **已支持**（runnable） | 中    | 中    | 中    | 视情况   | `CoreRenderer`、`AxialLayerSpec.fill` + `LatticeLoadingSpec.overrides` |
| hex lattice / 六角组件      | **仅 skeleton** | 中    | 中    | 高    | 高     | skeleton 可画，无 hex renderer                                          |
| TRISO / pebble（单颗粒）     | **部分支持**（runnable） | 中    | 高    | 高    | 高     | `TrisoRenderer`；packing 仅占位（`schemas.py:771`）                       |
| pebble_bed（堆积）          | **未支持**（skeleton） | 高    | 高    | 高    | 高     | 无 renderer、无堆积求解器                                                  |
| C5G7 MGXS               | **部分支持**     | 中    | 高    | 高    | 中     | `standard_mgxs_library="c5g7"`（`schemas.py:1207`）；无通用 MGXS 构造       |
| depletion / 燃耗          | **未支持**     | 高    | 高    | 高    | 高     | schema 无 `burnup`、`run_mode` 仅 eigenvalue                           |
| runtime error 修复        | **未系统化**    | 高    | 中    | 高    | 视情况   | `tools.parse_openmc_output` 仅 5 类文本匹配，无 → 错误码回灌                   |
| reflector / control_rod 独立渲染 | **未支持**（skeleton） | 中    | 中    | 中    | 中     | 仅作 core 子部件                                                        |

---

## 5. 边界总结

- **能跑（runnable）**：pin-cell、rect assembly、rect core（含轴向分层装载）、单 TRISO/pebble。
- **能出图不能跑（exportable/skeleton）**：hex lattice、reflector/control_rod 独立体、pebble_bed、诊断失败的任意 plan。
- **能表达但不能跑**：MGXS 仅 c5g7 预置、TRISO 随机堆积。
- **完全不能表达**：depletion / 燃耗 / 固定源 / 多物理场。

下一步研究方向见 [[research_roadmap]]；错误如何被发现与修复见 [[error_taxonomy_draft]]。
