# 算例3：C5G7-3D 四分之一堆芯 OpenMC 模型

建立 OECD/NEA C5G7 MOX 基准题的三维四分之一堆芯 OpenMC 模型。保留 C5G7 的几何与棒位排布，但每个材料区改用连续能核素组成（C5G7 原始七群均匀化截面不适用于蒙特卡罗）。模型只要求完整几何、能导出 OpenMC XML、通过几何检查并完成低代价本征值运行诊断，不作高统计量物理基准；本文给出的连续能核素组成不是 C5G7 基准标准数据，所得 keff 不与 C5G7 七群参考值直接比较。未插棒构型，不建模控制棒。

## 几何尺寸

长度单位 cm。栅元边长 1.26；燃料棒、导向管、裂变室圆柱半径 0.54。每个燃料组件 17×17 棒位、边长 21.42；2×2 活性组件区边长 42.84；外侧水反射层厚 21.42；四分之一堆芯径向总边长 64.26。轴向分两段：燃料有效区 0–192.78，顶部水反射层 192.78–214.20，总高 214.20。

## 径向排布

活性区为 2×2 燃料组件，按工程图视角（上为北、下为南、左为西、右为东）：西北 UO2、东北 MOX、西南 MOX、东南 UO2。活性区外一圈为水反射层（右侧一列、下侧一行、右下角）。

## 材料

连续能模式，所有材料温度 293.6 K。组成基除特别说明外为原子百分数（ao）。

- UO2 燃料：U-235 富集 3.3 wt%（在铀中），U-238 为铀余量，O-16 按 UO2 化学计量配平（O/U=2）；密度 10.0。
- 4.3% MOX（mox43）：钚含量 4.3 wt%（占重金属），钚同位素 Pu-238 1.9、Pu-239 58.0、Pu-240 24.0、Pu-241 11.2、Pu-242 4.9 ao%，余为贫铀（U-238 为主，U-235 约 0.25 ao%），O-16 按 (U+Pu)O2 配平；密度 10.0。
- 7.0% MOX（mox7）：钚含量 7.0 wt%，钚同位素组成同 mox43，其余同 mox43；密度 10.0。
- 8.7% MOX（mox87）：钚含量 8.7 wt%，钚同位素组成同 mox43，其余同 mox43；密度 10.0。
- 导向管 guide_tube：Zircaloy-4，Zr 98.23、Sn 1.45、Fe 0.21、Cr 0.10 wo%；密度 6.56。
- 裂变室：连续能版本无标准定义，本算例复用 guide_tube 的 Zircaloy-4。
- 水：H-1 66.67、O-16 33.33 ao%，密度 0.997，热散射 c_H_in_H2O。

## UO2 组件棒位

17×17。R09C09 为裂变室，导向管位置见下图。

| 符号 | 含义 | pin universe |
|---|---|---|
| U | UO2 燃料棒 | uo2_pin |
| G | 导向管 | guide_tube_pin |
| F | 裂变室 | fiss_chamber_pin |

```text
R01: U U U U U U U U U U U U U U U U U
R02: U U U U U U U U U U U U U U U U U
R03: U U U U U G U U G U U G U U U U U
R04: U U U G U U U U U U U U U G U U U
R05: U U U U U U U U U U U U U U U U U
R06: U U G U U G U U G U U G U U G U U
R07: U U U U U U U U U U U U U U U U U
R08: U U U U U U U U U U U U U U U U U
R09: U U G U U G U U F U U G U U G U U
R10: U U U U U U U U U U U U U U U U U
R11: U U U U U U U U U U U U U U U U U
R12: U U G U U G U U G U U G U U G U U
R13: U U U U U U U U U U U U U U U U U
R14: U U U G U U U U U U U U U G U U U
R15: U U U U U G U U G U U G U U U U U
R16: U U U U U U U U U U U U U U U U U
R17: U U U U U U U U U U U U U U U U U
```

计数：U 264、G 24、F 1，共 289。

## MOX 组件棒位

17×17。

| 符号 | 含义 | pin universe |
|---|---|---|
| A | 4.3% MOX | mox43_pin |
| B | 7.0% MOX | mox7_pin |
| C | 8.7% MOX | mox87_pin |
| G | 导向管 | guide_tube_pin |
| F | 裂变室 | fiss_chamber_pin |

```text
R01: A A A A A A A A A A A A A A A A A
R02: A B B B B B B B B B B B B B B B A
R03: A B B B B G B B G B B G B B B B A
R04: A B B G B C C C C C C C B G B B A
R05: A B B B C C C C C C C C C B B B A
R06: A B G C C G C C G C C G C C G B A
R07: A B B C C C C C C C C C C C B B A
R08: A B B C C C C C C C C C C C B B A
R09: A B G C C G C C F C C G C C G B A
R10: A B B C C C C C C C C C C C B B A
R11: A B B C C C C C C C C C C C B B A
R12: A B G C C G C C G C C G C C G B A
R13: A B B B C C C C C C C C C B B B A
R14: A B B G B C C C C C C C B G B B A
R15: A B B B B G B B G B B G B B B B A
R16: A B B B B B B B B B B B B B B B A
R17: A A A A A A A A A A A A A A A A A
```

计数：A 64、B 100、C 100、G 24、F 1，共 289。

## 四分之一堆芯总数

两个 UO2 组件加两个 MOX 组件。活性棒位 4×289 = 1156。燃料棒：UO2 528、4.3% MOX 128、7.0% MOX 200、8.7% MOX 200，共 1056；导向管 96，裂变室 4。

## 轴向分层

燃料有效区（0–192.78）内径向 2×2 组件与外侧水反射层同时存在。顶部水反射层（192.78–214.20）整截面为水，不含燃料棒、导向管或裂变室。两段之间是普通分界面，不设边界条件。

## 边界条件

非负坐标约定。径向：左侧（西）与上侧（北）是对称面，反射边界；右侧（东）与下侧（南）外表面是泄漏面，真空边界。轴向：底面反射，顶面真空。径向外表面位于水反射层外侧，而非活性组件外侧。内部组件、棒位、轴向分界面不带边界条件。

## 运行诊断

低代价 eigenvalue：batches 15、inactive 5、particles 1000。初始源放在活性燃料区内（覆盖含燃料的组件区与燃料有效高度范围，避开真空边界与水反射层）。运行后检查 XML 解析错误、几何重叠、lost particle、源采样失败、ERROR 级日志；结果仅作程序可运行性诊断。

连续能核数据库为 ENDF/B-VII.1 HDF5，由环境变量 OPENMC_CROSS_SECTIONS 指向 /home/wbd/openmc_data/endfb-vii.1-hdf5/cross_sections.xml，应含 U-235、U-238、Pu-238~242、O-16、Zr、Sn、H-1 与水热散射核 c_H_in_H2O。
