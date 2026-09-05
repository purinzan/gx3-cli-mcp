# gx3-cli-mcp ユーザーマニュアル

`gx3-cli-mcp` は、GX Works3 (`.gx3`) プロジェクトをローカルで読み取り解析する CLI / MCP サーバーです。設備やプロジェクトを書き換えるためのツールではなく、デバイス、コメント、xref、ラダー根拠、通信境界を調べるためのツールです。

## できること

- デバイスがどこで使われているか調べる。
- コメント文字列から起動、停止、異常、手動、サイクルなどの候補を探す。
- コイルの成立条件をたどり、近くのラダー根拠を表示する。
- AI エージェントから MCP 経由で解析コマンドを呼び出す。
- duplicate coil、multi-writer、dead logic、interlock などの静的チェックを行う。
- 外部入力、HMI、通信、IP、リンク、タイミング候補を整理する。
- 明示指定した PLC に対して、現在のデバイス値を read-only で取得する。

## できないこと

- GX Works3 プロジェクトを MCP から書き換えること。
- 解析結果だけで設備動作や安全性を保証すること。
- すべての GX Works3 バージョン、PLC 機種、プログラム形式を完全保証すること。
- プロジェクトデータを自動でオンライン送信すること。
- `.gx3` ファイルから PLC 接続先を推測して、自動で設備に接続すること。

## インストール

```powershell
python -m pip install git+https://github.com/purinzan/gx3-cli-mcp.git
gx3-cli --version
gx3-mcp-server --version
```

このリリースでは MCP サーバーの利用に license token は不要です。

## 最初に実行すること

実プロジェクトでは、まず次の順番で確認します。

```powershell
gx3-cli doctor --root C:\path\to\project.gx3
gx3-cli index-lite build --root C:\path\to\project.gx3
gx3-cli xref build --root C:\path\to\project.gx3
```

`.gx3` を指定した場合は `.gx3_cache\<sha256>\` に展開し、そのローカルキャッシュを解析対象として使います。

## よく使うコマンド

| 目的 | コマンド |
|---|---|
| 解析できる状態か確認する | `gx3-cli doctor --root project.gx3` |
| 検索用 SQLite インデックスを作る | `gx3-cli index-lite build --root project.gx3` |
| xref DB を作る | `gx3-cli xref build --root project.gx3` |
| デバイス情報を見る | `gx3-cli query-device M100 --root project.gx3` |
| コメント語句から探す | `gx3-cli query-comment "起動" --root project.gx3` |
| 同義語も広げてコメント検索する | `gx3-cli query-comment alarm --root project.gx3 --expand-synonyms` |
| 外部入力、HMI、通信境界を見る | `gx3-cli query-external --root project.gx3` |
| サイクル、ステップ、状態系候補を見る | `gx3-cli query-cycle --root project.gx3` |
| 使用デバイス範囲と空き領域を見る | `gx3-cli device-map --root project.gx3 --types M,D,W --min-free 100` |
| writer/reader と POU/step を見る | `gx3-cli xref where-used M100 --root project.gx3` |
| スクリプト向け JSON を出す | `gx3-cli query-device M100 --root project.gx3 --json` |
| デバイス辞書を出力する | `gx3-cli device-dictionary --root project.gx3 --format json -o address-comment.json` |
| コイル成立条件を追う | `gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact` |
| 現在値を読む | `gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word` |
| 現在値をラダー根拠へ重ねる | `gx3-cli ladder-print MAIN --root project.gx3 --device M100 --live-values live.json` |
| GX 印刷風のラダー根拠を見る | `gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M100` |
| ビューア/画像生成向けのラダー座標を出す | `gx3-cli ladder-layout <PROGRAM_OR_LDDB> --root project.gx3 --format svg -o ladder.svg` |
| 2 つのコイルが同時 ON 可能か静的確認する | `gx3-cli interlock-check M100 M200 --root project.gx3` |
| 静的チェックを走らせる | `gx3-cli lint project.gx3` |
| サポート用の診断 ZIP を作る | `gx3-cli support-bundle --root project.gx3 -o support.zip` |

## 典型的な調査の流れ

「M100 がなぜ ON するか知りたい」場合:

```powershell
gx3-cli query-device M100 --root project.gx3
gx3-cli xref where-used M100 --root project.gx3
gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact --ja
gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M100
```

「起動に関係する信号を探したい」場合:

```powershell
gx3-cli query-comment "起動" --root project.gx3
gx3-cli query-cycle --root project.gx3
gx3-cli query-external --root project.gx3
```

「全体の静的リスクを見たい」場合:

```powershell
gx3-cli lint project.gx3
gx3-cli dead-logic --root project.gx3
gx3-cli reliability-report --root project.gx3 -o reliability.md
```

## 現在値の読み取り

`live-read` は MC Protocol/SLMP 3E binary の batch read で、明示指定した PLC から現在値を読みます。
`.gx3` から勝手に接続先を探してオンライン監視する機能ではありません。IP、ポート、デバイス、点数を毎回指定します。

```powershell
gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word --dry-run
gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word
gx3-cli live-read --ip <PLC_IP> --port 5000 --device M100 --count 16 --type bit --format json
```

JSON を保存して `ladder-print` に渡すと、GX 印刷風のラダー根拠に現在値を重ねられます。

```powershell
gx3-cli live-read --ip <PLC_IP> --port 5000 --device M100 --count 16 --type bit --format json -o live.json
gx3-cli ladder-print MAIN --root project.gx3 --device M100 --live-values live.json
gx3-cli ladder-print MAIN --root project.gx3 --device M100 --live-values live.json --format json -o rung-live.json
```

A 接点/B 接点には `live:ON pass`、`live:OFF block` のような注記を付けます。コイルは現在値を表示します。これは診断用の重ね合わせで、常時監視ループではありません。

## デバイス辞書

`device-dictionary` は GX3 のデバイスコメントを、外部MCP、OPC UA、Node-RED、現場メモに渡しやすい JSON/CSV にします。xref DB がある場合は read/write 回数、使用 POU、最初の step も付与します。

```powershell
gx3-cli xref build --root project.gx3
gx3-cli device-dictionary --root project.gx3 --format json -o address-comment.json
gx3-cli device-dictionary --root project.gx3 --format csv -o address-comment.csv
```

このコマンドは CLI 専用です。MCP からは公開せず、PLC 書き込み、run/stop、download、online edit は実装しません。

## MCP で AI から使う

MCP クライアント設定例:

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

PATH 上の console script を使える環境では、`docs/mcp_client_config_console_script.json` の設定も使えます。

MCP では typed tool を優先してください。一般的な検索は `gx3_run_command` から `query-device`、`query-comment`、`xref`、`index-lite` を呼び出せます。`synthetic-project` と `live-read` はローカル CLI 専用で、MCP からは実行できません。

## デモデータ

公開資料、動作確認、スクリーンショットには顧客プロジェクトを使わず、合成プロジェクトを使ってください。

```powershell
gx3-cli synthetic-project demo.gx3 --overwrite
gx3-cli doctor --root demo.gx3
gx3-cli trace-device M100 --root demo.gx3 --strict-logic --compact
```

## 意味差分の比較範囲

`gx3-cli semantic-diff old.gx3 new.gx3` は、同じブロックIDの回路を比較します。
配線、接点の実行属性（立上りなど）、未解釈のオペランドを含む変更も
`logic` として表示します。`logic` は動作に関わる可能性がある変更を意味し、
実際の動作差を証明したという意味ではありません。

`layout-only` として既定で隠すのは、外側の描画領域サイズだけが変わった場合です。
接点・コイルの座標や配線の引き直しは、論理的に同等な場合でも保守的に表示します。
ブロック内の座標は接続関係に影響するため、単なる配置変更とは断定しません。
`--show-layout-only` で描画領域サイズの変更も確認できます。

## 注意

- 解析結果は参考情報です。実設備への変更判断は GX Works3 と現場の検証で確認してください。
- 一部コマンドは CSV、Markdown、ZIP、SQLite DB などをローカルに生成します。
- `live-read` は実設備に TCP 接続します。現場ルール、PLC 設定、ネットワーク権限を確認してから使ってください。
- AI に出力を渡す場合は、社内ルールと機密情報の扱いを確認してください。
