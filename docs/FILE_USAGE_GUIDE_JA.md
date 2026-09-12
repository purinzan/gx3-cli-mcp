# ファイル別利用ガイド

`gx3cli/gx3_index_build.py`: xref/lite構築前後の入力確認と一時SQLiteからの原子的な公開。

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
| `glama.json` | Glama（MCP サーバーディレクトリ）向けのメタデータ。掲載時の保守者情報を示す。 |
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
| `test_gx3_xref_results.py` | 検索上限による省略・総件数・インデックス修飾の警告が、テキストとJSONに残ることを検査する。 |
| `test_gx3_used_devices_source.py` | used-devices のデバイスが共有デコーダ由来であり、16進デバイス型がGX Works3 と同じ綴りで出力されることを検査する。型トークンと数値の位置対応で実在しないデバイスを報告する退行を防ぐ。 |
| `test_gx3_operand_parse_is_shared.py` | ladder-print と gx3_arg_decode がヘッダトークンの歩進を自前で持たず gx3_operand_parse を共有していること、両者が同じオペランドを見ていることを検査する。同じ解読バグが二重に入る退行を防ぐ。 |
| `test_gx3_coverage_gaps.py` | 桁指定の範囲展開、lint が範囲を参照できること、パス起因の失敗をパーサ不具合として報告しないこと、ラダーのみのプロジェクトを非対応形式と呼ばないことを検査する。 |
| `test_gx3_rung_text_driver.py` | rung-text が「駆動デバイス」として名指すのが実際に書き込まれるデバイスであることを検査する。同一デバイスが複数オペランドに現れる算術命令で、ソースを駆動先と報告する退行を防ぐ。 |
| `test_gx3_dead_logic_runs.py` | ブロック命令や桁指定の範囲として読まれるデバイスを dead-logic が「読まれない」と報告しないことを検査する。書き込みのみの範囲を読み扱いしない点も併せて検査する。 |
| `test_gx3_project_config.py` | project-config が「読めない」と「そもそも無い」を区別して報告することを検査する。デバイスメモリ不在・MESジョブがプロジェクト外・暗号化本体・DataDefault が空である旨を、それぞれ理由付きで出す。 |
| `test_gx3_module_params.py` | 記述子テーブル（Prm3=257 の署名）と設定値テーブルの判別、既定値のままの行を設定として報告しないこと、チャンネル/軸ごとの行が取れることを検査する。 |
| `test_gx3_ladder_layout_svg.py` | SVG が全ラングを同じ幅（印字と同じ12セル格子）で描き、立上がり/立下がり接点・b接点・INV/ME/MEF を記号として描き分けることを検査する。パルス接点が通常接点と同じ絵になる退行を防ぐ。 |
| `test_gx3_device_code_table.py` | コメントDBの DevCode 表が単一の出所であること、実データで確認済みのC=70 / ST=74 が入っていること、未確認の LT/LC/LST/LZ が入っていないことを検査する。 |
| `test_gx3_operand_alignment.py` | ポインタオペランド（CALL #P240）と継続コネクタ（src/dst）が後続オペランドの型を奪わないことを検査する。実データ51本の経路間照合で見つかった2件の退行を防ぐ。 |
| `test_gx3_display_fidelity.py` | 合成LD行を通して文字列定数、命令幅、ZZ添字、間接指定の表示を検査する。実行意味やxrefの間接アドレス解決は対象外。 |
| `test_gx3_input_bus.py` | 合成LDで共通入力配線、INV/MEP/MEFの入力式と分岐内の適用範囲、trace・dependency・snapshotへの伝達を検査する。 |
| `docs/REVIEW_QUESTIONS_JA.md` | 変更を出す前にコードへ問う8つの質問。実際にバグを出した問いだけを載せ、それぞれに再現例を添える。テストではなくコード自身の契約と突き合わせるための手順で、AGENTS.md と CONTRIBUTING.md から必読として参照される。 |
| `docs/ANALYSIS_CONTRACT_MIGRATION_JA.md` | #153の問い合わせ別移行台帳。正本・保存・reader・検証箇所と未完項目を区別し、部分修正を親Issue全体の完了と誤認しないための記録。 |
| `docs/ANALYSIS_BENCHMARK_JA.md` | #153の固定合成検体によるcold/warm/trace比較。測定値、環境、計測の限界、後続変更の調査予算。 |
| `benchmark_analysis_contract.py` | 別checkoutを固定検体で測る開発用script。SQL数、loader回数、wall時間、Python peakを別processで計測する。 |
| `test_analysis_benchmark.py` | 検体の再現性、実CLI/traceを通した測定値、warmの再復号なし・traceの各loader1回を検査する。 |
| `test_gx3_covered_lookup.py` | 同一の検体を xref と index-lite の両方に通し、範囲に覆われたデバイスがどちらでも見つかること、覆われたデバイスを「独立した occurrence」として水増ししないこと、範囲外は従来どおり未検出であることを検査する。 |
| `test_gx3_health_scoring.py` | Doctor の点数が「調べていない」を「問題なし」として出さないことを検査する。検査が1つも評価されなければ全次元が `--` かつ `NOT ASSESSED`、その次元を担う検査が欠けていれば数字を出さない。評価数が少ない実行が多い実行より高い点にならないこと、link-range（補助検査）の欠如では次元を空にしないことも併せて検査する。 |
| `test_gx3_topology_conditions.py` | 並列接点が OR として報告されること（`&` にしない）、直列＋分岐の形が保たれること、b接点が保たれること、出力ごとに条件が分かれること、配線を読めない場合は「接点の一覧であり配線は未読」と明示すること、timing-chart も同じ経路を通ることを検査する。 |
| `test_gx3_live_read_states.py` | #147 `live-read` の2つのオフラインモード（`explain` / `replay`）がコマンドから実際に到達できること、フラグ先頭の従来呼び出しが変わらないことを検査する。さらにスナップショットに値が無いデバイスが `NO_MEASUREMENT` として構築されること（この状態はこれまで構築箇所がゼロだった）、値が揃えば `checked` になること、駆動行が無ければ `not_evaluated` になること、JSON をまたいだ状態が読み戻せること、知らない形が `checked` に化けないことを検査する。 |
| `test_gx3_mcp_offline_modes.py` | `live-read` の許可判定がコマンド名ではなくモード語で行われることを検査する。素の `live-read` と `--ip` 付きが拒否されること、モード語の後ろにネットワーク専用フラグを混ぜても拒否されること、`live-read` が read-only コマンド一覧に入っていないこと、オフラインの3モードが許可され、でっち上げのモード語は許可されないこと、型付きツール（`gx3_explain_snapshot` / `gx3_replay_capture`）がどんな引数でも先頭にモード語を置くこと、スナップショットが答えられない範囲が説明文に載っていること、コマンド一覧にモードが載ることを検査する。 |
| `test_gx3_bundle_names.py` | support-bundle が顧客名・設備名をフォルダ/ファイル名から漏らさないことを、合成ツリーから**実際にZIPを生成して**エントリ名と全テキストpayloadで検査する。拡張子・サイズ・階層は診断情報として残ること、対応表を含めないことも確認する。 |
| `test_gx3_input_trust.py` | #97 複数プロジェクトがあるとき自動選択せず候補を挙げて停止すること（`--help`・`--version`・明示 root・位置引数指定は従来どおり動く）、#88 xref を生 connect せず入力指紋を検証すること、#90 LabelData の「無い」「読めない」「知らないスキーマ」を区別することを検査する。 |
| `test_gx3_xref_reader_boundary.py` | 新しい読み手が生の `where device=?` を書いたら落ちること、member 索引が範囲の全デバイスを持つこと、範囲途中を問えば命令が見つかり範囲外では見つからないこと、member 表が無いDBでも落ちずに縮退すること、長さ不明の範囲は先頭1件のみになることを検査する。さらに #96 の語幅（DMOV=2語、EDMOV=4語）が occurrence に載ること、ブロック件数と語幅を二重に掛けないこと、デコーダ版が後退していないことを検査する。 |
| `test_gx3_lint_block_runs.py` | ブロック命令が書く範囲の途中を別の行が書いた場合に多重書込として検出されること、範囲の1つ外は検出しないこと、1つの重複が**デバイス数だけの件数**に膨れず範囲1件にまとまること、書き手が異なる隣接デバイスは別件のままであることを検査する。 |
| `test_gx3_empty_answers.py` | プロジェクトに存在しないデバイスを問うたときに、「存在するが誰も書かない」と同じ答えを返さないこと（trace-device / ladder-report）、命令の書込も駆動行として数えること、逆に本当に駆動の無いデバイスは通常どおり答えることを検査する。 |
| `test_docs_review_questions.py` | 8つの問いが AGENTS.md と CONTRIBUTING.md から参照されていること、文書が載せているコマンドが実際に動くこと、各問いに再現例が添えられていることを検査する。文言は固定しない（新しい型のバグが出たら書き換わるため）。 |
| `test_gx3_unread_and_provenance.py` | 読めない形式のプログラム（FBD/ST）がラダーの本数に紛れて消えず `未対応` として先に表示されること、ブロック転送が書いたデバイスを `graph` が「値の出所」と断言しないこと、逆に本当に何も書かないデバイスは出所のままであることを検査する。 |
| `test_gx3_shared_reach.py` | #76 の再現3件（BMOV の範囲途中を経由した到達、ラング順序の入替え、別プロジェクトの xref 指定）と、change-impact と downstream が同じグラフを歩くことを検査する。さらに、`BMOV D300 D400 K4` → `MOV D401 D900` の検体を**無加工**で通し、D300〜D303 のどこから探索しても D900 へ到達すること、FMOV の読出は展開されないこと、書込範囲が探索中も引き継がれることを検査する。 |
| `test_gx3_change_impact.py` | 接点変更が駆動先まで到達すること、コメントのみ・キャンバス寸法のみの変更に影響一覧を付けないこと、要素の移動は保守的に論理変更として扱うこと、解釈できないラングを含む場合に到達先が不完全である旨を出すことを検査する。 |
| `test_gx3_flow_consumers.py` | `graph --type device-flow` が値の出所を辿れること（ワードデバイスは以前まったく辿れなかった）、`lint` の multi-writer が分かる範囲で値の出所を併記すること、辺が無い場合は従来どおりの表示に戻ることを検査する。さらに `external-value-source` チェックが、ラダーが書かないワードからの転送を「外部との境界」として挙げること、リフレッシュエリアのデバイスを除外すること、リフレッシュ情報が無い場合は件数を水増しせず未評価とすることを検査する。 |
| `test_gx3_flow_in_xref.py` | xref に値の流れ（source→destination）が格納され、MOV が1本の有向辺、BMOV が範囲と語数、二項演算が read-modify-write として記録されること、未知命令に辺を作らないこと、`downstream` が転送（via OPCODE）と同一ラング上の共起（same-rung）を区別することを検査する。 |
| `test_gx3_shared_row_analysis.py` | CSV・lint・描画・条件解析の共有読取りと、行変更・ラベル変更・部分解析の保持を検査する。 |
| `test_gx3_shared_xref_decode.py` | xrefとdata-flowの共有読取りを検査。回路ごとの復号が1回であること、辺の全列の一致、partialの保持と再読取りの不在を確認する。 |
| `test_gx3_query_statistics.py` | 索引・xref が問い合わせ統計を持って作られること、統計の無い既存DBは作り直さずその場で修復されること、遅かった問い合わせでプランナが `access` ではなく `device` の索引を選ぶことを検査する。dead-logic が75秒かかった退行を防ぐ。 |
| `test_gx3_logic_budget.py` | 論理式の展開に上限があり、越えたときに黙って短い条件を返さず `[TOO LARGE]` として数えられること、分岐の同一判定が子の識別子から作られることを検査する。実プロジェクトの1ラングが3355万ノード・146秒に膨らんだ退行を防ぐ。 |
| `test_gx3_ladder_report.py` | 各デバイスにプロジェクト全体の件数が併記されること、描画本数が不足するとき打切りと表示されること、外部リソースを読まない単一ファイルであること、現在値を知っているかのような表示をしないことを検査する。 |
| `test_gx3_explore.py` | 4つの入口が揃っていること、実行できなかった項目・時間切れの項目が黙って抜けず終了コードにも出ること、相対パスの `--root` が届くことを検査する。 |
| `test_gx3_workspace.py` | 索引が「どこにあるか」ではなく「どの入力から作られたか」で判定されること、別ディレクトリで作った索引を見つけて重複作成しないこと、編集後・旧版の索引を再利用しないことを検査する。 |
| `test_gx3_same_input_across_artefacts.py` | xref・調査パッケージ・通信CSV の3成果物が同じ入力指紋を持ち、途中で編集された場合は一致しなくなることを検査する。「論理とコメントと通信設定が同じ入力からか」を確認できる状態を保つ。 |
| `test_gx3_identity_reach.py` | index-lite と project-survey も入力指紋を記録し、別プロジェクトの索引を拒否すること、root 未指定の呼び出しは従来通り動くことを検査する。 |
| `test_gx3_input_identity.py` | 入力指紋の依存と内容変化、別入力xrefの拒否、root付き照会での指紋欠損/入力消失の拒否を検査する。rootなしの単独DB読取りは維持し、optional traceはxref拒否時もpruningなしで継続する。 |
| `test_gx3_step_not_pos.py` | rung-text の位置表示が内部 pos ではなく GX Works3 のステップ番号であること、ステップ不明時は pos と明示すること、印字ラダーの表示と一致することを検査する。 |
| `test_gx3_trace_state.py` | trace-device の打切り・未解釈が共通語彙で結論の直前に出ること、完了した追跡では何も出ないこと、未解釈が打切りより優先されることを検査する。さらに SET/RST・PLS/PLF・タイマ/カウンタ・条件付きジャンプを跨いだ追跡が `checked` を返さず semantics 段階として報告すること、通常のコイルには何も付けないこと、複数の制約があるとき勝者以外の理由も残ることを検査する。 |
| `test_gx3_analysis_state.py` | 実行できなかった検査が「検出0件」として正常扱いされないこと、理由と次の手順が summary に残ること、--require-evaluated で落とせることを検査する。さらに、checked 以外の結果は5段階（発見／復元／配線／実行意味／追跡）のどれで止まったかを必ず名乗ることを検査する。 |
| `test_gx3_block_range.py` | ブロック命令が書き込む範囲（BMOV ... K4 は4デバイス）が occurrence に記録され、範囲内のデバイスを where-used で検索できることを検査する。名前が出ないデバイスが「該当なし」と返る退行を防ぐ。BMOV は読出側も同じ範囲を持ち、FMOV は持たないこと（命令ごとの規則）も検査する。 |
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
| `LADDER_PRACTICAL_TIPS_JA.md` | ラダー変更・レビュー時の実務的な作法と、AI解析で誤解しやすいポイントを簡潔にまとめる。 |
| `FILE_USAGE_GUIDE_JA.md` | この索引。 |
| `SECURITY_JA.md` | ローカルデータ処理、read-only MCP 方針、利用時の注意。 |
| `VALIDATION_MATRIX.md` | 検証済み範囲と誇大表示を避けるための表。 |
| `GX_WORKS3_FEATURE_MATRIX_JA.md` | GX Works3 標準機能との対応状況、機能差、採用/保留/対象外、実装優先順位を整理した比較表。 |
| `GITHUB_PROJECT_REVIEW_JA.md` | 関連する GX Works3/GX3/MELSEC GitHub プロジェクトの調査結果と設計上の取り込み候補。 |
| `mcp_client_config.json` | `python -m gx3cli.gx3_mcp_server` で起動する MCP 設定例。 |
| `mcp_client_config_console_script.json` | PATH 上の `gx3-mcp-server` を直接起動する MCP 設定例。 |

## scripts

| ファイル | 役割 |
|---|---|
| `cross_check_corpus.py` | 実プロジェクト群を独立した2経路で読み、食い違う箇所を報告する。印字 vs xref、駆動デバイス vs 書き込み集合、SVG の要素網羅、parse status を突き合わせる。GX Works3 が無い環境で取れる最良の検証。 |
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
| `gx3_mcp_server.py` | `gx3-mcp-server` | MCP 本体 | AI クライアントから GX3 解析 tool を呼ぶ。プロジェクトを書き換えるコマンドとローカル生成系は公開しない。`live-read` はコマンド名ではなく**モード語**で判定し、接続を開かない `explain` / `replay` / `modes` だけを許可する（`gx3_explain_snapshot` / `gx3_replay_capture`）。 |
| `gx3_mcp_fs_guard.py` | internal | MCP subprocess only | MCP が起動した Python のファイル作成・変更を `GX3_MCP_OUTPUT_DIR` 内へ閉じ込め、外部 SQLite を読み取り専用にする。 |
| `gx3_cli.py` | `gx3-cli` | `gx3_run_command` | CLI dispatcher、help/list、query 系ラッパー。 |
| `gx3_doctor.py` | `doctor` | `gx3_run_command` | 解析対象、index、xref DB、link-map の状態確認。 |
| `gx3_ladder_report.py` | `ladder-report` | `gx3_run_command` | オフラインで開ける単一HTML。中央にラダーSVG、左にデバイス・コメント検索、右に選択デバイスの読出・書込一覧と根拠。各デバイスに「この画面での件数」と「プロジェクト全体の件数」を併記し、描画本数が足りない場合は打切りとして表示する。実測値は扱わず、接点をONとして着色しない。`--all` は全プログラム分のページを相互リンク付きで出力し、他プログラムの読み書きへ移動できるようにする。リンク先ラングがページに無い場合はその旨を表示する。 |
| `gx3_explore.py` | `explore` | `gx3_run_command` | 目的別の4つの入口（overview / why / concerns / changed）。新しい解析はせず、索引を自動準備して既存コマンドを問いに沿った順で実行し、共通のヘッダ（対象・入力指紋）の下にまとめる。時間切れの項目は「取得できなかった」ではなく「終わらなかった」と区別して報告する。 |
| `gx3_xref_read.py` | — | xref を読む全コマンド | xref をデバイスで引くための唯一の境界。`xref.device`（命令が名乗るデバイス）と `member_device`（その occurrence が覆うデバイス）を分けて扱い、member 表が無い古いDBでは完全一致へ縮退する。読み手が範囲規約を知らなくても正しい答えしか取れないようにする。 |
| `gx3_reach.py` | — | `gx3_change_impact`, `gx3_xref` | 「このデバイスは何に届くか」の探索を一箇所に置く。同一ラング上の共起と値の転送を区別し（転送を優先）、ブロック命令の範囲をまとめて辿り、上限で実際に隠れたものがあったときだけ打切りとして返す。 |
| `gx3_change_impact.py` | `change-impact` | `gx3_run_command` | 2版の差分を取り、変更ラングが書き込むデバイスと、その到達先（出力・警報を切り出して表示）を出す。到達先は静的な候補であり実行順・実行条件・インタロックは判定していないと明示する。コメントのみ・キャンバス寸法のみの変更は「動作は変わらない」として影響一覧を付けない。 |
| `gx3_flow_db.py` | — | `gx3_graph`, `gx3_dependency_flow` | 値の流れを持つ xref の場所をworkspace 経由で解決する。呼び出し側にパスを覚えさせないための小さな共通部品。 |
| `gx3_workspace.py` | `workspace` | `gx3_run_command` | 索引と xref の置き場所と再利用可否を一箇所で判断する。入力指紋・格納版・解析器版が一致するものだけを「使える」とし、作業ディレクトリが変わっても既存の索引を見つける。 |
| `gx3_index_lite.py` | `index-lite`, `query-device`, `query-comment`, `query-external`, `query-cycle`, `device-map` | `gx3_run_command`, `gx3_device_map` | SQLite-first 検索の中核。 |
| `gx3_xref.py` | `xref` | `gx3_xref_where_used`, `gx3_run_command` | writer/reader、下流影響、CSV export。 |
| `gx3_data_flow.py` | `data-flow` | `gx3_data_flow`, `gx3_run_command` | 命令引数単位の source→destination value-flow。未知/部分解析は unresolved として保持する。 |
| `trace_gx3_device_dependencies.py` | `trace-device` | `gx3_trace_device` | デバイス成立条件、停止条件、上流依存を追う。呼出し単位のTraceInputsと条件参照providerを既存エンジンへ渡す。import時の他モジュール関数差替えは行わず、rows/comments/labelsを前処理・本体・後処理で共有する。 |
| `gx3_ladder_print.py` | `ladder-print` | `gx3_ladder_print` | GX Works3 印刷風のラダー根拠を出す。 |
| `gx3_ladder_layout.py` | `ladder-layout` | `gx3_run_command` | LadderBlocks の座標からビューア向け JSON/SVG レイアウトを出す。 |
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
| `gx3_live_read.py` | `live-read` | CLI only | 3つのモードを持つ。(1) 既定: 明示指定した PLC から MC Protocol/SLMP 3E binary で現在値を read-only 取得する（`--dry-run` / `--explain-frame` は接続せず送信予定 frame を表示）。(2) `live-read explain <device> --snapshot <file>`: 採取済みスナップショットに対して静的トレースの成立条件を突き合わせる。(3) `live-read replay <normalize/series/changes/snapshot> <log>`: 採取済み CSV/JSON ログをオフラインで読む。(2)(3) は接続を開かない。いずれも結果は共有の解析状態（`analysis`）で語る。 |
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
| `gx3_ladder_layout.py` | 座標を視覚の正、既存 operand/comment 解読を意味の正として合流し、ビューアや画像生成向けの JSON/SVG を作る。 |
| `gx3_mc_zones.py` | MC/MCR master-control zone の再構成。 |
| `gx3_project_config.py` | ラダー以外のプロジェクト情報を1コマンドで読む。CPU・ユニット構成・アドレス・接続方法・モジュール設定・モーションと、読めないものとその理由を出す。md ではなく実行して得る形にしてある。 |
| `gx3_input_identity.py` | 解析対象の入力（ラダー・コメント・ラベル・ユニット設定・CPUパラメータ）をまとめて指紋化する。成果物がどの入力から作られたかを記録・照合し、別プロジェクトの索引で答えることを防ぐ。 |
| `gx3_analysis_state.py` | 結果の状態を表す共通語彙（確認済み / 一部未解釈 / 未対応 / 打切り / 評価不能 / 実測値なし）と理由・次の手順。「検出0件」と「評価できなかった」を区別するための土台。集約時は代表状態に加えて個々の制約を constraints に保持し、JSON往復や再集約でも段階・理由・位置を失わない。 |
| `gx3_index_contract.py` | 派生xref/liteの用途別の必須表・列を、照会やworkspace再利用の前に検証する。行内容の完全性・元言語の対応範囲の証明とは別の構造契約。 |
| `gx3_module_params.py` | インテリジェント機能ユニットの設定を読む。ProfileTableInfo で記述子テーブルと設定値テーブルを判別し、既定値から変更された設定だけを型名・スロット・バッファU番号とともに出す。 |
| `gx3_operand_parse.py` | ヘッダの型トークン列と要素の値を突き合わせてオペランドを読む共通処理。ladder-print（表示文字列）と gx3_arg_decode（occurrence）が同じ歩進を共有し、同じ解読バグが二重に入るのを防ぐ。 |
| `gx3_operand_display.py` | 図面表示と条件式で使う命令名・順序付き引数の表記を共有する。文字列・添字・間接指定と型を保持する。 |
| `gx3_arg_decode.py` | ラダー命令引数の共通 decoder。gx3_operand_parse の結果を occurrence と read/write 分類に変換する。 |
| `gx3_data_flow.py` | 命令の read/write 意味付けから保守的な引数単位のデータフロー辺を生成する。 |
| `gx3_output.py` | 出力形式の共通処理。`--format` を唯一の綴りにし、既存の `--json` は同義として残す。JSON は ensure_ascii=False / indent=2 に統一。 |
| `test_gx3_output.py` | `--json` を持つコマンドが必ず `--format` も受けること、JSON の書き方が揃っていることを検査する。 |
| `gx3_guide.py` | プロジェクトの中身 (ラダー/ラベル/コメント/パラメータの有無、索引の構築状況) を見て、実行すべきコマンドを理由つきで挙げる。62 コマンドの入口。 |
| `test_gx3_guide.py` | プロジェクトに無いものを勧めないこと、および `--help` に全コマンドが出ることを検査する。 |
| `gx3_metrics.py` | プログラムごとの規模と、論理が集中している回路を出す。未知のプロジェクトをどこから読むかの入口。 |
| `test_gx3_metrics.py` | 回路の分岐数を経路数として数え、読めなかった回路を黙って除外しないことを検査する。 |
| `gx3_rung_text.py` | プログラムを 1 回路 1行 (条件 -> 駆動デバイス) で出力する。`ladder-print` の罫線出力に対し、読解と MCP 経由のエージェント利用向けの圧縮表現。 |
| `test_gx3_rung_text.py` | 回路が「条件 -> 出力」として読め、印刷レイアウトより桁違いに小さいことを検査する。 |
| `gx3_roundtrip.py` | 各回路を読んで AST 化し再生成して、元と一致するか検査する。デコーダの自己申告 (`parse_status`) に頼らない唯一の外部検証。 |
| `test_gx3_roundtrip.py` | 合成プロジェクトの全回路が再生成で一致することを検査する。読み取りが変質したら落ちる。 |
| `test_gx3_semantic_diff.py` | 配線・接点属性・未解釈オペランドの変更が意味差分から隠れないことを検査する。 |
| `gx3_label_resolve.py` | `LabelData.db` を読み、ラダーの `_lid/<LabelID>/<行>` をラベル名・クラス・割付デバイスへ解決する。 |
| `test_gx3_label_scope.py` | 別ラベル表の同名ラベルがtrace/xrefで混在しないことを実CLIで検査する。 |
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
| `test_gx3_mcp_filesystem.py` | #94 の filesystem sandbox。既存ファイル上書き、相対 traversal、symlink、xref/index-lite の出力 alias、typed tool を実 MCP 呼び出しで検査する。 |
| `test_gx3_data_flow.py` | MOV/DMOV/BMOV、read-modify-write、未知/部分解析の value-flow 回帰。 |
| `test_gx3_dependency_flow_topology.py` | dependency-flow が暗黙 horizontal gap、左母線推定、driver sink 越しの逆流を依存に混ぜないこと。 |
| `test_gx3_cli_issue_polish.py` | JSON 出力、同義語検索、カテゴリ別ヘルプ、`--no-color`。 |
| `test_gx3_lint.py` | lint rule 群。 |
| `test_gx3_ladder_logic.py` | ラダー論理生成。 |
| `test_gx3_ladder_layout.py` | LadderBlocks 座標から JSON/SVG レイアウトを作り、既存の論理解析と矛盾しないことを検査する。 |
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

- `gx3cli/gx3_comment_store.py`: コメントDBのデバイス種別・ビット・ユニットを区別する共通読取り。
- `tests/test_gx3_comment_identity.py`: 合成SQLiteを使ったコメント識別の回帰テスト。

- `gx3cli/gx3_csv_export.py`: 保存済みGX Works3命令CSVと、選択可能な解析CSV・コメント・ラベル補助表の出力。
- `tests/test_gx3_csv_export.py`: 合成GX3からCSVを出すCLIと原本保護の検査。

- `gx3cli/gx3_native_csv.py`: RCPUの保存命令列・StepInfo・回路図の照合とGX Works3 CSV行への復号。未知形式は拒否。
- `tests/test_gx3_native_csv.py`: 合成アーカイブからCLI経由の命令CSV出力、ステップ・分岐・修飾子・不一致時の出力中止を検査。


## 解析結果を短く読む

概要は `rung-text`、依存条件は `trace-device --compact`、図の確認は
範囲を絞った `ladder-print` を使います。以下のファイル名・デバイスは説明用です。

```powershell
gx3-cli rung-text --root demo.gx3 --program 001_LDDB.db --comments
gx3-cli trace-device M100 --root demo.gx3 --strict-logic --compact --max-depth 4
gx3-cli ladder-print 001_LDDB.db --root demo.gx3 --list-sections
gx3-cli ladder-print 001_LDDB.db --root demo.gx3 --device M100
gx3-cli ladder-print 001_LDDB.db --root demo.gx3 --pos-range 0-100 -o ladder.txt
```

- `rung-text --device` は指定デバイスを駆動する出力で絞ります。
  `ladder-print --device` は読取りを含む参照回路で絞ります。
- セクション名で選ぶ場合は `--section "タイトル"` を使います。
  `--pos-range` は内部位置 pos の範囲です。GX Works3 の表示ステップ番号と
  同一とは限りません。
- 呼び出し側で出力が切れる場合は `-o ladder.txt` で保存し、必要な行を読みます。
  出力の切捨てだけで CLI の失敗と判断せず、終了状態とエラーを確認します。
- `doctor --warn-only` は「警告だけ表示」ではなく「ERROR があっても終了コードを
  0 にする」指定です。OK 行も表示されます。スクリプト存在確認が不要な場合は
  `--no-script-check` を追加できます。終了コードだけで正常と判断しないでください。
- xref の Note は解析範囲の制約です。繰り返し表示されても解釈から除外せず、
  同じ証拠の再取得や不要な SQL 列の重複取得を減らします。

### rung-text のデバイスコメント

`--comments` を付けると、条件と出力に表示されたデバイスのコメントを
各行の末尾に `# X0="開始条件", M0="運転許可"` の形で添えます。
同じデバイスは1行につき1回表示し、コメント内の改行はエスケープして1行を保ちます。
省略時は従来の短い表示です。`--format json --comments` では
`condition` と `device` を変更せず、デバイス名からコメントへの `comments` 対応表を追加します。

コメントがないデバイス、デバイス名に似たラベル、動的アドレスには推測で補いません。
ビット指定にはそのビットのコメントを使い、ワードのコメントを流用しません。
桁指定のコメントは先頭デバイスのものです。表示に現れない命令引数を一覧する機能ではありません。

### ラダーSVGの命令幅

命令枠は命令名1セル＋各引数1セル（MOVは3セル、SETは2セル、TOは5セル）で描画し、右母線までの余白で引き伸ばしません。MOVなどの出力命令は固定幅のまま右母線に揃え、元の入力位置から配線で接続します。接点側の命令は元の位置を維持します。列幅の変更やFBの個別レイアウトの完全再現は対象外です。比較資料：三菱電機 [GX Works3 Operating Manual](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh081215eng/sh081215engaq.pdf)、印刷ページ351の画面例、353のセル表示の説明。
