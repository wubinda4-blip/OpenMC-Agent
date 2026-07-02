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
