# #153 解析契約の移行台帳

監査基準: main `acb08f2`、入力・外部境界の追記は`6dcc2b0`（2026-09-06）。これは完了宣言ではない。
対象は #153。#154 は #156 で修正・クローズ済み。#49 と #135 の完了判定は行わない。

「共通関数をimportしている」と「問いの入口から出力まで同じ契約を満たす」を区別する。
下表のテスト名は既存の検証箇所であり、残課題をすべて検証済みという意味ではない。
合成LDDBの検証をGX Works3による独立検証とは呼ばない。

## 問い合わせ単位の監査

| 問い合わせ | 正本→保存→現在のreader/出力 | 旧経路・今回の変更 | 検証箇所（tests/） | 状態と残課題 |
|---|---|---|---|---|
| xref where-used: 参照と分類別件数 | arg_decode→xref/range_len→xref.device_filter / rows_for_device / device_counts→text/JSON | interval条件がxref内に残る。#162でbothをread/write双方へ、raw件数を分離 | test_gx3_xref_results.py, test_gx3_xref_reader_boundary.py | 移行途中。#162は別PR。member readerとの照会契約一本化が未完 |
| xref downstream: 変更の到達先 | data_flow→xref.data_flow→gx3_reach.reach→CLI | 独自探索から共通reachへの移行は既存成果。#157でsource/destinationの独立span保存 | test_gx3_shared_reach.py, test_gx3_flow_in_xref.py | 既存共通経路。動的範囲とSTの制約がsummaryまで残るか追加受入が必要 |
| index-lite query-device: 名前と被覆参照 | LadderRow.occurrences→devices/device_usages/covered_ranges→covering_ranges→text/JSON | 名前だけでなく被覆範囲を別表示する既存経路 | test_gx3_block_range.py | 未完。ST能力差、dynamic/indexedを確定範囲としない保存・表示を監査する |
| index-lite device-map: 使用・外部境界一覧 | 同上→device_map | 全体一覧のprojectionは単一デバイス照会と問いが違うため保持 | test_gx3_block_range.py | 未完。query-deviceとのscope整合と物理範囲の共有契約を確認する |
| change-impact: 変更命令から影響先 | arg_decode.parse_row_occurrences→xref→reach→analysis/text/JSON | 既存のprepare / open_xref_dbと共通reachを利用 | test_gx3_shared_reach.py, test_gx3_flow_consumers.py | 既存共通経路。全段階の制約集約・範囲の上下限の縦断検証を追加する |
| dependency-flow: 値の出所 | data_flow→xref.data_flow→value_sources→build_flow | #157の独立spanに加え、実readerの検証と照会を同一SQLite snapshotへ接続 | test_gx3_flow_in_xref.py, test_gx3_dependency_flow_topology.py, test_gx3_flow_consumers.py | 部分移行。必要表欠損は未評価、ST/動的範囲はpartial。入力ファイル全体の途中変更検出とその他のdecode制約は未完 |
| graph device-flow | flow_db→dependency_flow.build_flow→graph出力 | 同じ値フローconsumerを呼ぶ既存adapter | test_gx3_flow_consumers.py | 上記snapshotとvalue_flow_analysisを共有。JSONに制約、図に状態と未知範囲を表示。全graphの完全性ではない |
| trace-device: 駆動条件と上流 | trace_stateのrows/labels/comments→出力別enable→provider→trace→text/JSON | #159でimport時関数差替えを除去。1回の入力ロードを明示注入 | test_gx3_topology_conditions.py, test_gx3_trace_state.py | 部分移行済み。外部CSV、未解析writer、実行条件を含む定数証明が未完。#161でxref拒否時にpruningだけ無効化 |
| dead-logic: 単一writerに基づく定数 | xref/member→counts_for + named OUT位置→propagate_constant_devices | #155でcovered writerとboth集計、#165で共通外部readerと未取得状態のsidecarを追加 | test_gx3_dead_logic_runs.py, test_gx3_xref_reader_boundary.py | 未完。外部DBの欠損/SQL失敗の空集合化は解消。未解析ST/実行保証を含む証明境界が残る |
| scan-order: 同じ物理デバイスの前後writer | xref/member→occurrences_ofまたは全memberのgrouping→writers | 単体照会と全デバイス列挙の問いを分けた既存経路 | test_gx3_xref_reader_boundary.py, test_gx3_dead_logic_runs.py | 同一実fixtureでxref/scan-order/定数判定のwriterを照合済み。単体/全件groupingのoccurrence ID一致、旧member欠損のCLI拒否も確認。ST/動的範囲と実行意味の全体scopeは引き続き未完 |
| alarm-map: reset/駆動候補 | xref/member→device_match→目的別SQL→レポート | lookupは共通、alarmのfilterは目的別として維持 | test_gx3_xref_reader_boundary.py | 既存共通経路。scope/未解釈・外部証拠不足が出力まで残るか受入が必要 |
| timing-chart: signalと条件 | link_mapのroot/xref→open_xref_db→device_match→signal selection | xref入力照合は既存経路。#161で指紋なしを拒否するため手書き選別fixtureにも実rootを使用 | test_gx3_timing_detect.py | 部分移行。link-map/外部証拠のprovenanceと未評価表示を監査する |
| lint: writer/occupancy/外部値 | rows + xref device_match + lite covered_ranges→目的別checks→summarise | #164でliteもopen_checked_liteへ。linkは複数project用として別契約 | test_gx3_lint_block_runs.py, test_gx3_analysis_state.py, test_gx3_same_input_across_artefacts.py | 部分移行。lite入力照合とhandle管理は統合済み。schema/capabilityと外部CSVの制約が残る |
| doctor --project-health: constant-chain等 | audit→LintContext/checks + dead_logic→health集約 | #164でlite入力照合、#165で既存handleの外部readerを利用し再openを除去 | test_gx3_analysis_state.py, test_gx3_same_input_across_artefacts.py, test_gx3_dead_logic_runs.py | 部分移行。schema/capability・定数証明が残る。#135の別受入PRはこの作業の統合対象外 |
| semantic-diff: 内容差分 | 生LDDB/config入力→parse_row_operations→差分 | raw DBは派生xrefのlookupではないため保持。差分と到達解析は問いが異なる | test_gx3_semantic_diff.py | 部分移行。変更要約は各版1回のdecodeを共有し、partialを画面/CSVに保持。raw差分は既存どおり保持。全言語coverageや未解釈operand意味の網羅は未完 |
| ladder-report: 参照集計と論理 | rows + open_xref_db→counts_for/occurrences_of→レポート | 既存range reader利用。#155のboth集計修正を共有 | test_gx3_xref_reader_boundary.py | 部分移行。未解析入力と論理制約がレポートの総括まで残るか確認する |
| MCP generic/typed | gx3_mcp_server→既存CLI subprocess→payload | CLIの意味論を再実装しない。offline/filesystem guardは保持 | test_gx3_mcp_server.py, test_gx3_mcp_offline_modes.py | #154のログ混在拒否は両経路検証済み。#153変更後のpayload/出力policy受入は継続 |
| explore | Context/workspace→既存CLIの連続実行 | 独自解析ではなく既存コマンドを組合せる | test_gx3_workspace.py | 未完。複数コマンドの入力同一性、cwdの外部CSV探索、部分結果集約を監査する |
| explain-snapshot / log snapshot | gx3_live_read→静的trace + 保存値→analysis | snapshotは実測値の一時点、static traceの実行保証ではない。#156でログ入力identityをdedup前に検証 | test_gx3_live_read.py, test_gx3_live_read_states.py | #154は完了。#153のscope/複数制約/入力変更検出は別途受入が必要 |

## 共通基盤の変更と残り

通常OUTの定数候補について、CALL invocationが静的にtrueへ確定しない行を除外する。
CALL不成立はOUTをfalseで実行するのではなく書込みをスキップするため、local enableが
falseでも保持/初期値をOFFと証明できない。実LDDB→builder→定数facts/trace context/CSVで
条件付き・SM401呼出しを除外し、SM400無条件呼出しと通常OUTは維持することを確認する。
派生定数からのCALL成立証明、初回実行前の値や全POU実行順の証明は未完。

cross-project where-usedのtext表示も、検証済みリンク先xrefを共通member照会へ接続。
範囲内、both、総件数、表示上限による実打切り、indexed/STの注意を表示し、guardの
該当KNOWN_UNMIGRATED_QUERYを除去した。2つの実合成LDDBと明示リンクfixtureで検証。
JSONとtextはcross_where_usedの同じ構造化結果を使い、元project未検出でも照会する。
リンク先欠損はnot_evaluated、target/occurrence両limitは実件数と比較して打切り判定。
従来のexit codeは元projectの該当有無を維持。link-map自体のprovenance、
全言語の参照完全性は引き続き未検証であり、全cross-project解析の証明とはしない。

Architecture guardはxref/link-map/readerのファイル丸ごと免除を廃止した。
execute呼出しのSQL文字列をASTで取り出し、ファイル・関数・SQL単位で例外を限定する。
commentの名前取得とraw exportを正当なprojectionとして許可し、cross-project照会は
KNOWN_UNMIGRATED_QUERIESで未移行と明記する（正当な設計扱いにはしない）。
別table・全件projection・member lookupのnegative testと、alias/連結文字列/f-stringの
迂回positive testを追加。変数から組み立てるSQLや全意味重複の証明は対象外で、
実builder→consumer回帰テストを引き続き必要とする。

xref単一プロジェクトのwhere-usedはページ・件数とも共通readerのcovered_queryを使う。
member表を使う実builder経路でD+のboth/第2語、BK+のsource/destination/範囲外を確認。
device_filterはreaderのinterval_filterへの互換facade。member表のない低水準呼出しは
従来のinterval動作を維持するが、現在のCLIの検証済み索引にはmember表が必要。
cross-project表示、exportの名前指定、comment取得などの目的別raw経路は別途監査する。

index-liteのdevice照会は、JSONにreference_scopeとavailability_analysisを追加し、
未検出を「未使用」と区別する。device-mapのfree_rangesは互換列名として残すが、
観測済みLD索引の隙間であって割当可能性の証明ではないことを直前に表示する。
STだけで使われるM105がLDのM100/M110間の隙間になる実builder→CLI検体で確認。
ST/inline-ST/FBD参照の収録、動的範囲の完全性や全consumer移行は未完であり、
この出力scopeの明示だけで完了扱いにしない。

xref/liteの明示buildはgx3_index_buildの一時SQLiteへ書込み、開始時・完了時の
入力指紋/ファイル状態と保存指紋を照合してから公開する。途中変更・例外で旧DBを
残し、入力/出力のWALや入力DBへの出力を拒否する。STDB内容だけの途中変更も検証。
全readerの実行中変更検出、外部CSVのprovenance、複数索引の同時公開は未完。

公開前照合後に両索引へbuild_contractを保存し、共通open_xref_db/open_existingと
workspaceで検証する。root省略時も構築契約は必須（入力同一性の照合にはrootが必要）。
旧索引は書換えず拒否し、prepareがOLD_BUILDの側だけ再構築する実経路を確認。
これは改ざん署名ではなく、接続を直接注入する内部API全体の保証でもない。

定数証明の共通入口は供給された全LD行の未解析状態と選択rootのFBDも確認する。
別ラングの未解析writerを候補行のexactで隠さない。ST/LD/FBD同時gapの3制約を
JSONへ残す実builder回帰を追加。全CLI consumerはrootを渡す。全入力の途中変更、
空デコードをexactとする既存ケース、実行順・初期値・外部証拠は別の残件。

通常OUTの定数証明は、保存されたST/inline-ST coverageがpartialなら停止する。
STDBの未対応IFに隠れたwriterを実builderで再現し、trace/dead-logic/Doctorに
decode段階の制約とsource位置を伝える。coverage表欠損も未評価でありSTなしではない。
対応済みSTの既知writerは既存counts_forで扱う。FBD/未解読LD、保持/初期値、
実行条件、外部CSVの証明範囲は別の未完項目として残す。

dead-logicのwriter未検出接点は、物理memberを含むcounts_forで照会し、
値不明のunwritten-contactとして表示する。未書込みからA/Bの常時値を推定しない。
外部境界が確認済みでも初期値・保持値・未解析writerの不存在証明にはならない。
通常OUT定数伝播における未解析writer/実行保証の受入は引き続き残る。

- #155（統合済み）: covered writer、bothのread/write集計。完全な実行時定数の証明ではない。
- #157（統合済み）: source/destination別span保存、xref decoder更新。全要素の一対一対応は主張しない。
- #158（台帳作成中に統合済み）: STDB/DM/module/config等の入力指紋依存、manifest版更新。
- #159（統合済み）: traceの明示provider、同じ入力の一度のロード、並行/入れ子/例外テスト。
- #160（統合済み）: 代表AnalysisStateに加えて全constraintsをJSON/再集約で保持。
- #161（統合済み）: root付き照会で指紋欠損/入力消失を拒否。検証失敗時close、optional traceの継続。
- #162（統合済み）: where-usedのbothを双方へ表示、raw件数と分類件数を分離。
- #164（統合済み）: lint/healthのlite照合、読取り専用と失敗時のhandle解放。
- #165（統合済み）: 外部分類の共通reader。空確認と取得不能を区別し、後者で定数判定をしない。

入力指紋だけではschema/capabilityの完全性を証明しない。読取り途中の入力変更、
外部CSVの由来、派生DBに必要tableが欠けた場合の判断は残課題。
これらの残課題を「整理済み」の名で対象外にしない。

## 性能と次の検証

### operation/output位置の保存境界

canonical parse_row_operationsのop_indexとelement_metaの座標、元LadderBlocks.idをxrefへ保存する。
旧parse_row_occurrences facadeでこれらを落としていたbuilderをcanonical操作単位へ接続。
同一ラング内の同じMOVを2か所へ置いた実LDDBで、decoder→xref→JSON/textの位置と引数を照合。
data-flowが元から持っていたoperation_indexも保存し、dependency-flow CLIのvalue edgeへ
元行・座標・引数のevidenceを渡す。旧版xref/必須column欠損の拒否、値フロー位置欠損時の
partialも検証。STにLD座標を捏造しない。reachの代表経路と全命令列挙は引き続き異なる問い合わせ。

### 同じwriterを条件・レポートまで伝える受入

実合成LDDBのBMOV+途中MOV、D+ both/上位語をxref・scan-order単体/全件・
ladder-report・alarm-map・timing-chartで照合。桁指定MOV+OUTの同一検体では
dead-logic/traceの定数除外とも比較する。member欠損のscan-order CLIはDBを変更せず拒否。

成立条件の出力選択は、ArgOcc.range_lenの既知write範囲をDeviceRefへ伝える。
DMOV/DMOVP/EDMOV/BMOVの途中語を名前不一致でFALSEにしない。read幅をwrite幅へ流用せず、
未知/修飾範囲は追加展開しない。共通デコーダの範囲を使い、命令ごとの幅表を追加しない。
alarm-map showのboth除外、timing readerのboth除外を修正し、後者の受信条件は実CLIの
CSV/Markdownへ出力する。読取り位置を出力に対応付けられない場合は、接点一覧fallbackと明記する。
全言語coverage・未知範囲の可能性・実行保証やlink-map由来の証明は、この受入では未完。

cold build / warm query / traceの6種の固定fixtureで、同一環境の比較を実施した。
詳細と制約・後続変更の予算は[性能基準](ANALYSIS_BENCHMARK_JA.md)に記録する。
SQL/loader回数、wall時間、Python peakとprocess peak RSSを区別する。WindowsのRSSは
未測定。少数の合成検体を全実案件の速度改善率やメモリ上限の保証として使わない。

後続移行の前後でも同じ手順を繰り返し、最終統合版で再測定する。
正しさは別に手で定めた期待値と照合する。
消費側の独自span換算・未検証DB lookupのguardは、既存reader境界テストを拡張する。
正当なprojectionは認め、既知の迂回を検出するpositive/negative testを用意する。

本表の未完項目が解消または根拠付きで範囲外と判定され、Issue本文の代表検体と
性能比較が揃うまでは、#153をクローズしない。

## refresh CSVからlintへの読取り状態

refresh CSVはread_refresh_areasで既知の範囲と読取り状態を一度に返す。
lint external-value-sourceはこの結果を使い、欠損/不正入力で依存checkを未評価にする。
実合成LDDB→xref→lint summary/CLI JSONとrequire-evaluatedで空確認と取得失敗を区別。
load_refresh_areasは既知範囲だけの互換projectionであり、空リストは不存在証明ではない。
従来のI/O/文字コードエラーは握り潰さない。他のCSV consumer、units CSV、保存済み境界の
入力由来・完全性の契約は未移行であり、このreader追加だけで全外部境界の受入とはしない。

## index-liteの外部ファイル依存と再構築

実buildが選択したrefresh/units CSVを明示依存として保存し、共通の
external_dependency_problemをreader/workspaceの再利用判断に使う。
未存在→存在、削除、同size/mtimeの内容差、構築途中の変更を合成project→実build→
実CLIで検証。再構築時は元のCSV指定を維持し、無関係なxrefは再利用する。
旧依存manifest欠損は拒否・再構築。曖昧な旧相対パスは明示buildを必要とする。
入力CSV自身への索引上書きも禁止する。範囲表示の手作りDBテストはprojection補助のまま残す。

依存ファイルの内容同一性と、CSVのproject由来・解析完全性は別である。
後者、直接CSVを読む他consumer、結果構築全体での複数artifact/source版の固定は未完。
