# gx3-cli-mcp

<!-- mcp-name: io.github.purinzan/gx3-cli-mcp -->

[![PyPI](https://img.shields.io/pypi/v/gx3-cli-mcp)](https://pypi.org/project/gx3-cli-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/gx3-cli-mcp)](https://pypi.org/project/gx3-cli-mcp/)
[![CI](https://github.com/purinzan/gx3-cli-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/purinzan/gx3-cli-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-source--available-blue)](LICENSE.txt)

**Work out why a coil never turns on, without opening GX Works3.**

`gx3-cli-mcp` reads a GX Works3 (`.gx3`) project on your own machine and
answers questions about it: where a device is written, what has to be true for
a coil to turn on, which conditions come from outside the PLC, and which
branches can never be true at all. It is read-only and never writes back to the
project.

It is a CLI, and it is also a stdio MCP server, so an AI agent can answer from
the same indexed facts instead of guessing at a binary file.

That matters for three reasons:

- **A GX Works3 licence is not on everyone's desk.** Maintenance, production
  engineering, quality and upstream software all end up asking whoever has one.
- **Following a coil upstream is where people lose the thread**, usually about
  three cross-reference hops in. A machine does not lose the thread.
- **You should not need to know the device number.** Search the comment you
  actually remember, and every answer comes back with its comment attached.

日本語: 三菱電機の MELSEC シーケンサ（PLC）のプロジェクトファイル `.gx3` を、
GX Works3 を開かずに解析する CLI / MCP サーバーです。「このコイルがなぜ ON に
ならないか」をラダーから追い、クロスリファレンスを何度も開く代わりに一度で
答えます。読み取り専用で、元のプロジェクトファイルは書き換えません。
ライセンスが手元に無い人でも調べられること、デバイス番号を知らなくても
デバイスコメントから引けることを狙っています。

**紹介記事: [GX Works3 を開かずにラダーを追えるようにした](https://zenn.dev/purinzan/articles/gx-works3-ladder-cli-mcp)**
— 何ができるかは、実際の出力つきでこちらにまとめています。

## Try It Without a Real Project

You do not need a `.gx3` you are allowed to share. Generate a synthetic one:

```powershell
python -m pip install gx3-cli-mcp
gx3-cli synthetic-project line.gx3 --profile demo-line --overwrite
gx3-cli index-lite build --root line.gx3
gx3-cli xref build --root line.gx3
gx3-cli trace-device M2313 --root line.gx3 --strict-logic --compact
```

That builds a fictional 14-station transfer line — about 500 rungs across 10
programs — and traces one step back through every station upstream of it.

This is an unofficial, independent tool. It is not endorsed by Mitsubishi
Electric.

## Questions This Answers

日本語の質問例も併記しています。CLI でも、MCP 経由で AI エージェントに聞く場合でも同じです。

**Where is this device written, and where is it read?**
このデバイスはどこで書かれて、どこで読まれているのか

```powershell
gx3-cli xref where-used M2313 --root project.gx3
```

**Why does this coil never turn on?**
なぜこのコイルが ON にならないのか / 起動条件は何か

```powershell
gx3-cli trace-device M2313 --root project.gx3 --strict-logic --compact
```

**Which conditions come from outside the PLC?**
どの条件が外部入力・HMI・通信から来ているのか（＝ラダーではなく盤を見るべきか）

`trace-device` の出力に `External/HMI/communication boundaries` として出ます。

**I only remember what the device is called, not its number.**
デバイス番号は覚えていないが、コメントの文言なら分かる

```powershell
gx3-cli query-comment "クランプ確認" --root project.gx3
```

**Is anything in this project dead or contradictory?**
二重コイル、リセットの無いラッチ、成立しない条件、矛盾したコメントはないか

```powershell
gx3-cli lint project.gx3
gx3-cli dead-logic --root project.gx3
```

**Can an AI agent answer these instead of me typing commands?**
コマンドを覚えずに、Claude や Cursor から日本語で聞けるか

Yes — that is what the MCP server is for. See [Use With MCP](#use-with-mcp).
`gx3-mcp-server` を Claude Desktop / Claude Code / Cursor などの MCP クライアント
に登録すると、エージェントが上記の解析を呼び出して、根拠つきで答えます。

## Scope

- **Supported:** MELSEC iQ-R / iQ-F series projects saved as `.gx3` by
  GX Works3. 三菱電機の MELSEC シーケンサ、GX Works3 の `.gx3` 形式。
- **Not supported:** GX Works2 (`.gxw`), GX Developer (`.gpj`), and `.gx3`
  files saved with the compressed/lightweight option, which are password
  protected and cannot be extracted. 軽量保存された `.gx3` は展開できません。
- **Not a substitute** for GX Works3, for PLC validation, or for your own
  safety and quality review. Output is advisory.

Verified coverage is listed in [docs/VALIDATION_MATRIX.md](docs/VALIDATION_MATRIX.md).

## How This Reads `.gx3`, And What It Does Not Do

This project is unofficial and independent. It is not affiliated with, endorsed
by, or supported by Mitsubishi Electric. `GX Works3` and `MELSEC` are their
trademarks, used here only to say what this tool reads.

What it does:

- It opens a `.gx3` **data file that the user already has on their own machine**
  and reads the SQLite databases and text inside it. The format was worked out
  by looking at project files the author is authorised to work with.
- It uses only the Python standard library — `zipfile` and `sqlite3`. The
  project has **no third-party Python dependencies** and bundles, links against,
  or redistributes **no Mitsubishi Electric code, libraries, or assets** of any
  kind. If a `.gx3` uses a 7z-style container, the CLI can try a locally
  installed archive tool such as 7-Zip/7zz/bsdtar for extraction.

What it does not do:

- It does **not** decompile, disassemble, patch, instrument, or otherwise
  analyse GX Works3 or any other Mitsubishi Electric software. It never runs
  their software and never touches its binaries.
- It does **not** remove, defeat, or work around any access control. A `.gx3`
  saved with the compressed/lightweight option is password protected, and this
  project contains **no code to decrypt one and will not gain any** — that
  format is listed as unsupported in [Scope](#scope) and stays that way by
  choice, not by oversight. Some official sample `.gx3` files are 7z/AES
  containers; if a normal archive tool cannot extract them, export or extract
  them with GX Works3 first and pass the extracted project folder to this CLI.
- It does **not** modify the project. The archive is extracted to a local cache
  and only that copy is read; the original file is never written back to.
- It does **not** communicate with a PLC, a network, or any remote service.
- It does **not** reproduce Mitsubishi Electric's software, documentation, or
  file format specification. It reads a customer's own data.

Using this tool does not change the agreements you have with Mitsubishi
Electric. Complying with the terms of your GX Works3 licence, and with your
employer's and customers' rules about project data, remains yours.

If Mitsubishi Electric has a concern about this project, please open an issue
or contact the author through GitHub; it will be addressed.

日本語: 本プロジェクトは非公式・独立のもので、三菱電機とは無関係です。読むのは
**利用者が自分の手元に持っているデータファイル**（`.gx3`）だけで、形式は作者が
業務上アクセスできるプロジェクトファイルを観察して把握しました。GX Works3 を
はじめとする三菱電機のソフトウェアを逆アセンブル・デコンパイル・改変することは
一切していません。三菱電機のコードやライブラリを同梱・参照・再配布もしていません
（依存パッケージはゼロで、Python 標準ライブラリのみを使います）。

**保護の解除も行いません。** 軽量保存された `.gx3` はパスワード保護されており、
本プロジェクトには復号のためのコードが存在しません。今後も実装しません。これは
実装漏れではなく方針です。

GX Works3 のライセンス条項や、勤務先・顧客のプロジェクトデータの取り扱い規則を
守る責任は利用者にあります。三菱電機の方でご懸念があれば、Issue または GitHub
経由でご連絡ください。対応します。

## What You Can Do

- Find where a device is used and whether it is read, written, or referenced.
- Search device comments such as start, stop, alarm, cycle, step, or manual
  operation keywords.
- Trace upstream conditions for a coil and print nearby ladder evidence.
- Build xref and SQLite indexes so AI agents can answer from structured facts.
- Review static risks such as duplicate coils, multi-writers, dead logic, and
  interlock possibilities.
- Inspect external inputs, HMI/communication boundaries, IP maps, link maps,
  timing candidates, and project-wide summaries.

## What It Does Not Do

- It does not include real customer or production PLC projects.
- It does not send project data to an online service by itself.
- It does not provide a GUI in this release.
- It does not require a license token or paid-plan token.
- The MCP server does not expose project-mutating commands.
- `live-read` can contact a PLC, but only when you explicitly provide the
  connection details. It is CLI-only and read-only.

## Install

```powershell
python -m pip install gx3-cli-mcp
gx3-cli --version
gx3-mcp-server --version
```

Requires Python 3.10 or later. Installing into a virtual environment is
recommended so the two console scripts stay off the system PATH.

If your site blocks PyPI, install the latest source directly:

```powershell
python -m pip install git+https://github.com/purinzan/gx3-cli-mcp.git
```

For a local source checkout:

```powershell
python -m pip install -e .
```

If your `.gx3` is a 7z-style container, install 7-Zip or point the CLI at an
existing executable:

```powershell
$env:GX3_7Z = "C:\Program Files\7-Zip\7z.exe"
```

日本語: 通常は `pip install gx3-cli-mcp` だけで CLI と MCP サーバーの両方が
入ります。社内プロキシで PyPI に到達できない場合のみ、上の git 直接指定を
使ってください。

## First Analysis

Run these three commands first for a real project:

```powershell
gx3-cli doctor --root C:\path\to\project.gx3
gx3-cli index-lite build --root C:\path\to\project.gx3
gx3-cli xref build --root C:\path\to\project.gx3
```

Then inspect a device:

```powershell
gx3-cli query-device M100 --root C:\path\to\project.gx3
gx3-cli xref where-used M100 --root C:\path\to\project.gx3
gx3-cli trace-device M100 --root C:\path\to\project.gx3 --strict-logic --compact
```

When you pass a `.gx3` file, the tool extracts it into
`.gx3_cache\<sha256>\` and analyzes that local cache.

## Common Tasks

| Goal | Command |
|---|---|
| Check project readiness | `gx3-cli doctor --root project.gx3` |
| Build the search index | `gx3-cli index-lite build --root project.gx3` |
| Build cross references | `gx3-cli xref build --root project.gx3` |
| Look up one device | `gx3-cli query-device M100 --root project.gx3` |
| Search by comment text | `gx3-cli query-comment "起動" --root project.gx3` |
| Show external/HMI/communication boundary devices | `gx3-cli query-external --root project.gx3` |
| Find cycle, step, or state candidates | `gx3-cli query-cycle --root project.gx3` |
| Show used/free device ranges | `gx3-cli device-map --root project.gx3 --types M,D,W --min-free 100` |
| Show writers/readers | `gx3-cli xref where-used M100 --root project.gx3` |
| Trace coil conditions | `gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact` |
| Generate structure/device-flow graphs | `gx3-cli graph --root project.gx3 --type structure --format mermaid` |
| Read current PLC values | `gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word` |
| Overlay current values on ladder evidence | `gx3-cli ladder-print MAIN --root project.gx3 --device M100 --live-values live.json` |
| Print ladder evidence | `gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M100` |
| Check static interlock possibility | `gx3-cli interlock-check M100 M200 --root project.gx3` |
| Run static review checks | `gx3-cli lint project.gx3` |
| List lint checks and rule IDs | `gx3-cli lint --list-checks` |
| Emit lint summary JSON | `gx3-cli lint project.gx3 --format json` |
| Create a support summary | `gx3-cli support-bundle --root project.gx3 -o support.zip` |
| Capture a parser failure as a regression case | `gx3-cli failure-corpus capture --root project.gx3 --case-id case-name --reason "what failed"` |
| Rerun captured failure cases | `gx3-cli failure-corpus run` |

Use the indexed commands above for normal lookup and discovery. Avoid starting
AI workflows with raw text search over extracted GX3 files; GX Works3 projects
contain binary/database files, and the CLI preserves device, comment, POU, role,
and step context.

## Use With MCP

The MCP server is intended for AI clients that support stdio MCP servers.

```json
{
  "mcpServers": {
    "gx3": {
      "command": "python",
      "args": ["-m", "gx3cli.gx3_mcp_server"]
    }
  }
}
```

If your client can resolve console scripts from PATH:

```json
{
  "mcpServers": {
    "gx3": {
      "command": "gx3-mcp-server"
    }
  }
}
```

The MCP server exposes read-only analysis tools and a restricted command
runner. Synthetic demo generation is local CLI-only.

## Agent Skills

This repository includes focused skills for agent workflows:

- [Agent guidance](AGENTS.md): short default rules for this repository.
- [GX3 existing project audit](skills/gx3-existing-project-audit/SKILL.md):
  read-only `.gx3` analysis workflow.
- [GX3 failure corpus](skills/gx3-failure-corpus/SKILL.md): capture failed
  parser/xref/ladder-print/lint cases as regression fixtures.

## Demo Project

Two synthetic profiles, neither of which contains anything real. See
[Try It Without a Real Project](#try-it-without-a-real-project) for the quick
start.

`basic` (the default) is three rungs — enough to check that a command runs:

```powershell
gx3-cli synthetic-project demo.gx3 --overwrite
gx3-cli doctor --root demo.gx3
gx3-cli graph --root demo.gx3 --type structure --format mermaid
gx3-cli trace-device M100 --root demo.gx3 --strict-logic --compact
```

`demo-line` is a 14-station pick-and-place line: about 500 rungs across 10
programs with roughly 500 commented devices, chained so that tracing an output
on the last station walks back through every station upstream. Use this one for
demos, for screenshots, and for reproducing a bug without sending anyone a real
project.

```powershell
gx3-cli synthetic-project line.gx3 --profile demo-line --overwrite
gx3-cli lint line.gx3
gx3-cli dead-logic --root line.gx3
```

It also contains a small number of deliberate faults - a duplicate coil, a
latch with no reset, a coil nobody reads, a contact that can never close, and
two devices whose comments contradict each other - so the review commands
report findings instead of an empty table. This is also the fastest way to
reproduce a bug for an issue without sending anyone a real project.

## Failure Corpus

When a real `.gx3` exposes a parser, printer, indexing, or lint coverage gap,
capture it before fixing the bug:

```powershell
gx3-cli failure-corpus capture --root C:\path\to\project.gx3 --case-id title-row-gap --reason "ladder-print did not detect section titles" --failed-command "gx3-cli ladder-print MAIN --root {root}"
gx3-cli failure-corpus run
```

The command stores the case under `.gx3_failures\cases\` with a small
`case.json` record, then reruns schema, `doctor`, `xref`, `ladder-print`, and
the captured `--failed-command` for every active case. Use `{root}` and
`{reports_dir}` placeholders in failed commands so fixtures remain portable.
Projects with FBD/ST/MIL databases but no LDDB are reported as detected
unsupported formats, and ladder-only checks are skipped instead of being treated
as raw extraction failures. This turns one-off GX3 failures into reusable
regression fixtures.

## Optional Live Read

`gx3-cli live-read` reads current PLC device values over MC Protocol/SLMP 3E
binary batch read. It does not infer connection targets from a `.gx3` file and
it is not exposed through MCP; you must explicitly provide the PLC IP/port and
device range each time.

```powershell
gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word
gx3-cli live-read --ip <PLC_IP> --port 5000 --device M100 --count 16 --type bit --format json
```

Save JSON output and pass it to `ladder-print` when you want GX-print-style
rung citations with current-value annotations:

```powershell
gx3-cli live-read --ip <PLC_IP> --port 5000 --device M100 --count 16 --type bit --format json -o live.json
gx3-cli ladder-print MAIN --root project.gx3 --device M100 --live-values live.json
```

Contacts are annotated as `live:ON pass`, `live:OFF block`, and so on. Coils
show their current value. This is a diagnostic overlay, not a PLC monitor loop.

Use this only on equipment you are authorized to access. The command implements
read-only batch reads; write, run/stop, download, and online edit operations are
out of scope.

## Data And Safety

- Project files stay on your machine unless you pass outputs to another tool.
- Some commands create local files such as SQLite indexes, CSV reports, ZIP
  support bundles, or Markdown summaries.
- `live-read` can open a TCP connection to real equipment only from the CLI and
  only with explicit connection parameters.
- Analysis output is advisory. Verify findings in GX Works3 and through your
  own safety/quality process before changing real equipment.
- The tool is not a substitute for PLC validation, machine safety review, or
  official engineering software.

## Documentation

Recommended reading:

1. [User manual (JA)](docs/USER_MANUAL_JA.md): Japanese install and usage guide.
2. [Agent usage guide (JA)](docs/AGENT_USAGE_JA.md): how AI agents should use the indexed tools.
3. [Security note (JA)](docs/SECURITY_JA.md): local data handling and read-only MCP policy.
4. [Validation matrix (JA)](docs/VALIDATION_MATRIX.md): verified scope and limitations.
5. [File usage guide (JA)](docs/FILE_USAGE_GUIDE_JA.md): repository file map for agents and contributors.
6. [GitHub project review (JA)](docs/GITHUB_PROJECT_REVIEW_JA.md): related GX Works3/GX3/MELSEC projects and design takeaways.

[llms.txt](llms.txt) is a short machine-readable summary of what this project is
and is not, for tools that index repositories for AI assistants.

MCP configuration examples:

- [MCP client config](docs/mcp_client_config.json): robust `python -m gx3cli.gx3_mcp_server` launch.
- [MCP client config, console script](docs/mcp_client_config_console_script.json): direct `gx3-mcp-server` launch when PATH is reliable.

## License In Plain Words

Full terms are in [LICENSE.txt](LICENSE.txt); this section is only a summary and
the license text governs.

This is **source-available proprietary software**, not open source. In practice:

- You **may** clone it, read the source, and run it for evaluation and for
  internal analysis work, including inside a company.
- You **may not** redistribute it, host it as a service, resell it, or ship it
  as part of a paid product or commercial service without written permission.
- There is **no license token, activation, or paid plan** to run it.
- It is provided as is, with no warranty, and its output is advisory only.

日本語: 社内での評価・業務利用は許諾されています。禁止しているのは再配布、
SaaS としての提供、有償製品やサービスへの組み込みです。実行にライセンス
キーや課金は不要です。商用利用の相談は Issue からご連絡ください。

## For Contributors

Bug reports, questions, and small pull requests are welcome, in Japanese or
English. Start with [CONTRIBUTING.md](CONTRIBUTING.md), which covers the
no-project-data rule, the development setup, and what a useful bug report
contains. Issues labeled `good first issue` are scoped to be approachable
without deep knowledge of the GX Works3 file format.

If you clone this repository and find a source code problem, prefer sending a
pull request with the smallest reproduction, a focused fix, and test evidence.
When the bug is exposed by a `.gx3`, capture it with `failure-corpus` before
changing parser logic so the failure becomes a regression case.

Before changing or publishing this project, run the same checks used by CI:

```powershell
python run_tests.py
python scripts\release_gate.py .
python -m build --wheel
python scripts\release_gate.py dist\gx3_cli_mcp-*.whl
```

See [Contributing](CONTRIBUTING.md) for the Windows-first PR workflow and data
safety rules.
