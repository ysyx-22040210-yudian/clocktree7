# L1 层次输入直达分支验收

日期：2026-11-11。工作区：`D:/VMshare/clocktree7`；Linux 共享路径：`/mnt/hgfs/VMshare/clocktree7`。

## 最终行为

L1 是用户配置的 RTL 层次本身的 input 接口。输入反查确认到达该接口时，保存完整端口路径、精确 bit 与 driver 证据；画布增加 `L1 · INPUT` 显示节点和连线，真实器件计数不增加。层次聚合、局部视图、独立分支、上下游跳转、分支高亮、SVG 和历史恢复共用这份证明。未知、超时、多个候选或混合未证明结果不补画为已确认入口。

SV 的新入口分支是未经过已配置 L1 器件、直接进入后续层器件的路径。它使用无选择器模板，继续跟随已确认路径到末端：有输出取实际输出连接，无输出取相关输入。穿线末端按内部已证明通道逐输出处理；一个末端输出汇合不同 L1 来源或混合普通起点时明确阻止自动生成。

缺省控制值为 `pll_sel=1'b1`、`div=0`、`sw_done=1'b1`、`gt0/gt1/gt2=1'b1`；`pll0` 为 L1 端口路径、其余 PLL 槽位为 `1'b1`。**配置了实际辅助控制端口时继续使用其寄存器映射**，失败或多候选不能静默改成默认值。`var_rst` 仍按最初真实器件所属层取得。普通 L1 器件起始路径与旧结果保持原有 SV 语义。

## 环境和验证

原生环境为 Python 3.6.8、Tk/Tcl 8.5.13、Verdi O-2018.09-SP2、VCS O-2018.09-1。GUI 使用隔离 Xvfb，不覆盖日常 GUI 会话。

| 项目 | 验收结果 |
| --- | --- |
| 全部自动测试 | **325 passed，1 skipped**；跳过项为 Windows 不适用的 Linux 包装测试 |
| 时钟 / 复位 × target / full | 四种原生组合通过，6 个真实实例、4 条器件间边、4 条已确认 L1 接口连接 |
| 精确 bit 与跨层 | `raw[5:2]` 在更外层以不规则 `{pads[4],pads[6],pads[5],pads[7]}` 连接；仍准确保存 L1 的 `raw[5]`、`raw[3]`、`raw[4]`、`raw[2]` |
| 原生 SV | 两种时钟提取模式各生成 5 段、0 跳过；分别核对独立末端输出、直接子系统输入、经过中间器件后进入子系统、原普通选择器路径 |
| 负例 | 原生条件表达式及未连接输入不获得 L1 直达标记；单元测试覆盖部分失败、超时、多个 L1 来源、普通/直达混合 |
| 穿线末端 | 单元测试确认一入多输出分别取输出信号，不把整条输入总线当作一个末端 |
| 实际控制优先 | 真实 Verdi 寄存器回归保留 DIV、sw_done 与三个 gate 的位映射；sw_done 多候选仍要求选择，生成 SV 经 VCS 编译通过；单元测试另核对缺失控制任务不会被常量替代 |
| 原生 GUI | L1 入口属性、连线证据、原始证据、上下游跳转、整分支入口高亮、独立分支、无寄存器目录的 SV 分析/导出、两次进程启动恢复全部通过，回调错误 0 |
| 旧功能 | doctor、子系统、子系统负例、原 SV 原生与两次 GUI、OpenTitan/PULP/OpenC910 已记录范围通过；穿线 8 项矩阵含超时撤回、两次断点轮换通过 |

实际末端 oracle：`gd.o` 取 `l1_top.crg.direct_clk`，来源为 `l1_top.crg.raw[4]`；`s.c0` 取 `l1_top.crg.raw[2]`；`s.c1` 取 `l1_top.crg.final_clk`，来源为 `l1_top.crg.raw[3]`。`ga`、`gb` 是进入 `s.c1` 的中间器件，不单独生成模板段。

## 千实例压力和便携包

1,000 个真实 elab gate 实例，每个输入连接不同的 L1 总线 bit，每个输出连接独立末端 bit。逐实例断言 `clocks[i] → outs[i]`，**1,000 条入口映射、1,000 段 SV、0 跳过、0 未解析、0 诊断**。接口已被本地 NPI 端口索引和对象同一性确认时，不再跨 L1 向外展开 driver。

| 运行位置 | 提取实测 | Verdi / 控制进程合计峰值 RSS |
| --- | ---: | ---: |
| VM 访问 HGFS 共享目录 | 15.986 秒 | 0.230 GiB |
| VM 独立本地目录的便携包 | 4.802 秒 | 0.230 GiB |

这是合成 RTL 的本机实测，两个工作目录和文件系统不同，不把它当成相同条件下的性能对比，也不代表商业整芯片的 elab 导入内存。

候选包解压到 `/home/host/clocktree7-portable-qa/clocktree7-l1-input-20261111-rc1`，清除 `PYTHONPATH`、设置 `PYTHONNOUSERSITE=1`。包内 279 个项目文件的 MANIFEST 校验通过（280 个文件含清单）。从独立目录重新生成 RTL、运行四种提取组合及负例、验证 SV、两次 GUI 启动及千实例压力，全部通过。最终包只补充文档，不改变已验收的运行代码；目标设备仍需自己的 Verdi/Tk/Python 和许可。

## 证据与复跑

本地证据保存于 `validation/l1-input-20261112/`，目录名是本轮任务标识，实际时间以日志为准。这些日志、KDB 和会话数据不上传 GitHub。

- `matrix2/acceptance.json`：四种提取组合、SV 和负例。
- `stress/acceptance.json`：共享目录千实例压力。
- `gui3/gui-write.json`、`gui3/gui-read.json`：原生界面及独立启动恢复。
- `regression/suite.json`：doctor、子系统、开源 KDB；其中早期 SV 用例暴露默认值覆盖问题。
- `regression-final/suite.json`：按真实控制优先规则修正后，原 SV 原生/编译及两次 GUI 全部复验通过。
- `wire-regression/acceptance.json`：穿线八项回归。
- `portable/`：独立目录的原生、GUI、压力、校验报告和候选 MANIFEST。
- `../l1-input-full-unit-release.log`：最终全部自动测试。

复跑：`python3 scripts/check_l1_inputs.py --out <新目录>`；千实例压力增加 `--phase stress --count 1000`；GUI 使用 `--phase gui-write --result <clocktree.json>`，再用相同输出目录运行 `--phase gui-read`。未用用户商业芯片数据，不宣称任意多维 packed 接口、动态选择或所有 NPI 版本已经支持。
