# gx3-cli-mcp 用户手册

`gx3-cli-mcp` 是一个在本机运行的、只读的 CLI 与 MCP 服务器，用于分析 GX Works3
（`.gx3`）工程。它帮助你查看软元件、注释、交叉引用、梯形图证据、通信边界，
以及你明确请求时才读取的 PLC 当前值。

其他语言版本：[日文](USER_MANUAL_JA.md) / [英文](USER_MANUAL_EN.md)

## 它做什么

- 找出某个软元件在哪里被使用。
- 在软元件注释中搜索启动、停止、报警、手动、循环、状态等信号。
- 追踪线圈的导通条件，并展示相关的梯形图证据。
- 通过 MCP 向 AI Agent 暴露只读的分析工具。
- 运行静态检查，例如重复线圈、多重写入、死逻辑、互锁可能性。
- 整理外部输入、HMI、通信、IP、链接和时序方面的候选项。
- 仅在你明确提供 PLC 地址、端口、软元件和点数时，才读取 PLC 当前值。

## 它不做什么

- 它不编辑 GX Works3 工程。
- 它不会仅凭静态分析就证明设备安全。
- 它不保证覆盖每一个 GX Works3 版本、PLC 型号或程序格式。
- 除非你主动使用其他服务，否则它不上传工程数据。
- 它不会从 `.gx3` 文件推断 PLC 连接信息并自行连接。

## 安装

```powershell
python -m pip install gx3-cli-mcp
gx3-cli --version
gx3-mcp-server --version
```

若要使用最新源码：

```powershell
python -m pip install git+https://github.com/purinzan/gx3-cli-mcp.git
```

## 最先运行的三条命令

在真实工程上，先运行这三条：

```powershell
gx3-cli doctor --root C:\path\to\project.gx3
gx3-cli index-lite build --root C:\path\to\project.gx3
gx3-cli xref build --root C:\path\to\project.gx3
```

当 `--root` 指向 `.gx3` 文件时，CLI 会把它解压到本地的
`.gx3_cache\<sha256>\` 目录，并分析该缓存副本。

## 常见任务

| 目的 | 命令 |
|---|---|
| 检查工程是否可分析 | `gx3-cli doctor --root project.gx3` |
| 建立搜索索引 | `gx3-cli index-lite build --root project.gx3` |
| 建立交叉引用 | `gx3-cli xref build --root project.gx3` |
| 查询单个软元件 | `gx3-cli query-device M100 --root project.gx3` |
| 搜索注释 | `gx3-cli query-comment "start" --root project.gx3` |
| 带同义词搜索 | `gx3-cli query-comment alarm --root project.gx3 --expand-synonyms` |
| 查找写入方/读取方 | `gx3-cli xref where-used M100 --root project.gx3` |
| 为脚本输出 JSON | `gx3-cli query-device M100 --root project.gx3 --json` |
| 追踪线圈条件 | `gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact` |
| 打印梯形图证据 | `gx3-cli ladder-print MAIN --root project.gx3 --device M100` |
| 运行静态检查 | `gx3-cli lint project.gx3` |
| 生成支持包 | `gx3-cli support-bundle --root project.gx3 -o support.zip` |

## 读取当前值（live-read）

`live-read` 使用 MC Protocol/SLMP 3E 二进制批量读取。它只从你明确提供的
PLC 端点和软元件范围读取当前值。

```powershell
gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word --dry-run
gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word
gx3-cli live-read --ip <PLC_IP> --port 5000 --device M100 --count 16 --type bit --format json
```

把 JSON 保存下来再传给 `ladder-print`，就能把当前值叠加到引用的梯形图行上：

```powershell
gx3-cli live-read --ip <PLC_IP> --port 5000 --device M100 --count 16 --type bit --format json -o live.json
gx3-cli ladder-print MAIN --root project.gx3 --device M100 --live-values live.json
```

### 离线读取已采集的数据

`live-read` 有两种完全不建立连接的模式，都只读取此前采集好的文件。

```bash
gx3-cli live-read explain M100 --root project.gx3 --snapshot live.json
gx3-cli live-read replay changes captured_log.csv
gx3-cli live-read modes
```

`explain` 会把采集到的值与静态追踪得出的导通条件进行比对。结果会以与其他命令
相同的分析状态返回：快照中没有值的软元件，会被明确报告为
`no measured value; file only`（无实测值，仅来自文件），并同时说明该值本应如何获取 ——
这样一来，已经完成求值的那些行就不会被误读成完整答案。

一次快照只能回答采集那一瞬间的情况，不能回答过去某次停机或跳闸的原因。

这两种离线模式也通过 MCP 提供，名称为 `gx3_explain_snapshot` 和
`gx3_replay_capture`。网络模式则不提供：MCP 服务器是按模式关键字决定的，
而不是按命令名。

## MCP

在支持 stdio MCP 服务器的 AI 客户端中这样配置：

```json
{
  "mcpServers": {
    "gx3": {
      "command": "gx3-mcp-server"
    }
  }
}
```

MCP 服务器暴露只读分析工具和一个受限的命令执行器。
演示工程的生成与 `live-read` 的网络模式仍然仅限本地 CLI。

## 安全与免责

输出仅供参考。静态分析不能替代 GX Works3 中的复核，也不能替代你所在组织的
安全流程。在接触真实设备之前，请务必自行确认。
