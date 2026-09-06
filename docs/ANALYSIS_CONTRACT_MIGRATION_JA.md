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
| scan-order: 同じ物理デバイスの前後writer | xref/member→occurrences_ofまたは全memberのgrouping→writers | 単体照会と全デバイス列挙の問いを分けた既存経路 | test_gx3_xref_reader_boundary.py | 未完。dead-logic/xrefと同一fixtureの結果比較、旧member欠損時のscopeを確認する |
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
