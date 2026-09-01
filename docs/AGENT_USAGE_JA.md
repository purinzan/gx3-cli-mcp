# エージェント向け利用ガイド

Codex、Claude Code、Cursor などの AI エージェントから使う場合は、ファイルを直接全文検索するのではなく、SQLite インデックス、xref、typed MCP tools を優先してください。GX3 プロジェクトには binary/database ファイルが多く、CLI が device/comment/POU/step/role の文脈を保持します。

## 回答規約（必須）

利用者への説明では、次の2点を例外なく守ってください。MCP 経由の場合は
`initialize` の `instructions` として同じ内容がクライアントに渡ります。

### 1. デバイスには必ずデバイスコメントを併記する

回答中にデバイスが現れるたび、リストでも表でも条件の言い換えでも、必ずコメントを
併記します。

- 良い: `M100（原点復帰要求）`、`X16（非常停止入力）`
- 悪い: `M100`、`X16`

デバイス番号だけを渡されても読む側には意味がなく、都度引き直させるならこのツールを
使う意味がありません。コメントは `xref where-used`、`trace-device`、`query-device`
の出力に含まれています。コメントが本当に存在しない場合は `M100（コメントなし）` と
書き、「調べていない」のか「無い」のかを曖昧にしないでください。

### 2. ラダーの説明は TXT のラダー図で行う

回路の結線を説明するときは、`ladder-print` の出力、または `ladder-diagram` の
`--format text`（既定）か `--format markdown` を、コードブロック内にそのまま
引用します。接点とコイルの桁が揃った状態で見せてください。

**`--format json` の出力や辞書のダンプを回路の説明として貼らないでください。**
JSON はエージェントが解釈するためのもので、人が読むためのものではありません。
PLC 技術者はラダーを読みます。ラダーを説明する JSON を見せるのは、ラダーそのものを
見せるより確実に劣ります。

出力が大きい場合は `ladder-print` の `--section`、`--pos-range`、`--device` で
議論中の回路だけに絞ります。プログラム全体を貼らないでください。

この規約は利用者に見せる内容に対するものです。内部処理で JSON を取得して解釈するのは
問題ありません。

## 基本方針

- 最初に `doctor` で解析対象と補助 DB の状態を確認する。
- 通常の検索は `index-lite`、`query-*`、`xref` を使う。
- 回答根拠は `trace-device`、`ladder-print`、`same-row`、`block-context` で確認する。
- 外部の live/OPC/SLMP ツールとつなぐ場合は、まず `device-dictionary` でデバイス辞書を出す。
- 生成された抽出フォルダに対する全文検索を主手段にしない。
- 不明な場合は `gx3-cli list` と `gx3-cli help <command> [subcommand]` を見る。
- ファイル別の役割は `docs/FILE_USAGE_GUIDE_JA.md` を見る。

## 初期化

```powershell
gx3-cli doctor --root project.gx3
gx3-cli index-lite build --root project.gx3
gx3-cli xref build --root project.gx3
gx3-cli device-dictionary --root project.gx3 --format json -o address-comment.json
```

`.gx3` を指定した場合は `.gx3_cache\<sha256>\` に展開されます。以後のコマンドも `--root project.gx3` のままで実行できます。

## SQLite ベースの検索

```powershell
gx3-cli query-device M100 --root project.gx3
gx3-cli query-comment "起動" --root project.gx3
gx3-cli query-external --root project.gx3
gx3-cli query-cycle --root project.gx3
gx3-cli device-map --root project.gx3 --types M,D,W --min-free 100
gx3-cli xref where-used M100 --root project.gx3
gx3-cli xref downstream M100 --root project.gx3
```

| 目的 | 優先コマンド |
|---|---|
| デバイスのコメント、役割、出現を見る | `query-device` |
| コメント語句からデバイス候補を探す | `query-comment` |
| 外部入力、HMI、通信境界を見る | `query-external` |
| サイクル、ステップ、状態系候補を見る | `query-cycle` |
| 空きデバイス範囲や使用密度を見る | `device-map` |
| writer/reader と POU/step を見る | `xref where-used` |
| 影響先をたどる | `xref downstream` |

## 根拠確認

```powershell
gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact
gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M100
gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M100 --live-values live.json --format json
gx3-cli same-row M100 --root project.gx3
gx3-cli block-context M100 --root project.gx3
gx3-cli signal-classify M100 --root project.gx3
```

回答では、検索結果だけで断定せず、成立条件や停止条件を `trace-device` と `ladder-print` の根拠で確認してください。

## 典型フロー

「自動運転の起動可能条件は？」のような質問:

```powershell
gx3-cli query-comment "自動運転" --root project.gx3
gx3-cli query-comment "起動" --root project.gx3
gx3-cli query-device M43 --root project.gx3
gx3-cli xref where-used M43 --root project.gx3
gx3-cli trace-device M43 --root project.gx3 --strict-logic --compact --ja
gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M43
```

「なぜ起動しない？」のような質問:

```powershell
gx3-cli trace-device M43 --root project.gx3 --strict-logic --compact --ja
gx3-cli same-row M43 --root project.gx3
gx3-cli block-context M43 --root project.gx3
gx3-cli xref downstream M43 --root project.gx3
```

## MCP での対応

MCP では typed tool を優先します。

| MCP tool | 対応 CLI |
|---|---|
| `gx3_trace_device` | `trace-device` |
| `gx3_xref_where_used` | `xref where-used` |
| `gx3_ladder_print` | `ladder-print` |
| `gx3_lint` | `lint` |
| `gx3_dead_logic` | `dead-logic` |
| `gx3_device_map` | `device-map` |
| `gx3_alarm_map` | `alarm-map` |
| `gx3_network_map` | `network-map` |
| `gx3_semantic_diff` | `semantic-diff` |
| `gx3_run_command` | project-read-only CLI escape hatch |

typed tool がない検索は `gx3_run_command` で `query-device`、`query-comment`、`query-external`、`query-cycle`、`index-lite`、`xref` を呼び出してください。`synthetic-project` はローカル CLI 専用で、MCP からは実行できません。
