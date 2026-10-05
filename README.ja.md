# gx3-cli-mcp

[日本語](README.ja.md) · [English](README.md) · [简体中文](README.zh-CN.md) · [한국어](README.ko.md)

<!-- mcp-name: io.github.purinzan/gx3-cli-mcp -->

[![PyPI](https://img.shields.io/pypi/v/gx3-cli-mcp)](https://pypi.org/project/gx3-cli-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/gx3-cli-mcp)](https://pypi.org/project/gx3-cli-mcp/)
[![CI](https://github.com/purinzan/gx3-cli-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/purinzan/gx3-cli-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-source--available-blue)](LICENSE.txt)
[![gx3-cli-mcp MCP server](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp/badges/score.svg)](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp)

**GX Works3を開かずに、コイルがONにならない理由を調べます。**

手元のマシンにある三菱電機 MELSEC の GX Works3 `.gx3` プロジェクトを読み取り、
デバイスがどこで書き込まれるか、コイルがONになるために何が成立する必要があるか、
どの条件がPLCの外部から来るか、どの分岐が決して成立しないかをラダーから調べます。
読み取り専用で、元のプロジェクトには一切書き戻しません。

CLIとして使えるほか、同じ解析をstdio MCPサーバーでも提供します。
AIエージェントはバイナリファイルの内容を推測する代わりに、
索引化された事実に基づいて回答できます。

---

## インストール

```bash
pip install gx3-cli-mcp
```

Python 3.10以上が必要です。`gx3-cli` と `gx3-mcp-server` の2つの
コンソールスクリプトがインストールされます。

## 30秒で試す

既存のプロジェクトは不要です。まず生成します。

```bash
gx3-cli synthetic-project demo.gx3 --profile demo-line
gx3-cli guide --root demo.gx3
```

`guide` はプロジェクトを読み取り、そのプロジェクトで実行する価値のあるコマンドと
理由を示します。「コマンドが60個もあるけれど、どこから始めればいいのか」に答えます。

## プロジェクトで使う

```bash
gx3-cli doctor --root project.gx3        # does it read?
gx3-cli index-lite build --root project.gx3
gx3-cli xref build --root project.gx3
gx3-cli guide --root project.gx3         # what to run next
```

次に、知りたいことを調べます。

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

各コマンドはスクリプト向けの `--format json` と、画面への出力の代わりに
ファイルへ書き出す `-o FILE` に対応します。`gx3-cli --help` で全コマンドを
グループ別に確認できます。

`.gx3` を渡すと `.gx3_cache/<sha256>/` に展開し、そのコピーを解析します。

## わかること

| 質問 | コマンド |
|---|---|
| このコイルがOFFなのはなぜか？ | `trace-device`, `interlock-check` |
| このデバイスはどこで書き込まれ、読み取られるか？ | `xref where-used`, `xref downstream` |
| このプログラムは何をしているか？ | `rung-text`, `ladder-print`, `metrics` |
| ラダーのラングを図で見たい | `ladder-layout --format svg` |
| PLCの外部から何が来るか？ | `external-inputs`, `comm-refresh` |
| 決して成立しない条件は何か？ | `dead-logic` |
| 問題がありそうな箇所はどこか？ | `lint PROJECT`（重複コイル、複数の書き込み元、オペランド幅、型） |
| バージョン間で何が変わったか？ | `diff`, `semantic-diff` |
| プロジェクトを正しく読み取れたか？ | `roundtrip` |

## AIエージェントで使う

```json
{
  "mcpServers": {
    "gx3": { "command": "gx3-mcp-server" }
  }
}
```

クライアントがPATHからコンソールスクリプトを見つけられない場合は、
`"command": "python", "args": ["-m", "gx3cli.gx3_mcp_server"]` を使えます。
サーバーは読み取り専用の解析ツールと、制限付きのコマンド実行機能を公開します。

エージェントの操作方法は[エージェント利用ガイド（日本語）](docs/AGENT_USAGE_JA.md)を参照してください。

## 対応範囲と制限

手元のマシンにある `.gx3` のラダーデータを読み取り専用で解析します。

プロジェクトを編集したり、変更のためにPLCへ接続したり、GX Works3の代わりになったりはしません。
`live-read` はMC Protocol/SLMPで実機のデバイス値を読み取れますが、
CLI限定で、接続パラメータを明示した場合にのみ利用できます。

出力は参考情報です。実機に手を加える前に、GX Works3と自身の安全確認手順で検証してください。

プロジェクトの解析に失敗した場合、`gx3-cli failure-corpus capture` で
ローカルの回帰検証用サンプルにできます。データを外部へ送信することはありません。

## トラブルシューティング

**7z形式の `.gx3`** — 7-Zipをインストールするか、パスを指定します。
`set GX3_7Z=C:\Program Files\7-Zip\7z.exe`。暗号化されたコンテナは復号しません。
代わりにGX Works3からフォルダをエクスポートしてください。

**PyPIにアクセスできない** — `pip install git+https://github.com/purinzan/gx3-cli-mcp.git`

**読み取り結果がおかしい** — まず `gx3-cli doctor --root ...` を実行し、
その後[Issueを作成](https://github.com/purinzan/gx3-cli-mcp/issues/new/choose)してください。

## ドキュメント

- ユーザーマニュアル [日本語](docs/USER_MANUAL_JA.md) / [英語](docs/USER_MANUAL_EN.md) / [中国語](docs/USER_MANUAL_ZH.md)
- [エージェント利用ガイド（日本語）](docs/AGENT_USAGE_JA.md)
- [ラダー実務のヒント（日本語）](docs/LADDER_PRACTICAL_TIPS_JA.md) — 現場での変更・レビューに向けたヒント
- [セキュリティ上の注意（日本語）](docs/SECURITY_JA.md) — ローカルデータの扱い、読み取り専用MCPの方針
- [検証の作業管理](https://github.com/purinzan/gx3-cli-mcp/issues/202) — 検証タスクと受け入れ条件
- [独立検証台帳（日本語）](docs/INDEPENDENT_VALIDATION_LEDGER_JA.md) — Issue #49に対するGX Works3の検証根拠
- [Doctor受け入れ台帳（日本語）](docs/DOCTOR_ACCEPTANCE_JA.md) — Issue #135に対するプロジェクト健全性の受け入れ検証
- [GX Works3機能マトリクス（日本語）](docs/GX_WORKS3_FEATURE_MATRIX_JA.md) — 標準機能の対応範囲、不足、実装の優先順位
- [ファイル別利用ガイド（日本語）](docs/FILE_USAGE_GUIDE_JA.md) — リポジトリの構成
- [解析ベンチマーク（日本語）](docs/ANALYSIS_BENCHMARK_JA.md) — 合成データによる性能基準と測定の制限
- [レビューの問い（日本語）](docs/REVIEW_QUESTIONS_JA.md) — PRを開く前に変更に対して問う項目と、それぞれが見つけた不具合
- [関連プロジェクト（日本語）](docs/GITHUB_PROJECT_REVIEW_JA.md) — 他のGX Works3/MELSECツールと、そこから取り入れた内容
- [llms.txt](llms.txt) — このツールの役割と対象外の範囲を機械可読でまとめたもの

エージェント用スキル：[既存プロジェクト監査](skills/gx3-existing-project-audit/SKILL.md)
· [失敗コーパス](skills/gx3-failure-corpus/SKILL.md)

## 解析契約の移行

#153に対する問い合わせごとの現在の状況と、残っている受け入れ検証は
[解析契約の移行台帳](docs/ANALYSIS_CONTRACT_MIGRATION_JA.md)を参照してください。

## ライセンス

**ソースを閲覧できますが、オープンソースではありません。** 完全な条件は
[LICENSE.txt](LICENSE.txt)にあります。この節は要約であり、ライセンス本文が優先されます。

ソースの閲覧と、評価・社内業務を含む内部利用のための実行は**許可されています**。
書面による許可なしに再配布したり、サービスとして提供したり、有償製品に組み込んだりすることは
**許可されていません**。ライセンスキー、アクティベーション、有料プランはありません。
商用利用についてはIssueからご相談ください。

## Glamaへの掲載

MCPサーバーとして索引に登録され、各ツールの説明がその機能をどれだけ適切に伝えているか、
ツールごとのスコアが付いています。ツールの公開インターフェースに対する外部からの
フィードバックとして役立ち、スコアの低いツールは説明の改善が必要なものです。

[![gx3-mcp-server on Glama](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp/badges/card.svg)](https://glama.ai/mcp/servers/purinzan/gx3-cli-mcp)

貢献について：[CONTRIBUTING.md](CONTRIBUTING.md) · [AGENTS.md](AGENTS.md)

ラダーCSVを入力に使う場合は、`gx3-cli rung-text --csv ladder.csv` を使います。
検証済みの範囲と制限は[CSVパーサーガイド（日本語）](docs/LADDER_CSV_JA.md)を参照してください。
