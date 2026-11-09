# 辅助端口寄存器追踪验收

日期：2026-11-09～10。范围为 `pll_sel/div/gt` driver 到时序输出、`sw_done` load 到时序写入位、DFT 置零以及精确 bit 映射。SV 模板生成按用户要求延后；本轮没有生成 SV。

## 实现与判定依据

独立任务通过原生 Tk 的“辅助口 → 寄存器”入口启动，也提供 `trace-registers` CLI。种子来自已经提取的实际实例和辅助端口，不重建或改写原有时钟树边。`npi/register_worker.tcl` 只用 NPI Language Model，逐级查询位连接和语句父链；Python 按 elab AST 求位对应和 DFT 条件，SQLite 保存已完成查询、共享路径、端点和端口事务断点。

依据本机提供的 `VC_APPS_NPI.pdf` 检查了 event control、always 类型、expression/part select 与 L1 trace 接口，并在 Verdi O-2018.09-SP2 实测。不以 `reg/logic` 声明判断寄存器。`D` 端点保存目的存储变量 bit、写入表达式和触发依据，不虚构 HDL 中不存在的 D 引脚路径。

## 验收环境与复现

- 共享源目录：`D:\VMshare\clocktree7` / `/mnt/hgfs/VMshare/clocktree7`。
- VM：Linux、Python 3.6、Tk 8.5、Verdi O-2018.09-SP2 和本机 VCS，使用目标设备既有 EDA 配置及许可。
- 新功能独立 fixture 由脚本生成，包含真实时序赋值、组合 reg、简单锁存器、非零/升序范围、参数化端口、DFT 别名、未知条件、动态下标、未连接与组合环路。
- 详细证据目录：`validation/register-trace-20261109/`，不随便携包附带 EDA、KDB、运行历史或手册原文。

```bash
source scripts/eda_env.sh
python3 scripts/check_register_trace.py --out /path/qa/registers --fanout 1000
python3 scripts/check_register_recovery.py --request /path/qa/registers/request.json --out /path/qa/recovery
python3 scripts/check_register_trace.py --out /path/qa/registers-10k --fanout 10000
```

原生 Tk 复验在有效桌面 DISPLAY 下执行下面两条独立进程命令；无 Pillow 时加 `--no-screenshots`，不会省略任何 GUI 功能断言：

```bash
python3 scripts/check_register_trace.py --out /path/qa/registers --phase gui-write
python3 scripts/check_register_trace.py --out /path/qa/registers --phase gui-read
```

## 已完成验收

| 检查 | 实测结果 |
| --- | --- |
| 全部自动测试 | 216 passed，1 skipped；新寄存器单元测试 11 项，覆盖条件选择、精确匹配、位序、动态索引拒绝、时序证明、负载兄弟分支、SQLite/CSV/JSON、混合 signed 上下文。 |
| 1,000 负载，RTL 导入 | 17 个辅助端口、33 个起始 bit、1,032 条寄存器映射；追踪 10.297 s，RSS 峰值 0.230 GiB。 |
| 10,000 负载，VCS KDB 导入 | 17 个辅助端口、10,032 条寄存器映射；追踪 33.786 s，RSS 峰值 0.293 GiB，全部预期端点与 bit 集合精确相等。计时包括本任务导入和追踪，不包括事先 VCS 编译、原树提取及导出验收。 |
| 控制口精确 bit | `choose_src[7:4]` → `D[5:2]`，`enable_ctl` → `C[7]`，`ratio_cfg` → `{C[3:2], D[9:8]}`，均逐位验证。升序、反序、重复和参数化宽度均通过。 |
| sw_done 多负载 | 保存 `E[10]`、`E[11]` 及 10,000 个生成实例的 sink 写入位。DFT 关闭的 `E[12]` 分支排除；以 done 作时钟的 `E[13]` 不误认为 D 负载。 |
| 时序证明 | `comb_reg`、组合 `if_sel` 穿透至真实 FF；FF 以 posedge/negedge 父链证明；简单 latch 以所有写入共享保持条件证明。 |
| 条件 / 异常 | DFT 数据分支的 F 寄存器完全排除；未知选择返回 conditional 依赖，动态下标未解析，组合环路独立标记，未连接和范围边界保留。 |
| 原生 Tk | 启动真实任务、100 行分页、实例/寄存器搜索、`E[10]` 证据、定位原器件、历史参数及结果在两次独立启动恢复；0 callback 错误。截图已人工查看。 |
| 导出 | CSV 和独立 JSON 全部记录数一致，含完整证据；导出不受当前页过滤，重复恢复不增加结果。 |
| 原有 SoC KDB 回归 | OpenTitan 15 实例/10 边；PULP 62 实例/48 边；OpenC910 已选直接父层子网 30 实例/29 边。实际节点及边集合与既有参考完全一致。 |

带负例的完整测试结果故意为 `complete: false`：1 个动态下标未解析、2 个边界、1 个环路、1 个常量终点；另有 3 条 conditional 记录（含未知选择输入的边界）。这是期望结果，不能以隐藏负例换取“完整”。剔除负例的四辅助端口基线得到 1,011 条映射，全部起始 bit 找到寄存器，`complete: true`。

SoC 回归检查的是原有连接集合不变，不宣称这三个项目已经按新辅助规则全量查过所有控制寄存器。OpenTitan/PULP 既有测试规则仍含层级规则诊断；连接完整不等于这些人为分层规则全部正确。

## 故障恢复验收

`scripts/check_register_recovery.py` 使用真实 Verdi 工作进程，在 QA 中注入 RSS 读数或单个原生查询延迟。产品代码没有测试注入口或强制超时。

| 场景 | 验收断言 |
| --- | --- |
| 仅 Language Model | 对所有 Netlist Model 调用装执行保护，实际任务没有触发禁用接口。 |
| 接近阈值 | 一个辅助端口事务提交后轮换 1 次 Verdi，全部结果与基线相等。 |
| 触及阈值 | 当前未提交端口回退，真实 Verdi 终止并重启 1 次，从该端口重试；无遗漏或重复。 |
| 控制进程硬退出 | 首端口提交后 `os._exit`；新进程恢复时核对进程身份并回收旧工作进程，原 4 条已提交记录完全不变，最终集合等于基线。 |
| 原生查询超时 | DIV 的 div 原生查询延迟超过 2 秒；div 记 timeout，同实例 sw_done 记 skipped，其他实例继续得到 5 条寄存器位映射。 |
| 修改输入 | 改变 DFT 配置后拒绝恢复旧断点。 |
| 连续无进展 | 相同端口连续触及阈值 3 次后停止，前 3 个已完成端口保留，结束后没有仍存活的已记录 Verdi 工作进程。 |

RSS 阈值场景是**确定性的保护流程测试**，不代表物理分配了 64 GiB。峰值读数包含受监控控制进程与 Verdi 进程树，共享页面可能重复计数。所测设计的内存和耗时不能作为商业超大 KDB 的上限保证。

## 已知边界

当前支持已实现 AST 运算、静态选择、端口/别名与可证明的时序结构。动态下标、函数调用、复杂过程控制、多维数组或黑盒没有足够行为证据时会显式未解析；不会猜一个寄存器 bit。未知模式条件不会擅自取 0。组合依赖和直接映射区别保存，使用结果生成后续 SV 前必须检查这一列。

Verdi 自身单次 Language Model API 的内部内存仍由厂商实现决定。没有全设计 Netlist Model 展开，并不意味着一次高扇出 API 永远不会慢；独立任务保留可选超时、内存保护、持久阶段及端口断点。目标设备需要自己的兼容 EDA 和许可；本次实际验证的版本是 2018。

## 便携交付

候选包 `clocktree7-register-trace-20261110-rc1` 解压到 VM 的 `/home/host/clocktree7-portable-qa/clocktree7-register-trace-20261110-rc1`。清除 PYTHONPATH，并设置 PYTHONNOUSERSITE=1，未借用共享目录的 Python 模块。

| 独立目录检查 | 结果 |
| --- | --- |
| SHA-256 清单 | 222 个内容文件全部匹配，另有 MANIFEST.json。 |
| doctor --self-test --gui | 原生 Tk、真实时钟/复位 golden、会话保存恢复、设备 EDA 检查通过。 |
| 新功能 KDB | 1,000 个负载、所有正负例及结果集合断言通过。 |
| 保护与恢复 | 7 场景通过，包括连续 3 次无进展停止；真实工作进程被终止/重启。 |
| 原生 GUI 两次启动 | 新任务、分页、证据查看和自动恢复全部通过，0 callback 错误；无第三方 Python 包模式。 |

候选包为提交前快照，MANIFEST 的 `source_dirty: true`。正式包及提交信息在干净提交出包后补录。详细操作见 [GUI 手册第 32 章](GUI_USER_MANUAL.zh-CN.md#register-trace)。

## 验收过程中的问题记录

VM 的默认 Xvfb `:99` 存在遗留 socket，首次 GUI 检查未进入 Tk 窗口；换用未占用的隔离显示后，实际 GUI 功能全部完成。最初截图中详情面板过矮，已改为可拖动分隔条并重新查看截图。独立目录脚本最初在汇总含阶段前缀的 doctor 输出时解析 JSON 失败；各检查已经结束，修复汇总器后从既有证据重新汇总，没有把未运行的步骤标成通过。

Windows Anaconda 的两次全量重复检查在不同旧用例初始化 Tcl 时出现 `init.tcl` 临时读取失败；对应 37 项局部检查随后通过，最终一次全量为 216 passed、1 skipped。此问题没有通过跳过测试或更改产品逻辑处理；VM Python 3.6/Tk 8.5 的独立实际功能验收通过。
