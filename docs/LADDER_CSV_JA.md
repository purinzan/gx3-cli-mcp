# ラダーCSVをrung-textで読む

```console
gx3-cli rung-text --csv ladder.csv
gx3-cli rung-text --csv ladder.csv --format json
gx3-cli rung-text --csv ladder.csv --device D20
```

GX Works3の「リスト形式」の命令CSVを専用パーサで読みます。
先頭のプロジェクト情報、タブ区切り、英語・日本語の列名、UTF-16 BOM／UTF-8／CP932、
次行に続くオペランドに対応します。通常のカンマ区切りも読めます。

命令列を読み取ってから、LD／AND／OR、B接点、比較、エッジ接点、INV／MEP／MEF、
ANB／ORBとMPS／MRD／MPPの独立したスタックを解釈します。
複数出力は各出力時点の条件を保持します。RISE／FALLはエッジ条件を表し、PLCの実行シミュレーションではありません。

MOV／BMOVなどは既存の命令引数定義で出力先を判定します。
表示には入力元や転送数も残します。JSONの`device`は出力先、`operands`は全引数です。
TOなどローカル出力先がない命令も表示します。

```text
ladder.csv:1  X0 -> MOV D0 D10
ladder.csv:4  X0 -> BMOV D1 D20 K4
```

未知の命令や未対応の制御フロー（MC、CALL、CJなど）は省略せず、`?`と理由を表示します。
以降の同一プログラムの条件も不確実として表示し、JSONには`diagnostic`を付けます。
スタックの不整合や必須オペランドの欠落を検出した場合はエラー終了します。
`--device`で絞り込んでも解析はプログラム全体に対して行います。

内部ラダーデータ列（`data`など）を含むCSVは従来のGX3ラダー解析を使用します。
命令CSVはプロジェクト全体のラベル定義、FB本体、機器設定の代替ではありません。
完全同等と判定するには、対応するGX Works3出力CSVと元のGX3で、命令順・全引数・出力先・条件を照合する必要があります。

## 確認できた範囲

`tests/test_gx3_ladder_csv.py`で再現できます。

- 合成101出力について、入力の全ON/OFF組合せ1,184件で、CSV解析・GX3ラダー解析・独立した回路仕様の真偽値が一致。
  B接点、入れ子のANB／ORB、MPS／MRD／MPPによる複数出力を含みます。
- 合成MOV／BMOV／FMOV／FROM／XCHの5ケースで、命令、全引数、出力先、条件がGX3解析と一致。
- 12命令の合成CSVで命令順・全引数を照合。UTF-16とCP932のタブ区切り、UTF-8のカンマ区切りで確認。
- 余分な引数付きMOV、引数付き分岐命令、ヘッダー外の値、重複列名、不正な文字列引用符はエラーとなり、黙って誤解釈しません。

## 未検証の範囲

- GX Works3が実際に出力したCSVと、その元のGX3全体との一致。
- FB、制御フロー、全命令の対応、およびGX Works3での再インポート。
- エッジ・タイマーなどを含むスキャン間の動作。上記真理値表は組合せ論理の検証です。

合成データの一致をGX Works3上の表示や設備動作の検証完了として扱いません。

形式と命令の参考:
- [GX Works3 Operating Manual（CSV export/import）](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh081215eng/sh081215engar.pdf)
- [QnACPU Programming Manual（ANB／ORB）](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080810eng/sh080810engb.pdf)
- [QSCPU Programming Manual（MPS／MRD／MPP）](https://www.mitsubishielectric.com/dl/fa/document/manual/plc/sh080628eng/sh080628engd.pdf)
