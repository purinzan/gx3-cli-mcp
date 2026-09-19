# GX Works3 独立照合台帳

この台帳は、Issue #49 のクローズ条件を機械的に確認するためのものです。
内部の合成テストや経路間照合だけではなく、GX Works3 側の画面、印字、公式資料など
外部の根拠と CLI の結果を1行で結びます。

現在の状態は次のコマンドで確認できます。

```bash
gx3-cli validation-ledger
gx3-cli validation-ledger --format json
```

`Closeable: yes` は、必須8群について「外部根拠」と「公開CIで回る非機密テスト」の
対応が台帳に揃っていることを示します。これは静的解析の対応範囲を閉じるための判定であり、
実設備の安全性やライブ値の一致を保証するものではありません。

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
| `series_parallel_contacts` | GX Works3 Operating Manual SH-081215ENG-AQ のラダー編集/モニタ表示。直列・並列の接続、a/b接点の区別を外部根拠にする。 | `tests/test_gx3_topology_conditions.py` と `tests/test_gx3_change_impact.py` が AND/OR、b接点、接点変更、配置のみ変更の扱いを検査。 | checked |
| `outputs_and_multiple_writers` | 三菱電機 GX Works3 Operating Manual SH-081215ENG-AQ 印刷ページ351/353。SET、DTOP、TO の命令ブロックが命令+オペランドのセル幅で表示されることを確認。 | `tests/test_gx3_ladder_layout.py` の `test_instruction_width_uses_cells_instead_of_remaining_rail` が SET=2セル、MOV=3セル、TO=5セル、出力命令の右詰めと入力側配線を検査。 | checked |
| `timers_counters_edges` | iQ-R命令マニュアルとGX Works3表示上のタイマ/カウンタ/パルス接点の扱い。 | `tests/test_gx3_trace_state.py` が SET/RST、PLS/PLF、timer/counter、条件付き実行を `checked` ではなく制約として出すことを検査。 | checked |
| `execution_constraints` | iQ-R命令マニュアルの MC/MCR、ジャンプ、CALL の実行制約。 | `tests/test_gx3_mc_interlock.py` と `tests/test_gx3_trace_state.py` が MC zone、jump、CALL、未解決実行制約を検査。 | checked |
| `labels_and_scope` | GX Works3のラベル表/スコープモデル。 | `tests/test_gx3_label_scope.py` と `tests/test_gx3_label_resolve.py` が同名別scope、LabelID、曖昧名の扱いを検査。 | checked |
| `indexed_and_bit_devices` | iQ-R命令/デバイス表記のインデックス修飾、ビット/桁指定表記。 | `tests/test_gx3_indexed_buffer_memory.py`、`tests/test_gx3_xref_results.py`、`tests/test_gx3_native_csv.py` が indexed operand、indexed warning、bit-designated device を検査。 | checked |
| `word_width_and_ranges` | iQ-R命令マニュアルのブロック転送・倍長命令・範囲幅。 | `tests/test_gx3_block_range.py`、`tests/test_gx3_covered_lookup.py`、`tests/test_gx3_flow_in_xref.py`、`tests/test_gx3_xref_reader_boundary.py` が BMOV/DMOV/EDMOV/DFMOV/BTOW/WTOB と範囲境界を検査。 | checked |
| `external_writers` | iQ-R Ethernet/通信リフレッシュとGX Works3プロジェクトパラメータの外部writerモデル。 | `tests/test_gx3_flow_consumers.py` と `tests/test_gx3_input_identity.py` が refresh除外、refresh証拠欠落、外部CSV入力指紋を検査。 | checked |

## 判定の限界

この台帳は、公開リポジトリに置ける非機密テストと、公式資料・画面/印字由来の根拠を
結び付けます。顧客プロジェクト、実機PLC、GX Works3のライブモニタ値、スキャン後の
実測値は含めません。実設備で使う場合は、引き続き GX Works3 と現場の安全プロセスで
確認してください。
