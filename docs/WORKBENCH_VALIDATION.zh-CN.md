# ClockTree7 3.0 画布与子系统逐项验收

日期：2026-11-07。对应[已实现操作手册第 30 章](GUI_USER_MANUAL.zh-CN.md#workbench-v3)。结构化实测数据随仓库保存于 [WORKBENCH_VALIDATION.data.json](WORKBENCH_VALIDATION.data.json)，完整日志、原始结果和截图位于共享工作区 `validation/workbench/`，不上传 EDA 库、第三方源码或用户历史。

## 1. 实现范围

| 计划项目 | 实现及验收结论 |
|---|---|
| 流畅画布 | 保留 Tk 图元；平移移动已有对象并增删可见集；空间索引、布线、文字截断及高亮集合缓存。缩放事件合并，停止后恢复细节；导航小图仅布局变化时重建。达到本机实测目标，见下表 |
| 后台布局 | 独立 Python 进程计算布局/布线；切换视图终止旧计算；支持显式取消并恢复已完成视图；最多保留 3 个布局缓存且限制节点/路径总量 |
| 导航和历史 | 单对端直接定位，多对端可搜索、每页 100 条连接；后退/前进恢复选中器件与视口。100 条显式搜索按设计及模式去重保存，使用独立偏好文件 |
| 分支布局与高亮 | 默认紧凑布局，保留按层排列；非递归拓扑/SCC 处理，矩形避让正交线路。全部祖先边与后代边并集，排除上游其他旁支；子系统引用限定所属输入 |
| SUBSYSTEM | 时钟/复位均支持输入型模型；编辑器内独立 Verdi 前处理，正则及手动选择确认；主提取只用确认名单。实际范围逐位追踪，独立输入不合并网络，显示引用不重复计算物理实例 |
| 证据 | 默认实例/端口/实际信号摘要，明确已执行核验方向；最多 8 步映射预览；完整 NPI 路径按需展开，保留原始证据 |
| 问题表 | 全量索引、分类/搜索/分页/关联对象/CSV；分开计算问题数和涉及实例数；定位器件端口、规则或阶段日志。无结果的导入失败也可查看 |
| 兼容 | 旧 JSON、配置、历史和 v1 索引继续可读；旧索引复制升级，不改原历史。Tk、SVG、索引及引用使用相同真实边；提取断点版本校验保留 |

## 2. 环境与计时口径

- 同一台授权 VM：CentOS 7.9、16 GiB 内存、Python 3.6.8、Tk/Tcl 8.5.13。
- EDA：Verdi O-2018.09-SP2；真实 NPI、实际许可环境，以普通用户运行。
- GUI：Xvfb 1920×1080×24，窗口固定 1840×1000。帧耗时包括 Tk `update_idletasks()` 刷新；使用本机单调时钟。
- 基线代码：未修改的 `clocktree7-resilience-20261106` 便携包，基线提交 `51a5b568d7b82f6d70d2cdb7611fba6e03e1a297`。
- 宿主机自动测试：Windows、Python 3.11；Linux 原生验证另行执行，不能用宿主机测试代替 EDA 验证。

性能图为此前真实 Verdi 提取的 8,195 实例、8,194 条边结果：`validation/resilience/scales/fanout/run/clocktree.json`。两版使用相同数据、窗口和操作脚本；新版采用默认紧凑布局，旧版使用旧默认层级布局，视口内节点位置并不相同。记录的是自动驱动原生 Tk 的更新耗时，不是显示器到眼睛的端到端延迟。

## 3. 交互性能

| 指标 | 旧版 | 新版最终复测 | 目标 / 结果 |
|---|---:|---:|---|
| 拖动更新 P95 | 2,738.06 ms | **25.05 ms** | ≤33 ms，通过 |
| 拖动更新 P50 / 最大 | 2,265.89 / 2,878.28 ms | 22.02 / 28.85 ms | 79 次更新 |
| 缩放更新 P95 | 2,617.37 ms | **26.40 ms** | ≤50 ms，通过 |
| 缩放更新 P50 / 最大 | 2,156.66 / 3,182.07 ms | 19.79 / 38.59 ms | 新版 82 次更新，含收尾恢复 |
| 停止缩放到细节刷新完成 | 未单独计量 | **147.70 ms** | ≤200 ms，通过 |
| 测量结束 GUI RSS | 103.13 MiB | **128.54 MiB** | 缓存约增加 25.42 MiB |
| 载入及初始投影耗时 | 0.790 s | 2.961 s | 后台进程启动/初始布局较慢，已记录 |

原始计时文件为 `baseline-canvas.json` 和 `final-canvas-v2.json`。旧版每帧重建对象并逐边读取索引；新版保留场景与批量高亮状态。实测结束时旧版 80 个 Tk 图元 / 4 个可见器件，新版 8,332 个图元 / 8 个可见器件；新布局仍绘出经过视口的扇出线段，并未通过删除边换取性能。

这组数字不是所有设计的性能上限；初始布局和缓存占用存在成本。用户设备的图形栈、远程桌面、数据形态和可见线段密度会改变耗时。

## 4. 十万节点与布局正确性

`scripts/check_workbench_gui.py` 在原生 Tk 中使用 100,000 个物理节点、99,999 条边的索引合成图验证：

- 概览、实例局部展开、搜索、整分支高亮均完成；高亮集合包含 99,999 条真实边。
- 连续 6 轮交互，每轮 24 次缩放，Tk 图元均为 **35**；RSS 均为 **57,664 KiB（56.31 MiB）**；没有持续增长。
- 文字缓存 19 项，字体缓存 7 项；后台过期结果被丢弃，显式取消恢复之前已完成的位置数据；Tk 回调异常为 0。
- 大图通过聚合和局部视图浏览，未同时渲染 10 万个详细器件，也未在 VM 重新 elaboration 10 万节点夹具。这是 GUI/索引压力测试，与真实 EDA 压测分开报告。

布局断言覆盖同层串接、跳级、扇出、MUX 汇合、多根及环路：无矩形重叠，所有线路线段不穿越其他器件内部，实际边 ID 集合不变。固定分支样例的相邻节点水平距离总和由 **7,514 降为 2,796**（约下降 62.8%）；此指标是样例的水平距离，不是全部设计的总布线长度保证。共享普通器件保留一个真实节点；环路保留实际边，使用返回通道及特殊线型。

脚本与测试：`scripts/check_canvas_performance.py`、`scripts/check_workbench_gui.py`、`tests/test_workbench.py`、`tests/test_horizontal_layout.py`。

## 5. 子系统实际 Verdi 验证

`scripts/check_subsystem.py` 的实际 RTL 含两个驱动器、同一个子系统 module 的两个参数化实例，范围分别 `[3:2]` 和 `[4:2]`；子系统内部还放置一个同名驱动器，检查直接父层查找不递归。

| 用例 | 验证结果 |
|---|---|
| formal input 发现 | 2 个匹配实例，6 项实例端口，包含实际位宽；前处理 `traced_ports=0` |
| 正则/手动选择 | 大小写行为、非法表达式、零匹配、空选择及手动子集均检查；GUI 未确认不能保存 |
| 主提取不重跑正则 | 配置保留故意无法匹配的正则，仍严格追踪已确认名单 |
| 时钟 target / full | 均为 4 真实实例、7 条精确端口边、2 个独立网络；无子系统输出边 |
| 复位 target / full | 均保持上述端点集合与独立网络语义 |
| 手动只选 aux_clk | 只产生 2 条预期连接，不扩大到 clk_bus |
| 缺失端口 | 2 处未解析，保留可定位信息 |
| 未连接子系统 | 增加一个实例后仍为 7 条已证明边，不补画连接 |
| 子系统输入超时 | 当前实例超时与剩余口跳过；其他同名实例继续，最终 4 条边；无虚构输出超时 |
| 编辑窗口取消 | 实际启动独立读取进程后取消、再次读取成功；不把取消结果确认为规则 |

超时测试在真实 Verdi 工作进程内注入同步 `exec sleep`，验证外部监测在阻塞时仍能终止、撤回未提交追踪并重启。它复现阻塞控制语义，不能冒充商业芯片的原始 vendor 卡死调用。

完整证据：`subsystem-3/acceptance.json`、`subsystem-edges/acceptance.json`、`gui-release/acceptance.json`。早期范围读取失败及轮换目录漏复制脚本的问题已修复；旧失败记录保留在 `subsystem-1`、`subsystem-2`，未覆盖为通过。

## 6. 导航、历史、证据和问题表

- 时钟写入/读取、复位写入/读取分别使用独立 GUI 进程，确认两类搜索历史互不污染、布局偏好恢复、会话结果可用；历史上限 100 和去重通过自动测试。
- 单邻居直跳、多邻居搜索选择、当前视图定位不重布局、导航后退/前进、跨聚合定位、子系统引用和总线展开通过原生 Tk 检查。
- 输入反查、双向核验和未执行方向分别显示；摘要最多读取 8 步，400 步索引路径的摘要查询不超过 11 次 SQL，完整原路径仍为 400 步。
- 原生问题表夹具 **704 个问题、3 个涉及实例**，每页 100 条，第 8 页 4 条；CSV 704 条数据加表头，搜索 `s0` 返回 702 条。正常范围边界不计入问题。
- 环路关联对象可展开；问题端口定位产生端口标记；无结果导入失败夹具可定位阶段日志。问题表回调异常为 0。
- 原有 Tk / SVG / 索引恢复 / 8,195 实例定位回归通过，原始结果与边 ID 未改变。

数据：`gui-release/acceptance.json`、`issues-release/acceptance.json`、`legacy-gui/acceptance.json`。

## 7. 自动测试与既有 EDA/KDB 回归

宿主机最终 `python -m pytest -q`：**189 passed, 1 skipped**。跳过的是只适用于 Linux wrapper/symlink 的测试；真实 VM 已运行 Verdi 命令发现与提取。宿主机曾遇同进程多个 Tcl 解释器报错文本为空的测试干扰，相关保护测试现使用独立 Tcl 子进程；没有忽略失败断言。

既有 `check_target_mode.py` 的 **21 项**矩阵全部通过，包含 clock/reset、2/10 传播层、直接父层/generate、别名、位选、反相、多驱动、缺失输出、动态索引、未知逻辑、实例数组及 target/full 差异。未解析夹具按预期保留未解析状态，测试通过不表示所有夹具的连接都完整。

既有端口超时回归 **9 项**通过：clock/reset 输入、全负载输入/输出、关闭阈值、512 扇出和超时决定保存后控制进程中断再恢复；同名其他实例继续，未提交数据撤回正确。

已有开源 KDB 的选定规则回归：

| 数据库/范围 | 实例 / 边 | 耗时 | 原结果对比及保留诊断 |
|---|---:|---:|---|
| OpenTitan clkmgr 块 | 15 / 10 | 4.757 s | 节点/端口边一致；8 逆序层、3 根不在 L1 |
| PULP SoC 已记录配置 | 62 / 48 | 13.997 s | 节点/端口边一致；27 逆序层、17 根不在 L1 |
| OpenC910 指定直接父层子网 | 30 / 29 | 6.916 s | 节点/端口边一致，无诊断 |

OpenTitan/PULP 的既有层级规则与连接顺序存在上述诊断，本轮没有删掉它们或把拓扑有效性改成通过。三个用例的未解析数均为 0。OpenC910 这里是明确选定的子网，不是全芯片全部时钟器件。

## 8. 便携包与复现

离线包由 `scripts/build_portable.py` 生成，包含源码、NPI 脚本、原生 Tk、示例、手册及自检脚本，不包含 EDA 安装、许可证、KDB、用户状态或第三方项目源码。包使用 manifest 对全部文件校验。

发布候选包 `clocktree7-workbench-20261107-rc1` 已在 VM 独立目录 `/home/host/clocktree7-portable-qa/clocktree7-workbench-20261107-rc1` 解包验证，205 个文件 manifest 校验通过；禁止用户 site-packages 并清除 PYTHONPATH，确认导入的工具根目录就是解包目录。

包内真实 clock/reset golden 提取分别得到 13 实例 / 11 边、12 实例 / 12 边，节点、端口边及末端集合全部匹配。原生 Tk 自检完整渲染两图，中文字体可用；包内再次运行子系统发现和 target/full 提取，两种模式均得到 4 实例 / 7 边 / 2 网络。`doctor` 总结果为 pass，`eda_verified`、`gui_verified` 均为 true。

此次独立测试验证的是提交前候选代码；最终归档在 GitHub Desktop 提交后由同一源码生成并另存校验记录。候选包证据见结构化数据中的 `portable` 项，原始报告在 `validation/workbench/portable-clocktree7-workbench-20261107-rc1/acceptance.json`。

正式包 `clocktree7-workbench-20261107` 已由干净提交 `3e857582507144220183c03ac82e78f69bbe2a9d` 生成，并在 VM 另一个独立目录 `/home/host/clocktree7-portable-qa/clocktree7-workbench-20261107` 重新完成相同 manifest、clock/reset golden、Tk 和子系统测试，全部通过。正式包证据为 `portable_final` 项；本次后续文档提交只补充正式包验收结果及复现命令，不改运行代码。安装包保存在共享目录 `validation/releases/`。

| 正式归档 | SHA-256 |
|---|---|
| `clocktree7-workbench-20261107.zip` | `a1495c746bf09badd861dff290af770532404888b35abc896beb007fa85f27d7` |
| `clocktree7-workbench-20261107.tar.gz` | `535948fa5be8f0942a329b2dd4a347b1750792e2bc21f4cd59e81ae84730f77b` |

开发复现（在正确设置 EDA 环境的 Linux 上，输出目录建议独立）：

```bash
python3 scripts/check_subsystem.py --out validation/recheck/subsystem
python3 scripts/check_subsystem_edges.py --base validation/recheck/subsystem \
  --out validation/recheck/subsystem-edges
xvfb-run -a python3 scripts/check_workbench_gui.py --out validation/recheck/gui \
  --result validation/recheck/subsystem/target/clocktree.json
xvfb-run -a python3 scripts/check_issue_gui.py --out validation/recheck/issues \
  --result validation/recheck/subsystem/target/clocktree.json
python3 scripts/check_target_mode.py --out validation/recheck/native
python3 -m clocktree doctor --self-test --gui --out validation/recheck/doctor
```

实际桌面已有 `DISPLAY` 时可直接运行 GUI 检查脚本；无显示环境的 doctor GUI 自检也需要 `xvfb-run`。

## 9. 已知限制与未验收范围

- 未拿到用户商业芯片原始 KDB，本轮未宣称完成该芯片验收。16 GiB VM 也不能重现 64 GiB 商业任务的全部资源条件。
- 已实际验证参数化一维 packed 总线、非零下标。复杂多维接口或不支持的表达式继续给出未解析状态，不推测位宽或连线。
- 任意图上的最短布线、最少交叉没有全局最优保证；正交通道保持器件避让，可能出现不同信号线交叉或近距离共线。交叉不画成连接点，查看真实连接以证据为准。
- 全分支高亮保留完整真实边集合；概览会聚合显示。10 万节点稳定性指标不代表同时显示全部端口文字。
- 新版首次布局有进程启动成本，8,195 实例 GUI RSS 也较基线增加；实测表已列出。性能数字不是在其他 EDA 设备上的承诺。
- 升级后的未完成任务继续接受实现指纹校验；不能因为 GUI 能读旧结果，就假定新版本可跳过校验恢复旧实现中的任意断点。
