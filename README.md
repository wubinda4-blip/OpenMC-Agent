# OpenMC-Agent

把一句自然语言的反应堆建模需求，变成可审查、可校验、可运行的 OpenMC Python 模型。

`openmc-agent` 用 **Pydantic 结构化输出 + LangGraph 编排 + 可插拔渲染器** 把 LLM 限制在"只生成结构化数据"的安全角色里：LLM 不直接写代码、不直接调用 OpenMC，而是产出一个强类型的 `SimulationPlan`，再由本地渲染器将其转成 `model.py`，最后用 OpenMC 做 XML 导出、几何绘图和低粒子 smoke test。

> 设计原则：**LLM 只负责结构化建模决策，执行权完全在本地。** 缺失的物理数据会被放进 `requires_human_confirmation`，绝不伪造材料、密度或截面库。

系统 prompt 位于 `openmc_agent/prompts.py`，负责定义 Agent 能力边界、JSON-only 输出契约、缺失数据处理和 OpenMC 安全规范；`Input/case*.md` 只保留算例事实、默认假设、不确定项和审查目标。

---

## 核心特性

- **结构化 IR**：`SimulationPlan` / `ComplexModelSpec` 用 Pydantic v2 描述 pin-cell、组件、全堆、TRISO、燃料球等，校验失败即拒绝。
- **双工作流**：轻量 `SimulationSpec`（仅 pin-cell）与完整 `SimulationPlan`（复杂 IR + 能力评估 + 渲染）。
- **渲染能力分级**：`none → skeleton → exportable → runnable`，宁可产出"仅可审查的骨架"也不输出错误的可执行模型。
- **可插拔渲染器**：PinCell / RectAssembly / Triso / Core / Skeleton（兜底），按能力自动选优。
- **多模型适配**：内置智谱 GLM、DeepSeek 的 OpenAI 兼容 HTTP 客户端（含 SSE 流式、超时重试），其余走 aisuite。
- **检索增强**：本地 OpenMC Python API 内省 + few-shot 示例注入到生成 prompt。
- **可观测**：每次运行产出 `transcript.json`、`capability_report.json`、`TODO.md` 与 JSONL 运行记录。

---

## 工作流

### SimulationPlan 工作流（`--plan`，默认当指定 `--plot`/`--smoke-test`/专家反馈时也启用）

```
receive_requirement
   → retrieve_openmc_docs      # 本地内省 OpenMC API，取相关符号/签名
   → select_few_shots           # 按结构特征+关键词挑 few-shot（抽象范式 + gold case）
   → generate_plan              # LLM 产出 SimulationPlan（带 normalization 容错）
   → validate_plan              # Pydantic 校验
   → repair_plan_format ─坏 JSON/坏 schema 重试──▶ validate_plan
   → reflect_plan ──验证失败重试───────────────▶ validate_plan
   → assess_capability          # 本地重算 capability_report（覆盖 LLM 草稿）
   → ask_expert                 # 可选：LangGraph interrupt/resume 专家反馈
   → render_plan_script         # choose_renderer 选渲染器 → model.py（或骨架）
   → execute_tools              # export_xml / 几何绘图 / smoke test
   → reflect_plan ──失败重试──▶ validate_plan
   → save_record
```

关键容错点：`generate_structured_output` 支持传入 `normalizer`，默认对 plan 启用 `normalize_capability_report`——LLM 若给出"非可执行却带具体 renderer"的矛盾 capability_report，会在 Pydantic 校验前被修正为 `supported_renderer="none"`，避免整个 plan 坍缩为 null。若模型返回坏 JSON 或 schema 不合格，Plan 工作流会先尝试格式修复；若已生成 plan 但验证失败，会进入 reflection 修复，而不是直接终止。

### 渲染能力分级

| `renderability` | 含义 | 产物 |
|---|---|---|
| `none` | 无渲染器能处理 | 仅结构化 IR，供专家审查 |
| `skeleton` | 信息不全，产出审查骨架 | `model.py`（**不可执行**，`export_to_xml` 被注释）+ `TODO.md` |
| `exportable` | 可导出 XML，但不可运行 | `model.py` + XML 文件 |
| `runnable` | 完整模型 | `model.py` + XML + 可选 smoke test |

**3D assembly guard**：当需求包含轴向异质结构（axial layers、spacer grid、explicit z 范围、nozzle/plenum 等通用信号）但 plan 仍只是 2D assembly root 时，`openmc_agent/assembly3d_guard.py` 会在 plan validation 阶段（而非等到 renderer 抛错）就阻断导出，发出结构化 issue（`assembly3d.axial_layers_required` / `assembly3d.default_z_extent_for_axial_problem` / `assembly3d.spacer_grid_material_slab` / `assembly3d.pin_through_path_missing`），降级为 skeleton 或要求 human confirmation——避免产出 z=-1..1 的"形式可导出但物理错误"的伪 3D 模型。该 guard 只看通用词汇与 IR 形状，不含任何 benchmark 专用事实。

---

## 安装

需 Python ≥ 3.10 与 OpenMC（运行目标，不在 `pyproject.toml` 依赖里）。

```bash
conda create -n openmc-env python=3.10 -y
conda activate openmc-env
conda install -c conda-forge openmc        # OpenMC Python API 与可执行文件
pip install -e ".[dev]"                     # aisuite / httpx / langgraph / pydantic / pytest
```

---

## 配置

复制 `.env.example` 为 `.env` 并填入所用 provider 的 key：

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `OPENMC_AGENT_MODEL` | `zhipu:glm-5.2` | `provider:model` 格式 |
| `ZHIPUAI_API_KEY` | — | 智谱 GLM |
| `DEEPSEEK_API_KEY` | — | DeepSeek |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | 走 aisuite 的 provider |
| `ZHIPUAI_TIMEOUT_SECONDS` / `_MAX_RETRIES` | 180 / 2 | provider 级超时与重试 |
| `OPENMC_AGENT_STREAM` | `1` | SSE 流式（慢模型建议开） |
| `OPENMC_AGENT_LLM_HEARTBEAT_SECONDS` | 10 | 心跳日志间隔 |

---

## 使用

CLI 入口：`scripts/run_inspect.sh`（封装）或 `python -m openmc_agent.inspect`。

```bash
# 从 Markdown 需求文件跑完整 plan 工作流（导出 + 绘图 + smoke test）
scripts/run_inspect.sh --md-file Input/case1.md --full

# 一句话需求
scripts/run_inspect.sh --requirement "建立一个 UO2 pin-cell 临界计算" --full --text

# 换模型 / 指定复杂组件用例
scripts/run_inspect.sh --model deepseek:deepseek-chat --md-file Input/case2.md --full

# 直接用 Python 模块
python -m openmc_agent.inspect "建立一个 2x2 组件模型" --plan --plot --smoke-test --json
```

常用参数：`--plan`（强制 plan 工作流）、`--plot` / `--smoke-test` / `--full`、`--output-dir`、`--json` / `--text`（输出格式）、`--expert-feedback`、`--interactive-feedback`、`--max-expert-rounds`、`--verbose`。

交互式专家反馈：

```bash
python -m openmc_agent.inspect --md-file Input/case2.md --plan --interactive-feedback --max-expert-rounds 2
```

当 capability report 或 IR 中存在阻塞性人工确认项时，LangGraph 会通过 `interrupt` 暂停，CLI 展示问题并用 `Command(resume=...)` 把专家反馈写回图状态，然后重新生成/修复 `SimulationPlan`。

---

## 组件建模示例

`Input/case2.md` 是一个 15x15 PWR 组件用例：默认组件包含燃料棒和导向管，另有一个候选 burnable poison universe，但默认不插入 lattice。完整流程会让 LLM 生成 `SimulationPlan`，本地 `RectAssemblyRenderer` 再做可达性分析，只渲染默认 lattice 实际使用的材料、cell、universe。

```bash
scripts/run_inspect.sh \
  --model deepseek:deepseek-chat \
  --md-file Input/case2.md \
  --full \
  --text
```

成功时，摘要应显示生成的是可执行 `model.py`，而不是 `Status: NOT EXECUTABLE` 的 skeleton：

```text
Generated model.py
Exported XML files: materials.xml, geometry.xml, settings.xml, tallies.xml, plots.xml
```

这个例子覆盖了几个组件建模中的常见坑：

- **组件 root 自动重建**：渲染器使用 `AssemblySpec.lattice_id` 生成 OpenMC root cell，LLM 输出中未插入的 root universe 不会阻塞默认模型。
- **候选 BP 不阻塞默认组件**：未插入 lattice 的 `burnable_poison_universe` 可以保留在 IR 中；其缺失的硼硅酸盐玻璃密度/成分只进入 warning 和 `TODO.md`。
- **UO2 富集材料安全渲染**：若 LLM 给出 `U235/U238` 的 weight percent 和 `O16` 的 atom ratio，渲染器会用 `chemical_formula="UO2"` + `enrichment_percent` 生成 OpenMC 接受的材料卡，避免 `Cannot mix atom and weight percents`。
- **几何边界兼容**：`rectangular_prism` 可作为复合 region 使用，并转换成当前 OpenMC API 的 `openmc.model.RectangularPrism`。

也可以离线跑固定 case2 回归验证，不调用远程模型：

```bash
conda activate openmc-env
python scripts/verify_case2_renderer.py
```

该脚本会检查默认组件不是 skeleton、候选 BP 材料没有进入 `model.py`、XML 可导出，并执行几何绘图/低粒子 smoke test 工具链。

---

## 输出

默认写入 `data/runs/<run>/`：

- `model.py` —— 渲染出的 OpenMC 模型（或不可执行的骨架）
- `transcript.json` —— 全流程结构化记录（需求 / IR / capability / 验证 / 工具结果）
- `capability_report.json`、`TODO.md` —— 骨架模式下的待办与缺口说明
- `materials.xml` / `geometry.xml` / `settings.xml`、`*.png` 截面图、`statepoint.*.h5`
- `inspect_runs.jsonl` —— 累积运行记录

---

## 项目结构

```
openmc_agent/
├── schemas.py            # Pydantic IR：Material/Geometry/ComplexModel/SimulationPlan/Capability
├── llm.py                # OpenAI 兼容客户端(智谱/DeepSeek) + 结构化输出 + normalization + repair
├── graph.py              # LangGraph 两条工作流（build_graph / build_plan_graph）
├── validator.py          # spec / plan / 生成脚本 校验
├── renderers/            # 可插拔渲染器：pin_cell / assembly / triso / core / skeleton + registry
├── renderer_authoring/   # 预留：agent 在线编写新渲染器（当前为安全受控的 stub，主流程未启用）
├── executor.py           # 早期直接渲染脚本（pin-cell + 复杂模型直写）
├── tools.py              # OpenMC 子进程工具：export_xml / 绘图 / smoke test
├── openmc_api.py         # 本地 OpenMC API 内省与文档检索
├── few_shots.py          # few-shot 选取（抽象范式 + gold case，堆型无关）
├── few_shot_cases.py     # gold case loader（slim IR / patch / 结构特征）
├── records.py            # JSONL 运行记录
└── inspect.py            # CLI 与可编程入口
tests/                    # 覆盖 schemas/llm/graph/renderers/executor/tools/validator 等
Input/                    # 示例建模需求（case1.md / case2.md）
```

## 协作与文档维护规则

仓库根目录维护两份 agent 规则文件：

- `AGENTS.md`：Codex 使用。
- `CLAUDE.md`：Claude 使用。

核心约定：

- **本仓库默认开启自动 commit/push**，显式覆盖全局 `~/.claude/CLAUDE.md` 中"不自动提交除非明确要求"的默认。
- 每次代码改动完成后，运行相关测试；能跑全量测试时优先运行 `conda run -n openmc-env python -m pytest -q`。
- 测试通过且确认改动范围无误后，自动 commit 并 push 当前分支。
- 自动提交时只 stage 本次任务相关文件，不把用户已有脏文件、临时脚本、PDF 或未确认输入资料混入提交。
- 每次重要代码或架构变更后，同步维护 `README.md` 和 `docs/project_technical_report.md`。
- `docs/project_technical_report.md` 是当前项目进度、架构状态、验证结果、风险边界和下一步建议的总入口。

## 默认检索策略

当前默认开启检索工具链：

```text
grep -> graph -> GraphRAG query planner -> GraphRAG -> plain RAG -> evidence ranking
```

默认策略会尽量用本地代码、测试、文档、知识图谱和 GraphRAG evidence 帮助 `reflect_plan` 修复结构问题，减少不必要的人工参与。对 cross section 路径、材料密度、composition、benchmark 常数、真实 loading map 等 fact gap，系统也会检索文档解释和配置上下文，但仍保留 human confirmation，不会自动编造缺失事实。

Knowledge Asset Runtime Loader：若设置 `OPENMC_AGENT_KNOWLEDGE_DIR`（或 inspect CLI 传 `--knowledge-dir`，或 `RetrievalPolicy.knowledge_graph_path`），orchestrator 会在 GraphRAG stage 自动加载 `data/knowledge` 中的持久化 graph nodes/edges 作为 `extra_nodes/extra_edges`；目录缺失或损坏只产生 warning，不影响 workflow。详见 `docs/knowledge_runtime_strategy.md`。

---

## 可插拔渲染器

每个渲染器继承 `BaseRenderer`，实现：

- `can_render(plan) -> RenderCapabilityReport`：不写文件，只声明能把这个 plan 做到哪一级（`none/skeleton/exportable/runnable`）。
- `render(plan, outdir) -> RenderResult`：写出 `model.py` 及 sidecar。

在 `renderers/registry.py:RENDERERS` 注册；`choose_renderer` 按 **runnable > exportable > skeleton > none** 选最高能力者，`SkeletonRenderer` 始终作为最后兜底（注册顺序须保持靠后）。新增渲染器只需实现接口并加入注册表。

---

## 测试

```bash
conda activate openmc-env
pytest                 # 或 python -m pytest
```

测试用 fake HTTP/LLM client，不依赖真实远程模型；涉及真实 OpenMC 执行的用例会在缺 OpenMC 时跳过。

---

## 局限

- 仅支持 `eigenvalue` 模式；复杂渲染器目前覆盖 pin-cell、矩形组件/全堆、TRISO/单球，其余落为 skeleton。
- `renderer_authoring`（agent 自主编写新渲染器）为预留接口，当前显式返回"未实现"，不会自动执行生成代码。
- LLM 偶发输出仍可能不合规，依赖 schema 校验 + normalization + repair 重试兜底；远程模型调用建议开启流式与重试。
