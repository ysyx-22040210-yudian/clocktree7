# 两级四类器件示例

`clocktree.json` 是 VM 上实际 Verdi NPI 提取的记录结果：13 个实例、11 条连线、2 个根、4 个末端和 2 个独立网络。器件包括 MUX 2、GATE 5、OCC 3、DIV 3；全部 28 个端口追踪完整，无未解析项或诊断。

GUI 点击 **查看示例** 即可打开，无需 EDA 安装。真实连接、NPI 证据和统计保持不变；记录中的机器执行路径已移除，RTL 来源位置换成仓库内同一份文件。DIV RTL 为二分频触发器；OCC 为用于连接验证的简化模型，图中 ÷N 表示类型，不自动推断分频比。

2026-11-04 使用同一 RTL fixture 在 VM 重新提取，补入全部 28 个关注端口的实际例化连接字段 `port_connections`。原有实例、连接及证据保留；打开后可在端口下方、属性面板和 SVG 中查看信号名。

在具有 Verdi 的机器重新生成绝对路径配置后再提取：

```bash
python3 scripts/make_flexible_fixture.py validation/fixtures/flexible --layers 2
source scripts/eda_env.sh
python3 -m clocktree extract --config validation/fixtures/flexible/config.json --out validation/runs/flexible
python3 scripts/check_golden.py validation/runs/flexible/clocktree.json validation/fixtures/flexible/golden.json
```

同一输出目录可反复使用。将生成命令的 `--layers 2` 改为 `--layers 10` 可生成包含跳层、同层串接以及 L1/L2/L8/L10 末端的十级版本。两种版本均已通过全部实例、边和末端的 golden 比对。

完整操作见 [GUI 操作手册](../../docs/GUI_USER_MANUAL.zh-CN.md)。旧七级示例继续保留在 [device_models](../device_models/README.md)。
