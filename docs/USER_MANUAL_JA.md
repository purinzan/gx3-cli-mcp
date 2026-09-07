# gx3-cli-mcp ユーザーマニュアル

`gx3-cli-mcp` は、GX Works3 (`.gx3`) プロジェクトをローカルで読み取り解析する CLI / MCP サーバーです。設備やプロジェクトを書き換えるためのツールではなく、デバイス、コメント、xref、ラダー根拠、通信境界を調べるためのツールです。

## できること

- デバイスがどこで使われているか調べる。
- コメント文字列から起動、停止、異常、手動、サイクルなどの候補を探す。
- コイルの成立条件をたどり、近くのラダー根拠を表示する。
- AI エージェントから MCP 経由で解析コマンドを呼び出す。
- duplicate coil、multi-writer、dead logic、interlock などの静的チェックを行う。
- 外部入力、HMI、通信、IP、リンク、タイミング候補を整理する。
- 明示指定した PLC に対して、現在のデバイス値を read-only で取得する。

## できないこと

- GX Works3 プロジェクトを MCP から書き換えること。
- 解析結果だけで設備動作や安全性を保証すること。
- すべての GX Works3 バージョン、PLC 機種、プログラム形式を完全保証すること。
- プロジェクトデータを自動でオンライン送信すること。
- `.gx3` ファイルから PLC 接続先を推測して、自動で設備に接続すること。

## インストール

```powershell
python -m pip install git+https://github.com/purinzan/gx3-cli-mcp.git
gx3-cli --version
gx3-mcp-server --version
```

このリリースでは MCP サーバーの利用に license token は不要です。

## 最初に実行すること

実プロジェクトでは、まず次の順番で確認します。

```powershell
gx3-cli doctor --root C:\path\to\project.gx3
gx3-cli index-lite build --root C:\path\to\project.gx3
gx3-cli xref build --root C:\path\to\project.gx3
```

`.gx3` を指定した場合は `.gx3_cache\<sha256>\` に展開し、そのローカルキャッシュを解析対象として使います。

## よく使うコマンド

| 目的 | コマンド |
|---|---|
| 解析できる状態か確認する | `gx3-cli doctor --root project.gx3` |
| 検索用 SQLite インデックスを作る | `gx3-cli index-lite build --root project.gx3` |
| xref DB を作る | `gx3-cli xref build --root project.gx3` |
| デバイス情報を見る | `gx3-cli query-device M100 --root project.gx3` |
| コメント語句から探す | `gx3-cli query-comment "起動" --root project.gx3` |
| 同義語も広げてコメント検索する | `gx3-cli query-comment alarm --root project.gx3 --expand-synonyms` |
| 外部入力、HMI、通信境界を見る | `gx3-cli query-external --root project.gx3` |
| サイクル、ステップ、状態系候補を見る | `gx3-cli query-cycle --root project.gx3` |
| 観測した使用範囲と索引上の隙間を見る（割当可能性の証明ではない） | `gx3-cli device-map --root project.gx3 --types M,D,W --min-free 100` |
| writer/reader と POU/step を見る | `gx3-cli xref where-used M100 --root project.gx3` |
| スクリプト向け JSON を出す | `gx3-cli query-device M100 --root project.gx3 --json` |
| デバイス辞書を出力する | `gx3-cli device-dictionary --root project.gx3 --format json -o address-comment.json` |
| コイル成立条件を追う | `gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact` |
| 現在値を読む | `gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word` |
| 現在値をラダー根拠へ重ねる | `gx3-cli ladder-print MAIN --root project.gx3 --device M100 --live-values live.json` |
| GX 印刷風のラダー根拠を見る | `gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M100` |
| ビューア/画像生成向けのラダー座標を出す | `gx3-cli ladder-layout <PROGRAM_OR_LDDB> --root project.gx3 --format svg -o ladder.svg` |
| 2 つのコイルが同時 ON 可能か静的確認する | `gx3-cli interlock-check M100 M200 --root project.gx3` |
| 静的チェックを走らせる | `gx3-cli lint project.gx3` |
| サポート用の診断 ZIP を作る | `gx3-cli support-bundle --root project.gx3 -o support.zip` |

## 典型的な調査の流れ

「M100 がなぜ ON するか知りたい」場合:

```powershell
gx3-cli query-device M100 --root project.gx3
gx3-cli xref where-used M100 --root project.gx3
gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact --ja
gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M100
```

「起動に関係する信号を探したい」場合:

```powershell
gx3-cli query-comment "起動" --root project.gx3
gx3-cli query-cycle --root project.gx3
gx3-cli query-external --root project.gx3
```

「全体の静的リスクを見たい」場合:

```powershell
gx3-cli lint project.gx3
gx3-cli dead-logic --root project.gx3
gx3-cli reliability-report --root project.gx3 -o reliability.md
```

## 現在値の読み取り

`live-read` は MC Protocol/SLMP 3E binary の batch read で、明示指定した PLC から現在値を読みます。
`.gx3` から勝手に接続先を探してオンライン監視する機能ではありません。IP、ポート、デバイス、点数を毎回指定します。

```powershell
gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word --dry-run
gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word
gx3-cli live-read --ip <PLC_IP> --port 5000 --device M100 --count 16 --type bit --format json
```

### 採取済みデータをオフラインで読む

`live-read` には接続を開かない2つのモードがあります。どちらも採取済みのファイルだけを読みます。

```powershell
gx3-cli live-read explain M100 --root project.gx3 --snapshot live.json
gx3-cli live-read replay changes captured_log.csv
gx3-cli live-read modes
```

`explain` は静的トレースで求めた成立条件に、採取した値を突き合わせます。答えは他コマンドと同じ解析状態で返り、
スナップショットに値が無いデバイスがあれば `no measured value; file only`（実測値なし）として、
その値をどう採るかまで示します。値が揃っている行だけを見て「確認済み」と読めてしまわないようにするためです。

一枚のスナップショットが答えるのは「採取した瞬間の条件」だけで、過去の停止・トリップの原因ではありません。

ログ再生は1ファイルにつき単一の取得元・プロジェクト版を扱います。
`source`または`project_fingerprint`が混在するログは、重複除去の前に拒否します。
PLC・版ごとにファイルを分けてください。ファイル共通metadataは各行へ継承しますが、
別の行から欠けた識別情報を補いません。全行で識別情報がない従来形式は受け付けますが、
同じPLCの記録であることをツールが検証できるわけではありません。

この2モードは MCP からも使えます（`gx3_explain_snapshot` / `gx3_replay_capture`）。ネットワークに接続する既定モードは MCP から実行できません。

JSON を保存して `ladder-print` に渡すと、GX 印刷風のラダー根拠に現在値を重ねられます。

```powershell
gx3-cli live-read --ip <PLC_IP> --port 5000 --device M100 --count 16 --type bit --format json -o live.json
gx3-cli ladder-print MAIN --root project.gx3 --device M100 --live-values live.json
gx3-cli ladder-print MAIN --root project.gx3 --device M100 --live-values live.json --format json -o rung-live.json
```

A 接点/B 接点には `live:ON pass`、`live:OFF block` のような注記を付けます。コイルは現在値を表示します。これは診断用の重ね合わせで、常時監視ループではありません。

## デバイス辞書

`device-dictionary` は GX3 のデバイスコメントを、外部MCP、OPC UA、Node-RED、現場メモに渡しやすい JSON/CSV にします。xref DB がある場合は read/write 回数、使用 POU、最初の step も付与します。

```powershell
gx3-cli xref build --root project.gx3
gx3-cli device-dictionary --root project.gx3 --format json -o address-comment.json
gx3-cli device-dictionary --root project.gx3 --format csv -o address-comment.csv
```

このコマンドは CLI 専用です。MCP からは公開せず、PLC 書き込み、run/stop、download、online edit は実装しません。

## デバイス参照検索の件数と限界

`gx3-cli xref where-used DEVICE --root project.gx3` は既定で最大200件を表示します。
上限で省略がある場合、書込・読出・未分類参照それぞれの表示件数と総件数、
省略の警告を表示します。表示された書込が0件でも、総件数が0件とは限りません。
`--limit -1` で全件を表示できます。

JSONでは各 `results` 要素に `total_counts`、`total_count`、`returned_count`、
`limit`、`truncated`、`warnings` を返します。既存の `writers` / `readers` / `refs`
配列は表示対象の行です。該当0件でもJSONを返し、終了コードは従来どおり1です。

read/write両方の引数は `writers` と `readers` の両方に含めます。
`total_counts` の両項目に数えますが、`total_count` / `returned_count` は元の参照を
1件として数えます。そのため分類別件数の合計と総参照件数は一致しない場合があります。

インデックス修飾によって検索対象へ到達する可能性は、該当0件の場合も警告します。
総件数は静的に特定できた参照の件数であり、実行時に到達する全アドレスを保証しません。

## MCP で AI から使う

MCP クライアント設定例:

```json
{
  "mcpServers": {
    "gx3": {
      "command": "python",
      "args": ["-m", "gx3cli.gx3_mcp_server"]
    }
  }
}
```

PATH 上の console script を使える環境では、`docs/mcp_client_config_console_script.json` の設定も使えます。

MCP では typed tool を優先してください。一般的な検索は `gx3_run_command` から `query-device`、`query-comment`、`xref`、`index-lite` を呼び出せます。`synthetic-project` と `live-read` はローカル CLI 専用で、MCP からは実行できません。

## デモデータ

公開資料、動作確認、スクリーンショットには顧客プロジェクトを使わず、合成プロジェクトを使ってください。

```powershell
gx3-cli synthetic-project demo.gx3 --overwrite
gx3-cli doctor --root demo.gx3
gx3-cli trace-device M100 --root demo.gx3 --strict-logic --compact
```

## 意味差分の比較範囲

`gx3-cli semantic-diff old.gx3 new.gx3` は、同じブロックIDの回路を比較します。
配線、接点の実行属性（立上りなど）、未解釈のオペランドを含む変更も
`logic` として表示します。`logic` は動作に関わる可能性がある変更を意味し、
実際の動作差を証明したという意味ではありません。

`layout-only` として既定で隠すのは、外側の描画領域サイズだけが変わった場合です。
接点・コイルの座標や配線の引き直しは、論理的に同等な場合でも保守的に表示します。
ブロック内の座標は接続関係に影響するため、単なる配置変更とは断定しません。
`--show-layout-only` で描画領域サイズの変更も確認できます。

## 注意

`xref where-used --cross`はtext/JSONともリンク先の範囲参照・both・総件数を表示します。
JSONでは既存resultsとは別の`cross`にリンク先結果・解析状態を追加します。
元projectが未検出でもcrossを調べますが、既存exit code 1は元projectの未検出を表します。
リンク先DB欠損は未評価、リンク先件数/参照件数の上限は実際に隠れた結果だけを打切り扱いに
します。`--cross-limit -1`と`--cross-xref-limit -1`は各上限を解除します。
保存されたlink-mapの対応付け自体や全言語の参照完全性を検証した結果ではありません。

index-liteのdevice照会は観測済みLDオペランドと固定範囲の検索です。JSONの
`reference_scope`が収録範囲、`availability_analysis`が割当可能性の未評価を示します。
未検出の既存exit code 1・空resultsは維持しますが、ST/inline-ST/FBD、未解釈・
動的アクセス、外部機器・予約領域が存在しないことは証明しません。device-mapの
`free_ranges`は互換列名であり、索引上の隙間です。安全な空き領域と扱わないでください。

定数伝播では、CALLが静的に無条件成立と分からないサブルーチン内のOUTを定数候補に
しません。CALL不成立は「OUTにfalseを書込む」ではなく「書込みを行わない」ためです。
この場合も通常の静的traceは継続します。初回実行前の値・全POUの実行順まで証明する
変更ではありません。

xref/index-liteのbuildは一時SQLiteで構築し、前後の入力指紋・ファイル状態と保存指紋を
照合してから出力を置き換えます。途中変更や構築失敗では以前のDBを残し、新規出力なら
不完全なDBを公開しません。入力DBそのものを出力先にする指定は拒否します。
入力/既存索引にSQLite WAL sidecarがある場合は、使用中のアプリを閉じて再実行してください。
公開前の照合を通ったDBには`build_contract`を記録します。xref/index-liteの読取りは
この構築契約がない旧DBを、指紋やdecoderが一致しても拒否します。rootを省略しても
迂回できません。prepareは未対応の側だけを再構築します。この内部メタデータは
改ざん検証用の署名ではなく、rootなし照会は現在のプロジェクトとの同一性も証明しません。
これはファイル変更の検出であり、元プロジェクトの排他ロックやxref/lite二つの同時更新、
外部CSVの完全なprovenance、既存readerすべての実行中スナップショットを保証しません。

定数証明は候補ラングだけでなく、供給された全LD行のparse_statusと、選択rootの
FBD形式一覧も確認します。別のLD行が未解析、またはFBDが存在する場合、通常OUTの
プロジェクト全体での定数証明を停止します。ST/LD/FBDの制約は同時に保持します。
rootなしの低レベルAPIは供給行・索引に限った契約で、プロジェクト全体の監査では
ありません。trace/dead-logic/Doctorの呼出しは選択rootを明示します。
解読済みという状態だけで実行順・初期値・外部書込みまで保証するものではありません。

semantic-diffの変更要約は各版を一度だけ解読し、旧版または新版がpartialの場合は
`summary-scope`にdecode段階の注記を出します。画面とCSVのsummary両方に残り、
解読できた命令・引数だけで変更全体を説明したとは扱いません。差分の有無は従来
どおり描画領域寸法だけを除いた生データで判断し、解読結果の一致で隠しません。

ST/inline-STの保存coverageに未解析部分がある場合、dead-logicの通常OUT定数伝播、
traceの定数枝刈り、Doctorのconstant-chainはwriter集合の完全性を証明しません。
trace自体は継続し、`constant_pruning.analysis`にdecode段階の制約を表示します。
dead-logicのsidecarは`constant_propagation_analysis`と実施有無を分けます。
ST coverage表がない旧DBも「STなし」とみなさず再構築を案内します。
対応済みSTの既知writerは従来どおり数えます。FBD、未解読LD、実行順・保持値・
外部CSVを含む全証明条件がこれだけで解決するわけではありません。

dead-logicの`unwritten-contact`は「索引に物理writerが見つからない」という観測です。
旧`const-off-contact` / `always-on-contact`のようにA/B接点の値を断定しません。
CSVの`contact_role`はa/b、`analysis_state`はpartial、定数値は空欄です。
初期値・保持値・未解析領域・外部書込みを確認する必要があり、sidecarの
`unwritten_contact_analysis`にその制約を残します。範囲内writerも共通readerで数えます。
これは通常OUTからの定数伝播の証明条件すべてを解決する変更ではありません。

dependency-flow / graphの値フローは、実際に使うSQLite読取りトランザクション内で
入力・版・基本構造を照合します。パスを選んだ時点の検証結果を再利用しません。
`value_flow_analysis`は保存LD値フローの評価範囲を表し、未取得/必要表欠損を未評価、
STや動的範囲を一部解析として残します。未確定範囲のedgeは`span_uncertain`を持ち、
図にも`range unresolved`を表示します。これは解析中の全projectファイルの変更を
防ぐ仕組みや、値フロー以外の解析全体の完全性を保証するものではありません。

root付きのxref/lite照会は、版と入力指紋に加えて用途に必要な表・列も検証します。
欠損時は再構築を案内し、workspaceのprepareは壊れた側の索引だけを再構築します。
xrefの基本照会に値フロー表は必須ではありません。外部境界のみを読む処理も、その表の
契約だけを要求します。全workspaceの再利用判定は全表を確認します。追加表・追加列は許可します。
これは保存形式の検証であり、保存行の完全性やSTを含む元プログラムの解釈範囲とは別です。

dead-logic / trace / Doctorのconstant-chainは、保存された外部境界分類の共通readerを使います。
境界DBが未取得・壊れている・別入力・必要表欠損なら、「外部デバイス0件」とみなさず、
境界に依存する判定や定数pruningを未評価にします。dead-logicはCSVに加えて
`<prefix>_analysis.json`に評価状態を出力します。正常な空の分類表とは区別しますが、
正常に読めた場合でも、実設備の全外部writerや実行時の定数を保証するものではありません。

lintの`external-value-source`は、refresh CSVの未取得・不正なheader・不正な範囲・
文字コード/CSV構文の不正を、正常な空CSVと区別します。不正な入力では依存するcheckを
`not_evaluated`とし、JSONに原因・段階・CSV読取り範囲を残します。`--require-evaluated`も
失敗します。有効な空CSVは「指定CSV内に範囲なし」の意味に限定され、別プロジェクトの
CSVでないことや実設備の全外部writerの不存在を証明するものではありません。

lintとDoctorのproject-healthもlite索引の入力指紋・device naming版を検証します。
別入力・指紋欠損・旧版のlite索引は拒否し、欠損している場合は従来どおり依存する検査を未評価にします。
liteの照会は読取り専用で開き、失敗した場合も先に開いた解析DBを閉じます。

`xref` / `index-lite` の照会で `--root` を指定した場合、DB側の入力指紋が未記録、またはrootに解析入力がない場合は照合不能として拒否します。古いDBは入力を復元したうえで再構築してください。rootを指定しないDB単独照会は互換性のため残しますが、特定プロジェクトとの一致は保証しません。

入力指紋はST/FBD、初期・保持値、ユニット設定を含むプロジェクト直下のDBと、
SourceInfo・Config・モーション等の入力も対象にします。依存ファイル定義の更新により、
以前の索引は再構築が必要です。生成した索引やCSVはプロジェクト入力と分けて保存してください。

xrefのデータフロー索引は、読出し側・書込み側の物理範囲を別々に保存します。
命令の件数`range_count`を範囲長として再利用しません。動的件数やインデックス修飾で
終端が不明な場合は、`source_range_len` / `destination_range_len`の0で不明を保持します。
この変更以前のxrefは再構築が必要です。これは範囲内の各語が一対一に対応することや、
実行時の値・順序を保証するものではありません。

定数伝播の単一書込判定には、桁指定やブロック命令による範囲内の書込みも含めます。
動的な範囲・インデックス修飾・未分類参照、または範囲索引の欠損により書込先を
確定できないデバイス種別では、保守的に通常コイルの定数化を行いません。
これは実行順・初期状態・外部書込みを含む設備動作全体の証明ではありません。

- 解析結果は参考情報です。実設備への変更判断は GX Works3 と現場の検証で確認してください。
- 一部コマンドは CSV、Markdown、ZIP、SQLite DB などをローカルに生成します。
- `live-read` は実設備に TCP 接続します。現場ルール、PLC 設定、ネットワーク権限を確認してから使ってください。
- AI に出力を渡す場合は、社内ルールと機密情報の扱いを確認してください。

### 同じラング内の命令を区別する根拠位置

xrefのLD参照にはblock_id、0始まりのop_index、element_position（保存されたx,y座標）を
追加しています。arg_indexと合わせて、同じ行の同じ命令・同じデバイスを区別できます。
テキストにもrow/op/xyを表示します。STの参照には架空のLD座標を付けずnullとし、
既存のST source/statement情報を使います。座標は保存位置であって実行順やGX Works3 stepではありません。

data-flowのoperation_index/ブロック/座標もxref DBを経由して保持し、dependency-flowの
値フローedgeにはevidenceとして元行・命令・source/destination引数番号を残します。
根拠位置を保存していない旧xrefは再構築が必要です。値フロー側の位置情報が欠けている場合は
既知の辺を返せてもpartialと表示し、位置が分かるようには装いません。

### 範囲内デバイスの成立条件とread/write両用命令

成立条件の出力選択は、命令デコーダが確定した書込み範囲を使います。DMOV/D+の上位語や
BMOVの途中語を、先頭語と名前が異なるだけでFALSEにはしません。同じデバイスを別引数で
読み書きするとき、広いread範囲を狭いwrite範囲へ流用しません。未知/インデックス修飾の
終端は追加展開せず、既知の範囲だけを扱います。これは実行保証・値・外部writerの証明ではありません。

alarm-map showはbothもwriterとして表示し、timing-chartはbothをreaderにも含めます。
timing-chartのCSVは既存列の末尾にreceiver_conditionを追加、Markdownにも受信側の
read-site条件を表示します。出力に対応付けられない読取り位置は接点一覧へのfallbackと明記し、
「対応する出力が見つからない」をFALSEと断定しません。

### ラダー表示の命令と引数

`ladder-print` は文字列定数、文字列比較、16ビット乗算の命令名、`ZZ` 添字、`@` 間接指定を表示に保持します。32ビットの出力引数だけを理由に16ビット乗算を `D*` と表示しません。表示の一致は、実行時のアドレス解決や回路ロジック全体の一致を保証しません。

### 共通入力配線と式全体への命令

出力の座標は入力端子として扱い、同じ縦配線に接続する接点条件を全出力へ渡します。出力命令の右側への通過は行いません。INVとMEP/MEFは独立した接点ではなく、対象の入力式を持つpredicateとして保持します。並列分岐の内側にある場合は、合流する迂回経路と支配する分岐点を使い、その分岐内の式に適用します。

MEP/MEFを含む条件は前スキャンの状態が必要です。traceはその制約を記録し、単一snapshotでは成立を断定しません。INVはsnapshotの対象式を反転し、MEP/MEFはMATIEC出力でも状態を仮定せずプレースホルダーに残します。

条件式にも比較命令の型と順序付き引数を保持します。P/F接点は立上り・立下りを区別し、通常接点の定数へ置き換えません。依存関係は式全体への命令の入力もたどりますが、前スキャンの状態や実行時の間接アドレスを推測しません。

### 定数を前方の読取りへ適用しない

通常OUTの成立条件が定数でも、そのOUTより前の接点は初期値・保持値を読む可能性が
あります。定数の置換は、既知の読取りがすべて同一LDDBの後続行にある場合に限定します。
同一行（命令順未証明）、別POU、位置欠損、未知・インデックス修飾の読取りが影響する場合は
候補を定数にせず、semanticsのpartialと確認すべき位置を残します。範囲読取りも対象です。

同一POUでOUTの後に接点を読む通常の連鎖は継続して扱います。traceは除外された候補を
FALSE/TRUEに置換せず、前処理・後処理・JSON要約で同じ制約を使います。dead-logicの
解析JSONとDoctorのconstant-chainにも未証明を残します。これは保存位置による必要条件で、
実行スケジュール・全外部writer・全状態依存の証明が完了したことは意味しません。

### index-liteの通信CSV更新検知

index-liteは実際に読んだrefresh/units CSVの絶対パスと内容hashを保存します。
未存在も記録し、内容変更・削除・後からの追加があれば、読取readerとworkspaceの
再利用判定は再構築を要求します。更新時刻だけを戻しても内容が異なれば再利用しません。
構築中の変更は旧索引を残して中止し、索引出力とCSV入力の同一パスも拒否します。

`gx3-cli workspace --prepare --root <project>`は以前のCSV指定を保持して再構築します。
指定を変更する場合は`index-lite build`の既存`--comm-dir`/`--comm-prefix`、または
優先される`--refresh-csv <path>`/`--unit-csv <path>`を使います。旧索引のCSVパスが
相対パスだけの場合、元の作業ディレクトリを推測せず、明示buildを案内します。

これは「どのCSV内容から保存されたか」の整合性検査です。CSVがそのプロジェクトから
生成されたこと、全外部writerを網羅すること、欠損CSVを既知の空と扱ってよいことは
証明しません。CSV由来・読取り状態の全consumerへの伝達は別の契約です。

### 索引の検証と照会を同じ版に固定する

xref/index-liteの読取接続は、decoder・入力・schemaの検証から結果照会までを同じ
SQLite読取りトランザクションで行います。別接続が途中で索引を更新しても、同じ接続の
結果へ更新後の行が混ざりません。新しく開いた接続は新しい版を再検証します。
workspaceのmetadata/schema確認も、一つの読取り版で行います。

Python APIのopen_xref_dbは読取り専用が既定です。明示的な保守更新には
read_only=Falseを指定し、通常のCLI照会と区別します。呼出し側は完了時に必ず接続を
closeしてください。索引パスの空白・#・%等はURIとしてescapeします。

この固定の対象は各SQLite索引です。元プロジェクトのファイル群、CSV、別の索引まで
一括で固定するものではありません。結果全体のsource版の整合性は別の検証が必要です。

### 引数とコメントの識別

添字とビット指定は両方を元の順序で保持します。U指定は16進表記、負のH定数は引数型の幅に応じたビット列で表示します。文字列とステートメントはヘッダーのUTF-16長を使って読み、コロンや改行を含む内容を保持します。

コメントの共通読取りはワード本体、各ビット、ユニット番号を区別します。ラダー表示は完全なデバイス名に対応するコメントを使い、動的な添字・間接アドレスへ固定アドレスのコメントを推測で付けません。ワード単位の解析ではビット単位コメントをワード本体へ上書きしません。未知のデバイスコード・ローカル領域・未対応の上位アドレスは推測せず対象外にします。旧xref/index-liteは再構築してください。

これらの表示修正はジャンプやループの実行順、前スキャンの状態、実行時アドレス解決を証明しません。巨大条件式の上限と未検証状態は維持します。

### CSVエクスポート

```powershell
gx3-cli csv-export --root project.gx3 --output-dir csv-output
gx3-cli csv-export --root project.gx3 --output-dir comments-output --kind comments
gx3-cli csv-export --root project.gx3 --output-dir one-program --kind ladder --program MAIN
```

新しい出力ディレクトリにUTF-16・タブ区切り・全項目引用符付きのCSVを作ります。既存ディレクトリへの上書きや展開済みプロジェクト内への出力は拒否します。入力の変化や途中エラーがあれば完成ディレクトリは公開しません。

`ladder_0001.csv`などはプログラム別の解析表です。プログラム名、LDDB、ブロック位置・開始ステップ、配置座標、命令、引数配列、接点種別、出力条件を含みます。`block_step`は命令ごとのステップではなく、開始位置が不明なら空欄です。接点はCONTACT_A/CONTACT_Bで表し、LD/AND/ORやMPS/MPPへのコンパイルはしません。**ラダーCSVはGX Works3へ再インポートする命令リストではありません。**

`COMMENT.csv`は「プロジェクト名／デバイス名・コメント」のGX Works3コメントCSVのレイアウトです。アーカイブ名を見出しに使い、`--project-name`で変更できます。実際のGX Works3へのインポート動作は未検証です。`comment_languages.csv`には言語番号ごとのテキストを残します。ラベルは`labels_*.csv`の解析表で、Global.csvの再生成ではありません。

`statements.csv`、`pointers.csv`、`wiring.csv`は回路の補助情報です。`manifest.json`はファイルと件数・入力指紋・制約、`issues.csv`は未対応言語・解析欠落などを記録します。CSVを作れたことは元のGX3の完全な再現を意味しません。
