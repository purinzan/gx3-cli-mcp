# ファイル別利用ガイド

このガイドは、初めて見る人や AI エージェントが「どのファイルを直接読むべきか」「どの CLI/MCP 入口を使うべきか」を判断するための索引です。通常はソースを直接読む前に `gx3-cli list`、`gx3-cli help <command>`、MCP `tools/list` を確認してください。

## まず使う入口

| 目的 | 入口 |
|---|---|
| インストール、MCP 登録 | `README.md`, `mcp_client_config.json`, `mcp_client_config_console_script.json` |
| エージェント運用 | `AGENT_USAGE_JA.md` |
| CLI 全体一覧 | `gx3-cli list` |
| コマンド詳細 | `gx3-cli help <command> [subcommand]` |
| MCP tool 一覧 | MCP `tools/list` |
| 開発者向けテスト | `python run_tests.py` |

## ルートファイル

| ファイル | 役割 |
|---|---|
| `README.md` | GitHub のトップ説明。インストール、MCP 設定、基本ワークフロー、全 Markdown へのリンクを持つ。 |
| `CONTRIBUTING.md` | クローンした利用者/開発者向けの Windows-first PR 手順。ソース問題を再現、検体化、修正、検証して PR する流れ。 |
| `AGENTS.md` | エージェント向けの最小常時指示。詳細な反復手順は `skills/` の各 `SKILL.md` に逃がす。 |
| `pyproject.toml` | Python パッケージ定義。`gx3-cli` と `gx3-mcp-server` の console script を定義する。 |
| `Dockerfile` | Glama の自動検査などで MCP サーバーをコンテナ起動し、`initialize` / `tools/list` に応答させる。実プロジェクトは同梱しない。 |
| `MANIFEST.in` | wheel/sdist 同梱ルール。顧客データや生成物を配布物に入れないための保険。 |
| `LICENSE.txt` | source-available proprietary の配布条件と免責。 |
| `CONTRIBUTING.md` | コントリビュータ向け入口。データ持ち込み禁止ルール、開発環境、CI と同じ検査、バグ報告の作法。PR 送付をもって現ライセンス下での利用許諾とみなす旨を記載（CLA なし）。 |
| `server.json` | 公式 MCP レジストリ（registry.modelcontextprotocol.io）への登録定義。`version` は pyproject.toml と揃える。README 冒頭の `mcp-name:` コメントが PyPI 側の所有証明になる。 |
| `llms.txt` | AI 向けの短い要約。何をする/しないツールかと、主要ドキュメントへのリンクを機械可読な形で置く。 |
| `.gitignore` | キャッシュ、解析結果、GX3/GTX/DB/文書データ、desktop shell 生成物を除外する。 |
| `.gitattributes` | 公開 repo の改行コードを LF に揃え、binary artifact を明示する。 |
| `run_tests.py` | 標準テスト入口。 |
| `test_gx3_device_name.py` | デバイス名の 16 進/10 進の整形・解釈・往復を検査する。 |
| `test_gx3_device_name_is_the_only_source.py` | 16 進デバイス型の定義が `gx3_device_name.py` 以外に複製されていないことを検査する。DX/DY が 10 進で出る #16 の再発を防ぐ。 |
| `test_gx3_device_types_contacts.py` | 命名テーブルにあるデバイス型が接点としてヘッダ解析を通ることを検査する。LT/LST/LC/LZ/RD/FX/FY/FD の接点が消えて幻の命令になる退行を防ぐ。 |
| `gx3_device_name.py` | デバイス名の唯一の定義。X/Y/B/W などを 16 進、それ以外を 10 進として整形・解釈する。表示も入力もここを通す。 |
| `test_gx3_cli_root_passthrough.py` | --root で指定したプロジェクトが、--root オプションを持たず環境変数から root を読むコマンド（used-devices / hmi-build-info / extended-instructions）まで届くことを検査する。別プロジェクトを黙って解析して成功報告する退行を防ぐ。 |
| `test_gx3_indexed_buffer_memory.py` | インデックス修飾付きバッファメモリ（U96\G196608Z0）の Zs トークンが消費され、次オペランドの型を奪わないことを検査する。BMOV の D48200Z2 が存在しないインデックスレジスタ Z48200 として記録される退行を防ぐ。 |
| `test_gx3_xref_decoder_version.py` | xref DB にデコーダ版が刻まれ、別バージョンが書いた DB は読み込み時に拒否されることを検査する。デコーダ修正後も古い DB が lint/trace/dead-logic に黙って読まれる退行を防ぐ。 |
| `test_gx3_used_devices_source.py` | used-devices のデバイスが共有デコーダ由来であり、16進デバイス型がGX Works3 と同じ綴りで出力されることを検査する。型トークンと数値の位置対応で実在しないデバイスを報告する退行を防ぐ。 |
| `test_gx3_operand_parse_is_shared.py` | ladder-print と gx3_arg_decode がヘッダトークンの歩進を自前で持たず gx3_operand_parse を共有していること、両者が同じオペランドを見ていることを検査する。同じ解読バグが二重に入る退行を防ぐ。 |
| `test_gx3_block_range.py` | ブロック命令が書き込む範囲（BMOV ... K4 は4デバイス）が occurrence に記録され、範囲内のデバイスを where-used で検索できることを検査する。名前が出ないデバイスが「該当なし」と返る退行を防ぐ。 |
| `test_gx3_audit_bundle.py` | audit が別の作業ディレクトリからでも全ステップを完走し、lint の CSV が bundle 内に出力されることを検査する。相対パスの解決先がずれて lint が失敗する退行を防ぐ。 |
| `test_gx3_synthetic_demo_line.py` | demo-line フィクスチャの規模、セクション名の可読性、デバイス名の 16 進整合を検査する。 |
| `ci.yml` | GitHub Actions。Windows / Linux / macOS 上で install、console script 確認、test、release gate を実行し、wheel build は Windows で行う。 |
| `parser-gap.yml` | 解析に失敗したときの issue フォーム。実データを貼らせないための注意と確認チェックを先頭に置いている。失敗時のエラーメッセージからこのフォームへ直接リンクする。 |
| `config.yml` | issue 作成画面の導線。バグ以外は Discussions、初見の人は紹介記事へ送る。 |
| `release.yml` | GitHub Actions。`v*` タグで wheel と sdist を build し、release gate と tag/version 一致確認を通してから Trusted Publishing で PyPI へ公開する。 |

## docs

| ファイル | 役割 |
|---|---|
| `USER_MANUAL_JA.md` | 人間向けの基本操作、MCP 設定、主要コマンド。 |
| `USER_MANUAL_EN.md` | 英語圏利用者向けのインストール、最初の3コマンド、主要ワークフロー。 |
| `AGENT_USAGE_JA.md` | Codex/Claude Code/Cursor 向けの SQLite-first 運用手順。 |
| `FILE_USAGE_GUIDE_JA.md` | この索引。 |
| `SECURITY_JA.md` | ローカルデータ処理、read-only MCP 方針、利用時の注意。 |
| `VALIDATION_MATRIX.md` | 検証済み範囲と誇大表示を避けるための表。 |
| `GITHUB_PROJECT_REVIEW_JA.md` | 関連する GX Works3/GX3/MELSEC GitHub プロジェクトの調査結果と設計上の取り込み候補。 |
| `mcp_client_config.json` | `python -m gx3cli.gx3_mcp_server` で起動する MCP 設定例。 |
| `mcp_client_config_console_script.json` | PATH 上の `gx3-mcp-server` を直接起動する MCP 設定例。 |

## scripts

| ファイル | 役割 |
|---|---|
| `release_gate.py` | 開発者/メンテナ向けの混入チェック。GX3/GTX/DB/CAB/CSV/PDF/鍵ファイル、ユーザーパス、IP、外部指定の禁止語を検出する。 |

## skills

| ファイル | 役割 |
|---|---|
| `skills/gx3-existing-project-audit/SKILL.md` | 既存 `.gx3` の read-only 解析手順。doctor/index/xref/survey/trace/ladder-print の使い分け。 |
| `skills/gx3-existing-project-audit/agents/openai.yaml` | OpenAI/Codex 側の表示メタデータ。 |
| `skills/gx3-failure-corpus/SKILL.md` | 解析失敗を `.gx3_failures` の回帰検体へ昇格する手順。 |
| `skills/gx3-failure-corpus/agents/openai.yaml` | OpenAI/Codex 側の表示メタデータ。 |

## gx3cli の公開入口

| ファイル | CLI | MCP | 使いどころ |
|---|---|---|---|
| `gx3_mcp_server.py` | `gx3-mcp-server` | MCP 本体 | AI クライアントから GX3 解析 tool を呼ぶ。 |
| `gx3_cli.py` | `gx3-cli` | `gx3_run_command` | CLI dispatcher、help/list、query 系ラッパー。 |
| `gx3_doctor.py` | `doctor` | `gx3_run_command` | 解析対象、index、xref DB、link-map の状態確認。 |
| `gx3_index_lite.py` | `index-lite`, `query-device`, `query-comment`, `query-external`, `query-cycle`, `device-map` | `gx3_run_command`, `gx3_device_map` | SQLite-first 検索の中核。 |
| `gx3_xref.py` | `xref` | `gx3_xref_where_used`, `gx3_run_command` | writer/reader、下流影響、CSV export。 |
| `gx3_data_flow.py` | `data-flow` | `gx3_data_flow`, `gx3_run_command` | 命令引数単位の source→destination value-flow。未知/部分解析は unresolved として保持する。 |
| `trace_gx3_device_dependencies.py` | `trace-device` | `gx3_trace_device` | デバイス成立条件、停止条件、上流依存を追う。 |
| `gx3_ladder_print.py` | `ladder-print` | `gx3_ladder_print` | GX Works3 印刷風のラダー根拠を出す。 |
| `gx3_device_dictionary.py` | `device-dictionary` | `gx3_run_command` | GX3 コメントと xref 使用状況から address-comment JSON/CSV を出力する。 |
| `gx3_tools.py` | `tools`, `inspect`, `sourceinfo`, `version`, `ip-map`, `scon-map`, `query-instruction`, `diff`, `block-context`, `same-row`, `signal-classify`, `impact-add-nc`, `state-chain` | `gx3_run_command` | 補助調査、近傍根拠、状態/命令検索。 |
| `gx3_lint.py` | `lint` | `gx3_lint` | duplicate coils、multi-writer、alarm、unused/comment、math/type checks。 |
| `gx3_dead_logic.py` | `dead-logic` | `gx3_dead_logic` | 常時 OFF、未読 coil/word、SET without RST。 |
| `gx3_interlock.py` | `interlock-check` | `gx3_interlock_check` | 2 コイルの同時成立可能性を静的 SAT で確認する。 |
| `gx3_alarm_map.py` | `alarm-map` | `gx3_alarm_map` | アラーム/異常の trigger、hold、reset 整理。 |
| `gx3_network_map.py` | `network-map` | `gx3_network_map` | IP、CC-Link、SCON、安全/通信関係の集約。 |
| `gx3_link_map.py` | `link-map` | `gx3_run_command` | 複数プロジェクト間の通信デバイスリンク。 |
| `gx3_external_inputs.py` | `external-inputs` | `gx3_run_command` | 外部入力、端子、HMI、通信境界の抽出。 |
| `extract_hmi_build_info.py` | `hmi-build-info` | `gx3_run_command` | HMI/操作、単動/手動出力候補。 |
| `extract_comm_refresh_areas.py` | `comm-refresh` | `gx3_run_command` | 通信ユニットとリフレッシュ範囲。 |
| `gx3_comm_detail.py` | `comm-detail` | `gx3_run_command` | 詳細通信候補と AJ65BT-R2N 設定。 |
| `gx3_live_read.py` | `live-read` | CLI only | 明示指定した PLC から MC Protocol/SLMP 3E binary で現在値を read-only 取得する。`--dry-run` / `--explain-frame` は接続せず送信予定 frame を表示する。 |
| `gx3_w3pa_probe.py` | `w3pa-probe` | `gx3_run_command` | `.w3pa` パラメータ文字列、modules、IP、device candidates。 |
| `gtx_probe.py` | `gtx-probe` | `gx3_run_command` | GT Designer3 `.gtx` HMI project containers。 |
| `gx3_dm_probe.py` | `dm-probe` | `gx3_run_command` | `_DM.db` の初期値/保持値。 |
| `gx3_label_probe.py` | `label-probe` | `gx3_run_command` | LabelData/SourceInfo labels、comments、arrays、device assignments。 |
| `gx3_mildb_probe.py` | `mildb-probe` | `gx3_run_command` | `_MilDB.db` と MIL device references。 |
| `gx3_motion_rd77.py` | `motion-rd77` | `gx3_run_command` | RD77 simple motion buffer/G label。 |
| `gx3_iut_probe.py` | `iut-probe` | `gx3_run_command` | RD77 `.iut` motion-setting container strings and paths。 |
| `gx3_convertdata_probe.py` | `convertdata` | `gx3_run_command` | ConvertData qpg / PouPCode record layout。 |
| `gx3_program_map.py` | `program-map` | `gx3_run_command` | LDDB から POU 名、program file、step 対応を作る。 |
| `gx3_exec_config.py` | `exec-config` | `gx3_run_command` | program execution order、POU groups、unit configuration。 |
| `gx3_scan_order.py` | `scan-order` | `gx3_run_command` | writer/reader の scan-order stale-read 候補。 |
| `gx3_timing_chart.py` | `timing-chart` | `gx3_run_command` | link-map/xref から handoff timing draft を生成する。 |
| `gx3_dependency_flow.py` | `dependency-flow` | `gx3_run_command` | upstream coil dependency の Mermaid flow。 |
| `gx3_ladder_diagram.py` | `ladder-diagram` | `gx3_run_command` | 対象 device の driver rows を ASCII ladder 化。 |
| `gx3_graph.py` | `graph` | `gx3_run_command` | structure/device-flow を markdown/mermaid/json で出す統一 graph 入口。 |
| `gx3_format.py` | internal | internal | LDDB/FBDDB/STDB/MilDB などの形式インベントリを共通化する。 |
| `gx3_matiec_export.py` | `matiec-st` | `gx3_run_command` | enable logic を MATIEC Structured Text 化。 |
| `gx3_semantic_diff.py` | `semantic-diff` | `gx3_semantic_diff` | 2 プロジェクトの rung-level diff。 |
| `review_gx3_project.py` | `review` | `gx3_run_command` | 静的レビュー CSV 群。 |
| `gx3_project_survey.py` | `project-survey` | `gx3_run_command` | プロジェクト調査パッケージ。 |
| `gx3_audit.py` | `audit` | `gx3_run_command` | doctor/index/xref/lint/dead-logic をまとめる。 |
| `gx3_support_bundle.py` | `support-bundle` | `gx3_run_command` | ラダー本文を含めない診断 ZIP。 |
| `gx3_failure_corpus.py` | `failure-corpus` | CLI only | 解析失敗した GX3 を回帰検体として保存し、形式検出/schema/doctor/xref/ladder-print/失敗コマンド再実行を回す。 |
| `gx3_reliability_report.py` | `reliability-report` | `gx3_run_command` | parse gap/decoder coverage の 1 ページ報告。 |
| `gx3_coverage.py` | `coverage`, `instruction-coverage`, `device-coverage` | `gx3_run_command` | 命令/デバイス知識の coverage。 |
| `extract_gx3_extended_instruction_knowledge.py` | `extended-instructions` | `gx3_run_command` | 拡張命令/デバイス使用知識の抽出。 |
| `extract_used_devices_without_comments.py` | `used-devices` | `gx3_run_command` | コメントなし使用デバイスの抽出。 |
| `analyze_gx3_intermediate_parse_gaps.py` | `parse-gaps` | `gx3_run_command` | 中間表現の parse gap 集計。 |
| `gx3_synthetic_project.py` | `synthetic-project` | CLI only | 非機密の合成 GX3 fixture 生成。MCP からは不可。 |
| `gx3_ai_context.py` | `ai-context`, `evidence-bundle` | `gx3_run_command` | AI レビュー/引き継ぎ用の根拠 bundle。 |

## gx3cli 内部ライブラリ

| ファイル | 役割 |
|---|---|
| `gx3_intermediate_tool.py` | LadderBlocks.data を解析し、中間表現/operation model を作る中核 parser。 |
| `gx3_ladder_logic.py` | 接点/coil/AND/OR/MC zone を論理式にする共通ロジック。 |
| `gx3_mc_zones.py` | MC/MCR master-control zone の再構成。 |
| `gx3_operand_parse.py` | ヘッダの型トークン列と要素の値を突き合わせてオペランドを読む共通処理。ladder-print（表示文字列）と gx3_arg_decode（occurrence）が同じ歩進を共有し、同じ解読バグが二重に入るのを防ぐ。 |
| `gx3_arg_decode.py` | ラダー命令引数の共通 decoder。gx3_operand_parse の結果を occurrence と read/write 分類に変換する。 |
| `gx3_data_flow.py` | 命令の read/write 意味付けから保守的な引数単位のデータフロー辺を生成する。 |
| `gx3_guide.py` | プロジェクトの中身 (ラダー/ラベル/コメント/パラメータの有無、索引の構築状況) を見て、実行すべきコマンドを理由つきで挙げる。62 コマンドの入口。 |
| `test_gx3_guide.py` | プロジェクトに無いものを勧めないこと、および `--help` に全コマンドが出ることを検査する。 |
| `gx3_metrics.py` | プログラムごとの規模と、論理が集中している回路を出す。未知のプロジェクトをどこから読むかの入口。 |
| `test_gx3_metrics.py` | 回路の分岐数を経路数として数え、読めなかった回路を黙って除外しないことを検査する。 |
| `gx3_rung_text.py` | プログラムを 1 回路 1 行 (条件 -> 駆動デバイス) で出力する。`ladder-print` の罫線出力に対し、読解と MCP 経由のエージェント利用向けの圧縮表現。 |
| `test_gx3_rung_text.py` | 回路が「条件 -> 出力」として読め、印刷レイアウトより桁違いに小さいことを検査する。 |
| `gx3_label_resolve.py` | `LabelData.db` を読み、ラダーの `_lid/<LabelID>/<行>` をラベル名・クラス・割付デバイスへ解決する。 |
| `test_gx3_label_resolve.py` | ラベル方式のプログラムがラベル名として解読されることを検査する。xref が空になり下流全部が沈黙する退行を防ぐ。 |
| `gx3_instruction_table.py` | 命令の書込み先オペランド位置。マニュアルのオペランド表 (SH-081226 ほか) から生成した数値データで、手編集しない。 |
| `test_gx3_instruction_table.py` | 書込み先オペランド位置がマニュアルどおりであることを検査する。手書き表が個数オペランドを書込み先と誤判定していた退行を防ぐ。 |
| `test_gx3_exec_condition.py` | 命令の実行条件 (レベル/立上り/立下り) がマニュアルどおりであることを検査する。`endswith("P")` による推測が EXP/NOP を誤判定し `+P_U` 等を見落としていた退行を防ぐ。 |
| `gx3_project_paths.py` | `.gx3` 展開、root 解決、出力/cache path。 |
| `gx3_redaction.py` | support/AI context 向けのマスク処理。 |
| `gx3_version.py` | package/CLI version。 |
| `__init__.py` | Python package marker。 |

## tests

| ファイル | 検証対象 |
|---|---|
| `test_gx3_mcp_server.py` | MCP initialize/tools/list、変更系コマンド拒否。 |
| `test_gx3_data_flow.py` | MOV/DMOV/BMOV、read-modify-write、未知/部分解析の value-flow 回帰。 |
| `test_gx3_dependency_flow_topology.py` | dependency-flow が暗黙 horizontal gap、左母線推定、driver sink 越しの逆流を依存に混ぜないこと。 |
| `test_gx3_cli_issue_polish.py` | JSON 出力、同義語検索、カテゴリ別ヘルプ、`--no-color`。 |
| `test_gx3_lint.py` | lint rule 群。 |
| `test_gx3_ladder_logic.py` | ラダー論理生成。 |
| `test_gx3_ladder_print_filter.py` | ladder-print の section/device filter。 |
| `test_gx3_mc_interlock.py` | MC zone と interlock SAT。 |
| `test_gx3_intermediate_tool_regression.py` | 中間表現 operation model の回帰。 |
| `test_gx3_parse_gaps_zero.py` | 合成プロジェクトの parse gap 0 確認。 |
| `test_gx3_timing_detect.py` | timing detect。 |
| `test_gx3_version.py` | Python 3.10 で `tomllib` がない場合の version fallback。 |
| `test_gx3_project_paths_convertdata.py` | ConvertData の通常レイアウト、backslash 保持レイアウト、FBDDB root 検出。 |
| `test_gtx_probe.py` | GTX probe。 |
| `test_gx3_failure_corpus.py` | 失敗検体の capture/run ループ。 |
| `test_gx3_doctor_next_steps.py` | doctor の WARN/ERROR が次の一手を出すこと。 |
| `test_gx3_format_graph.py` | 形式インベントリ、graph、lint check listing。 |
| `test_gx3_live_read.py` | MC Protocol/SLMP 3E binary read frame と応答 decode。 |
| `test_docs_navigation.py` | README から全 Markdown へ辿れること、このガイドが全ファイルを索引すること。 |

## 迷ったときの選び方

| 状況 | 最初に使う |
|---|---|
| デバイス名が分かっている | `query-device` -> `xref where-used` -> `trace-device` |
| コメント語句しか分からない | `query-comment` -> `query-device` |
| 条件を説明したい | `trace-device` -> `ladder-print` |
| 起動しない原因を探す | `trace-device` -> `same-row` -> `block-context` |
| 通信や HMI が絡む | `query-external` -> `external-inputs` -> `network-map` |
| プロジェクト全体を棚卸ししたい | `audit` -> `project-survey` -> `reliability-report` |
| 解析失敗を再発防止したい | `failure-corpus capture` -> `failure-corpus run` |
| サポートへ渡す | `support-bundle` |
