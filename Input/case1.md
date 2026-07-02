# 算例1：单栅元/单棒 OpenMC 模型

请根据以下自然语言描述，生成用于 OpenMC 模型自动生成与校验的结构化建模信息。要求保留参数来源、默认假设和校验目标，不要补充源资料中未给出的材料成分、截面库或物理结果。

## 建模目标

建立一个单栅元/单燃料棒 OpenMC 模型，用于验证智能体流程能否完成基本栅元几何的结构化描述、OpenMC Python 脚本生成、XML 导出、几何一致性校验和低计算成本 eigenvalue 试运行。

该算例主要检验：

- 圆柱燃料棒在正方形栅元中的几何分区是否正确；
- 燃料区、间隙/冷却剂区、包壳区、外侧冷却剂区是否能够被正确组织为 OpenMC cell/region；
- 半径顺序、面积闭合和外圆内含关系是否满足几何约束；
- 自动生成的 OpenMC Python 脚本是否包含 materials、geometry、settings、tallies 和 `model.export_to_xml()`；
- 导出的 OpenMC XML 输入是否能够被 OpenMC 读取并完成低计算成本运行诊断。

## 已知几何参数

- 栅距：1.33 cm。
- 燃料半径：0.4215 cm。
- 包壳内半径：0.43 cm。
- 包壳外半径：0.50 cm。
- 栅元形状：正方形栅元。
- 几何区域划分：
  - 燃料区；
  - 间隙/冷却剂区；
  - 包壳区；
  - 外侧冷却剂区。

## 默认假设与不确定项

- 原始文档未给出轴向高度，因此需要将轴向处理方式作为验证假设记录；若采用二维无限栅元，应使用 z 方向无限圆柱和 x/y 反射边界；若采用有限高度，应显式给出 z 平面边界。
- OpenMC 的 `settings.run_mode = "eigenvalue"`、`settings.batches`、`settings.inactive`、`settings.particles` 和 `settings.source` 仅用于低计算成本诊断，不用于证明物理计算结果准确性。
- 文献未给出完整材料成分、温度、热散射数据和 `cross_sections.xml` 信息，不应虚构具体 OpenMC 材料卡或截面库路径。

## OpenMC 建模要求

请在生成模型时同步给出以下 OpenMC 组织逻辑：

- 使用 `openmc.Material` 和 `openmc.Materials` 表示材料；材料缺口必须进入“需要人工确认的信息”。
- 使用 `openmc.ZCylinder` 表示燃料外表面、包壳内表面和包壳外表面。
- 使用四个 `openmc.Plane` 或等价边界定义正方形栅元；x/y 外边界优先采用 `boundary_type="reflective"` 表示无限阵列单栅元近似。
- 使用 OpenMC region 布尔表达式建立：
  - fuel cell：`-fuel_surface`；
  - gap/coolant cell：`+fuel_surface & -clad_inner_surface`；
  - cladding cell：`+clad_inner_surface & -clad_outer_surface`；
  - outer coolant cell：`+clad_outer_surface & square_region`。
- 使用 `openmc.Geometry`、`openmc.Settings`、`openmc.Tallies` 和 `openmc.Model(...).export_to_xml()` 形成可导出的模型脚本。

## 几何校验要求

请在生成模型时同步给出以下校验逻辑：

- 四个几何区域的横截面积之和应为 1.7689 cm²，并与 1.33 cm × 1.33 cm 的栅元正方形面积一致。
- 半径关系应满足单调递增：
  - 燃料半径 < 包壳内半径 < 包壳外半径。
- 包壳外圆应完全位于正方形栅元内。
- OpenMC cell region 分区应无重叠、无遗漏。
- OpenMC 脚本应至少包含 `openmc.Materials`、`openmc.Geometry`、`openmc.Settings()`、`openmc.Tallies` 和 `model.export_to_xml()`。

## 运行诊断要求

生成的 OpenMC 模型应支持低计算成本 eigenvalue 运行诊断。

诊断设置建议：

- `settings.run_mode = "eigenvalue"`。
- 使用少量 batches、inactive batches 和 particles，例如 15 个 batches、较少 inactive batches、每批 1000 个以下粒子，具体值必须标记为诊断假设。
- 使用 `openmc.IndependentSource` 和位于栅元内、优先限制在可裂变区的空间源。
- 检查 OpenMC 导出和运行日志中是否存在 XML 导出失败、几何重叠、lost particle 或 ERROR 级错误。

## 期望输出

请输出以下内容：

1. 该算例的结构化中间表示 IR，包括参数、几何区域、默认假设和校验规则。
2. OpenMC Python API 生成思路，说明 material、surface、cell、universe、geometry、settings、tallies 和 `model.export_to_xml()` 应如何组织。
3. 静态校验清单。
4. 几何一致性校验清单。
5. 低计算成本 OpenMC eigenvalue 运行诊断设置建议。
6. 需要人工确认或源资料未提供的信息列表。

注意：该算例的目的不是获得可信物理量，而是验证 OpenMC 模型可生成、XML 可导出、几何可闭合、程序可低成本运行。
