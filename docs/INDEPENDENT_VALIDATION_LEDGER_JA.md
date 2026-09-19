# GX Works3 独立照合台帳

この台帳は、Issue #49 のクローズ条件を機械的に確認するためのものです。
内部の合成テストや経路間照合だけではなく、GX Works3 側の画面、印字、公式資料など
外部の根拠と CLI の結果を1行で結びます。

現在の状態は次のコマンドで確認できます。

```bash
gx3-cli validation-ledger
gx3-cli validation-ledger --format json
```

`Closeable: yes` になるまでは、Issue #49 は閉じません。`Closeable: no` のときは、
出力された missing group が残件です。

## 必須8群

| group | 内容 |
|---|---|
| `series_parallel_contacts` | 直列/並列/A・B接点、接続変更と配置変更の区別 |
| `outputs_and_multiple_writers` | OUT/SET/RSTと複数writer |
| `timers_counters_edges` | タイマ/カウンタ/立上り・立下り |
| `execution_constraints` | MC/MCR・ジャンプ・CALLの実行制約 |
| `labels_and_scope` | グローバル/ローカルラベル、同名別scope |
| `indexed_and_bit_devices` | インデックス修飾・ワード内bit指定 |
| `word_width_and_ranges` | 固定語幅・ブロック範囲・境界外の1点 |
| `external_writers` | 通信リフレッシュ/外部writer、および解析対象外の領域表示 |

## 登録済み証拠

| group | 根拠 | CLI/CI側の確認 | 状態 |
|---|---|---|---|
| `outputs_and_multiple_writers` | 三菱電機 GX Works3 Operating Manual SH-081215ENG-AQ 印刷ページ351/353。SET、DTOP、TO の命令ブロックが命令+オペランドのセル幅で表示されることを確認。 | `tests/test_gx3_ladder_layout.py` の `test_instruction_width_uses_cells_instead_of_remaining_rail` が SET=2セル、MOV=3セル、TO=5セル、出力命令の右詰めと入力側配線を検査。 | checked |

この1件は、ラダーSVGの命令幅表示に限った外部根拠です。GX Works3プロジェクトの実行意味、
クロスリファレンス、スキャン後の値を検証した証拠ではありません。
