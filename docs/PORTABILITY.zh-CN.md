# 在其他 EDA 设备部署 ClockTree7

更新日期：2026-11-03。工具可从任意目录运行，产品运行只用 Python 标准库；不需要联网下载 Python 包。先在目标设备执行本章的自检，再用目标项目的 elab 做工程验收。

2026-11-04 增补：大图索引使用 Python 标准库 SQLite，已验证系统 SQLite 3.7.17；Linux 内存保护通过 `/proc` 读取本次 Verdi 和提取控制进程的 RSS，默认 60 GiB，可在 GUI 修改或关闭。当前版本支持提交断点后自动轮换 Verdi，详细行为与适用边界见 [内存优化与重启续跑](MEMORY.zh-CN.md)。运行目录应有足够磁盘空间保存证据、索引、断点和每次 Verdi 工作区。

2026-11-06 增补：默认目标结构模式使用 NPI Language Model 的 `npi_trace_driver_by_hdl2`、`npiHighConn`、`npi_compare_objects` 等接口。离线包包含 `npi/target_trace.tcl`，不能只更新 Python 文件。Verdi 2018 的复杂数组连接存在已知未实现类型，详见 [本次验收报告](TARGET_MODE_VALIDATION.zh-CN.md)。现场仍需用实际 KDB 验证；目标范围不会自动裁剪 elab 导入内存。

## 1. 支持范围与设备条件

| 用途 | 系统及依赖 | 验收方式 |
| --- | --- | --- |
| 真实时钟树 / 复位树提取 | Linux、Python 3.6+、完整 Verdi 安装及基本 NPI 功能的有效许可 | `doctor --self-test` |
| 原生 GUI、结果浏览、编辑与导出 | Windows 或 Linux、Python 3.6+、系统 Tkinter / ttk、可用的图形会话、中文字体 | `doctor --viewer-only --gui` |
| Linux GUI 发起真实提取 | 上述两组条件同时满足 | `doctor --self-test --gui` |

当前提取后端使用 **Verdi/NPI**。“安装了 EDA”本身不能替代这个条件；仅有其他厂商工具、VCS 或 Verilator 时，可以浏览已有结果，不能直接使用当前后端读取其专有数据库。

已实际验证的是 CentOS 7.9、Python 3.6.8、Tk 8.5.13、Verdi O-2018.09-SP2，以及 Windows、Python 3.11.5、Tk 8.6.12 的浏览功能。其他系统和 Verdi 版本需要运行目标设备自检，尚未逐版本实测。自检通过也不替代目标 SoC 的 RTL 编译、依赖库配置和数据库兼容性检查。

同一离线包已在独立的只读安装目录完成两类真实提取、Tk 画布、无显示环境、默认用户存储及重开恢复；另重新提取 OpenC910 的 6,984 实例 / 6,983 连线，全量连接集合一致。详细记录见 [验证摘要](VALIDATION.zh-CN.md)。

## 2. 获得和校验离线包

可以克隆整个工具仓库，也可在已有设备构建 ZIP / tar.gz：

```bash
python3 scripts/build_portable.py
```

默认写入 `validation/releases/clocktree7-portable-日期.zip` 和同名 `.tar.gz`。同名文件已存在时不会覆盖；可用 `--name` 另取名称，或用 `--out` 选择输出目录。包中包含工具、手册、模型、原生 GUI、小型 RTL / golden 和设备自检脚本。

包中不含 EDA 安装、许可证、第三方 SoC 源码、KDB、历史配置缓存和原始验证数据库。无需把原设备的共享盘、虚拟机地址或构建目录搬到目标设备。

在目标设备解压后，进入解压目录并检查文件校验和：

```bash
python3 scripts/build_portable.py --verify .
```

`MANIFEST.json` 记录逐文件 SHA256、源码提交和打包时工作区是否存在未提交改动。只有离线包有这个文件；Git 克隆目录无需执行该校验命令。

## 3. 启用目标设备自己的 EDA 环境

推荐从平时能正常使用 `verdi` 的终端启动：

```bash
command -v verdi
bash clocktree.sh doctor
```

工具优先使用 `PATH` 中的实际 `verdi` 命令，支持安装目录的符号链接和 Synopsys `.wrapper` 分派，自动定位 `share/NPI/L1/TCL/npi_L1.tcl`。未找到命令时，会尝试当前环境的 `VERDI_HOME/bin/verdi`。旧配置里的安装目录仅作最后的兼容回退。

启动脚本不再设置任何固定 Verdi/VCS 版本、安装目录、架构或许可证服务器；许可证沿用目标设备环境。没有 `LM_LICENSE_FILE` 或 `SNPSLMD_LICENSE_FILE` 也不直接判失败，因为站点可能有其他正常许可配置。是否实际可用由真实自检确认。

若从桌面双击启动时没有继承 EDA 环境，可以一次性设置用户自己的 Bash 环境入口。默认位置是 `${XDG_CONFIG_HOME:-$HOME/.config}/clocktree7/eda.sh`，内容例如：

```bash
# 替换为本设备管理员提供的 Bash EDA 环境脚本
source "/path/to/site/eda_setup.sh"
```

默认用户入口仅在当前找不到 `verdi` 命令时读取，避免覆盖终端中已选择的版本。也可显式指定另一个入口，此时每次启动都会读取它：

```bash
export CLOCKTREE_EDA_SETUP=/path/to/site/eda_setup.sh
bash launch.sh
```

环境脚本要由 Bash 可读取；csh/tcsh 的 `setenv` 脚本不能直接 `source` 到 Bash。可以先在已配置好的原终端中运行工具。以上自动入口由 Bash 启动器加载；直接运行 `python3 -m clocktree` 时，应先自行加载 EDA 环境。

如系统默认 Python 不合适，可选择已有的解释器。变量填写可执行文件路径，不附带参数：

```bash
export CLOCKTREE_PYTHON=/path/to/python3
bash clocktree.sh doctor
```

Windows 对应使用 `clocktree.cmd` / `launch_gui.cmd`，也支持 `CLOCKTREE_PYTHON`。系统 Tkinter、图形会话和中文字体由目标系统提供；Pillow、Playwright、pytest、Markdown 和 Paramiko 仅用于开发或扩展验证，不是产品运行依赖。

## 4. 在目标设备做一次真实验收

Linux 图形终端执行：

```bash
bash clocktree.sh doctor --self-test --gui
```

工具会新建独立自检目录，重新生成小型时钟和复位 RTL，实际启动目标设备的 Verdi，然后核对全部实例、连线和末端集合，并在 Tk 原生画布绘制两类结果。自检期间不读取或覆盖日常 GUI 的自动恢复记录，不设置提取运行超时。

验收应满足：

- 最终状态为 `PASS`，JSON 报告的 `eda_verified` 和 `gui_verified` 均为 `true`。
- `clock_eda_golden`：13 个实例、11 条连线、4 个末端，完整集合一致。
- `reset_eda_golden`：12 个实例、12 条连线、4 个末端，完整集合一致。
- 原生画布确实绘制相同数量的实例和连线，自动配置/结果快照读写通过。

只有执行 `doctor` 时，检查的是环境、安装文件和存储条件；报告 `eda_verified: false`，不能据此认定许可证已通过。`--gui` 会短暂打开并关闭一个测试窗口。

无图形会话的 Linux 服务器先执行：

```bash
bash clocktree.sh doctor --self-test --out /path/to/writable/checks --json
```

它不启动 Tk 窗口；某些 Verdi 版本本身仍可能要求显示服务，具体错误见该次 `verdi.log`，按站点既有方式提供显示服务。浏览功能可另在图形终端验收：

```bash
bash clocktree.sh doctor --viewer-only --gui
```

Windows 浏览验收：

```powershell
clocktree.cmd doctor --viewer-only --gui
```

每次自检生成新的 `doctor_日期_唯一编号` 子目录，`doctor.json` 保存结果；`--out` 指定其父目录。真实 NPI 证据及编译/运行日志保留，便于定位失败原因。

## 5. 安装 GUI 快捷方式

在 Linux 解压目录执行：

```bash
python3 scripts/install_desktop.py --desktop
```

脚本根据当前安装位置生成当前用户的应用菜单项和 `~/Desktop/ClockTree7.desktop`；不需要管理员权限。若桌面目录是中文名或其他目录，使用 `--desktop-dir /实际桌面目录`。只生成应用菜单项时省略 `--desktop`。仓库中的 `ClockTree7.desktop.in` 是生成模板。

移动工具目录后重新执行安装脚本，更新快捷方式所指向的位置。Windows 直接使用解压目录的 `launch_gui.cmd`。

## 6. 共享安装、运行目录和历史恢复

可写的工具目录继续使用 `validation/runs`，保留已有运行记录。系统公共目录或其他只读安装位置会自动选择当前用户状态目录下的 `clocktree7/projects/目录标识/runs`。可以显式指定：

```bash
export CLOCKTREE_RUNS=/path/to/my/runs
bash launch.sh
```

`gui --runs /path/to/runs` 的优先级高于默认目录。自动恢复记录仍按用户和运行目录隔离；Windows/Linux、不同用户不会互相覆盖。需要跨设备继续同一工程时，用 GUI 保存配置、导出结果，再在目标设备导入；离线工具包不包含用户工作记录。

旧 Verdi 对工作路径中的空格、中文或 shell 特殊字符有兼容限制。工具会为这类运行位置选择安全的临时目录，暂存 Tcl/NPI 运行文件，再把原始证据和结果写回指定位置；`.clocktree-runs/本次编号/runtime-location.json` 记录实际位置。若默认临时目录也不合适，可以设置：

```bash
export CLOCKTREE_EDA_TMPDIR=/path/to/ascii_scratch
```

该目录需可写，使用 ASCII 字符且不含空格或 shell 特殊字符。临时证据保留供诊断；确认不再需要后可以按站点数据管理规则清理。非 ASCII 安装/工程路径还需要 Python 所在进程使用 UTF-8 locale，`doctor` 会报告文件系统编码。

RTL 文件名、filelist 语法和数据库路径仍须被实际使用的 EDA 版本支持；本功能不保证旧编译器支持任意 Unicode RTL 文件名。

## 7. 工程配置与 elab 库迁移

大芯片可以设置独立的 `trace_scope`，将端口追踪严格限定到一个共同层次及其子层次；各层 module 实例查询也直接按各自范围执行。越界记录为范围入口/出口，不作为真实分支末端。字段、配置约束与界面步骤见 [操作手册第 23 章](GUI_USER_MANUAL.zh-CN.md#bounded-tracing)。这降低查询和追踪的额外开销，完整 KDB 导入的基础内存仍需在目标设备实测。

已有结果图的拓扑和证据包含在 JSON 内，换设备后可以直接打开。重新提取时，必须选择目标设备可访问的 filelist / elab，不能继续使用原设备的盘符和绝对路径。

在 JSON 配置中可设置 `design.base_dir`。其相对路径以**配置文件所在目录**为基准，`design.elab` / `design.filelist` 的相对路径再以 `base_dir` 为基准。例如工程根目录下的配置：

```json
{
  "design": {
    "base_dir": ".",
    "top": "soc_top",
    "elab": "build/simv.daidir/kdb.elab++"
  },
  "layers": [
    {"index": 1, "hierarchy": "soc_top", "modules": [
      {"module": "clock_mux", "kind": "mux", "clock_inputs": ["a", "b"], "clock_output": "y"}
    ]},
    {"index": 2, "hierarchy": "soc_top", "modules": []}
  ]
}
```

可以在路径中使用已定义的环境变量和 `~`。GUI 导入时解析为本机路径，避免创建新任务目录后发生偏移。没有 `base_dir` 的旧配置保持从启动工作目录解析的规则。GUI 保存的已解析绝对路径适合本设备继续使用；跨设备搬工程时，按目标位置调整 `base_dir` 或在 GUI 重选设计入口。

filelist **内容**中的相对路径不会因为 `base_dir` 自动改写。提取在独立 Verdi 工作目录执行；建议沿用工程构建流程生成含绝对 RTL、include 和库路径的 filelist，或导入已成功生成的 KDB。宏、include、库映射等属于目标工程编译配置。

目标 Verdi 必须能够读取给定 elab 数据库。遇到数据库版本不兼容时，在目标 EDA 环境用工程原有构建流程重新生成数据库。最后用真实工程配置提取，并检查完整性、结构诊断及预期规模，不能仅以小型设备自检代替工程验收。

## 8. 常见失败的定位

| 报告 / 现象 | 处理位置 |
| --- | --- |
| 找不到 `verdi` | 在同一个启动终端启用站点 EDA 环境；检查 `PATH` 或用户 EDA 入口。 |
| 找到命令但没有 `npi_L1.tcl` | 确认命令指向完整 Verdi 安装；若是站点包装脚本，加载其配套 `VERDI_HOME`。 |
| 许可证或 NPI 调用失败 | 查看自检目录中的 `verdi.log`；使用本设备有效许可，由管理员检查对应功能。 |
| `Tkinter` 导入失败 | 由系统/环境管理员给所选 Python 安装匹配的 Tkinter 与 Tcl/Tk。 |
| `no display` / 无法打开显示 | 在图形会话运行 GUI，或使用无 GUI 的 CLI；Verdi 需要显示时按站点配置处理。 |
| 中文方框 | 安装中文字体，重开 GUI；`doctor --gui` 会给出字体提示。 |
| 无写权限 | 设置 `CLOCKTREE_RUNS`、`--out`，或让工具使用用户目录；无需改公共安装权限。 |
| Tcl/NPI 接口在新版本不可用 | 保留 `doctor.json` 和 `verdi.log`，按实际版本的 NPI 接口核对；不将该设备标记验收通过。 |
| RTL 编译或 KDB 导入失败 | 使用目标设备的工程编译设置或重建兼容 KDB，查看对应 `compiler.log`。 |

GUI 日常操作见 [操作手册](GUI_USER_MANUAL.zh-CN.md)，已完成的验证见 [验证摘要](VALIDATION.zh-CN.md)。
