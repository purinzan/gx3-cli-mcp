# gx3-cli-mcp

<!-- mcp-name: io.github.purinzan/gx3-cli-mcp -->

[![PyPI](https://img.shields.io/pypi/v/gx3-cli-mcp)](https://pypi.org/project/gx3-cli-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/gx3-cli-mcp)](https://pypi.org/project/gx3-cli-mcp/)
[![CI](https://github.com/purinzan/gx3-cli-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/purinzan/gx3-cli-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-source--available-blue)](LICENSE.txt)
[![Glama](https://img.shields.io/badge/Glama-MCP%20server-black)](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp)

**Work out why a coil never turns on, without opening GX Works3.**

Reads a GX Works3 `.gx3` project on your own machine and answers questions
about it — where a device is written, what has to be true for a coil to turn
on, which conditions come from outside the PLC, which branches can never be
true. Read-only: it never writes back to the project.

It is a CLI, and the same analysis is a stdio MCP server, so an AI agent can
answer from indexed facts instead of guessing at a binary file.

日本語: 三菱電機 MELSEC の `.gx3` を GX Works3 を開かずに解析する CLI / MCP
サーバーです。「このコイルがなぜ ON にならないか」をラダーから追います。
読み取り専用で、元のプロジェクトは書き換えません。

---

## Install

```bash
pip install gx3-cli-mcp
```

Python 3.10+. Installs two console scripts: `gx3-cli` and `gx3-mcp-server`.

## Try it in 30 seconds

No project needed — generate one:

```bash
gx3-cli synthetic-project demo.gx3 --profile demo-line
gx3-cli guide --root demo.gx3
```

`guide` reads the project and tells you which commands are worth running on it,
and why. That is the answer to "there are sixty commands, where do I start".

## On a real project

```bash
gx3-cli doctor --root project.gx3        # does it read?
gx3-cli index-lite build --root project.gx3
gx3-cli xref build --root project.gx3
gx3-cli guide --root project.gx3         # what to run next
```

Then ask it something:

```bash
# where is this device written, and what reads it?
gx3-cli xref where-used M100 --root project.gx3

# why is this coil not turning on?
gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact

# the whole program, one line per rung
gx3-cli rung-text --root project.gx3

# search the comment you remember, not the device number you don't
gx3-cli query-comment "clamp pressure" --root project.gx3
```

Every command takes `--format json` for scripting, and `-o FILE` to write
instead of print. `gx3-cli --help` lists all of them by group.

Passing a `.gx3` extracts it to `.gx3_cache/<sha256>/` and analyses that copy.

## What it can tell you

| Question | Command |
|---|---|
| Why is this coil off? | `trace-device`, `interlock-check` |
| Where is this device written or read? | `xref where-used`, `xref downstream` |
| What does this program do? | `rung-text`, `ladder-print`, `metrics` |
| Show me the rung as a picture | `ladder-layout --format svg` |
| What comes from outside the PLC? | `external-inputs`, `comm-refresh` |
| What can never be true? | `dead-logic` |
| What looks wrong? | `lint PROJECT` (duplicate coils, multi-writer, operand widths, types) |
| What changed between versions? | `diff`, `semantic-diff` |
| Did it read the project correctly? | `roundtrip` |

## Use with an AI agent

```json
{
  "mcpServers": {
    "gx3": { "command": "gx3-mcp-server" }
  }
}
```

Or `"command": "python", "args": ["-m", "gx3cli.gx3_mcp_server"]` if your
client cannot resolve console scripts from PATH. The server exposes read-only
analysis tools and a restricted command runner.

See [Agent usage guide (JA)](docs/AGENT_USAGE_JA.md) for how an agent should
drive it.

## Scope, honestly

Read-only analysis of `.gx3` on your machine. **Ladder is what it reads well.**
FBD, ST, SFC and MIL are detected and reported as such rather than guessed at,
so a program it cannot read comes back saying so instead of coming back empty.

It does not edit projects, connect to a PLC to change anything, or replace GX
Works3. `live-read` can read live device values over MC Protocol/SLMP, CLI-only
and only with explicit connection parameters.

Output is advisory. Verify in GX Works3 and through your own safety process
before touching real equipment. See
[Validation matrix (JA)](docs/VALIDATION_MATRIX.md) for what has been checked.

If a project fails to parse, `gx3-cli failure-corpus capture` turns it into a
local regression sample without sending anything anywhere.

## Troubleshooting

**7z-style `.gx3`** — install 7-Zip, or point at it:
`set GX3_7Z=C:\Program Files\7-Zip\7z.exe`. Encrypted containers are not
decrypted; export the folder from GX Works3 instead.

**PyPI blocked** — `pip install git+https://github.com/purinzan/gx3-cli-mcp.git`

**Something reads wrong** — `gx3-cli doctor --root ...` first, then
[open an issue](https://github.com/purinzan/gx3-cli-mcp/issues/new/choose).

## Documentation

- [User manual (JA)](docs/USER_MANUAL_JA.md) / [(EN)](docs/USER_MANUAL_EN.md)
- [Agent usage guide (JA)](docs/AGENT_USAGE_JA.md)
- [Security note (JA)](docs/SECURITY_JA.md) — local data handling, read-only MCP policy
- [Validation matrix (JA)](docs/VALIDATION_MATRIX.md) — verified scope and limits
- [CPU and unit configuration (JA)](docs/CONFIG_CPU_UNITS_JA.md) — CPU, units, device memory, labels
- [Network configuration (JA)](docs/CONFIG_NETWORK_JA.md) — IP, connection method, CC-Link, refresh areas
- [Intelligent module settings (JA)](docs/CONFIG_MODULES_JA.md) — MES, recording, data logging, EtherNet/IP, serial
- [Simple motion (JA)](docs/CONFIG_MOTION_JA.md) — RD77MS buffer access and what the .iut still hides
- [File usage guide (JA)](docs/FILE_USAGE_GUIDE_JA.md) — repository map
- [Related projects (JA)](docs/GITHUB_PROJECT_REVIEW_JA.md) — other GX Works3/MELSEC tools and what was taken from them
- [llms.txt](llms.txt) — machine-readable summary of what this is and is not

Agent skills: [existing project audit](skills/gx3-existing-project-audit/SKILL.md)
· [failure corpus](skills/gx3-failure-corpus/SKILL.md)

## License

**Source-available, not open source.** Full terms in
[LICENSE.txt](LICENSE.txt); this is a summary and the license text governs.

You **may** read the source and run it for evaluation and internal work,
including inside a company. You **may not** redistribute it, host it as a
service, or ship it in a paid product without written permission. There is no
licence key, activation or paid plan.

日本語: 社内での評価・業務利用は許諾されています。禁止しているのは再配布、
SaaS 提供、有償製品への組み込みです。実行にライセンスキーや課金は不要です。
商用利用の相談は Issue からどうぞ。

## Listed on Glama

Indexed as an MCP server, with per-tool scores for how well each tool
describes what it does. Useful as outside feedback on the tool surface —
the low scores there are the ones whose descriptions need work.

[![gx3-mcp-server on Glama](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp/badge)](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp)

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) · [AGENTS.md](AGENTS.md)
