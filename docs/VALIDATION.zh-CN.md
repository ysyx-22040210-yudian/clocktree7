# 验证摘要

记录日期：2026-11-02。数据来自本地实际保存的 Verdi NPI 结果和 Tk 界面验收；以下为可随源码分享的摘要。完整原始记录保留在本机 `validation/`，不随仓库上传。

## 实际提取结果

### 复位树分支回归

- 默认两级，界面增至 20 级再删除恢复；类型选择仅 MUX / RSY_SYNC，RSY_SYNC 多输入会被拒绝。配置使用 `tree_type: reset` 与 `reset_inputs/reset_output`，旧时钟配置继续可用。
- VM 中分别执行两级、十级真实 Verdi NPI：均为 **12 实例、12 连接、4 MUX、8 RSY_SYNC、2 根、4 末端、2 网络、3 复位源、28/28 追踪**，未解析和诊断均为 0。完整实例、端口边和末端集合与测试预期一致。
- 十级版本包括同层串接、跨层跳接、空的 L6/L7，以及 L1/L2/L8/L10 末端；源文件中的同定义范围外 MUX 未被收进结果。`clk_i` 未被配置为复位端口，未进入复位源清单。
- 时钟/复位工作区的未保存草稿、结果和类型列表分别恢复；运行时模式切换禁用。复位历史记录、真实连线点击与双向证据、全量 JSON、RSY_SYNC 原生 Tk/SVG 模型均通过。
- 同一当前目录连续复位提取、切到时钟提取、再回到复位提取全部通过；两种结果分别归档，本次根目录不残留另一种分析的旧结果文件。
- 再次通过原有七级真实时钟 NPI、6,984 实例 OpenC910 图形回归及真实进程组取消。当前 Python 检查为 **49 通过，Windows 跳过 1 项 Linux 符号链接检查**。

原始记录：本机 `validation/reset-gui/check.json`、`validation/tk_gui_reset_regression/tk-check.json`。复位成功案例为自建 RTL 的真实 elaboration，尚未计为大规模真实 SoC 的复位网络验证。现有 PULP `pulp_soc` KDB 对 `rstgen` 规则的补充尝试未枚举到该定义，工具正确报告 `missing_module`；该记录已保存在 `validation/failed_runs/pulp_soc_reset_missing_rstgen_20261102`，不计为复位提取通过。

### 本次五项需求回归

- 自动定位：配置不含 `verdi_home`，测试进程取消 `VERDI_HOME` 环境变量，仅依靠 PATH 中的 `verdi` 命令，在 VM 成功找到安装与 NPI 并完成实际提取。
- 两级与十级：每个案例恢复全部 13 个实例、11 条端口边、2 个根、4 个末端、2 个独立网络，28/28 端口追踪；未解析与诊断均为 0。完整实例、边和末端集合与独立 golden 一致。四类数量为 MUX 2、GATE 5、OCC 3、DIV 3。
- 层级控件：启动默认两级；实际点击增至 20 级，删除中间层及末层后正确重新编号，已填范围保留。原有七级配置往返和真实提取仍通过。
- 同目录重跑：省略 `--out`，在同一当前目录运行三次，再故意导入不存在的顶层，最后成功恢复；旧证据自动归档，失败结果不会沿用旧成功数据，源 RTL 未被修改。
- 无超时：上述 CLI 保留旧 `timeout_seconds: 1`，每次实际 NPI 用时超过 1 秒仍正常完成；产品直接等待进程结束，没有提取计时终止器。QA 脚本自身的等待期限只用于判定测试是否失效。
- 大图与取消：重新通过 6,984 实例 OpenC910 的原生 Tk 概览、展开、定位、证据、JSON/SVG 导出，并再次取消真实 NPI 进程组；未产生 GUI 回调异常。跨层跳接连线绕开中间层器件，避免图形误连。
- 上次五项需求的 Python 检查为 **38 通过，1 跳过**，已纳入上述当前检查；跳过项仅针对 Linux 符号链接包装命令。VM 实际使用的 Verdi 本身即通过包装符号链接运行，自动定位和真实提取已通过。

本次原始记录位于本机 `validation/flexible-gui/check.json` 与 `validation/tk_gui_v2/tk-check.json`。以下 SoC 全库规模数据为此前验收记录；本次大图回归使用其完整已提取数据，不宣称重新完成全部 SoC 的 elaboration。

### 已完成的规模验证

| 案例 | elab 全设计实例 | 目标实例 | 端口连线 | 根 | 末端 | 未解析 | 端口追踪 | 秒数 | 结果 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| 七层全网样例 | 24 | 16 | 15 | 3 | 7 | 0 | 34/34 | 2.732 | PASS |
| generate 层次样例 | 24 | 16 | 15 | 3 | 7 | 0 | 34/34 | 2.731 | PASS |
| 三类器件七层样例 | 24 | 16 | 15 | 3 | 7 | 0 | 34/34 | 2.674 | PASS |
| 4096 实例七层压力样例 | 4104 | 4096 | 3840 | 768 | 1792 | 0 | 8704/8704 | 53.7 | PASS |
| PULP SoC 完整 KDB | 3527 | 62 | 48 | 18 | 39 | 0 | 129/129 | 6.366 | PASS |
| OpenC910 SMART SoC 有序层次 | 16004 | 6984 | 6983 | 1 | 6959 | 0 | 13968/13968 | 103.79 | PASS |
| OpenTitan clkmgr 子系统 | 371 | 15 | 10 | 5 | 8 | 0 | 30/30 | 2.883 | PASS |

## 核对方法

- 七层样例、三类器件样例、generate 样例和压力样例逐项比较完整节点集合、完整输出口→输入口边集合和完整末端集合。压力样例共 1792 个末端，L1–L7 每层各 256 个。
- SoC 枚举完整 elab 中所有符合配置的实例，核对关注输入/输出追踪覆盖、驱动与负载双向证据、未解析项和结构约束。SoC 没有独立人工全网 golden；通过指配置关注网络证据完整。
- OpenC910 有序规则为 L1 发生器、L2 全局缓冲、L3 门控；末端位于 L2 和 L3。与平铺 L1 配置的完整节点和端口边集合相等。
- 历史 SoC 规则在加入器件类型前生成，未标注类型的部分以未分类模型显示；新的模块规则由用户选择选择器、门控、OCC 或 DIV。

## 原生 GUI 与器件模型

- VM：CentOS 7.9 / Python 3.6.8 / Tk 8.5.13；普通桌面用户实际运行。Windows Tk 8.6.12 也完成启动与结果读取。
- 真实控件验收包括七层规则编辑、类型选择、门控多输入校验、配置往返、真实 NPI 提取、连线点击及双向证据、聚合展开、完整实例搜索、路径高亮、JSON 和 SVG 导出。
- 三类器件样例包含选择器 2、门控 4、OCC 10；原始采集和 SVG 的类型计数一致。仅改变类型时，已有全部端口边及证据保持不变。
- OpenC910 6984 实例 / 6983 连线能以层次概览显示，展开和搜索定位回归通过。小视口裁剪只影响绘制，数据及全量导出保留。
- 已验证取消真实 NPI 任务可结束本次进程组并保留原结果。
- 原有检查已合并到上述当前 Python 检查中。Tk 验收未记录回调异常。

## 覆盖边界

OpenTitan 仅 `clkmgr` 子系统通过。完整 `top_earlgrey` 在 VM 的 VCS O-2018.09-SP2 上遇到 `otp_ctrl` 常量结构数组 part-select / 参数求值错误（IRIPS、CEPO），未计为整芯片通过。没有修改第三方 RTL 来制造连通结果。

提取使用可运行的基本 NPI 接口；模型类别是用户配置的显示元数据。结果属于静态结构分析，不覆盖物理 CTS、全部时钟工作模式、门控/OCC 行为或时序签核。

## 固定源码版本

- [pulpissimo](https://github.com/pulp-platform/pulpissimo)：`bfc3d9a1ef72443464509a22d948f0a6f5b65b4c`。
- [openc910](https://github.com/XUANTIE-RV/openc910)：`b91c90914c19f114d35c8f6b73408eb241ed847c`。
- [opentitan-silver-v5](https://github.com/lowRISC/opentitan)：`ed044fc9760bdf9fc075d0015ba1db07fa075355`。
- [pulp_soc](https://github.com/pulp-platform/pulp_soc)：`bf65372aab4edd404160170e2a4d2c63b27ab5f2`。

公开 SoC 源码、锁定依赖和 EDA 编译缓存保留在本机，未打入源码仓库。第三方项目各自的许可继续适用于其内容。

## 重跑小型样例

```bash
python3 scripts/make_fixture.py validation/fixtures/device-models --device-models
source scripts/eda_env.sh
python3 -m clocktree extract --config validation/fixtures/device-models/config.json --out validation/runs/my-device-check
python3 scripts/check_golden.py validation/runs/my-device-check/clocktree.json validation/fixtures/device-models/golden.json
```

`--out` 可使用同一个目录反复运行；省略时输出到当前目录，旧证据自动归档。新增两级/十级及四类器件验收可运行 `python3 scripts/check_flexible_gui.py --extract`。完整交互步骤见 [GUI 操作手册](GUI_USER_MANUAL.zh-CN.md)。

复位分支验收：`python3 scripts/check_reset_gui.py --extract`。无需 EDA 即可在 GUI 进入复位树模式，打开随附的 [复位记录示例](../examples/reset_tree/README.md)。
