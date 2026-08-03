# 検証マトリクス

公開 README や回答で、検証済み範囲を実際より大きく見せないための表です。

| PLC | GX Works3 version | Program types | Sample source | parse gap rate | critical gaps | status | notes |
|---|---|---|---|---:|---:|---|---|
| iQ-R | TBD | LD | synthetic | 0 | 0 | synthetic-only | 顧客データ不使用 |
| iQ-F/FX5 | TBD | TBD | human task | TBD | TBD | not verified | サンプル入手待ち |
| iQ-R | TBD | ST/FBD/SFC | human task | TBD | TBD | not verified | 誇大表示禁止 |

## 公開時に言えること

- 合成プロジェクトを使った CLI/MCP 回帰テストは通しています。
- MCP サーバーは project-read-only の allowlist だけを公開しています。
- `.gx3`、`.gtx`、DB、CAB、CSV、PDF、鍵ファイルなどを公開成果物に含めない release gate を実行しています。

## 公開時に言わないこと

- すべての GX Works3 バージョンで完全対応している。
- すべての PLC 機種やプログラム種別で検証済みである。
- 解析結果が設備動作や安全性を保証する。
- 公式ツールまたは Mitsubishi Electric 公認ツールである。

商用販売や現場適用の前には、自社外の GX3 プロジェクト、複数機種、複数 GX Works3 バージョンで `gx3-cli reliability-report` を保存し、検証範囲を更新してください。
