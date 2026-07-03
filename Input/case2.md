# 算例2：15×15 组件 OpenMC 模型（图片资料完善版）

## 1. 建模目标

建立一个 15×15 组件级 OpenMC 模型，用于验证智能体流程能否从单棒几何扩展到组件尺度，并处理：

- 15×15 棒阵列的 `openmc.RectLattice` 组织；
- 燃料棒位与导向管 / 可燃毒物棒候选棒位的区分；
- 明确的棒位映射；
- 组件级 material、surface、cell、universe、lattice、geometry、settings 和可选 tally 组织；
- 几何一致性检查；
- XML 导出和低计算成本诊断。

该算例的目的不是复现真实堆芯物理结果，而是验证组件级 OpenMC 模型生成、棒位组织、默认假设记录和早期错误诊断能力。

## 2. 图片资料中可直接确定的信息

### 2.1 组件规模

- 组件排布：15×15。
- 总棒位数：225。
- 导向管数量：21。
- 燃料棒位数量：204。
- 棒位类型：
  - `F`：燃料棒位；
  - `G`：导向管 / 可燃毒物棒候选棒位。

### 2.2 导向管位置

以下坐标使用 1-based 行列号，即左上角为 `(row=1, col=1)`，右下角为 `(row=15, col=15)`。

导向管 / 可燃毒物棒候选位置共 21 个：

```text
[(3, 3), (3, 6), (3, 10), (3, 13), (4, 8), (5, 5), (5, 11), (6, 3), (6, 13), (8, 4), (8, 8), (8, 12), (10, 3), (10, 13), (11, 5), (11, 11), (12, 8), (13, 3), (13, 6), (13, 10), (13, 13)]
```

对应 15×15 棒位映射为：

```text
01: F F F F F F F F F F F F F F F
02: F F F F F F F F F F F F F F F
03: F F G F F G F F F G F F G F F
04: F F F F F F F G F F F F F F F
05: F F F F G F F F F F G F F F F
06: F F G F F F F F F F F F G F F
07: F F F F F F F F F F F F F F F
08: F F F G F F F G F F F G F F F
09: F F F F F F F F F F F F F F F
10: F F G F F F F F F F F F G F F
11: F F F F G F F F F F G F F F F
12: F F F F F F F G F F F F F F F
13: F F G F F G F F F G F F G F F
14: F F F F F F F F F F F F F F F
15: F F F F F F F F F F F F F F F
```

其中：

- `F` 表示燃料棒 universe；
- `G` 表示导向管 universe；
- 默认情况下，所有 `G` 位置均建模为水填充导向管；
- 若后续提供真实可燃毒物棒插入图，可将其中部分 `G` 位置替换为可燃毒物棒 universe。

### 2.3 导向管尺寸

图片文字给出：

- 导向管外径：12.9 mm；
- 导向管内径：11.9 mm。

换算为 OpenMC 使用的 cm 半径：

- 导向管外半径：0.645 cm；
- 导向管内半径：0.595 cm。

### 2.4 可燃毒物棒候选尺寸

图片文字给出“毒物棒尺寸”：

- 外包壳：10 mm – 9 mm；
- 硼玻璃层：8.8 mm – 5.8 mm；
- 不锈钢内衬：5.6 mm – 5.14 mm。

按“外径 – 内径”理解，换算为 cm 半径：

| 部件 | 外径 mm | 内径 mm | 外半径 cm | 内半径 cm |
|---|---:|---:|---:|---:|
| 可燃毒物棒外包壳 | 10.0 | 9.0 | 0.500 | 0.450 |
| 硼玻璃层 | 8.8 | 5.8 | 0.440 | 0.290 |
| 不锈钢内衬 | 5.6 | 5.14 | 0.280 | 0.257 |

建模约定：

- 可燃毒物棒不是默认插入件；
- 该尺寸仅用于定义一个候选 `burnable_poison_universe`；
- 只有当人工提供插入位置后，才允许将某些 `G` 位置替换为 `burnable_poison_universe`；
- 图片未给出硼玻璃材料组成，因此硼玻璃材料必须列为需要人工确认，除非采用明确标注的工程默认值。

## 3. 单棒几何闭合定义

图片说明“燃料棒、包壳、栅距的尺寸同单棒元件的数据”。为了使本 `case2.md` 可以独立驱动组件建模，组件级输入必须显式包含单棒几何参数。

若上游单棒算例已有更权威数据，应优先使用上游单棒数据。若本文件独立运行，可采用以下工程默认闭合值，并在 IR 中标记为默认假设：

- 栅距 `pitch`：1.43 cm；
- 燃料芯块外半径 `fuel_radius`：0.4095 cm；
- 包壳内半径 `clad_inner_radius`：0.4180 cm；
- 包壳外半径 `clad_outer_radius`：0.4750 cm；
- 燃料棒间隙区域：`fuel_radius < r < clad_inner_radius`；
- 包壳区域：`clad_inner_radius < r < clad_outer_radius`；
- 冷却剂区域：`clad_outer_radius < r < pitch/2` 的 pin cell 剩余区域。

几何内含检查：

- `clad_outer_radius = 0.4750 cm < pitch/2 = 0.715 cm`，燃料棒可被栅元包含；
- `guide_tube_outer_radius = 0.645 cm < pitch/2 = 0.715 cm`，导向管可被栅元包含；
- `burnable_poison_outer_radius = 0.500 cm < guide_tube_inner_radius = 0.595 cm`，可燃毒物棒候选件可放入导向管内；
- 所有圆柱半径单位均为 cm。

组件边界：

- 组件边长：`15 * pitch = 21.45 cm`；
- 组件半宽：`10.725 cm`；
- `RectLattice.lower_left = (-10.725, -10.725)`；
- 外边界使用矩形边界包围 lattice；
- 单组件诊断默认采用 `reflective` 边界；若放入上级堆芯模型，边界条件应由堆芯模型提供。

## 4. 材料定义

### 4.1 燃料材料

图片给出：

- 燃料富集度：3.1%。

建模定义：

- 燃料材料：UO2；
- U-235 富集度：3.1 wt%；
- 燃料密度：10.4 g/cm³，作为工程默认假设；
- 温度：默认 600 K，需人工确认。

IR 记录项：

- 富集度来自图片资料；
- UO2 化学式、密度和温度为工程默认假设；
- 若上游资料给出更准确燃料密度、温度或氧铀比，应覆盖默认值。

### 4.2 包壳材料

图片给出包壳为 Zr-4 合金，重量百分比：

| 元素 | 重量百分比 |
|---|---:|
| Sn | 1.5% |
| Fe | 0.2% |
| Cr | 0.1% |
| O | 0.1% |
| Zr | 98.1% |

建模定义：

- 包壳材料：Zr-4；
- 组成采用重量百分比；
- 密度：6.56 g/cm³，作为工程默认假设；
- 温度：默认 600 K，需人工确认。

### 4.3 冷却剂 / 慢化剂材料

图片未给出冷却剂组成、密度、温度或热散射数据。为了生成可执行 OpenMC 模型，可采用以下工程默认假设：

- 冷却剂：H2O；
- 密度：0.743 g/cm³；
- 温度：600 K；
- 若截面库支持，可加入 `c_H_in_H2O` 热散射数据；
- 若截面库不支持热散射，应保留 warning，而不是伪造库路径。

冷却剂密度、温度和热散射数据属于默认假设 / 待确认项。

### 4.4 间隙材料

若需要显式建模燃料芯块与包壳之间的间隙，默认采用：

- 间隙材料：He；
- 密度：0.001598 g/cm³；
- 温度：600 K；
- 该项为工程默认假设，需人工确认。

### 4.5 导向管与可燃毒物棒金属结构材料

图片给出导向管及毒物棒相关金属结构材料为不锈钢，重量百分比：

| 元素 | 重量百分比 |
|---|---:|
| Fe | 71% |
| Cr | 18% |
| Ni | 11% |

密度：

- 7.9 g/cm³。

该材料用于：

- 导向管壁；
- 可燃毒物棒外包壳；
- 可燃毒物棒不锈钢内衬。

### 4.6 硼玻璃材料

图片给出了硼玻璃层尺寸，但未给出硼玻璃组成、密度、硼同位素丰度和温度。

默认处理：

- 不在默认 lattice 中插入可燃毒物棒；
- 仅保留可燃毒物棒候选几何定义；
- 硼玻璃材料属于人工确认项；
- 若生成含可燃毒物棒的可运行模型，需要硼玻璃组成、密度、温度和插入位置。

## 5. OpenMC 几何组织参考

### 5.1 Universe 划分

至少建立以下 universe：

1. `fuel_pin_universe`
   - `fuel_cell`：`r < fuel_radius`，填充 UO2；
   - `gap_cell`：`fuel_radius < r < clad_inner_radius`，填充 He 或冷却剂默认材料；
   - `clad_cell`：`clad_inner_radius < r < clad_outer_radius`，填充 Zr-4；
   - `moderator_cell`：pin cell 剩余区域，填充 H2O。

2. `guide_tube_universe`
   - `guide_inner_cell`：`r < guide_tube_inner_radius`，默认填充 H2O；
   - `guide_wall_cell`：`guide_tube_inner_radius < r < guide_tube_outer_radius`，填充不锈钢；
   - `guide_outer_moderator_cell`：pin cell 剩余区域，填充 H2O。

3. `burnable_poison_universe`，候选，不默认插入
   - `bp_inner_void_or_central_cell`：`r < 0.257 cm`，材料需人工确认或空腔；
   - `bp_inner_liner_cell`：`0.257 cm < r < 0.280 cm`，填充不锈钢；
   - `bp_glass_cell`：`0.290 cm < r < 0.440 cm`，填充硼玻璃，材料需人工确认；
   - `bp_gap_or_clearance_cell`：根据尺寸间隙填充 H2O 或 He，需人工确认；
   - `bp_outer_clad_cell`：`0.450 cm < r < 0.500 cm`，填充不锈钢；
   - `guide_annulus_coolant_cell`：`0.500 cm < r < guide_tube_inner_radius`，填充 H2O；
   - 该 universe 只有在提供实际插入位置时才用于 lattice。

### 5.2 RectLattice

使用 `openmc.RectLattice`：

- `lattice.pitch = (1.43, 1.43)` cm；
- `lattice.lower_left = (-10.725, -10.725)` cm；
- `lattice.universes` 为 15×15 二维数组；
- `F` 位置映射为 `fuel_pin_universe`；
- `G` 位置默认映射为 `guide_tube_universe`；
- 不得把 21 个 `G` 位置悄悄映射成 fuel universe。

### 5.3 Root geometry

- 建立 assembly 外边界矩形 region；
- 外边界 cell 填充 `assembly_lattice`；
- root universe 只包含 assembly cell；
- 使用 `openmc.Geometry(root_universe)`；
- 使用 `openmc.Model(geometry, materials, settings, tallies)`；
- 最终调用 `model.export_to_xml()`。

## 6. Settings 与低成本诊断

默认低成本 eigenvalue 诊断设置：

- `run_mode = "eigenvalue"`；
- `batches = 50`；
- `inactive = 10`；
- `particles = 1000`；
- 源分布使用组件包围盒内的 box source；
- `only_fissionable = True` 时必须确认源采样范围能落入燃料区；
- 低成本运行仅用于发现 XML、几何、lost particle 和明显材料定义错误，不用于物理结果评价。

## 7. 校验清单

模型审查时检查：

### 7.1 棒位映射

- 15×15 lattice 总位置数为 225；
- `F` 数量为 204；
- `G` 数量为 21；
- 21 个 `G` 位置必须与第 2.2 节一致；
- `universe_pattern` 中不能引用不存在的 universe；
- `G` 位置默认使用 `guide_tube_universe`，不得全部退化为 `fuel_pin_universe`。

### 7.2 尺寸与内含

- 所有圆柱半径均为正；
- 半径单位必须为 cm；
- `fuel_radius < clad_inner_radius < clad_outer_radius < pitch/2`；
- `guide_tube_inner_radius < guide_tube_outer_radius < pitch/2`；
- `burnable_poison_outer_radius < guide_tube_inner_radius`；
- 组件边界必须完整包围 15×15 lattice。

### 7.3 区域闭合

- 每个 pin cell 内的圆柱分区必须无重叠、无遗漏；
- guide tube universe 内部区域必须无重叠、无遗漏；
- 可燃毒物棒候选 universe 中尺寸间隙必须显式处理；
- OpenMC cell region 布尔表达式必须可导出 XML。

### 7.4 材料

- 所有参与默认 lattice 的材料必须有密度和组成；
- 燃料富集度 3.1% 必须进入 UO2 材料定义；
- Zr-4 和不锈钢组成必须按重量百分比处理；
- 冷却剂热散射数据若缺失，应记录 warning；
- 不得伪造 `cross_sections.xml` 路径。

### 7.5 OpenMC 输出

- `materials.xml`、`geometry.xml`、`settings.xml` 必须可导出；
- 可选导出 `plots.xml` 或 `tallies.xml`；
- OpenMC 日志中不应出现 XML 解析错误、几何重叠、lost particle 或 ERROR 级错误。

## 8. 需要人工确认的信息

尽管本文件已经给出足以生成组件模型的默认闭合定义，以下信息仍应在 IR 中标记为人工确认项：

1. 单棒燃料棒、包壳和栅距尺寸是否确实采用第 3 节默认值；
2. 燃料 UO2 密度、温度和氧铀比；
3. 包壳 Zr-4 密度和温度；
4. 冷却剂密度、温度、硼浓度和热散射数据；
5. 间隙气体材料和密度；
6. 硼玻璃组成、密度、温度和硼同位素丰度；
7. 是否真实插入可燃毒物棒；
8. 若插入，可燃毒物棒具体插入哪些导向管位置；
9. `cross_sections.xml` 路径；
10. 单组件边界条件应为 reflective、vacuum，还是由上级堆芯模型提供。
