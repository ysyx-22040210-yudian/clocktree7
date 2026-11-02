# ClockTree7

**原生 Tk 桌面时钟树架构分析工具：默认两级，可自定义层级，从 RTL elaboration 自动恢复全部关注结构和连接。**

用户填写 RTL 层次范围、module 定义名、器件类型及关注输入/输出端口。工具枚举所有匹配实例，依据实际 elab 网络连接全部扇出、选择器汇合、同层串接及跨层跳接。分支可以在任意已配置层结束；产品名称 ClockTree7 不再表示层级上限。

**[详细 GUI 操作手册](docs/GUI_USER_MANUAL.zh-CN.md)** · [离线 HTML 阅读版](docs/GUI_USER_MANUAL.zh-CN.html) · [验证摘要](docs/VALIDATION.zh-CN.md) · [可浏览示例](examples/flexible_layers/README.md)

![原生 Tk 工作台与完整七层架构图](docs/images/gui-overview.png)

## 功能

- 原生 Python Tkinter / ttk 工作台，层级配置、历史记录、实例导航、属性与连接依据。
- 默认 L1、L2，支持添加层级、删除当前层并自动重新编号；兼容原有七级配置。
- 四类用户选定的器件：**选择器 / 门控 / OCC / DIV 分频器**，带专用模型和实时预览。
- 自动从 `verdi` 命令定位安装目录，无需每次填写；提取运行时间不设上限。
- 同一当前目录可反复运行，旧证据自动归档，每次使用独立 Verdi 工作目录。
- 完整静态结构图，保留多源、全部扇出、汇合、孤立结构及异常。
- 驱动/负载双向 NPI 证据；缺失端口、未知逻辑、逆序层、环路、多驱动等诊断。
- 大图层次概览、逐层展开、全量搜索、缩放平移、全图导航和路径高亮。
- 独立提取进程、日志进度及取消任务。
- 自定义层级配置、全量 JSON 和当前范围 SVG 导出。

![选择器、门控、OCC 与 DIV 专用模型](docs/images/device-library.png)

## 先浏览，无需 EDA 安装

运行 GUI 需要 Python 3.6+、系统 Tkinter 和中文字体；不需要第三方 Python GUI 库。

Windows 双击 `launch_gui.cmd`，或在仓库根目录执行：

```powershell
python -m clocktree gui
```

Linux 图形桌面终端：

```bash
bash launch.sh
```

GUI 启动为两级空白配置；点击“新建配置”也会回到两级。点击“查看示例”打开随附的 `examples/flexible_layers/clocktree.json`，或点“打开结果”选择其他结果。两级四类器件记录样例有 13 个结构、11 条连线、2 个根、4 个末端、2 个网络，未解析为 0。旧七级样例仍保留在 `examples/device_models/`，导入结果会恢复其原有层数。

## 真实提取

在实际运行 GUI 的机器上提供有效 Verdi 安装和许可。复杂设计建议导入已经成功生成的 `kdb.elab++`；也支持通过 RTL filelist 建立 elaboration。

### 重新生成并提取示例

在具有 Verdi 的 Linux 机器、仓库根目录执行：

```bash
python3 scripts/make_flexible_fixture.py validation/fixtures/flexible --layers 2
```

在 GUI 中：

1. 点击“导入配置”，选择生成的 `validation/fixtures/flexible/config.json`。
2. 工具自动定位 `verdi`；“运行环境”只用于查看检测结果，无需填写路径。
3. 点击“开始提取全部时钟树”。
4. 核对绿色完成状态、实例/连线统计和右侧诊断。

也可使用 CLI：

```bash
source scripts/eda_env.sh
python3 -m clocktree extract \
  --config validation/fixtures/flexible/config.json \
  --out validation/runs/my-first-run
python3 scripts/check_golden.py \
  validation/runs/my-first-run/clocktree.json \
  validation/fixtures/flexible/golden.json
```

`--out` 可重复使用；省略时写入当前目录。上次证据保存在 `.clocktree-history/`，本次编译工作区位于 `.clocktree-runs/`。不限制重跑次数，不设置运行超时；旧 `timeout_seconds` 字段也不再生效。Windows GUI 可以浏览和编辑；使用虚拟机内 Verdi 提取时，请在虚拟机运行 GUI。

## 规则与模型

| 用户类型 | `kind` | 关注输入 | 关注输出 | 图形 |
| --- | --- | --- | --- | --- |
| 选择器 | `mux` | 一个或多个 | 唯一一个 | 蓝色多输入梯形，标注 N:1 |
| 门控 | `gate` | 恰好一个 | 唯一一个 | 青绿色 D 形门控符号 |
| OCC | `occ` | 恰好一个 | 唯一一个 | 琥珀色切角控制块及脉冲图标 |
| DIV 分频器 | `div` | 恰好一个 | 唯一一个 | 紫色六边形分频块，标注 DIV / ÷N |

类型由用户选择。旧配置没有类型时显示“未分类”。Tk 与 SVG 使用相同模型几何及端口锚点。

每条规则示例：

```json
{
  "module": "clock_mux",
  "kind": "mux",
  "clock_inputs": ["clk0_i", "clk1_i"],
  "clock_output": "clk_o"
}
```

配置包含从 L1 开始连续编号的任意正数层级，默认两级。空层可没有规则；范围填实际 RTL 路径，层号独立于 RTL 路径深度。DIV 的 ÷N 是器件类型标记，不推断实际分频比。完整示例、字段说明和全部操作见 [GUI 操作手册](docs/GUI_USER_MANUAL.zh-CN.md)。

## 结果边界

结果覆盖配置中的全部目标实例及关注端口。末端表示“当前关注结构网络没有下游目标结构”；范围边界、寄存器/RAM 等负载另行记录。未知逻辑保留为明确断点。GUI 绿色完成状态要求连接完整且结构约束通过。

工具恢复静态结构，不推断 MUX/select/en 的实际工作模式，不执行物理 CTS、时序或毛刺签核。示例 OCC 是用于连接与显示验收的简化脉冲控制器。

## 验证

已完成七层/generate/三类器件样例全集 golden 比对、4096 实例压力样例，以及 PULP SoC、OpenC910 配置关注网络的完整 KDB 提取。OpenTitan 通过的是 `clkmgr` 子系统，整芯片旧编译器兼容问题单独记录。

- Python 检查：38 项通过，Windows 跳过 1 项 Linux 符号链接专项检查。
- 新增 VM 两级/十级四类器件全网 golden 核对、20 级增删控件、同目录连续重跑与失败后恢复验证。
- VM 原生 Tk GUI：规则编辑、真实 NPI 提取、类型选择、模型预览、6,984 实例大图操作、证据和导出验收通过。
- 已验证取消 NPI 进程组并保留原结果。
- 测试环境、规模、固定版本及限制：[验证摘要](docs/VALIDATION.zh-CN.md)。

```bash
python -m pytest -q
```

可在 VM 重跑本次新增验收：

```bash
source scripts/eda_env.sh
python3 scripts/check_flexible_gui.py --extract
```

GUI 验收脚本需测试用 Pillow 和系统 X11 工具；浏览器验收脚本需 Playwright。这些不是产品 GUI 运行依赖。

## 目录与本地部署

| 目录/入口 | 内容 |
| --- | --- |
| `clocktree/` | Tk 工作台、器件模型、视图、图分析及提取入口 |
| `npi/extract.tcl` | 基本 NPI 采集 |
| `examples/flexible_layers/` / `examples/device_models/` | 两级四类器件示例、旧七级示例及 RTL/golden |
| `docs/` | 操作手册、截图、验证摘要 |
| `scripts/` / `tests/` | 样例生成、验证和自动检查 |
| `launch.sh` / `launch_gui.sh` / `launch_gui.cmd` | 默认 Tk GUI 启动 |
| `launch_web.sh` | 可选 Web 入口 |
| `validation/` | 本机生成数据，Git 默认忽略 |

当前共享部署是 Windows `D:\VMshare\clocktree7` ↔ VM `/mnt/hgfs/VMshare/clocktree7`，同一份源码。大型编译缓存位于 VM `/home/clocktree7-work`。

源码仓库包含工具、详细手册、实际截图和小型示例。大型第三方 SoC 源码、EDA 工具与数据库、许可证、原始运行缓存保留在本机，未打入仓库。第三方项目许可继续适用于各自内容。
