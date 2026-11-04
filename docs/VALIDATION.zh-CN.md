# 验证摘要

更新日期：2026-11-04。数据来自本地实际保存的 Verdi NPI 结果和 Tk 界面验收；以下为可随源码分享的摘要。完整原始记录保留在本机 `validation/`，不随仓库上传。

## 闪退后重开提示与跨次断点续跑

- Windows Python 检查 **120 通过，1 项 Linux 符号链接检查跳过**。新增覆盖进程被杀后释放排他锁、运行中任务拒绝重复恢复、输入/config/断点变化拒绝、GUI 正常关闭与异常退出区分，以及安全启动不删除结果缓存。
- Linux VM 普通用户 `host`、Python 3.6、实际 Verdi O-2018.09-SP2，**16 项跨进程恢复**通过：时钟/复位分别在导入、查找、追踪、拓扑、索引、JSON 写出时杀控制进程，在查找和追踪时杀 Verdi；每项另起 Python 控制进程执行 `resume`。
- 时钟对照 **42 实例 / 41 连线 / 85 次追踪**；复位对照 **21 / 20 / 43**。查找在第 1 条 module 断点后续跑，追踪在第 32 次已提交追踪后续跑；实例、边、边界及完整 load/driver 证据与不中断对照一致。只有一个结束标志，没有重复 trace ID。
- 临时 Tcl 包装在未提交尾部写入不完整 JSON 和 Tcl 列表，恢复后两文件都没有测试尾部。控制进程被杀时刻意保留活的 Verdi，恢复核验原进程组身份并停止后才撤回尾部；旧工作进程均已退出。
- 拓扑、索引和结果写出阶段的 6 项中断，使用已提交完整原始证据重建，Verdi `attempts` 保持 1；没有重新导入。部分证据解析只读提交前缀，二次错误不会遮住原 Verdi 退出信号。
- **4 项原生 Tk 检查**：时钟、复位 GUI 和控制进程均被杀后，新进程打开提示并点击“从断点继续”；只杀 GUI 而后端继续运行时观察原任务，Verdi 次数仍为 1；绘图 `canvas` 阶段杀 GUI 后，重开先恢复配置，提示后手动打开已生成结果，未增加 Verdi 次数。均无 Tk 回调异常。
- OpenC910 真实 KDB 的 **22 个直接门控实例 / 44 次追踪**子网，追踪中杀控制进程后从第 32 次继续；写结果中断后复用完整证据，均与对照一致。本次不是商业 64G OOM 复现，也不是重新提取 OpenC910 全芯片网络。
- 同一输出目录先中断时钟任务，再完成新的复位任务，随后指定原时钟 `run-id` 恢复：时钟结果与对照一致，新的复位发布结果已归档，其独立原始证据逐字节保持不变。
- 5 项真实 Verdi RSS 回归继续通过：0.5 GiB 软阈值轮换、0.375 GiB 硬阈值终止、0.625 GiB CLI 覆盖；每项两次重启后全量结果与对照一致。采样读数未注入；过低阈值无进展仍停止并保留记录。记录位于 `validation/crash-recovery/real-rss/acceptance.json`。

复现：`scripts/check_crash_recovery.py --out <新目录>`；同目录追加 `--gui` 验证 4 项界面流程，追加 `--reuse` 验证目录复用；另选目录追加 `--soc` 验证已有 OpenC910 KDB 子网。测试暂停/损坏尾部只在测试包装和临时工作副本中，生产工具不含测试暂停开关。原始记录：`validation/crash-recovery/acceptance-final/`、`validation/crash-recovery/soc/`。

该版本的新任务才带跨次恢复清单。恢复要求原设备、原路径与原输入/提取实现仍可核验；旧版没有清单的中断任务只可诊断。实际 dsub 作业被结束后需重新申请资源，工具不自动提交调度任务；SIGKILL 不等于已证明 OOM。

## 当前实例查找：仅本层直接例化

每层 `hierarchy` 现在表示目标 module 例化的直接父层路径。使用 NPI `npiInternalScope` 扫描直接作用域，不递归进入模块/generate 子层次；路径本身也不作为自己的候选。直接实例数组成员仍被保留，定义名精确匹配。不再使用递归查找后过滤路径的方式。端口追踪根及其内部子层次的原有规则保持。

- Windows Python 检查 **112 通过，1 项 Linux 符号链接检查跳过**。新增模拟迭代器检查只打开指定父层一次，不进入返回的子作用域，并释放不匹配及已处理的语言模型句柄。
- VM 真实 NPI：时钟 **5 实例 / 4 连线 / 11 次追踪**，复位 **2 实例 / 1 连线 / 5 次追踪**，均完整。深层同定义实例、同前缀兄弟范围、generate 内未显式指定的实例不被选中；直接数组元素和参数化模块正确保留。
- 显式填写嵌套父层与 `gen_branch[0]` 后，两类模式均找到该层唯一直接目标。目标只存在于更深处或仅是查询根本身时，返回 `missing_module`，不递归兜底，仍提交该规则的处理断点。
- 真实连接通过未选中的 `u_path` 子模块后返回本层目标，完整保留该边及驱动/负载双向证据，验证实例查找深度与端口追踪范围独立。
- 普通/generate 范围时钟、复位、空路径回退、常量端口及严格追踪边界回归通过。逐 module 检查、共用查询组中连续 3 次轮换、阈值为 0、实际 RSS 受控子进程检查及 Verdi 的软/强制回退对照通过。
- OpenC910 使用实际父层 `...x_ct_ifu_top.x_ct_ifu_ras` 的 **22 个直接门控实例、44 次追踪**；实例集合与此前记录的该父层直接实例集合一致。此子网经历两次强制回退后与本次不中断对照一致。本项是指定父层子网验收，**不计作重新提取 6,984 实例全芯片网络**。
- 原生 Tk 使用新字段提示实际发起提取，显示 5 实例 / 4 连线，保存后新进程恢复配置和结果通过，无回调异常。

复现：`scripts/check_direct_instances.py --soc`；图形项另运行 `--gui-phase write`，并指向相同 `--out`。记录位于本机 `validation/direct-instances/acceptance-final/`、`scoped-regression/`、`module-regression/`、`memory-regression/`。

以下各节保留历史验收结果。以前依赖递归搜索的配置需填写实际父层后重新提取；已有全芯片结果仍可浏览。旧配置直接填 `soc` 时不会再搜索深层门控，历史全芯片通过记录不能作为该旧配置在新语义下的通过结论。

## 运行节点与异常退出定位

- 原生 Tk 显示准备、导入、查找、追踪、拓扑、索引、结果、显示 8 个节点，以及当前 module/范围/实例/端口/方向和最近断点。真实窗口验证蓝色运行状态、完成状态、节点点击筛选及中断后的诊断入口。
- 真实 Verdi 的时钟 13/13、复位 11/11 端口追踪均有成对开始/结束日志；6/4 和 5/3 实例/连接、边界及全部双向证据与对照一致。
- 在导入、范围内原生 module 查询、逐端口追踪时分别杀死 Verdi 进程组，3 项均定位为相应阶段及 `SIGKILL`；查询包含 module/范围，追踪包含实例/pin/方向/trace ID。部分结果不会被记录为后端正常完成。
- 在查找、追踪、拓扑、索引和 JSON 写出时杀死整个控制进程，5 项均从持久记录识别 `interrupted`，后处理不会误用已经结束的 Verdi 追踪阶段。不会仅凭退出信号推断 OOM。
- 在真实 Tk 布局和绘图阶段分别杀死 GUI，均保留具体阶段；新进程重开后仍可通过 `previous_gui_issue` 查到上次位置。界面逐端口实时显示、日志源切换、节点快照、完整结果显示和诊断入口通过。
- 后端每次运行有独立日志及根目录副本；同目录重跑保留历史，配置失败也写阶段日志。CLI `diagnose` 只读记录，不依赖 EDA。Linux 使用进程启动身份防止 PID 复用误判，Windows 只查询进程句柄。
- OpenC910 真实 NPI 回归仍为 **6,984 实例 / 6,983 连线 / 13,968 次追踪**，全部双向证据与对照一致。逐 module 断点、共享查询中的 3 次续跑、阈值为 0，以及实际 RSS 小阈值的 5 组重启检查再次通过。
- 阶段记录版本的 Windows Python 检查为 **111 通过，1 项 Linux 符号链接检查跳过**。一次全量检查遇到本机 Conda Tcl 的暂时性 `init.tcl` 读取失败；单项及随后全量重跑通过。VM 的原生 Tk 检查未出现该问题。

复现：`scripts/check_runtime_stages.py`，图形项追加 `--gui-only`。本机记录在 `validation/runtime-stages/acceptance/acceptance.json` 和 `gui-acceptance.json`。中断为测试进程/临时 Tcl 包装注入；没有向生产提取添加超时或暂停逻辑。本项未在商业芯片上复现真实 64G OOM。

## 内存优化与 Verdi 重启续跑

- 删除非必要全芯片统计，按相同查询根＋module 复用查找；回收队列项、共享路径前缀、逐终点写入，Python 逐事件处理，SQLite 保存证据与结果索引，完整 JSON 流式导出。
- 同一 OpenC910 原始证据（6,984 实例 / 6,983 边）的独立进程基准：旧版 Python 后处理峰值 **426.19 MiB**，优化后 **73.41 MiB**；打开结果、投影概览和准备快照的模型基准由 **203.32 MiB** 降为 **19.51 MiB**。均不含 Verdi；后处理耗时由约 2.1 秒增加到约 19.8 秒，交换了内存与磁盘开销。
- 原生 Tk：大图概览、点选双向证据、全量 JSON、源文件移动后新进程恢复和两类工作区切换通过。合成 **100,001 实例 / 100,000 边** 检查全网覆盖、宽层次翻页、连接列表翻页、精确定位及磁盘路径高亮，未记录 Tk 回调异常；此项是 GUI 压力测试，不能当成真实 NPI SoC 提取。
- Linux 真实 RSS 检测：受控子进程分配 128 MiB，在 **0.12 GiB** 阈值下触发两次实际强制重启并完成 3 个已提交单元，观察峰值约 **0.147 GiB**。
- 真实 Verdi 的时钟、复位分别在模块枚举和端口追踪阶段注入 55/61 GiB 读数，验证软轮换和强制终止回退；各经历两次重启后，完整实例/边集合、双向路径及边界证据与不中断提取一致，实例及 trace ID 没有重复，原始证据仅一个最终完成标志。
- OpenC910 全量 Verdi 在枚举和追踪各注入一次强制重启，最终 **6,984 实例 / 6,983 边 / 6,959 末端，13,968/13,968 追踪**，完整拓扑和全部双向路径与原始结果一致。测试没有实际分配 60 GiB；商业芯片 64G 额度的结果仍需目标设备实测。
- 低于基线的真实阈值验证：同一断点连续 3 次无法推进即停止，不生成完整结果；普通 EDA 错误不会被误当内存重试。GUI 阈值、读数、配置保存与关闭重开，以及取消时同时结束 CLI 与 Verdi 的两个进程组均通过。
- 自定义阈值的真实 Verdi RSS 检查：测试只在工作区 Tcl 中实际申请内存，不改写生产采样器。时钟、复位各在 0.5 GiB 下于约 0.475 GiB 正常轮换两次，在 0.375 GiB 下于约 0.437–0.438 GiB 强制重启两次；配置为 0.5、CLI 覆盖 0.625 的第五组于约 0.687 GiB 触发两次重启。五组均完成，所有实例、连线、边界、双向路径与对照一致，trace ID 不重复且旧工作进程退出。GUI 将自定义值改为 0.42 后保存、新进程恢复，并显示真实的两次重启。该项是小型 RTL 的受控实际内存增长测试，不是 60 GiB 分配或商业负载测试。
- 每条 module 规则结束后独立保存快照并强制额外 RSS 检查：真实时钟 5 条、复位 2 条规则分别对应 5 / 2 次检查，日志确认下一条查找在前一条检查确认之后执行。4 条重复规则共用一个 NPI 查询时，在规则 1/2/3 后连续重启 3 次，查询组数仍为 0、实例数仍为 1，但规则游标正确推进；最终保存全部 8 条规则断点，图和双向证据与对照一致。快照中的原始数据/实例清单偏移均落在完整记录边界。
- 零匹配、无效层次保存失败状态并保持结果不完整；阈值为 0 时仍保存每条断点。OpenC910 保存 3 条 module 规则断点，6,984 实例 / 6,983 连线和全部双向证据一致。实际 RSS 的五组小阈值测试再次通过，GUI 显示逐 module 提交数量，保存重开、取消两个进程组通过。
- 加入阶段记录前的 Windows Python 检查为 **101 通过，跳过 1 项 Linux 符号链接检查**。VM 保持 Python 3.6.8、Tk 8.5.13、SQLite 3.7.17 和 Verdi O-2018.09-SP2，生产依赖仍为 Python 标准库和系统 EDA。

原始记录：本机 `validation/low-memory/profiles/`、`validation/low-memory/gui/`、`validation/low-memory/gui-final/`、`validation/memory-resume/full-acceptance/acceptance.json` 与 `validation/memory-resume/gui-final/acceptance.json`。复现脚本为 `profile_low_memory.py`、`check_indexed_gui.py`、`check_memory_resume.py --soc`、`check_memory_gui.py`。设置和行为详见 [内存优化与重启续跑](MEMORY.zh-CN.md)。

自定义阈值真实 RSS 的复现脚本为 `scripts/check_real_memory_threshold.py --gui`；五组报告、每次内存审计、原始证据、GUI 保存重开记录在本机 `validation/real-memory-threshold/run02/`。

逐 module 独立断点的复现脚本为 `scripts/check_module_checkpoints.py --soc`。本机记录在 `validation/module-checkpoints/acceptance/acceptance.json`；新版真实 RSS、GUI 保存重开记录在 `validation/module-checkpoints/real-rss-headless/`，GUI 取消回归在 `validation/module-checkpoints/gui/acceptance.json`。

## 实际提取结果

### 限定实例查找与端口追踪根层次

- 每条模块规则使用原生 NPI `rootScope` 查询，取消仅用于总数统计的全芯片枚举。实例查找与端口追踪根分别控制；错误路径不回退全芯片。
- VM 真实 NPI：普通实例路径与 `g[0].domain` 数组 generate 路径各验证时钟和复位。严格时钟均为 **6 实例 / 4 连线 / 1 末端 / 5 处范围边界**；复位为 **5 实例 / 3 连线 / 1 末端 / 5 处范围边界**。范围内完整端口边集合与显式预期一致，13/13 和 11/11 追踪，未解析与诊断均为 0。
- 测试刻意让一条连接从根内输出，经根外缓冲，再返回根内输入：旧兼容模式分别有 5 / 4 条边，严格模式正确切为两处边界，保留其他全部连接；`domain_extra` 同级同名定义不进入图。证据中的层次对象和 net 保持在指定根内。
- 根实例自身、generate 根、空规则范围使用追踪根、无效根拒绝以及常量驱动识别通过。范围输出不计为分支末端；范围入口位于后续传播层时不误报为非法初始根。
- Tk 实际控件、范围状态、器件边界标记、JSON/SVG 导出、时钟/复位独立字段和新进程关闭重开恢复通过，未记录回调异常。当前 Python 检查 **75 通过，Windows 跳过 1 项 Linux 符号链接检查**。
- 未填写新字段的 OpenC910 旧配置重新提取为 **6,984 实例 / 6,983 连线 / 6,959 末端**，完整节点、端口边及末端集合与此前记录一致，13,968/13,968 追踪，未解析和诊断均为 0。

验收脚本：`python3 scripts/check_scoped_trace.py` 与图形会话中的 `python3 scripts/check_scoped_gui.py`。原始记录位于本机 `validation/scoped-trace/check.json`、`gui-check.json`、各运行原始证据及真实 Tk 截图。本改动减少范围查询和范围外追踪开销，尚不代表商业芯片能在固定 64G 额度内完成 Verdi 导入。

### 跨设备部署回归

- 启动脚本移除了固定的 Verdi/VCS 安装目录、版本、架构和许可证服务器；采用设备既有环境及可选的用户 Bash EDA 入口。当前 VM 的桌面环境入口保存在该用户的本机配置目录，不打入离线包。
- 实际离线包解压到 `/tmp/clocktree7-portability-*/installed tool 中文/` 下，安装文件为 root 所有且只读；使用普通用户 `host`、独立环境启动包内工具，未引用原共享目录中的程序模块。
- 目标包的 `doctor --self-test --gui`：时钟 13 实例 / 11 连线 / 4 末端，复位 12 实例 / 12 连线 / 4 末端，完整 golden 一致，`eda_verified` 与 `gui_verified` 为 `true`。安装和输出目录中的空格/中文通过安全 Verdi 临时工作区处理。
- 原生画布实际绘制两类结果的全部实例和边；只读安装默认使用隔离的用户状态目录，两个独立进程关闭/重开后恢复时钟、复位草稿及三层配置，未修改公共安装权限。生成的快捷方式使用当前解压路径。
- 显式取消 `DISPLAY` 后，两类真实 EDA 自检仍通过。使用同一独立包重新读取现有 OpenC910 KDB，得到 **6,984 实例 / 6,983 连线 / 6,959 末端、13,968/13,968 追踪**；全部实例、端口边和末端集合与之前结果一致，未解析与诊断均为 0。
- Windows Python 3.11.5 / Tk 8.6.12 的设备浏览检查通过；Linux 实测环境仍为 CentOS 7.9 / Python 3.6.8 / Tk 8.5.13 / Verdi O-2018.09-SP2，尚未逐版本验证其他 Verdi。
- 迁移版本当时的 Python 检查为 **70 通过，Windows 跳过 1 项 Linux 符号链接专项检查**；包括配置基准路径、只读安装回退、设备自检的失败条件、全量 golden 对照、离线包内容/校验和及快捷方式转义，已纳入当前检查。

原始迁移验收在本机 `validation/portability/vm-relocated.json`、`vm-relocated-large.json`、`vm-relocated.log` 和各自检目录中；原有时钟/复位 GUI、真实 NPI 与取消回归也再次通过。部署步骤和适用范围见 [其他 EDA 设备部署手册](PORTABILITY.zh-CN.md)。

### 启动自动恢复回归

- VM 原生 Tk 以独立 Python 进程关闭、重开，恢复上次模式、两种分析的未导出草稿、选中层级和完整结果；时钟模式中的器件外观修改也保留。
- 原结果文件移走后依然恢复缓存图形；结果缓存与原文件都不可读时，保留配置并提示。首次升级读取最近可用历史，显式启动结果优先。
- 默认用户状态目录和独立测试缓存均通过写入/重开；入口选择为 elab 且路径尚空的草稿保持原选择。
- 6,984 实例 / 6,983 连线 / 原始 JSON 约 66 MB 的 OpenC910 记录通过自动保存和恢复。首次关闭写入约 1.31 秒，期间记录 35 次界面心跳；后续关闭复用未变化快照。恢复过程未启动 EDA。
- 原子更新失败保留原记录、损坏主记录回退、普通配置保存不重写大图、队列合并及旧快照清理有独立检查。自动恢复版本当时的 Python 检查为 **60 通过，Windows 跳过 1 项 Linux 专项检查**，已纳入当前检查。

原始记录位于本机 `validation/session-gui/check.json`、各阶段 JSON 和真实 Tk 截图。验收入口为 `python3 scripts/check_session_gui.py`；其他 GUI 测试显式关闭会话记录，避免污染实际用户工作区。

### 复位树分支回归

- 默认两级，界面增至 20 级再删除恢复；类型选择仅 MUX / RSY_SYNC，RSY_SYNC 多输入会被拒绝。配置使用 `tree_type: reset` 与 `reset_inputs/reset_output`，旧时钟配置继续可用。
- VM 中分别执行两级、十级真实 Verdi NPI：均为 **12 实例、12 连接、4 MUX、8 RSY_SYNC、2 根、4 末端、2 网络、3 复位源、28/28 追踪**，未解析和诊断均为 0。完整实例、端口边和末端集合与测试预期一致。
- 十级版本包括同层串接、跨层跳接、空的 L6/L7，以及 L1/L2/L8/L10 末端；源文件中的同定义范围外 MUX 未被收进结果。`clk_i` 未被配置为复位端口，未进入复位源清单。
- 时钟/复位工作区的未保存草稿、结果和类型列表分别恢复；运行时模式切换禁用。复位历史记录、真实连线点击与双向证据、全量 JSON、RSY_SYNC 原生 Tk/SVG 模型均通过。
- 同一当前目录连续复位提取、切到时钟提取、再回到复位提取全部通过；两种结果分别归档，本次根目录不残留另一种分析的旧结果文件。
- 再次通过原有七级真实时钟 NPI、6,984 实例 OpenC910 图形回归及真实进程组取消。复位版本当时的 Python 检查为 **49 通过，Windows 跳过 1 项 Linux 符号链接检查**，已纳入当前检查。

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
