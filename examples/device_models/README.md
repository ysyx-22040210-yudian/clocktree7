# 三类器件七层示例

`clocktree.json` 是已经在 Verdi NPI 中验证的记录结果，包含 16 个结构、15 条连线、7 个末端：选择器 2 个、门控 4 个、OCC 10 个。GUI 可直接打开它，无需先安装或运行 EDA 工具。

源 RTL 为 `fixture.sv`，独立预期集合为 `golden.json`。记录中的机器日志路径已移除、RTL 来源路径已换成仓库内的同一份示例；节点、关注端口、连线、NPI 路径及统计保持原有结果。OCC RTL 是用于提取和显示验收的简化捕获脉冲控制器。

`config.template.json` 和 `rtl.f` 用于说明配置结构。真正提取时，请先在执行 Verdi 的机器生成绝对路径配置：

```bash
cd /path/to/clocktree7
python3 scripts/make_fixture.py validation/fixtures/device-models --device-models
```

在 GUI 点击“导入配置”，选择新生成的 `validation/fixtures/device-models/config.json`，核对 Verdi 安装路径，然后点击“开始提取全部时钟树”。

详细步骤见 [GUI 操作手册](../../docs/GUI_USER_MANUAL.zh-CN.md)。
