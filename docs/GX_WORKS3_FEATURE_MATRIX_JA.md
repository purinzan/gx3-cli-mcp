# GX Works3 標準機能との対応表

Issue #128 の調査結果を、GX Works3 の標準機能と `gx3-cli-mcp` の既存機能を重複なく比較するための基準表として残す。

## 基準

- gx3-cli-mcp: `main` `efe886ccf02178994e03b74c625a58fa4b0b74db`
- GX Works3 現行マニュアル: `GX Works3 オペレーティングマニュアル` `SH-081214` / English `SH-081215ENG`。三菱電機 FA の公開一覧では 2026-07 発行 Ver.AT。
- 本文の章・ページ確認: `SH-081215ENG-AF`（2021-10）。現行 Ver.AT とページ番号が一致するとは限らないため、ページ番号だけでなく節名も併記する。現行版で再確認するときは節名を基準にする。
- 三菱公式マニュアル一覧: https://www.mitsubishielectric.co.jp/fa/download/search.page?kisyu=%2Fplceng&mode=manual&q=GX+Works3
- 三菱公式 GX Works3 機能紹介: https://www.mitsubishielectric.co.jp/fa/products/cnt/plceng/smerit/gx_works3/gx3go_all.html

この文書での「対応」は、GX Works3 と同じ UI や完全互換を意味しない。保守・調査で同じ問いに答えられるかを比較する。

## 状態の定義

| 状態 | 意味 |
|---|---|
| 対応済み | 目的に必要な情報を既存機能で取得でき、追加実装を要しない |
| 部分対応 | 土台はあるが、対象範囲・統合・意味付けのどれかが不足する |
| 未対応 | 対応する解析結果を現状は生成しない |
| 設計上対象外 | project-read-only の製品境界から外す |
| 未確認 | 仕様またはプロジェクト側の根拠が足りず断定しない |

## 結論

| 候補 | 状態 | 優先度 | 判断 |
|---|---|---:|---|
| 取得済み値と条件説明の統合 | 部分対応 | P1 | 採用。`live-read` / `ladder-print --live-values` / `trace-device` を重複実装せず統合する |
| ログ取込み・時系列表示・過去状態再生 | 未対応 | P2 | 採用。ただし最初は取得済みファイルだけを対象にし、PLC 常時監視は含めない |
| ST / インライン ST を含む解析 | 部分対応 | P1 | 採用。全面意味解析ではなく、まず参照切れを減らす最小スコープにする |
| 設定・初期値の意味付き差分 | 部分対応 | P0 | 採用。既存 extractor の再利用率が高く、回路同一でも設定差で動作が変わる実務リスクに直結する |
| ラベル宣言ベースの未使用一覧 | 部分対応 | 保留 | ST/FB/他言語参照の未読がある状態では false positive を出しやすい。ST 参照範囲拡張後に再評価 |
| 調査ブックマーク | 未対応 | 保留 | 実務価値はあるが解析精度には寄与しない。レポートの安定した位置 ID が先 |

## 1. 取得済み値と条件説明の統合

### GX Works3 側

`SH-081215ENG-AF`:

- 9.8 `Displaying a Range Affected by/Affecting a Device/Label`, pp.426-436: Dataflow Analysis。
- 14.2 `Checking Execution Programs on a Program Editor`, pp.576-579 付近: LD/ST/FBD-LD の monitor value 表示。
- Dataflow Analysis から monitor value / offline event history を扱えるが、対象プロジェクトと PLC に書かれたプロジェクトが一致しない場合の注意がある。

### gx3-cli-mcp 側

既存:

- `live-read`: 明示指定した MC Protocol/SLMP endpoint から単発 read-only 取得。CLI-only。
- `ladder-print --live-values`: 取得済み JSON を接点/コイルへ重ね、pass/block を表示。
- `trace-device --strict-logic`: 静的な成立・解除・保持条件と未知条件を追跡。
- `ladder-report`: オフライン HTML 上でラダー、検索、read/write 位置を移動可能。

実装根拠:

- `gx3cli/gx3_cli.py`
- `gx3cli/gx3_ladder_print.py`
- `gx3cli/trace_gx3_device_dependencies.py`
- `gx3cli/gx3_mcp_server.py`
- `tests/test_gx3_live_read.py`
- `tests/test_gx3_format_graph.py`

`live-read` は MCP の `EXTERNAL_IO_COMMANDS` として明示的に除外されている。取得済み JSON を使う `ladder-print` や project-read-only 解析は MCP から利用できる。

### 不足

- `trace-device` と取得済み値の評価結果が一つの説明に統合されていない。
- 現在の値だけで「異常が起きた過去原因」を断定してはいけない。
- 値の取得時刻、入力プロジェクトの fingerprint、欠測、値型、取得元 endpoint を根拠として保持する必要がある。
- SET/RST、タイマ、エッジ、実行順、MC/MCR、ジャンプなどは現在値だけでは過去状態を復元できない場合がある。

### 採用スコープ

`trace-device` に取得済み値を与えたとき、静的な論理式の各 leaf を `pass / block / unknown / missing` に評価し、最上位に「今の取得値ではどこで不成立か」を説明する。ただし結果名は `current-snapshot explanation` とし、fault root cause や historical cause とは呼ばない。

検証:

- AND/OR/b 接点、欠測、未知命令、SET/RST、タイマを合成 fixture で固定。
- 入力 fingerprint 不一致時は評価を拒否または強い警告。
- 同じ snapshot を `ladder-print` と `trace-device` に入れたとき pass/block が矛盾しない。

規模: M。

## 2. ログ取込み・時系列表示・過去状態再生

### GX Works3 側

`SH-081215ENG-AF` 17.4 `Checking Collected Data on Program Editor`, pp.681-698:

- memory dump result、logging file、recording file を offline monitor で表示。
- seek bar による時点移動、event history、GX LogViewer での waveform 表示を持つ。
- manual 記載上、R00CPU、RnPSFCPU、remote head module は offline monitor 非対応。
- logging file と recording file は同じものとして扱わない。

### gx3-cli-mcp 側

既存の `timing-chart` は link-map/xref から生成する静的な handoff timing draft で、実測ログではない。

### 不足

- timestamp を持つ device snapshot 列の標準入力形式がない。
- 時系列から指定時刻の snapshot を作り、既存ラダー説明へ渡す経路がない。
- GX Works3 固有 binary logging/recording format の互換読出しは未調査。

### 採用スコープ

第1段階は vendor binary parser を作らず、CSV/JSON の取得済みログを共通形式へ正規化する。

最低フィールド:

- timestamp
- device
- value
- value_type
- source
- project_fingerprint (分かる場合)

出力:

- 指定時刻 snapshot
- device ごとの時系列
- 変化点一覧
- snapshot を `ladder-print --live-values` / 条件説明へ渡せる JSON

GX Works3 の logging/recording binary 直接読出しは、公開仕様と検体を確認できた場合だけ別 Issue とする。

規模: M-L。

## 3. ST / インライン ST を含む解析

### GX Works3 側

`SH-081215ENG-AF` 9.6 `Displaying Device and Label Reference Information`, pp.415-417:

Cross Reference は Ladder / ST / FBD-LD / SFC / labels / parameter settings を検索対象にする。

同 9.8 Dataflow Analysis は ST も解析対象に含むが、ST の control syntax 内の条件式など一部を unanalyzable として扱う制約が明記されている。

Inline ST は ladder editor / FBD-LD editor 内にも存在する。GX Works3 自身も機能ごとに対応範囲を分けているため、gx3 側も「ST がある=全面解析可能」と扱わない。

### gx3-cli-mcp 側

- `gx3_format.py` は FBD/ST 等を non-ladder/unsupported として検出できる。
- `label-probe` は LabelData/SourceInfo から labels/comments/arrays/device assignments を抽出する。
- LDDB がない形式では ladder-only xref / ladder-print をスキップする。
- 現在の xref/data-flow の主解析は LDDB 中心。

### 不足

LD と ST が混在すると、ST 内で読み書きされる device/label が xref/value-flow の境界から抜ける可能性がある。その状態で「未使用」「唯一の書込み」「影響なし」を断定してはいけない。

### 採用スコープ

全面 ST evaluator は作らない。最初は以下だけを対象にする。

1. ST / inline ST source の存在と位置を列挙。
2. 読める構造に限り、device/label reference を `read / write / unknown-role` として抽出。
3. xref へ `partial source` として統合。
4. 未解釈構文がある場合、関連する unused / writer / downstream 判定を `partial` に降格。

制御構文の意味評価、関数呼出し内部、ポインタ相当、ユーザー定義型の全面展開は後続。

規模: L。

## 4. 設定・初期値の意味付き差分

### GX Works3 側

`SH-081215ENG-AF` 3.6 `Verifying Projects`, pp.143-151 付近:

- project/data verification で parameter を含む不一致を表示。
- parameter detail の不一致表示には件数制約がある。
- GX Works3 バージョン差や locale 差で、見かけ上 mismatch になる場合がある注意がある。

### gx3-cli-mcp 側

既存:

- `project-config`: CPU、units、address、module settings、motion の統合 report。
- `module-params`: intelligent function module の設定値。
- `dm-probe`: `_DM.db` の initial/retained values。
- `exec-config`: execution order / POU groups / unit configuration。
- `semantic-diff`: 現状は rung-level diff。
- `change-impact`: ラダー変更が何を書き、何へ到達するかを追跡。

### 不足

回路が同一でも以下が違えば挙動が変わるが、`semantic-diff` の主結果には統合されていない。

- CPU / execution setting
- module parameter
- refresh / communication setting
- initial / retained device memory

また、読み取れなかった領域を「差分なし」と扱ってはいけない。

### 採用スコープ

`semantic-diff` へ `configuration` セクションを追加し、既存 extractor の normalized JSON を比較する。

最初の対象:

1. project-config の CPU / unit / execution 構成
2. module-params の非 default 設定
3. dm-probe の initial / retained values

各セクションは `same / changed / missing / unreadable / unsupported` を持つ。

検証:

- 同一設定で順序だけ違う fixture は差分なし。
- 1 parameter だけ変更した fixture は該当項目のみ差分。
- extractor が読めない側は `unreadable` で、same にしない。
- ラダー差分ゼロでも設定差分を報告できる。

規模: M。採用候補中では最優先。

## 5. ラベル宣言ベースの未使用一覧

### GX Works3 側

`SH-081215ENG-AF` 9.6, p.422 `Displaying a list of unused labels`:

- project 全体で未使用の global labels
- 指定 search range の POU で未使用の local labels

を表示する。

### gx3-cli-mcp 側

- `label-probe`: label declaration / comment / array / assigned device を抽出。
- `lint` の `unused-device` は、宣言された label の unused 判定そのものではない。
- ST/FBD/SFC 参照を全面的には読めない。

### 判断

保留。

宣言一覧 minus LD references だけで作ると、ST/FB/other-language から使われる label を誤って未使用とする。先に ST 参照の partial coverage を導入し、各 label の判定へ `complete / partial / unknown` を持たせてから実装する。

削除操作は設計上対象外。

## 6. 調査ブックマーク

### GX Works3 側

`SH-081215ENG-AF` 9.9 `Registering a Bookmark`, pp.438-440:

- ladder / inline ST / ST / FBD-LD / Dataflow Analysis の位置を project に bookmark 登録。
- 最大100件。
- program 編集/convert 後は位置情報が一致しなくなり、再登録が必要になる場合があると明記。

### gx3-cli-mcp 側

`ladder-report` は検索結果や read/write 位置からラダーへ移動できるが、ユーザーの調査位置・メモを永続化する bookmark ではない。

### 判断

保留。

先に `program-map` / ladder evidence の stable locator を決める。bookmark を実装する場合は単なる行番号ではなく、最低でも以下を保存する。

- project fingerprint
- program/POU identity
- block/rung identity
- nearest step/position evidence
- user note

入力変更後に locator が一致しなければ silently jump せず `stale` とする。

## CLI / MCP の境界

| 機能 | CLI | MCP | 備考 |
|---|---|---|---|
| live-read | 対応 | 対象外 | external I/O のため MCP から明示除外 |
| ladder-print / live-values | 対応 | 対応 | 取得済み JSON の解析は project-read-only 範囲 |
| trace-device | 対応 | 対応 | typed MCP tool あり |
| xref / data-flow | 対応 | 対応 | project read-only |
| project-config / module-params / dm-probe | 対応 | 対応 | generic runner を含む read-only surface |
| semantic-diff | 対応 | 対応 | project read-only |
| timing-chart | 対応 | 対応 | 静的 draft。実測ログではない |

根拠: `gx3cli/gx3_mcp_server.py` の `MCP_DISABLED_COMMANDS` / `READ_ONLY_COMMANDS`。

## 別スコープの判断

| GX Works3 側の機能差 | 判断 | 理由 |
|---|---|---|
| 継続 watch / 常時 monitor | 保留 | `live-read` の単発 read-only と異なり、接続管理・sampling・負荷・scan 非同期性を扱う必要がある |
| PLC 内プログラムとの直接照合 | 保留 | PLC 接続・CPU/機種・権限・online read の検証が必要。ファイル解析の独立性と別問題 |
| CPU / unit / network の実機診断 | 対象外寄り | 既存の project config 解析と異なり、現場通信・機種依存診断になる |
| simulation | 設計上対象外 | gx3-cli-mcp は GX Works3 / CPU emulator の代替を目的にしない |
| edit / replace / write / RUN-STOP / forced I/O | 設計上対象外 | project-read-only / PLC-write-free の製品境界を維持する |

## 優先順位と子 Issue の切り方

### P0: configuration semantic diff

受入条件:

- `semantic-diff` で ladder diff と configuration diff を分離表示。
- CPU/unit/module parameter/initial-retained value の少なくとも3系統を比較。
- missing/unreadable/unsupported を same と混同しない。
- 既存 extractor を再利用し、別 decoder を複製しない。

### P1: current snapshot explanation

受入条件:

- 取得済み snapshot から `trace-device` leaf を pass/block/unknown/missing 評価。
- `ladder-print --live-values` と同じ leaf で判定が矛盾しない。
- timestamp/fingerprint/欠測を根拠として出力。
- historical root cause と断定しない。

### P1: ST reference bridge

受入条件:

- ST/inline ST の存在を source/POU 単位で列挙。
- 読める範囲の device/label reference を read/write/unknown-role で抽出。
- xref へ partial evidence として統合。
- 未解釈 ST が関係する unused/writer/downstream 判定を complete 扱いしない。

### P2: captured log replay

受入条件:

- CSV/JSON の timestamped values を共通形式へ正規化。
- 指定時刻 snapshot と change list を生成。
- snapshot を既存 live-values/condition explanation へ渡せる。
- polling を scan-synchronized recording と呼ばない。

## #49 / #122 との境界

- #49 の「GX Works3 と独立した照合」は解析結果の正しさを検証する親課題。本表の各実装 Issue でも fixture/根拠を追加するが、#49 自体を代替しない。
- #122 は count-bearing operand の物理 span semantics。ST、ログ、設定差分とは別。configuration diff や snapshot explanation で xref span を使う場合も、#122 の意味定義を勝手に再実装しない。

## 更新ルール

新しい GX Works3 機能を比較するときは、次を1行ずつ追加する。

1. GX Works3 manual number / revision / section / page
2. CPU/module/language restriction
3. gx3 側の CLI/MCP evidence
4. status
5. missing behavior
6. validation method
7. adopt / hold / out-of-scope

メニュー名が存在するだけでは「対応」と判定しない。gx3 側もコマンド名が存在するだけでは対応済みにしない。最終判断は、同じ保守・調査の問いへ正しい根拠付きで答えられるかで行う。
