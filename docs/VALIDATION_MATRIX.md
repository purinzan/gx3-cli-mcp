# 対応状況と注意

このページは、現時点でどの範囲を確認済みとして扱うかを利用者向けにまとめたものです。未確認の範囲では、結果を過信せず、GX Works3 と現場の検証で確認してください。

| PLC | GX Works3 version | Program types | Sample source | parse gap rate | critical gaps | status | notes |
|---|---|---|---|---:|---:|---|---|
| iQ-R | TBD | LD | 実プロジェクト 51本 + 合成 | 0 | 0 | cross-checked（GX Works3 未照合） | 211,300 rung。下記「経路間照合」参照。公開物に顧客データは含めない |
| iQ-F/FX5 | TBD | TBD | user verification needed | TBD | TBD | not verified | 実プロジェクトで確認してください |
| iQ-R | TBD | ST/FBD/SFC | user verification needed | TBD | TBD | not verified | LD 以外は過信しないでください |

## 経路間照合（GX Works3 の代わりに置いている検証）

GX Works3 を正解とした照合は**まだ行えていません**。合成データだけでは、生成器と
解析器が同じ思い違いを共有した場合に気づけません。実際、印字と解析の両方が同じ
バグを抱えていた例が過去にありました。

そこで、同じバイト列を**独立に組み立てた2経路**で読み、食い違いを探しています。
`python scripts/cross_check_corpus.py <.gx3 のあるフォルダ>` で再実行できます。

実プロジェクト 51本での結果:

| 照合 | 対象 | 不一致 |
|---|---:|---:|
| 印字ラダー vs クロスリファレンス（デバイス集合） | 211,300 rung | 0 |
| 駆動デバイス vs デコーダの書き込み集合 | 379,912 判定 | 0 |
| SVG に描かれる要素 vs レイアウトが持つ要素 | 1,477,257 要素 | 0 |
| parse status | 211,300 rung | partial 0 / エラー 0 |

**これは「正しい」ことの証明ではありません。** 2つの読み方が一致することしか
示しておらず、両方が同じように誤っている可能性は残ります。この照合で実際に
見つかった不具合の例:

- ブロック転送の第3オペランドが、存在し得ないインデックスレジスタとして解読されていた
- ポインタオペランド（`CALL #P240 D13491`）が次のオペランドの型を奪い、実在するデバイスが消えていた
- 回路の継続コネクタ（`src`/`dst`）を印字側が演算として数え、以降のデバイス名がずれていた
- バッファメモリのビット位置を、印字は16進・解析は10進で綴っていた

いずれも合成データには現れない形で、2プロジェクトだけの検証でも出ませんでした。

## 現時点で言えること

- 合成プロジェクトを使った CLI/MCP 回帰テストは通っています。
- 実プロジェクトで見つかった解析失敗は `gx3-cli failure-corpus capture` で検体化し、`gx3-cli failure-corpus run` で形式検出 / schema / doctor / xref / ladder-print / 失敗コマンド再実行の回帰確認に回せます。
- FBD/ST/MIL など LDDB がない形式は、未対応/非ラダー形式として検出し、LD 専用チェックはスキップします。これは「解析成功」ではなく「形式を見分けて過信を避ける」ための扱いです。
- 三菱電機公式の e-learning / FB library サンプルには 7z/AES 形式の `.gx3` が含まれることを確認しています。CLI はコンテナ種別を検出し、外部の 7-Zip/7zz/bsdtar で展開を試みますが、暗号化されている場合は復号せず、GX Works3 で展開/エクスポートしたフォルダを入力してください。
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
