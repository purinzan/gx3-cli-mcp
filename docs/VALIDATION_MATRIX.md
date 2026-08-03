# 対応状況と注意

このページは、現時点でどの範囲を確認済みとして扱うかを利用者向けにまとめたものです。未確認の範囲では、結果を過信せず、GX Works3 と現場の検証で確認してください。

| PLC | GX Works3 version | Program types | Sample source | parse gap rate | critical gaps | status | notes |
|---|---|---|---|---:|---:|---|---|
| iQ-R | TBD | LD | synthetic | 0 | 0 | synthetic-only | 顧客データ不使用 |
| iQ-F/FX5 | TBD | TBD | user verification needed | TBD | TBD | not verified | 実プロジェクトで確認してください |
| iQ-R | TBD | ST/FBD/SFC | user verification needed | TBD | TBD | not verified | LD 以外は過信しないでください |

## 現時点で言えること

- 合成プロジェクトを使った CLI/MCP 回帰テストは通っています。
- MCP サーバーは project-read-only の allowlist を使っています。
- `.gx3`、`.gtx`、DB、CAB、CSV、PDF、鍵ファイルなどを公開成果物に含めないチェックを CI で実行しています。

## 利用時の注意

- すべての GX Works3 バージョンで完全対応しているわけではありません。
- すべての PLC 機種やプログラム種別で検証済みではありません。
- 解析結果は設備動作や安全性を保証しません。
- このツールは Mitsubishi Electric の公式または公認ツールではありません。

実プロジェクトで使う場合は、`gx3-cli reliability-report --root project.gx3 -o reliability.md` を保存し、parse gap や decoder coverage を確認してください。
