# 研究路线图（Research Roadmap）

> Phase 0 现状盘点 · 定义研究边界与每个工具/能力在工作流中的职责。
> 事实依据见 [[current_capability_matrix]] 与 [[error_taxonomy_draft]]。

---

## 1. 系统定位与研究边界

**系统是什么**：一个 LLM 驱动的 OpenMC 建模 agent。输入自然语言需求 → 产出 `SimulationPlan`（结构化 IR）→ 由 renderer 生成可执行/可审查的 `model.py`，并通过 validator + auto_repair + retrieval + 人工回路闭环纠错。

**研究边界的核心标识是 `renderability`**（`schemas.py:6`）：

| renderability | 语义                                  | 系统承诺              |
| ------------- | ----------------------------------- | ----------------- |
| `none`        | 无 renderer 能识别 IR                    | 不产出任何 model       |
| `skeleton`    | IR 存在但诊断失败 / 缺核数据                   | review-only，不跑    |
| `exportable`  | 能导出 XML 但运行成本高/未验证                  | 可导出，谨慎运行          |
| `runnable`    | 通过全部诊断、低成本                           | 可 smoke test      |

**铁律**：从不假装 runnable。skeleton 故意省略 `export_to_xml()`（`skeleton.py:364`），核数据缺口必须经 `ask_expert` 人工确认（`graph.py:1038`）。

---

## 2. 各工具/能力在工作流中的职责边界

工作流主线（`graph.py`）：
`generate_plan → validate → assess_capability → [reflect_plan | ask_expert] → render → export_xml → smoke_test`。

| 工具 / 机制                       | 职责                                          | 边界（不做什么）                              |
| ----------------------------- | ------------------------------------------- | ------------------------------------- |
| **Schema `model_validator`**  | 拦截硬性非法（inactive<batches、空 plan、一致性）          | 不做跨实体引用检查（交给 validator/renderer）      |
| **`validator.py`**            | IR/plan/script 静态检查，产出带 code 的 issue        | 不分析运行时输出                              |
| **Renderer `can_render`**     | 决定 renderability + 产出结构化 `issues`            | 不修复，只诊断                              |
| **`auto_repair.py`（A 档）**     | 唯一解 id-reference typo → patch               | 不碰多解/shape/pin_count；patch 失败即放弃      |
| **`reflect_plan`（B 档）**       | LLM 修结构 typo（先 patch、再重生成）                   | 不填事实缺口                                |
| **`retrieval.py` + KnowledgeRef（C 档）** | 查手册/代码/示例，给 LLM 注入写法                        | 不做决策，只提供证据                            |
| **`ask_expert` interrupt（D 档）** | 向人工索取事实（密度/成分/核数据/假设确认）                     | 不问"怎么写"（那是 C 档）                       |
| **`tools.py` runtime**        | export_xml 完整性、dangling ref、smoke test、输出解析 | 输出解析目前仅 5 类文本匹配，无 code 回灌（待系统化）      |

---

## 3. 能力现状一页纸

| 能力                | 状态            | 关键缺口                                |
| ----------------- | ------------- | ----------------------------------- |
| pin-cell          | runnable      | —                                   |
| rect assembly     | runnable      | —                                   |
| rect core + 轴向分层  | runnable      | 仅 rect                              |
| hex lattice       | skeleton      | 无 hex renderer                      |
| TRISO / 单 pebble  | runnable      | packing 仅占位                         |
| pebble_bed        | skeleton      | 无堆积求解器                              |
| C5G7 MGXS         | 部分支持          | 无通用 MGXS 库构造                        |
| depletion         | **未支持**       | schema 无 burnup                     |
| runtime error 修复  | 未系统化          | 输出解析无 code、不回灌 auto_repair          |
| reflector/控制棒独立体  | skeleton      | 仅作 core 子部件                         |

---

## 4. 分阶段研究方向

> 排序原则：先补"已有数据但没串起来"的短板（性价比高），再扩表达力。

### Phase 1 — 闭环现有的诊断数据（低风险、高收益）
- **P1.1 Runtime 错误码化**：把 `tools.parse_openmc_output` 的 5 类文本匹配 + OpenMC stderr 关键错误，映射为稳定 code（如 `runtime.cross_section_missing` / `runtime.geometry_overlap` / `runtime.lost_particle`），纳入 `error_catalog`，并让它们能进 `reflect_plan`/`ask_expert` 路由。
- **P1.2 export_xml dangling ref 与 auto_repair 闭环**：`tools._geometry_lattice_reference_error` 已能定位 cell fill / lattice universe 悬空（`tools.py:194`），但结果未回灌为 patch。让它产出指向具体 plan path 的 issue，接 A/B 档。
- **P1.3 hex lattice 入 catalog + 诊断**：补 `lattice.hex.*` code，先让 hex 至少有结构化诊断（即便 renderer 仍 skeleton）。

### Phase 2 — 扩 renderer 覆盖
- **P2.1 HexAssemblyRenderer**：基于 [[openmc-agent-fullcore-repeated-geometry]] 的 cell→universe→lattice 嵌套范式，支持六角组件/快谱堆。
- **P2.2 Reflector / ControlRod 作为独立可渲染子系统**：脱离仅作 core 子部件的限制，支持独立反射层计算。
- **P2.3 pebble_bed renderer**：随机堆积（`pack_spheres` 落地）+ 整床 lattice。

### Phase 3 — 物理模型扩展
- **P3.1 depletion 支持**：扩 `RunSettingsSpec.run_mode`、新增 `BurnupSpec` / 材料 `depletable` 路径与 renderer。涉及核数据耦合，**强人工依赖**。
- **P3.2 通用 MGXS 库构造**：超越单一 c5g7，支持用户 MGXS HDF5 + 群结构选择（连续能与多群默认见 [[openmc-agent-ce-default]]）。
- **P3.3 TRISO packing 求解器**：`pack_spheres` 真实实现 + 体积分数/PF 错误码。

### Phase 4 — 知识层
- **P4.1 KnowledgeRef → 检索落地**：`schema_knowledge.py` + `retrieval.py` 已有钩子，把 `error_catalog` 的 `knowledge_refs` 真正接到检索，让 C 档闭环。
- **P4.2 GraphRAG / 概念图**：用 `concept_id` 建立概念图（material↔cell↔universe↔lattice 依赖），辅助多解 ref 消歧与影响面分析。

---

## 5. 跨阶段约束（设计原则）

1. **safety first**：未确认核数据/未通过诊断的 plan 永远停在 skeleton，不静默降级到 runnable。
2. **deterministic before LLM**：A 档确定性修复优先于 B 档 LLM，B 档优先于 D 档人工，最小化昂贵且不可靠的调用。
3. **stable codes as contracts**：所有诊断必须有 `code`，否则无法进自动修复回路；新增 renderer/validator 必须同步登记 catalog 条目。
4. **repeated-geometry first**：全堆扩展必须沿用 cell→universe→lattice 嵌套范式（[[openmc-agent-fullcore-repeated-geometry]]），禁止平面展开。
5. **facts vs typos**：永远区分"缺事实（D 档 ask_expert）"与"写错 id/形状（A/B 档自动）"，二者路由不混。

---

## 相关记忆
- [[openmc-agent-fullcore-repeated-geometry]] — 全堆重复几何里程碑
- [[openmc-agent-ce-default]] — 默认连续能
- [[openmc-agent-test-env]] — 测试环境 conda openmc-env
