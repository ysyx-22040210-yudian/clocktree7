# 两级复位树示例

GUI 顶部将分析功能切换为 **复位树**，点击 **查看示例**，即可打开 `resettree.json`。它是 VM 上实际 Verdi NPI 记录的结果，无需 EDA 环境即可浏览。

该案例包含 12 个实例、12 条输出口→输入口连接、2 个根、4 个末端、2 个独立网络和 3 个复位源；MUX 4 个、RSY_SYNC 8 个。全部 28 个关注端口追踪完整，无未解析项或诊断。时钟 `clk_i` 没有被配置为复位输入，未进入复位源清单。

源 RTL 含同层串接、MUX 汇合、全部扇出和早期末端。另有同一定义的 MUX 位于配置范围之外，以核对范围边界；它不会被误收进结果。同步器模型内部使用两级寄存器；GUI 的 S1/S2 图标仅作类型示意，不推断用户模块的真实同步级数。

记录中机器运行路径已移除、RTL 来源位置已换为仓库内同一份文件；真实节点、端口、连线、NPI 路径及统计均保留。`golden.json` 是测试预期集合，不参与工具的提取或连线生成。

2026-11-04 在 VM 使用同一 RTL 重新提取，补入全部 28 个关注端口的实际例化连接字段 `port_connections`。端口下方显示连接信号，长名称可在属性、悬停提示及 SVG 中查看；同步时钟口仍不属于关注的复位端口。

重新提取时，在具备 Verdi 的机器生成本机路径配置：

```bash
python3 scripts/make_reset_fixture.py validation/fixtures/reset-tree --layers 2
source scripts/eda_env.sh
python3 -m clocktree extract --config validation/fixtures/reset-tree/config.json --out validation/runs/reset-check
python3 scripts/check_golden.py validation/runs/reset-check/resettree.json validation/fixtures/reset-tree/golden.json
```

在 GUI 中导入新生成的 `config.json` 会自动进入复位树模式，然后可点击 **开始提取全部复位树**。把 `--layers 2` 改为 `--layers 10` 可生成十级版本；其末端位于 L1、L2、L8 和 L10，允许跳过空层。

详见 [操作手册的复位树章节](../../docs/GUI_USER_MANUAL.zh-CN.md#reset-tree)。
