# gx3-cli-mcp

<!-- mcp-name: io.github.purinzan/gx3-cli-mcp -->

[![PyPI](https://img.shields.io/pypi/v/gx3-cli-mcp)](https://pypi.org/project/gx3-cli-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/gx3-cli-mcp)](https://pypi.org/project/gx3-cli-mcp/)
[![CI](https://github.com/purinzan/gx3-cli-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/purinzan/gx3-cli-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-source--available-blue)](LICENSE.txt)

English: [README.md](README.md)

**不用打开 GX Works3，也能查清楚线圈为什么不导通。**

读取本机上的 GX Works3 `.gx3` 工程并回答关于它的问题 —— 软元件在哪里被写入、
线圈导通需要满足什么条件、哪些条件来自 PLC 外部、哪些分支永远不可能成立。
只读：绝不回写原工程。

它是一个 CLI，同样的分析也以 stdio MCP 服务器的形式提供，因此 AI Agent 可以
基于已建立索引的事实作答，而不是对二进制文件进行猜测。

---

## 安装

```bash
pip install gx3-cli-mcp
```

需要 Python 3.10 以上。安装后提供两个命令行入口：`gx3-cli` 和 `gx3-mcp-server`。

## 30 秒上手

不需要现成工程 —— 先生成一个：

```bash
gx3-cli synthetic-project demo.gx3 --profile demo-line
gx3-cli guide --root demo.gx3
```

`guide` 会读取工程，并告诉你针对这个工程值得运行哪些命令、以及为什么。
这就是对「命令有六十个，我该从哪里开始」的回答。

## 在真实工程上

```bash
gx3-cli doctor --root project.gx3        # 能正常读取吗？
gx3-cli index-lite build --root project.gx3
gx3-cli xref build --root project.gx3
gx3-cli guide --root project.gx3         # 接下来该运行什么
```

然后就可以提问了：

```bash
# 这个软元件在哪里被写入，又有谁在读它？
gx3-cli xref where-used M100 --root project.gx3

# 这个线圈为什么不导通？
gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact

# 整个程序，每个梯级一行
gx3-cli rung-text --root project.gx3

# 按你记得住的注释去搜，而不是你记不住的软元件编号
gx3-cli query-comment "clamp pressure" --root project.gx3
```

每个命令都支持 `--format json` 以便脚本处理，以及 `-o FILE` 写入文件而不是打印。
`gx3-cli --help` 会按分组列出全部命令。

传入 `.gx3` 时，会先解压到 `.gx3_cache/<sha256>/` 并分析该副本。

## 它能告诉你什么

| 问题 | 命令 |
|---|---|
| 这个线圈为什么是 OFF？ | `trace-device`、`interlock-check` |
| 这个软元件在哪里被写入或读取？ | `xref where-used`、`xref downstream` |
| 这个程序到底在做什么？ | `rung-text`、`ladder-print`、`metrics` |
| 把梯级画成图给我看 | `ladder-layout --format svg` |
| 哪些条件来自 PLC 外部？ | `external-inputs`、`comm-refresh` |
| 哪些分支永远不可能成立？ | `dead-logic` |
| 哪里看起来有问题？ | `lint PROJECT`（重复线圈、多重写入、操作数位宽、类型） |
| 两个版本之间改了什么？ | `diff`、`semantic-diff` |
| 工程是否被正确读取了？ | `roundtrip` |

## 配合 AI Agent 使用

```json
{
  "mcpServers": {
    "gx3": { "command": "gx3-mcp-server" }
  }
}
```

如果你的客户端无法从 PATH 解析控制台脚本，可以改用
`"command": "python", "args": ["-m", "gx3cli.gx3_mcp_server"]`。
该服务器暴露只读分析工具和一个受限的命令执行器。

## 能力边界（如实说明）

对本机上的 `.gx3` 做只读分析。**梯形图是它读得好的部分。**
FBD、ST、SFC 和 MIL 会被识别并如实标注，而不是靠猜测处理 —— 因此读不了的程序
会明确返回「读不了」，而不是返回一个空结果。

它不编辑工程，不连接 PLC 做任何更改，也不替代 GX Works3。
`live-read` 可以通过 MC Protocol/SLMP 读取 PLC 当前值，但仅限 CLI，
且必须显式提供连接参数。

输出仅供参考。在接触真实设备之前，请在 GX Works3 中复核，并走完你自己的安全流程。
已验证范围见[验证矩阵（日文）](docs/VALIDATION_MATRIX.md)。

如果某个工程解析失败，`gx3-cli failure-corpus capture` 会把它变成一份本地回归样本，
不会向任何地方发送数据。

## 疑难排查

**7z 格式的 `.gx3`** —— 安装 7-Zip，或者直接指定路径：
`set GX3_7Z=C:\Program Files\7-Zip\7z.exe`。加密容器不会被解密，
请改为从 GX Works3 导出文件夹。

**PyPI 无法访问** —— `pip install git+https://github.com/purinzan/gx3-cli-mcp.git`

**某处读取结果不对** —— 先运行 `gx3-cli doctor --root ...`，然后
[提交 issue](https://github.com/purinzan/gx3-cli-mcp/issues/new/choose)。

## 文档

- [用户手册（中文）](docs/USER_MANUAL_ZH.md)
- 用户手册 [日文](docs/USER_MANUAL_JA.md) / [英文](docs/USER_MANUAL_EN.md)
- [Agent 使用指南（日文）](docs/AGENT_USAGE_JA.md)
- [梯形图实务要点（日文）](docs/LADDER_PRACTICAL_TIPS_JA.md)
- [安全须知（日文）](docs/SECURITY_JA.md) —— 本地数据处理、只读 MCP 策略
- [验证矩阵（日文）](docs/VALIDATION_MATRIX.md) —— 已验证的范围与限制
- [GX Works3 功能对照表（日文）](docs/GX_WORKS3_FEATURE_MATRIX_JA.md)
- [llms.txt](llms.txt) —— 机器可读的能力与边界摘要

## 许可

**源码可见，但不是开源软件。** 完整条款见 [LICENSE.txt](LICENSE.txt)；
本节只是摘要，以许可证正文为准。

你**可以**阅读源码，并将其用于评估和内部工作，包括在公司内部使用。
你**不可以**在未获得书面许可的情况下重新分发它、将其作为服务托管，
或将其打包进付费产品。不存在许可证密钥、激活流程或付费套餐。

商用相关的咨询，欢迎通过 Issue 提出。
