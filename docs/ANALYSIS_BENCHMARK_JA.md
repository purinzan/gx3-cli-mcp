# #153 合成検体の性能基準

これは正しさの検証やGX Works3との照合ではなく、後続のconsumer移行で余計な読込みや
計算量の増大を見つけるための基準。既存の意味論回帰テストを置き換えない。

## 再現手順

同じPython・OS・マシン上で、他の測定と同時に走らせず順番に実行する。
`benchmark_analysis_contract.py`は同じファイルを両checkoutに対して使う。

```sh
python scripts/benchmark_analysis_contract.py --checkout <before-checkout> --repeat 3 --warm-queries 10 > before.json
python scripts/benchmark_analysis_contract.py --checkout <after-checkout> --repeat 3 --warm-queries 10 > after.json
```

比較前にpython/platform/fixture_version/width/span/warm_queriesが一致し、各caseの
fixture_sha256が全反復・両checkoutで一致することを確認する。結果はローカルに保持し、
プロジェクトデータや生成した索引をコミットしない。

- 検体は測定対象のgeneratorを使わず、固定した中間形式から作る。
- small: SM401→M0→Y0。wide: 24個の接点から同じY0へのwriter。
- deep: 24個の内部リレーを経由する鎖。multi-pou: 同じ鎖を4個のLDDBへ分割。
- large-span: 4096語のBMOVと、その途中のD10001を読むMOV。
- ld-st: deepにSTソースを追加。全STの実行意味を対応済みとは主張しない。
- 各sampleを別process、別一時ディレクトリで実行。coldは**索引なしからの構築**であり、
  OSのページキャッシュを消した状態ではない。モジュールimport後から計時する。
- warmは既存xrefへの`where-used --limit -1 --json`を10回。毎回root照合も含む。
  想定writer件数と打切りなしを確認する。traceはstrict、depth=28、devices=32。
- SQLはsqlite trace callback、読込みは実際のloader関数呼出しを数える。
  fingerprint_file_readsは入力hashのための読込みで、rowsの復号回数とは別。
- wall時間には計測のオーバーヘッドを含む。メモリはphase別Python peakに加え、
  macOS/Linuxではprocess peak RSSも記録する。RSSはworker起動以来の累積最大値であり、
  phase単独の増分ではない。WindowsのRSSは未対応でnullを返す（0とは扱わない）。

## 2026-09-06の測定

基準は`14f73fa6d25ef1d260a4cc2f391c2e1ff3586178`、比較先は
`f3fec6d6aa1cbc1c37ecd589fec8de4131c29adb`（#165候補、#155〜#164の統合を含む）。
環境: Python 3.14.4、macOS 26.6.2 arm64。6検体×各3回、順次実行。
全caseの入力ハッシュが一致し、traceは全18sampleで打切りなし。

時間とPython peakは3回の中央値。warmの時間・SQL数は**10照会合計**。
SQL数は各反復で同じ。表の矢印は基準→比較先。

| 検体 | phase | 時間ms | SQL回数 | Python peak KiB |
|---|---|---:|---:|---:|
| small | cold | 41.27→44.97 | 109→110 | 1459→1449 |
| small | warm | 43.48→43.96 | 90→90 | 1468→1439 |
| small | trace | 8.06→7.11 | 10→13 | 1176→1177 |
| wide | cold | 66.00→65.48 | 308→309 | 1511→1501 |
| wide | warm | 57.54→55.04 | 90→90 | 1494→1408 |
| wide | trace | 56.80→49.91 | 10→13 | 1235→1238 |
| deep | cold | 59.49→63.22 | 293→294 | 1511→1501 |
| deep | warm | 44.12→44.47 | 90→90 | 1473→1416 |
| deep | trace | 70.98→65.50 | 10→13 | 1234→1237 |
| large-span | cold | 107.58→111.40 | 8311→8312 | 1931→1921 |
| large-span | warm | 43.78→44.77 | 90→90 | 1468→1438 |
| large-span | trace | 8.12→7.03 | 10→13 | 1177→1179 |
| multi-pou | cold | 63.59→73.99 | 308→309 | 1509→1499 |
| multi-pou | warm | 47.10→50.96 | 90→90 | 1470→1413 |
| multi-pou | trace | 72.82→71.01 | 16→16 | 1230→1234 |
| ld-st | cold | 62.12→66.42 | 307→308 | 1517→1508 |
| ld-st | warm | 44.80→45.87 | 90→90 | 1473→1416 |
| ld-st | trace | 69.75→65.86 | 10→13 | 1234→1238 |

process peak RSS（各sampleのtrace終了時、3回の中央値）も別に測定した。
モジュールimport、SQLite等を含むworker全体の指標で、Python peakとは単位・範囲が違う。

| 検体 | 基準MiB | 比較先MiB |
|---|---:|---:|
| small | 34.86 | 36.05 |
| wide | 35.09 | 36.53 |
| deep | 35.05 | 36.63 |
| large-span | 39.73 | 41.11 |
| multi-pou | 35.08 | 36.72 |
| ld-st | 35.06 | 36.64 |

全caseのrows/comments/labelsの呼出し回数:

| phase | 基準 | 比較先 |
|---|---|---|
| cold | 1 / 3 / 2 | 1 / 3 / 2 |
| warm（10照会） | 0 / 0 / 0 | 0 / 0 / 0 |
| trace | 2 / 3 / 2 | 1 / 1 / 1 |

fingerprint_file_readsは、単一LDDBでcold/warm/traceが4/10/2、multi-pouが16/40/8で
前後同じ。ld-stは4/10/2→8/20/4となった。以前の指紋がSTDBを含まなかったためで、
入力照合の不足を埋める追加読込み。warmがrowsを復号しないことは、入力ファイルを
一切読まないという意味ではない。

この測定ではtraceの時間は短縮したが、coldは最大約16%増加した。複数writerの正しい
照会や追加の入力照合を含むため、「全体が高速化した」「実案件で同じ率で改善する」
とは言わない。少数反復・小規模検体の結果であり、Windowsの速度は未測定。

## 後続変更の予算（この測定以降に適用）

過去の修正前に定めた予算ではない。過去の比較は上記の実測値をそのまま報告する。
以後、この比較先を基準として次の増加を調査対象にする。

- 同じphase/caseの中央値が`基準×1.20 + 2ms`を超えた場合は再測定・原因説明が必要。
- Python peakの中央値が`基準 + max(基準×0.10, 128KiB)`を超えた場合も原因説明が必要。
- process peak RSSを取得できる環境では、中央値が`基準×1.10 + 1MiB`を超えた場合も調査する。
- warmは全caseでrows/comments/labelsの再ロードを増やさず、SQLは10回で90回を基準とする。
- traceはrows/comments/labelsを各1回まで。入力hash読込みと必要な意味論検証SQLは別に数える。
- 追加の必須入力・capability確認等による増加は黙って許容しない。必要性、計算量、
  比較値をPRに残して判断する。性能のために入力照合や正しさを弱めない。

SQL/loaderの構造的予算と正しさのテストはCIで確認し、揺れるwall時間の値だけで
自動的に正しさを判定しない。最終受入時には最新統合版で再測定し、WindowsのRSSや
大規模実案件など、未測定の範囲も明示する。
