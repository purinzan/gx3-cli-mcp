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
  下記の用途別schema検証追加後は140回へ更新する。元の90回という実測記録は変更しない。
- traceはrows/comments/labelsを各1回まで。入力hash読込みと必要な意味論検証SQLは別に数える。
- 追加の必須入力・capability確認等による増加は黙って許容しない。必要性、計算量、
  比較値をPRに残して判断する。性能のために入力照合や正しさを弱めない。

SQL/loaderの構造的予算と正しさのテストはCIで確認し、揺れるwall時間の値だけで
自動的に正しさを判定しない。最終受入時には最新統合版で再測定し、WindowsのRSSや
大規模実案件など、未測定の範囲も明示する。

## 用途別schema検証の追加測定

`130e3b7a3d555b373825e6804105c987f0c0c70c`→`38bb67366a198f2fbd1252e3d4fe2b916805c253`。
同じ環境・固定検体・各3回の順次実行。全caseで入力hash一致。
表は中央値ms、warmは10照会合計。元の性能予算を超えたcase/phaseはなかった。

| 検体 | cold | warm | trace |
|---|---:|---:|---:|
| small | 42.20→43.28 | 45.85→46.60 | 7.53→7.50 |
| wide | 66.53→63.92 | 56.75→55.54 | 53.08→50.41 |
| deep | 60.29→61.61 | 45.13→47.11 | 66.99→66.52 |
| large-span | 114.87→110.45 | 45.02→47.25 | 7.15→7.42 |
| multi-pou | 65.11→66.29 | 47.55→50.32 | 67.25→67.35 |
| ld-st | 63.45→64.21 | 45.85→47.91 | 66.08→66.27 |

warm SQLは90→140。1照会につきschema queryが1回、SQLite内部のtable_infoが
必須4表に対して4回増える。プロジェクト行のscanではなく固定個数の構造検証であり、
入力指紋やdecoderが一致しても必須表・列を落としたDBを拒否するために必要。
SQL予算テストはこの理由を明記して2照会18→28へ更新した。

coldのSQLは全caseで+13、traceは+7（multi-pou 16→23、他13→20）。
loader回数とfingerprint読込みは前と同じ。Python peak中央値は増加最大約4KiB、
process peak RSS中央値の増加も最大約0.24MiBで、いずれも前述の予算内だった。
単独の速度改善を狙う変更ではなく、構造検証の追加コストが固定的であることの確認。

## dead-logic未書込み接点の照会比較

`eeca7ff23248240c8338aed0d6421c1d87577cd7`→`65876a002d5c96f419f3f48235497b2e794867ec`。
既存のfixture/measureを使い、固定6検体のprepare後にdead_logic.mainを
root・xref・lite・一時出力先指定で実行。各caseを別processで3回、前後を順次測定。
出力のStringIOのみreconfigureを受理するadapterを使用し、解析関数は差替えない。
入力hash全一致。中央値msはsmall 8.49→8.54、wide 22.45→22.22、deep 37.61→36.37、
large-span 7.74→7.68、multi-pou 37.04→37.71、ld-st 35.76→36.47。

SQLはsmall/deep 24→26、wide 24→24、large-span 26→28、multi-pou 27→29、
ld-st 25→27。counts_forが対象集合を一括照会する固定2文で、対象なしのwideは増分0。
open回数（3、multi-pouは6）、rows/comments各1回、入力hash読込みは変更なし。
時間・Python peak・累積RSSは既存予算内。これは当該6合成検体の照会コスト測定で、
全プロジェクト規模やWindows性能を保証しない。通常OUTの定数証明の完全性とも別。

## 値フローの検証・読取りsnapshot化

`eeca7ff23248240c8338aed0d6421c1d87577cd7`→`6d48bf687f6075ff7765f13ed8f889881cb6da57`。
同じv2 harnessでCLI同様のパス選択とbuild_flowを測定した。前版のprobeと再openも
測定に含む。固定6検体・各3回を同じ環境で順次実行し、全入力hashが一致。
中央値msはsmall 3.08→2.96、wide 21.53→21.42、deep 24.45→22.55、
large-span 15.35→16.70、multi-pou 24.90→23.48、ld-st 22.72→22.81。

値フローphaseのSQLite openは3→2（multi-pouは6→5）、SQLは9→11
（multi-pouは12→14）。BEGINとST能力差の確認が各1回増える。
rows/commentsは各1回、labelsは0回、入力hash読込み回数は変更なし。
Python peak中央値の増分は最大約60KiBで、値フローphaseの時間・Python peak・
累積RSSは前述の予算内。構造的なSQL/open予算も回帰テストへ追加した。
速度改善の一般化はしない。全入力の同時変更防止、Windows性能、実案件規模は未検証。

## ST未解析writerによる定数証明の停止

`f55838706e016ec5bb56e73a67aa4c7d1614236b`→`b0e6c9e8efb57bf391a01f368b3c2f012141ceed`。
同じv2 harness・固定6検体・各3回、前後を順次測定。入力hash全一致。
trace中央値msはsmall 7.70→8.04、wide 49.82→50.05、deep 70.04→66.06、
large-span 7.45→7.46、multi-pou 68.06→66.88、ld-st 66.12→66.16。
SQLは20→22（multi-pouは23→25）。読取りsnapshotのBEGINとST coverage照会が
各1回追加され、rows/comments/labels各1回、DB openと指紋読込みは変わらない。
Python peak中央値は全caseで420bytes増加。traceの時間・メモリは既存予算内。
SQLの固定増分を回帰テストにも明記した。STを解析できない場合に枝刈りを止める
正しさは別のIF文検体で確認し、この性能検体の単純代入STと混同しない。

## 構築中の入力照合と原子的な索引公開

`8edcee6b1e2aee714cb91a47b7710c4895c99276`→`76f5e4def7dd32d9e1536eebe1297e6f79f0272b`。
同じv2 harness・固定6検体・各3回の順次比較で全入力hash一致。
cold中央値msはsmall 47.58→54.16、wide 69.96→79.59、deep 65.32→75.04、
large-span 121.17→121.93、multi-pou 70.94→84.15、ld-st 68.94→80.76。
coldのSQLは全caseで+2（保存指紋を公開前に読む照会）。DB openとrows/comments/labels
読込みは不変。入力hash読込みは4→8（multi-pou16→32、ld-st8→16）で、両builderの
前後確認による必要な追加I/O。Python peak中央値の増分は約22–23KiB。
coldの時間・Python peak・累積RSSは既存予算内。大規模入力では追加hash I/Oの影響が
増えうるため、一般的な高速化とは扱わない。固定検体のhash読込み予算を回帰テストに追加。

### 構築契約の保存と旧索引の再利用拒否

`557a44df09eaddff42e3b20f3b3b0569c024eab3`→`fc828c2625dd266c3d316746907bad1d82ae85b3`。
同じv2 harness・固定6検体・各3回の順次比較、全入力hash一致。
warmは10照会の合計、表は中央値ms。

| 検体 | cold | warm | trace | value-flow |
|---|---:|---:|---:|---:|
| small | 54.37→52.86 | 46.31→46.09 | 8.38→8.39 | 2.97→3.00 |
| wide | 78.42→75.10 | 55.53→55.23 | 51.74→51.80 | 21.48→21.46 |
| deep | 73.66→72.21 | 46.73→46.73 | 67.67→67.84 | 22.64→22.60 |
| large-span | 129.61→121.08 | 50.77→46.95 | 9.05→8.50 | 17.14→16.35 |
| multi-pou | 83.34→79.23 | 49.31→49.23 | 68.84→69.02 | 23.23→23.32 |
| ld-st | 82.64→75.76 | 51.24→47.93 | 72.82→68.00 | 24.70→22.68 |

cold SQLは各builderの証跡INSERTとBEGIN/COMMITで合計+6。warm SQLは140→150、
traceは22→24（multi-pou25→27）、value-flowは11→12（multi-pou14→15）。
読取側はDBごとの固定1回のメタデータ照会を追加する。open/loader/入力hash読込みは
全case/phaseで不変、時間・Python peak・累積RSSも既存予算内。揺らぎを含む少数測定で、
新しい速度改善の主張ではない。SQL予算テストにも用途別の固定増分を反映した。

## cross-project where-used

baseline `0e80a77`と本修正。既存v2固定6検体を各2projectに作成し、明示的な保存リンク
1件から実where-used(text)を実行。各3回の新規プロセス、macOS/Python 3.14.4、入力hash一致。
初回はPath.as_uriのurllib.request importでsmall9.17→42.66msと予算超過したため、
SQLite用URIをurllib.parse.quoteによるパスエスケープに限定し、読取り専用を維持して再比較。
再比較cross中央値msはsmall9.41→10.52、wide10.19→11.33、deep9.34→10.83、
large-span9.27→11.19、multi-pou10.39→10.86、ld-st10.35→10.54。
cross SQL28→34はmember存在/集計/制約照会、cold SQLと全phaseの
open/rows/comments/labels/hash読込みは不変。cold/crossの時間・Python peak・累積RSSは
既定予算内。状態を保つ追加コストであり、高速化の主張ではない。
MCP通信・多数のリンク先・ネットワーク設備の性能を保証する測定ではない。

## xrefとindex-liteの照会契約

xrefの単一project照会を共通member readerへ移した比較（baseline `3868cac`、
変更はgx3_xref/gx3_xref_readの照会経路のみ、同一v2固定6検体・各3回）。
warm10照会の中央値msはsmall46.04→49.24、wide55.29→55.34、deep50.86→46.86、
large-span46.82→50.29、multi-pou49.67→49.45、ld-st48.95→47.92。
warm SQL150→170はページ/件数それぞれのmember表存在確認。cold/trace/flowのSQL、
全phaseのopen/rows/comments/labels/hash読込みは不変。全phaseの時間・Python peak・
累積RSSは既存予算内。旧DB低水準呼出しへの互換判定であり、性能改善とは主張しない。

### index-liteの観測scope表示

`3868cac`→`0e7c18d`、同じ既存v2固定6検体・各3回の新規プロセスで比較。
macOS/Python 3.14.4、既存measureのSQL/loader/tracemalloc/RSS計測を利用し、
cold prepare後に実query_device(JSON)を10回、実device_map(text)を1回実行した。
この測定はxref warmとは別の問い合わせであり、以前のwarm表と直接比較しない。

| 検体 | cold ms | lite query 10回 ms | map ms |
|---|---:|---:|---:|
| small | 73.09→70.30 | 11.65→12.13 | 1.11→1.12 |
| wide | 93.27→91.95 | 15.23→15.42 | 1.18→1.19 |
| deep | 88.91→89.23 | 12.11→12.58 | 1.19→1.21 |
| large-span | 136.38→146.69 | 11.86→13.30 | 1.10→1.14 |
| multi-pou | 96.58→96.75 | 14.73→15.12 | 1.46→1.45 |
| ld-st | 93.41→93.22 | 13.20→13.49 | 1.30→1.29 |

全検体の入力hash一致。各phaseでSQL/open/rows/comments/labels/hash読込みは不変。
query10回のSQLは150、mapは11。時間・Python peak・累積RSSは既定予算内。
プロセス起動・MCP通信の速度や全CLIの性能を保証する測定ではない。

## LD/FBDを含む定数証明scopeの確認

`8edcee6b1e2aee714cb91a47b7710c4895c99276`→`32bdda4967df42a6928b982bd02c749c5b0e3508`。
同じv2 harness・固定6検体・各3回、前後を順次測定。入力hash全一致。
trace中央値msはsmall 7.42→9.37、wide 50.43→53.30、deep 66.66→67.63、
large-span 7.37→8.28、multi-pou 67.90→68.90、ld-st 67.97→67.84。
SQLは22（multi-pou25）、open・loader・入力hash読込み回数およびPython peakの
中央値は変更なし。既存形式一覧のファイル列挙と供給済みLD行の状態走査は追加される。
traceの時間/Python peak/累積RSSは既存予算内だが、特に小検体では列挙コストが見える。
新たな大規模入力・Windowsの速度保証や一律の高速化とは扱わない。

## semantic-diff要約の重複解読

`8edcee6b1e2aee714cb91a47b7710c4895c99276`→`a6644aeaae0d759dcb2b428352af46b7bf5036be`。
既存branch_rung合成例のM100→M102変更をsummarize_changeで100回、各3反復。
同じPython/macOSで前後を順次実行し、入力文字列hash一致を確認。
デコーダ実呼出しは400→200（1要約4→2）、中央値57.57→29.64ms、Python peak
中央値8696→8865bytes。各版の命令と引数で同じ解読結果を再利用する。
これは要約関数だけの小測定で、project読込み・SQL・ファイルI/Oや全CLIの速度を
測ったものではない。未解析時の注記は別の実CLI/CSV回帰で確認する。
