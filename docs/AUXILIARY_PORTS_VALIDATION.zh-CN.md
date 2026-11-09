# 辅助端口实例连接显示：验收报告

日期：2026-11-09。范围：MUX `pll_sel` 输入、DIV `div` 输入/`sw_done` 输出、GATE `gt` 输入。所有名称由用户配置，字段可空；读取真实例化绑定并显示，不参与树连接追踪。

## 环境与证据

- 源码目录：Windows `D:\VMshare\clocktree7`，VM `/mnt/hgfs/VMshare/clocktree7`。
- 真实 EDA：CentOS 7.9、Python 3.6.8、Tk 8.5.13、Verdi O-2018.09-SP2，`host` 用户的本机 EDA/许可环境。
- 原生 Tk：Xvfb 1920×1080，1840×1000 工作台；独立进程分别保存和恢复。截图经过实际查看，无额外绘制的辅助接线点。
- 可重复验收脚本：`scripts/check_auxiliary_ports.py`。原始报告/日志在 `validation/auxiliary-20261109/native-01`、`validation/auxiliary-*.log`；这些运行数据不随源码上传。
- 仓库保留精简机器报告 [auxiliary-ports-validation-data.json](auxiliary-ports-validation-data.json)，包含实际端口绑定、耗时、追踪数量和 GUI 结果。

## 逐项验收

| 项目 | 实际检查与结果 |
| --- | --- |
| GUI 输入 | MUX、DIV、GATE 显示各自可选字段；实际接口采用 `choose_src`、`ratio_cfg`、`locked_o`、`enable_ctl`，均不同于功能角色名。保存后映射正确；全部清空时省略 `aux_ports`。 |
| 参数校验 | 拒绝错误器件角色、非字符串、重复端口、与主追踪端口重合；旧配置及空字段兼容。 |
| 实际连接 | 真实 NPI 取得位选、片选、表达式、常量、位置关联和未连接。相同 module 的两个实例分别保留自身连接；不复用错误的实例绑定。 |
| 总线 | 参数化 DIV 声明 `[W+1:2] ratio_cfg`，W=4；实例 `d0` 显示 `ratio[5:2]`，`d1` 显示常量 `'h5`。显示整体 high connection，不追踪各位。 |
| 输出状态端口 | `locked_o` 取得 `npiOutput`，`d0` 显示 `done[0]`，`d1` 的空绑定正确显示未连接。 |
| 不增加追踪/连线 | 目标结构和全负载模式分别与不含辅助字段的基线比较：均 6 实例、4 条完全相同的边，追踪次数分别仍为 8、14。原始 trace 事件中无四个辅助实际端口。目标模式禁用 Netlist/load 的检查仍通过。 |
| 错误端口 | 2 个门控的缺失接口、2 个 DIV 各 2 个方向互换，共 6 条警告；树仍保持 4 条原有边、连接完整。 |
| 复位树 MUX | target/full 两种模式均通过；仅 MUX 配置 `pll_sel`，不改变复位器件集合。 |
| 断点轮换 | 两种模式都在 module 提交后强制轮换真实 Verdi 各 3 次；最终辅助映射和实际连接与无轮换结果完全一致，边和追踪次数不变。不是模拟读取的结果。 |
| Tk 与 SVG | 器件下方展示两行 DIV 辅助信息；悬停/属性保留全文；原有主端口锚点不移动、不生成辅助引脚。后台布局采用扩大后的器件尺寸，分支和按层模式几何检查通过。SVG 包含相同连接文本。 |
| 问题表 | 六条异常均可列出；点击缺失接口问题能定位该实例并标出辅助行。 |
| 历史恢复 | `gui-write` 与 `gui-read` 两次独立启动，配置映射和结果绑定完全一致，Tk callback 错误均为 0。索引 JSON 与会话快照单元测试通过。 |
| 主端口超时兼容 | 主端口 trace 超时不向辅助端口附加超时/跳过标签；辅助端口未加入 trace 计数或超时处理。 |
| 原有功能回归 | 全量 Windows 自动测试 **205 passed, 1 skipped**；跳过项为 Windows 不适用的 Linux wrapper/symlink 测试。VM 原有端口绑定验收 16 连接，以及 SUBSYSTEM 发现/确认/两模式追踪回归均通过。 |

真实 Verdi 7 个新场景全部通过，另运行了 target/full 两个无辅助字段基线。目标结构 4.569 秒、全负载 3.369 秒；强制 3 次轮换分别 11.207 秒、11.612 秒。运行监测峰值约 0.219–0.225 GiB。这些数值只描述本次小型验收电路，不是商业芯片内存或耗时保证。

## 复现方法

在已配置 Verdi 2018/NPI/许可的 Linux 环境中，从工具根目录执行。`--out` 使用独立可写目录。

```bash
python3 scripts/check_auxiliary_ports.py --out /path/to/qa/auxiliary
xvfb-run -a -s '-screen 0 1920x1080x24' python3 scripts/check_auxiliary_ports.py --out /path/to/qa/auxiliary --phase gui-write
xvfb-run -a -s '-screen 0 1920x1080x24' python3 scripts/check_auxiliary_ports.py --out /path/to/qa/auxiliary --phase gui-read
```

GUI 使用测试目录中的专用会话，不覆盖用户历史。脚本自行生成小型 RTL；强制轮换代码只注入该测试任务目录的 Tcl 副本，不改生产 NPI 代码。

PNG 截图是验收脚本的可选能力，使用 Pillow；工具运行本身不需要 Pillow。纯标准库环境在两条 GUI 命令末尾加 `--no-screenshots`，仍执行全部窗口、图元、文本、属性、SVG、问题定位和重开恢复断言，仅不保存截图。

## 使用边界

辅助数据是 elab 中的静态绑定，不是运行时数值；不会由 `pll_sel` 判断当前选中分支、由 `gt` 判断开关状态，或根据 `div` 计算时钟频率。NPI 规范化后的表达式可能与 RTL 原文的空格、常量位宽写法不同。缺失或无法取得的绑定明确显示，不进行猜测。

读取发生在原有实例接口枚举过程中，不递归查找内部器件，不展开信号的负载；DIV 最多增加两个关注绑定。旧结果需要重新提取才能得到新增信息。升级前未完成任务遵循现有版本指纹检查；本轮验证的是新版本自身的断点恢复。

## 便携包独立目录验收

候选包 `clocktree7-auxiliary-20261109-rc2` 解压至 VM 的 `/home/host/clocktree7-portable-qa/clocktree7-auxiliary-20261109-rc2`。清除 `PYTHONPATH` 并设置 `PYTHONNOUSERSITE=1`，从该独立目录运行，没有借用共享目录的 Python 模块。

| 检查 | 结果 |
| --- | --- |
| SHA-256 文件清单 | 212 个源文件全部通过，另有 MANIFEST.json；归档共 213 个文件。 |
| `doctor --self-test --gui` | Python 3.6/Tk 8.5、本机 Verdi、真实时钟及复位 golden、会话保存恢复、两类原生架构图全部 PASS；`tool_root` 为独立解压目录。 |
| 新辅助端口真实 NPI | 完整 7 场景及两个无辅助基线重新通过，包括两模式各 3 次 Verdi 轮换。 |
| 独立原生 Tk | `gui-write` / `gui-read --no-screenshots` 全部通过，0 callback 错误；配置及连接信息跨进程恢复。 |

第一次候选包 rc1 的产品自检及 NPI 均通过，验收在 PNG 截图步骤因隔离环境无 Pillow 停止。已给验收脚本增加明确的 `--no-screenshots` 选项，rc2 复验保留全部 GUI 功能断言。正式运行工具不依赖 Pillow；本报告及手册的截图仍来自先前实际通过的 VM Tk 窗口。

rc2 是提交前的候选快照，MANIFEST 标记 `source_dirty: true`，不能当作干净提交的发布包。正式便携包由提交后的源码生成并单独验证，发布校验记录随后追加。
