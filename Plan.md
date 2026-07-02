# OpenMC 自动化 Agent 从零实施计划

## Summary

目标是从零搭建一个基于 **aisuite + Pydantic + LangGraph + OpenMC** 的自动化建模 Agent。开发节奏采用“小步确认”：每一步只完成一个能力点，先测试、记录、确认，再进入下一步。

核心分工：

- **aisuite**：统一管理和切换模型，例如 OpenAI、Anthropic、本地 Ollama。
- **Pydantic**：作为中间格式，替代裸 JSON，积累高质量结构化数据。
- **LangGraph**：负责编排流程、状态管理、验证失败后的重试。
- **OpenMC Python API**：负责真正创建材料、几何、设置、tally 和运行模型。

## Step Plan

1. 
     - 创建项目目录结构：
     - `openmc_agent/`
     - `tests/`
     - `data/examples/`
     - `data/runs/`
     - `docs/`
   - 创建 `.env.example`，说明需要的 API key。
   - 通过标准：能运行 `python -c "import pydantic, aisuite, langgraph"`；若 OpenMC 暂时安装失败，记录原因但不阻塞前两步。

2. **Step 1：定义最小中间格式**
   - 新建 `openmc_agent/schemas.py`。
   - 先只定义：
     - `NuclideSpec`
     - `MaterialSpec`
     - `ValidationReport`
   - `MaterialSpec` 包含材料名、密度单位、密度值、核素或元素组成。
   - 写测试验证：
     - UO2 材料可通过。
     - 缺少密度会失败。
     - 组成列表为空会失败。
   - 通过标准：不用 LLM，仅靠手写 Python 对象即可完成 Pydantic 校验和 JSON 序列化。

3. **Step 2：建立 aisuite 模型调用层**
   - 新建 `openmc_agent/llm.py`。
   - 封装统一函数：
     - 输入：用户需求、目标 Pydantic schema、模型名。
     - 输出：通过 Pydantic 校验的对象。
   - 默认模型从环境变量读取，例如 `OPENMC_AGENT_MODEL=openai:gpt-4o`。
   - LLM 不直接控制 OpenMC，只负责生成结构化数据。
   - 通过标准：输入“创建 UO2 燃料”，输出一个合法 `MaterialSpec`；失败时返回可读错误，不让程序崩溃。

4. **Step 3：把 MaterialSpec 转成 OpenMC Material**
   - 新建 `openmc_agent/executor.py`。
   - 实现 `build_openmc_material(spec: MaterialSpec)`。
   - 只处理材料，不处理几何。
   - 如果本机 OpenMC 可用，就创建真实 `openmc.Material`；如果不可用，先跳过真实执行测试。
   - 通过标准：`MaterialSpec -> openmc.Material` 能跑通，并能打印材料名称、密度和组成。

5. **Step 4：建立第一条数据积累链路**
   - 新建 `openmc_agent/records.py`。
   - 每次成功生成材料后，保存一行 JSONL：
     - 原始需求
     - 使用模型
     - 生成的 `MaterialSpec`
     - 校验结果
     - 时间戳
   - 保存到 `data/examples/material_specs.jsonl`。
   - 通过标准：连续输入 3 个材料需求，JSONL 中有 3 条可重新加载的数据。

6. **Step 5：扩展到最小 pin-cell 中间格式**
   - 在 `schemas.py` 中增加：
     - `PinCellSpec`
     - `GeometrySpec`
     - `SettingsSpec`
     - `SimulationSpec`
   - 第一版只支持单燃料棒 pin-cell：
     - UO2 燃料
     - 水慢化剂
     - 可选锆包壳
     - 固定 pitch 和半径范围校验
   - 通过标准：输入“建立一个 UO2 pin-cell 临界计算”，生成合法 `SimulationSpec`。

7. **Step 6：生成最小 OpenMC 模型脚本**
   - 在 `executor.py` 中增加 `render_openmc_script(spec: SimulationSpec)`。
   - 先生成 Python 脚本文本，不直接运行。
   - 脚本必须包含：
     - materials
     - geometry
     - settings
     - tallies
     - `model.export_to_xml()`
   - 通过标准：生成 `model.py`，人工阅读结构清楚，基础字符串检查通过。

8. **Step 7：加入轻量验证**
   - 新建 `openmc_agent/validator.py`。
   - 检查：
     - 半径、包壳厚度、pitch 是否合理。
     - 脚本是否包含必要 OpenMC 结构。
     - 材料是否被几何引用。
   - 暂不做复杂物理正确性判断。
   - 通过标准：对“燃料半径 10cm”能给出明确错误；对标准 pin-cell 能通过。

9. **Step 8：建立第一个 LangGraph 线性流程**
   - 新建 `openmc_agent/graph.py`。
   - Graph 节点：
     - `receive_requirement`
     - `generate_spec`
     - `validate_spec`
     - `render_script`
     - `save_record`
   - 不做自动修复。
   - 通过标准：一个自然语言需求能从入口跑到 `model.py` 和 JSONL 记录生成。

10. **Step 9：加入一次自动修复**
   - 若 `validate_spec` 失败，把错误信息传回 LLM。
   - 只允许修复一次。
   - 修复后重新验证。
   - 通过标准：明显尺寸错误能被修正；若仍失败，系统停止并输出错误报告。

11. **Step 10：加入多轮重试和 SQLite checkpointer**
   - 引入 LangGraph checkpointer。
   - 保存每轮：
     - 原始需求
     - 当前 spec
     - 验证错误
     - 修复建议
     - retry_count
   - 最大重试次数固定为 3。
   - 通过标准：失败流程可恢复、可追踪，不会无限循环。

12. **Step 11：小规模测试集**
   - 建立 10 个测试需求：
     - 3 个材料生成
     - 4 个 pin-cell
     - 2 个参数异常修复
     - 1 个无法完成的边界案例
   - 批量运行并记录成功率。
   - 通过标准：10 个 case 中至少 8 个无需人工修改即可完成到脚本生成。

13. **Step 12：再扩展复杂度**
   - pin-cell 稳定后，再加入：
     - slab 屏蔽模型
     - fixed source 模型
     - 简单组件阵列
     - tally 扩展
   - 数据集从 10 条扩展到 30 条，再到 50 条。
   - 通过标准：不要一次扩展多个模型类型，每次只新增一种任务类型。

## Public Interfaces / Types

- `MaterialSpec`：材料结构化描述。
- `SimulationSpec`：完整仿真任务中间格式。
- `ValidationReport`：验证结果、错误、警告、修复建议。
- `generate_structured_output()`：aisuite 模型调用入口。
- `render_openmc_script()`：结构化 spec 到 OpenMC 脚本。
- `build_graph()`：LangGraph 工作流入口。
- JSONL 数据格式：每行记录一次成功或失败的建模尝试。

## Test Plan

- 环境测试：确认依赖能导入。
- Schema 测试：Pydantic 对合法和非法输入的校验。
- LLM 测试：aisuite 能返回可解析结构化数据。
- Executor 测试：`MaterialSpec` 能转为 OpenMC material。
- Validator 测试：能发现尺寸异常和缺失字段。
- Graph 测试：自然语言需求能完整跑到脚本生成。
- 数据测试：JSONL 文件可追加、可重新加载、字段完整。
- 回归测试：每完成一步都运行当前全部测试，确认没有破坏前一步。

## Assumptions

- 第一版不追求真实长时间 OpenMC 计算，先完成“生成、验证、导出、记录”闭环。
- OpenMC 安装可能受系统和截面库影响，因此真实运行放到后续确认门。
- aisuite 负责模型切换，但结构化输出仍由 Pydantic 校验兜底。
- 每一步只推进一个能力点，当前一步测试通过并确认后，再进入下一步。


 # OpenMC Agent 复杂建模 IR + RAG 扩展计划

  ## Summary

  第一版优先实现“复杂结构化输出能力 + OpenMC 官方知识检索 + few-shot + Python 接口查询 tool”，暂不把组件、全堆、TRISO、球床等全部转成可运行代
  码。现有 pin-cell 执行链保持兼容，复杂模型先进入可验证 IR，并明确标记哪些部分尚未支持代码渲染。

  依据：OpenMC 官方 Python API 覆盖 Material、Cell、Universe、RectLattice、HexLattice、TRISO、pack_spheres() 等能力，且本机 OpenMC 为 0.15.3。
  官方文档入口：https://docs.openmc.org/en/latest/pythonapi/index.html

  ## Key Changes

  - 扩展 schema 为 simulation_plan.v2，保留 v1 兼容：
      - MaterialSpec 支持 nuclide、element、chemical formula、enrichment、S(a,b)、temperature、depletable、volume、来源/假设标记。
      - 新增通用几何 IR：SurfaceSpec、RegionSpec、CellSpec、UniverseSpec、LatticeSpec、AssemblySpec、CoreSpec、ReflectorSpec、ControlRodSpec。
      - 新增高温气冷堆相关 IR：TRISOSpec、TRISOLayerSpec、PackedSphereSpec、PebbleSpec。
      - 新增 RenderCapabilityReport：说明当前 IR 哪些能执行、哪些只完成结构化描述、哪些需要人工确认。

  - 引入 OpenMC API 知识库：
      - 本地优先：用当前 conda 环境的 inspect.signature()、docstring 抽取 OpenMC 类/函数信息，保证与本机版本一致。
      - 在线补充：抓取 docs.openmc.org 的官方页面补全说明与示例。
      - 初始索引对象包括 Material、Cell、Universe、Geometry、Settings、Plot、RectLattice、HexLattice、Sphere、ZCylinder、pack_spheres()、
        TRISO、create_triso_lattice()。

      - 相关官方页面：
          - Material: https://docs.openmc.org/en/latest/pythonapi/generated/openmc.Material.html
          - HexLattice: https://docs.openmc.org/en/latest/pythonapi/generated/openmc.HexLattice.html
          - pack_spheres: https://docs.openmc.org/en/latest/pythonapi/generated/openmc.model.pack_spheres.html

  - 新增 RAG 与 few-shot 策略：
      - LLM 生成复杂 IR 前，先根据用户需求检索 OpenMC API 知识片段。
      - few-shot 按模型类型选择：pin cell、assembly lattice、core with reflector/control rod、TRISO compact、pebble/fuel sphere。
      - prompt 中强制要求：未知材料成分不得虚构，必须进入 requires_human_confirmation 或 assumptions。

  - 新增 Python 接口查询 tool：
      - inspect_openmc_api(symbol)：返回本机 OpenMC 对象的 signature、docstring 摘要、模块路径。
      - search_openmc_api(query)：在本地/在线索引中检索相关 API。
      - explain_openmc_interface(symbol)：给 Agent 提供接口用途、关键参数、常见约束。
      - 这些 tool 只用于查阅接口，不直接修改模型代码。

  - 调整 Agent 工作流：
      - 当前节点扩展为：需求读取 -> API/RAG 检索 -> few-shot 选择 -> LLM 生成 v2 IR -> schema 验证 -> capability 检查 -> 可执行子集渲染或标记
        unsupported -> plot/smoke test/reflection。

  ## Test Plan

  - Schema 单元测试：
      - pin-cell v1/v2 兼容。
      - TRISO/pebble IR 可表达多层颗粒、随机填充参数、燃料球材料。
      - 未知材料允许结构化记录，但不能被误判为可执行材料。

      - 搜索 “TRISO packing” 能命中 TRISO、pack_spheres()、create_triso_lattice()。
      - API key、环境变量等秘密不进入 transcript。

  - Workflow 测试：
      - 复杂组件输入能生成 v2 IR 和 capability report。
      - TRISO 输入能检索对应 OpenMC API 并生成结构化 TRISO IR。
      - 超出当前 executor 能力的全堆模型不会尝试硬生成错误代码，而是输出 unsupported render reason。
      - 原有 scripts/run_inspect.sh --md-file Input/case1.md --full pin-cell 流程保持通过。

  ## Assumptions

  - 第一版重点是提高 Agent 的结构化理解上限，不承诺所有复杂 IR 都能立即生成可运行 OpenMC 脚本。
  - 代码渲染仍先支持现有 pin-cell；复杂模型的可执行渲染后续按“组件 -> 反射层/控制棒 -> TRISO/球床 -> 全堆”逐步实现。
  - 官方知识库采用“本机 OpenMC introspection 优先，docs.openmc.org 补充”的策略，避免在线文档和本地版本不一致造成接口错误。


  下面是对目前问题的总结，以及一个可以直接交给 Codex CLI 的任务 Prompt。

# 一、目前遇到的问题总结

你当前的 OpenMC Agent 流程大致是：

```text
自然语言需求
→ 生成 SimulationPlan / IR
→ Pydantic 校验
→ capability 评估
→ renderer 渲染 model.py
→ export XML / smoke test
```

这次 case2 的关键现象是：

```text
SimulationPlan 校验通过
但 capability_report.is_executable = false
supported_renderer = "none"
所以没有生成 model.py
```

日志中已经明确显示：

```text
[node:validate_plan] passed
[node:assess_capability] renderer=none executable=False
```

Capability Report 也说明当前 assembly IR 不可被现有 renderer 渲染，并且多个材料缺少密度、组分或 chemical_formula。

### 主要问题 1：IR 合法，但没有可用 renderer

LLM 已经生成了 `complex_model.kind = "assembly"` 的结构化 IR，但当前 renderer 体系无法处理该复杂组件模型。

也就是说，Agent 不是“不会理解模型”，而是：

```text
能生成 IR
但执行层不会把这个 IR 转成 OpenMC Python API
```

### 主要问题 2：不可执行与不可生成被绑定了

当前逻辑近似是：

```text
is_executable = false
→ 不生成 model.py
```

这导致即使 IR 已经足够生成一个可审查的 skeleton，也没有任何 `model.py` 输出。

更合理的状态应该拆成：

```text
renderability: none | skeleton | exportable | runnable
executability: false | true
```

这样材料不完整时，也可以生成带 TODO 的 `model.py` skeleton，而不是完全不输出。

### 主要问题 3：材料缺失导致 OpenMC 不可运行

case2 的需求明确要求不要伪造材料细节、热散射数据、截面库路径等信息。因此 IR 中大量材料字段是：

```json
"density_unit": null,
"density_value": null,
"composition": [],
"chemical_formula": null
```

这在物理上是正确的保守处理，但 OpenMC 运行需要完整材料定义。因此该模型不能直接进入 runnable 状态。


### 主要问题 4：固定 renderer 不可能覆盖所有复杂堆型

随着模型复杂度上升，会出现：

```text
rect assembly
hex assembly
core lattice
reflector
control rod
burnable poison insertion
TRISO
pebble bed
显式 cell 分区
非规则几何
```

如果每次都人工写 renderer，扩展性很差。因此需要 renderer 插件化和 Agent 辅助生成 renderer 的能力。

---

## 二、解决思路总结

建议把 renderer 系统升级成四层结构：

```text
1. Renderer 统一接口
2. Renderer registry 注册与选择机制
3. SkeletonRenderer 兜底输出
4. RendererAuthoringAgent 自动生成候选 renderer，但必须经过测试和沙箱验证
```

### 1. 定义统一 Renderer 接口

所有 renderer 都实现：

```python
class Renderer(Protocol):
    name: str
    supported_kinds: list[str]

    def can_render(self, plan: SimulationPlan) -> CapabilityReport:
        ...

    def render(self, plan: SimulationPlan, outdir: Path) -> RenderResult:
        ...
```

这样 `assess_capability` 不再硬编码，而是遍历 registry。

### 2. 增加 SkeletonRenderer

当 IR 合法但不可执行时，仍然生成：

```text
model.py
capability_report.json
TODO.md
```

`model.py` 文件头明确写：

```text
NOT EXECUTABLE
missing density
missing composition
renderer fallback skeleton
```

这样调试时至少有可审查的 Python API 草稿。

### 3. 拆分渲染状态

建议引入：

```text
renderability = none | skeleton | exportable | runnable
```

对应含义：

| 状态           | 含义                             |
| ------------ | ------------------------------ |
| `none`       | IR 无法理解，不能生成任何代码               |
| `skeleton`   | 可生成审查用 model.py，但不能 export XML |
| `exportable` | 可导出 XML，但不建议运行                 |
| `runnable`   | 可执行低成本 smoke test              |

这次 case2 应该至少是：

```text
renderability = skeleton
executability = false
```

而不是直接 `renderer = none`。


### 4. Agent 自主添加 renderer，但不能直接信任

应该实现 `RendererAuthoringAgent`：

```text
当 existing renderer 无法支持 IR
→ Agent 生成 candidate renderer
→ AST 安全检查
→ 单元测试
→ 沙箱执行
→ OpenMC export_to_xml 测试
→ 通过后注册
```

禁止自动生成的 renderer 使用：

```text
os.system
subprocess
eval
exec
requests
任意路径写入
网络访问
```

允许它使用 OpenMC Python API 和指定输出目录。

---

## 三、可直接交给 Codex CLI 的 Prompt

下面这段可以直接粘贴给 Codex CLI。

你需要在当前 OpenMC Agent 项目中修复并扩展 renderer 系统。背景如下：

当前流程可以从自然语言需求生成 `SimulationPlan` / IR，并通过 Pydantic 校验。但当模型较复杂，例如 `complex_model.kind == "assembly"` 的 15×15 组件模型时，`assess_capability` 返回：

```text
renderer=none
executable=False
```

导致没有生成 `model.py`。日志中也出现类似：

```text
[node:validate_plan] passed
[node:assess_capability] renderer=none executable=False
```

Capability Report 中包含：

```json
{
  "is_executable": false,
  "supported_renderer": "none",
  "unsupported_subsystems": [
    "materials",
    "surfaces",
    "regions",
    "cells",
    "universes",
    "lattices",
    "assemblies"
  ],
  "reasons": [
    "Assembly IR was generated but is not renderable by the current rectangular assembly renderer.",
    "material 'fuel_mat' is missing density",
    "material 'fuel_mat' is missing composition or chemical_formula",
    "material 'clad_mat' is missing density",
    "material 'clad_mat' is missing composition or chemical_formula",
    "material 'coolant_mat' is missing density",
    "material 'coolant_mat' is missing composition or chemical_formula"
  ]
}
```

请完成以下任务。

## 目标

把 renderer 系统从“固定硬编码 renderer”升级为“可注册、可选择、可兜底、可扩展”的架构。即使复杂模型暂时不可执行，也应生成可审查的 `model.py` skeleton，而不是完全没有输出。

## 任务 1：梳理现有代码

请先检查项目中与以下功能相关的代码：

```text
SimulationPlan schema
CapabilityReport schema
assess_capability 节点
renderer 选择逻辑
model.py 生成逻辑
run_inspect.sh 或 CLI 输出逻辑
保存 run record 的逻辑
测试目录
```

不要删除现有 pin-cell renderer 或已有功能。所有改动必须保持向后兼容。

## 任务 2：引入统一 Renderer 接口

新增或重构 renderer 基类，建议接口如下：

```python
class BaseRenderer:
    name: str
    supported_kinds: list[str]

    def can_render(self, plan: SimulationPlan) -> CapabilityReport:
        ...

    def render(self, plan: SimulationPlan, outdir: Path) -> RenderResult:
        ...
```

如果项目已有类似结构，请在现有结构上最小改造。

`RenderResult` 至少应包含：

```python
renderer_name: str
renderability: str  # "none" | "skeleton" | "exportable" | "runnable"
is_executable: bool
output_files: list[str]
warnings: list[str]
errors: list[str]
```

## 任务 3：实现 renderer registry

新增 renderer registry，例如：

```python
RENDERERS = [
    PinCellRenderer(),
    RectAssemblyRenderer(),
    SkeletonRenderer(),
]
```

实现统一选择逻辑：

```python
def choose_renderer(plan: SimulationPlan) -> tuple[BaseRenderer | None, CapabilityReport]:
    ...
```

选择优先级：

1. 能生成 runnable 模型的 renderer；
2. 能生成 exportable 模型的 renderer；
3. 能生成 skeleton 的 renderer；
4. 否则返回 none。

注意：`SkeletonRenderer` 应该永远排在最后，作为兜底。

## 任务 4：拆分 renderability 和 executability

当前逻辑中 `is_executable = false` 会导致不生成 `model.py`。请修复这个问题。

新增或使用字段：

```text
renderability = none | skeleton | exportable | runnable
```

含义：

```text
none: 无法理解 IR，不能生成代码
skeleton: 可生成审查用 model.py，但不能 export XML
exportable: 可生成 model.py 并 export XML，但不建议 openmc.run()
runnable: 可生成 model.py、export XML，并运行低成本 smoke test
```

新的逻辑应为：

```text
SimulationPlan valid
→ choose_renderer
→ 如果 renderability != none，则生成 model.py
→ 如果 renderability == exportable 或 runnable，则尝试 export XML
→ 如果 renderability == runnable，才运行低成本 OpenMC smoke test
```

## 任务 5：实现 SkeletonRenderer

实现 `SkeletonRenderer`，要求：

1. 当 `SimulationPlan` 合法但没有可执行 renderer 时，仍然生成 `model.py`。
2. 生成的 `model.py` 顶部必须包含清晰状态说明，例如：

```python
# Auto-generated OpenMC model skeleton
# Status: NOT EXECUTABLE
# Renderability: skeleton
# Reasons:
# - material 'fuel_mat' is missing density
# - material 'fuel_mat' is missing composition or chemical_formula
```

3. Skeleton 文件中应尽量根据 IR 生成 OpenMC Python API 结构草稿，包括：

```text
import openmc
materials section
surfaces section
cells section
universes section
lattice section
geometry section
settings section
model = openmc.Model(...)
```

4. 对缺失材料、缺失边界、缺失 universe pattern 等位置写 TODO 注释。
5. SkeletonRenderer 不应调用 `model.export_to_xml()`，除非所有必要字段完整。
6. 同时输出 `TODO.md` 或 `capability_report.json`，记录缺失项。

## 任务 6：实现 RectAssemblyRenderer 的最小版本

实现一个最小可用的 `RectAssemblyRenderer`，支持：

```text
complex_model.kind == "assembly"
complex_model.lattices[*].kind == "rect"
```

它应至少处理：

```text
materials
surfaces, especially zcylinder and rectangular_prism
regions
cells
universes
lattices
assemblies
settings
plots if present
```

`can_render()` 中必须检查：

```text
1. complex_model 存在且 kind == "assembly"
2. 至少有一个 rect lattice
3. lattice.shape 与 lattice.universe_pattern 维度一致
4. universe_pattern 中引用的 universe id 都存在
5. cell 引用的 region_id 存在
6. region 引用的 surface_ids 存在
7. material cell 引用的 material id 存在
8. 所有 runnable 材料都有 density 和 composition 或 chemical_formula
9. 所有 zcylinder 的 r 为正数
10. 最大圆柱外半径小于 pitch/2
11. assembly boundary 明确
```

行为要求：

```text
- 如果结构完整但材料缺失：返回 renderability="skeleton", is_executable=False
- 如果结构和材料都完整，可 export XML：返回 renderability="exportable"
- 如果还满足低成本运行条件：返回 renderability="runnable"
```

注意：如果材料缺失，不要伪造密度、组分、热散射数据或 cross_sections.xml 路径。


## 任务 7：增加 Agent 自主 renderer 生成的预留接口

不要直接实现完全自主代码生成，但请预留干净接口：

```text
renderer_authoring/
  planner.py
  codegen.py
  validator.py
  sandbox.py
```

先实现 stub：

```python
class RendererAuthoringAgent:
    def propose_renderer(self, plan: SimulationPlan, capability_report: CapabilityReport) -> CandidateRenderer:
        ...
```

当前可以返回 “not implemented”，但主流程中应能识别：

```text
如果没有 renderer，可在未来调用 RendererAuthoringAgent
```

同时写清楚安全约束，后续自动生成 renderer 必须经过：

```text
AST 静态检查
禁止 os.system/subprocess/eval/exec/requests
沙箱目录执行
单元测试
OpenMC export_to_xml 测试
通过后才能注册
```

## 任务 9：更新 CLI / run_inspect 输出

当生成 skeleton 时，CLI 输出中应明确说明：

```text
Generated model.py skeleton
Status: NOT EXECUTABLE
See capability_report.json and TODO.md
```

不要让用户误以为模型已经可运行。

当生成 exportable 或 runnable 模型时，输出：

```text
Generated model.py
Exported XML files
Smoke test status
```

## 任务 10：增加测试

请新增或更新测试，至少覆盖：

### test_skeleton_renderer_for_incomplete_assembly

输入：一个 `complex_model.kind="assembly"`，包含 rect lattice，但材料缺 density/composition。

期望：

```text
SimulationPlan valid
choose_renderer 返回 SkeletonRenderer 或 RectAssemblyRenderer 的 skeleton 模式
生成 model.py
model.py 包含 NOT EXECUTABLE
model.py 包含 TODO
不调用 openmc.run()
```

### test_rect_assembly_lattice_shape_validation

输入：lattice.shape = [15, 15]，但 universe_pattern 不是 15×15。

期望：

```text
can_render 返回 warning/error
renderability 不得为 runnable
```

### test_rect_assembly_missing_universe_reference

输入：universe_pattern 引用了不存在的 universe id。

期望：

```text
can_render 报错
不生成 exportable/runnable 模型
```

### test_rect_assembly_material_completeness

输入：材料缺 density 或 composition。

期望：

```text
is_executable=False
renderability="skeleton"
reasons 中包含 material missing density/composition
```

### test_existing_pin_cell_still_works

确保已有简单 pin-cell renderer 不受影响。

## 任务 11：验收标准

完成后，运行以下命令或项目中等价测试：

```bash
pytest
```

然后用 case2 再跑一次：

```bash
scripts/run_inspect.sh --model deepseek:deepseek-chat --md-file Input/case2.md --full --text
```

预期结果至少应为：

```text
SimulationPlan validation passed
Capability assessment reports missing materials
Generated model.py skeleton
Generated capability_report.json
Generated TODO.md
No OpenMC run attempted because model is not executable
```

如果材料和 lattice pattern 后续补齐，则应能进入：

```text
Generated model.py
Exported materials.xml geometry.xml settings.xml
Optional low-cost smoke test
```

## 重要约束

1. 不要伪造核材料密度、组分、温度、热散射数据或 cross_sections.xml 路径。
2. 不要把未知的可燃毒物棒真实插入方案当作确定事实。
3. 不要为了通过测试而跳过 capability 检查。
4. Skeleton 输出必须清楚标记 NOT EXECUTABLE。
5. 所有新增 renderer 必须可测试、可注册、可诊断。
6. 保持已有 pin-cell 用例兼容。
7. 如果现有代码结构和上述建议不同，请采用最小侵入式改造，但必须实现同等功能。

最终请输出：

```text
1. 修改文件列表
2. 新增 renderer 架构说明
3. 新增测试列表
4. case2 的实际运行结果
5. 是否生成 model.py skeleton
6. 后续实现 RendererAuthoringAgent 的 TODO
```

这个 Prompt 的重点是先解决“**没有 model.py 输出**”的问题，再为“**Agent 自主添加 renderer**”预留安全、可测试的扩展入口。
