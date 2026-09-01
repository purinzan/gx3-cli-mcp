# 対応状況と注意

このページは、現時点でどの範囲を確認済みとして扱うかを利用者向けにまとめたものです。未確認の範囲では、結果を過信せず、GX Works3 と現場の検証で確認してください。

| PLC | GX Works3 version | Program types | Sample source | parse gap rate | critical gaps | status | notes |
|---|---|---|---|---:|---:|---|---|
| iQ-R | TBD | LD | synthetic | 0 | 0 | synthetic-only | 顧客データ不使用 |
| iQ-F/FX5 | TBD | TBD | user verification needed | TBD | TBD | not verified | 実プロジェクトで確認してください |
| iQ-R | TBD | ST/FBD/SFC | user verification needed | TBD | TBD | not verified | LD 以外は過信しないでください |

## 現時点で言えること

- 合成プロジェクトを使った CLI/MCP 回帰テストは通っています。
- 実プロジェクトで見つかった解析失敗は `gx3-cli failure-corpus capture` で検体化し、`gx3-cli failure-corpus run` で形式検出 / schema / doctor / xref / ladder-print / 失敗コマンド再実行の回帰確認に回せます。
- FBD/ST/MIL など LDDB がない形式は、未対応/非ラダー形式として検出し、LD 専用チェックはスキップします。これは「解析成功」ではなく「形式を見分けて過信を避ける」ための扱いです。
- `gx3-cli graph` は既存の program map / device-flow 解析をまとめる可視化入口です。初期版は markdown / Mermaid / JSON 出力で、設備変更判断ではなく説明・レビュー補助として使ってください。
- `gx3-cli live-read` は MC Protocol/SLMP 3E binary の batch read を使う CLI-only 機能です。合成 TCP サーバーで frame/decode を検証していますが、実設備では PLC 設定、ポート、ネットワーク権限、現場ルールを確認してください。
- `gx3-cli ladder-print --live-values` は取得済み JSON をラダー根拠に重ねます。接点単位の `pass/block` 注記であり、PLC からの継続監視やスキャン同期した波形記録ではありません。
- MCP サーバーは project-read-only の allowlist を使っています。
- `.gx3`、`.gtx`、DB、CAB、CSV、PDF、鍵ファイルなどを公開成果物に含めないチェックを CI で実行しています。

## 利用時の注意

- すべての GX Works3 バージョンで完全対応しているわけではありません。
- すべての PLC 機種やプログラム種別で検証済みではありません。
- 解析結果は設備動作や安全性を保証しません。
- このツールは Mitsubishi Electric の公式または公認ツールではありません。

実プロジェクトで使う場合は、`gx3-cli reliability-report --root project.gx3 -o reliability.md` を保存し、parse gap や decoder coverage を確認してください。

解析できなかった `.gx3` は、修正前に以下のように保存してください。

```powershell
gx3-cli failure-corpus capture --root project.gx3 --case-id short-name --reason "what failed" --failed-command "gx3-cli xref build --root {root} --db {reports_dir}/xref.sqlite"
gx3-cli failure-corpus run
```

顧客データを含む検体は公開リポジトリへ含めず、ローカルまたは許可された非公開環境で管理してください。
