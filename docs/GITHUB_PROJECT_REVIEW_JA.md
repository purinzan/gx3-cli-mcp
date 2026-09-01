# GX Works3 / GX3 / MELSEC 関連 GitHub レビュー

調査日: 2026-09-01

このメモは、公開 GitHub と三菱電機公式サンプルを、`gx3-cli-mcp` に取り込む設計材料として整理したものです。対象は「GX Works3/GX3そのもの」「三菱PLC通信」「PLCopen/L5Xなど近縁PLC解析」の3系統です。公開 `.gx3` や `.gtx` は機密データになり得るため、このリポジトリには取り込みません。

## まず結論

- 公開GitHubで `.gx3` を直接ローカル解析しているプロジェクトはほぼ見当たらない。`gx3-cli-mcp` の強みはここにある。
- GX Works3連携系は、ファイル解析よりも Windows UI Automation / Open I/F / MX Component で「IDEやPLCを操作する」方向が多い。
- 通信系は、タグ辞書、プロファイル制約、要求直列化、ランダム/バッチread、復旧時の安全境界が参考になる。
- PLCopen/L5X系は、形式変換、型付きモデル、コードスメル検出、構造グラフ/SVG出力が参考になる。
- 三菱公式サンプルの `.gx3` には 7z/AES コンテナがある。汎用ツールで展開できない場合は復号せず、GX Works3で展開/エクスポートしたフォルダを入力にする。

## 調査した母集団

GitHub repo search `"GX Works3"` で確認できた主な19件:

| Repo | 種別 | 実ファイル | 使える観点 |
|---|---|---:|---|
| `ktr0702ot/GX-Works3` | ハンズオン | `.gx3`なし | 教材目線の導線 |
| `Bibin-VR/GXBridge-MCP` | GX Works3/PLC操作MCP | `.gx3`なし | 破壊操作の確認、Open I/F分離、リモートWindows実行 |
| `HemathKumar007/PLC-Conveyor-Control-System` | 教材 | `.gx3`なし | コンベヤ題材 |
| `mikaelwittlock/Automatiserade-Processer` | 教材/GT | `.gx3` 7件, `.GTX` 5件 | 複数形式、非LD検出、HMI連携 |
| `rushabd1/Demo-Mixing-Station` | 教材/HMI | `.gx3`なし | 工程デモの説明軸 |
| `khangho2611/Automated-warehouse-PLC-MXOPC` | Factory I/O + MX OPC | `.gx3` 1件, `.factoryio` 1件 | 仮想設備、タグ辞書、OPC連携 |
| `unsalsoftwareentwickler/mxopc_csharp-example` | MX OPC C# | `.gx3`なし | タグ操作アプリ |
| `martin-encina/flocculation-water-treatment-plc-hmi` | 水処理/HMI | `.gx3`なし | 設備説明単位 |
| `IshanSharma97/plc-portability-lab` | IEC ST移植 | ST 3件 | ベンダ横断差分、同等性チェック |
| `mertsgrr/mxopc_csharp` | MX OPC C# | `.gx3`なし | タグ操作アプリ |
| `mokouliszt/RomajiToKana-ST` | GX Works3 ST | ST 2件 | ST資産の扱い |
| `MinJunKimsdaads/plc-ladder-viewer` | ラダービューア/シミュレータ | README中心 | 通電シミュレーション、FB可視化、Web UI |
| `MuratEmreDemirci/PLC-Ladder-Control-Applications` | FX5U教材 | `.gx3` 1件 | 基本ラダー検体 |
| `mongolyy/gen-ai-ladder-program-sample` | AIラダー/ST生成 | CSV 14件, ST 9件 | 生成パイプライン、静的チェック、GX Works3取込CSV |
| `coder007rahul/gxworks3-mcp-bridge` | GX Works3 UI Automation MCP | C# | preview→confirm、安全token、read-back verify |
| `mokouliszt/industrial-ide-docs` | IDE横断docs | docs/scripts | 共通分類、検索index |
| `pchumski/Cheese-cutting-machine` | FX5U+GT27教材 | `.gx3` 1件, `.GTX` 1件 | HMI/PLCペア |
| `pchumski/Palletization-practical-project` | FX5U+robot+HMI | `.gx3` 1件, `.GTX` 1件, robot XML | ロボット連携、周辺資産 |
| `purinzan/gx3-cli-mcp` | 静的GX3解析 | Python | 自プロジェクト |

追加で確認した近縁プロジェクト:

| Repo | 種別 | 使える観点 |
|---|---|---|
| `den-aliev/fx5_mbtcpserver` | FX5 Modbus TCP server `.gx3` | 通信FB/サーバ実装サンプル、iQ-F検体 |
| `YudaiKitamura/mcpx-mcp-server` | MC Protocol MCP | address-comment辞書、自然言語名でのdevice操作 |
| `fa-yoshinobu/node-red-contrib-plc-comm-slmp` | Node-RED SLMP | PLC profile、要求直列化、read/write safety flow |
| `Moge800/gomcprotocol` | Go MC Protocol | 3E/4E、random read/write、remote control、goroutine-safe直列化 |
| `plcpeople/mcprotocol` | Node MC Protocol | シンプルなSLMPクライアントAPI |
| `ChrisPulman/MitsubishiRx` | C# MC Protocol/SLMP | tag database、typed client、reactive polling、diff/rollout policy |
| `radevgit/plc` | L5X/PLCopen/ST解析 | code smell、SVG graph、IEC ST parser、typed generated model |
| `suifei/plcopen-go` | PLCopen XML Go | XSD由来の完全構造体、XML/JSON roundtrip、schema validation |
| `RoDoerIng/PlcOpen` / `PyLC` | PLCopen解析実験 | POU/FBDノード抽出の考え方 |
| `mokouliszt/iqr_device_validator` | iQ-R device validator | デバイス/定数の構文木、範囲、修飾子制約 |
| `mokouliszt/iQRSimpCPUCommSkill` | シンプルCPU通信CSV skill | GX Works3互換CSV、roundtrip、通信設定validation |

三菱公式サンプル:

| Source | 内容 | 状態 |
|---|---|---|
| 三菱 e-learning RD78G Basic | `Sample_RD78GBasic_en.gx3` | 7z/AES container。汎用展開は不可 |
| 三菱 e-learning RD78G Basic2 | `RD78GBasic2_Sample2.gx3` | 7z/AES container。汎用展開は不可 |
| 三菱 e-learning MX Controller | `Sample_MXContBasic_en.gx3` | 7z/AES container。汎用展開は不可 |
| 三菱 Motion FB library | `MotionControl_RD78_1.03D.mslm` | FB library。GX3ではないが、FB資産解析の参考 |

## 機能軸レビュー

### 1. 静的GX3解析

公開repoの多くはGX Works3で作ったプロジェクトを置いているだけで、`.gx3` 内部を静的解析する実装は見当たらなかった。ここは `gx3-cli-mcp` が独自に伸ばせる領域。

取り込み候補:

- `gx3-cli format-inventory` のように、入力が LD/FBD/ST/SFC/公式7z/AES/GTX/FB library のどれかを最初に分類する。
- 非LDDBを「失敗」ではなく「形式判定済み」として扱う。
- GitHub教材系で見つかったGX3を直接同梱せず、failure-corpusの手順だけ整備する。

### 2. ラダー可視化/通電説明

`plc-ladder-viewer` はWeb上のラダー表示、通電シミュレーション、FB可視化、タイムチャートを前面に出している。`gx3-cli-mcp` はCLI/MCPなので同じUIを作る必要はないが、出力形式として使える。

取り込み候補:

- `ladder-print --live-values` の次として、rungごとの `pass/block/unknown` をJSONにも出す。
- Mermaidだけでなく、将来的にSVG/HTML exportを追加する。
- FB呼び出しをピンブロックとして表示できる中間表現を作る。

### 3. Live値/通信

`mcpx-mcp-server`、`gomcprotocol`、`node-red-contrib-plc-comm-slmp`、`MitsubishiRx` は、静的ファイルではなくPLCと通信する系統。ここで重要なのは機能より安全境界。

取り込み候補:

- read-onlyの `live-read` を維持し、write/remote controlはMCPから出さない。
- 同一接続での要求直列化、タイムアウト、PLC end codeの明示を強化する。
- `RandomRead` 相当で、ラダーに出てくるデバイスをまとめて読む `live-snapshot` を追加する。
- GX3コメントから `address-comment.json` / `device-dictionary.json` を出し、外部MCP/OPC/SLMPツールと接続しやすくする。
- PLC profileを `iq-r`, `iq-f/fx5`, `q/l` で明示し、X/Yの基数や対応deviceをprofileで分ける。

### 4. タグ辞書/デバイス検証

`MitsubishiRx` の tag database、`iqr_device_validator` の構文木と範囲検証、`iQRSimpCPUCommSkill` のCSV roundtripはかなり参考になる。

取り込み候補:

- デバイス文字列を正規表現だけで終わらせず、`prefix`, `number`, `bit`, `digit`, `index`, `local`, `indirect`, `safety` に分解する。
- `device-dictionary` 出力に `address`, `comment`, `source`, `kind`, `scope`, `confidence` を入れる。
- `lint` に範囲外デバイス、怪しいX/Y基数、未定義コメント、予約特殊レジスタの誤用を追加する。

### 5. GX Works3操作MCP

`GXBridge-MCP` と `gxworks3-mcp-bridge` は、Windows上のGX Works3をAIから操作する思想。自プロジェクトとは安全境界が違うが、設計としては強い。

取り込み候補:

- `gx3-cli-mcp` は今後も静的read-onlyを本流にする。
- ただし「Windows hostにだけ許す別プロセス」として、Open I/F / UI Automation bridgeを将来分離できるよう、MCP tool名や責務を混ぜない。
- 書き込み系を入れる場合は preview→confirm token、TTL、hash drift check、read-back verify を最低条件にする。

### 6. PLCopen/L5X近縁解析

`radevgit/plc` は、L5X/PLCopen/STの解析・可視化・code smell検出がまとまっていて、`gx3-cli-mcp` の将来像に近い。`plcopen-go` はXSD由来の型付きモデルとroundtripが参考になる。

取り込み候補:

- GX3内部DBを直接処理するだけでなく、将来的に `gx3 export-ir` のような vendor-neutral IR を出す。
- `graph` を `structure`, `call`, `dataflow`, `combined` に分ける。
- `lint` を code smell として再整理し、rule id / severity / evidence / citation を標準化する。
- PLCopen XML export/importが手に入る環境では、GX3静的解析結果とPLCopen XMLを突き合わせる検証モードを作る。

## 設計軸レビュー

### 安全境界

もっとも参考になるのは、破壊操作を通常の解析系から分離する設計。`gx3-cli-mcp` は「GX3静的解析」「live read」「write/IDE操作」を明確に分けるべき。

推奨:

- MCP allowlistは read-only を基本にする。
- `live-read` はCLI-onlyまたは明示opt-in。
- write/remote/run/stop/downloadは別packageまたは別MCP serverにする。

### 根拠提示

既存回路分析で一番強くなるのは、rung citation。通信値やlint findingを出す時も「どのLDDB/row/section/comment/deviceから言っているか」を常に添える。

推奨:

- すべての解析結果に `evidence` 配列を持たせる。
- `ladder-print`, `xref`, `lint`, `graph` の出力形式を揃える。

### 互換性

Windows基本、Macも可能、という方針なら外部依存は明示パスで逃がすのが良い。7-Zipはその典型。

対応済み:

- `.gx3` のZIP/7z判定を追加。
- 7z系は `GX3_7Z` またはPATH上の `7z` / `7zz` / `7za` / `bsdtar` を試す。
- 暗号化は復号しない。

追加候補:

- `gx3-cli doctor` で7-Zip検出結果を表示する。
- 公式7z/AESサンプルを「展開不可だが検出できる」回帰ケースとして、実ファイルなしのmagic testで維持する。

## 優先度付き取り込み案

### P0: すぐ入れる

1. `device-dictionary` 出力  
   GX3コメント、ラベル、デバイス参照から `address-comment.json` 互換の辞書を出す。MC Protocol/OPC UA/MCP連携の土台になる。

2. `doctor` の入力形式診断強化  
   ZIP/7z/AES/GTX/MSLM/抽出済みフォルダを明示し、次の手順を出す。

3. live値 overlay JSON  
   `ladder-print --live-values` の人間向け表示だけでなく、AI/画面用にrung単位のJSONを出す。

### P1: 次に効く

4. device parser/validator  
   iQ-R/iQ-F profileを持つデバイス構文木を作り、lint/live-read/辞書出力で共通利用する。

5. graph出力の拡張  
   `structure`, `call`, `dataflow`, `combined` の出力タイプを追加する。

6. PLC profile  
   `--plc-profile iq-r|iq-f|q|l` でX/Y基数、対応device、SLMP制約を変える。

### P2: 将来価値が大きい

7. PLCopen/GX3 IR bridge  
   GX Works3 Open I/Fや手動exportで得たPLCopen XMLと、GX3内部DB解析を突き合わせる。

8. Web/SVG viewer  
   CLIからHTML/SVGを吐き、rung citation + live値 + xrefを視覚化する。

9. Windows bridge別プロセス  
   GX Works3操作やcompile確認をやるなら、read-only静的解析とは別MCPに分ける。

## 見送るもの

- 公式7z/AES `.gx3` の復号実装。アクセス制御回避に近づくのでやらない。
- GX Works3のバイナリ解析、COM DLL同梱、三菱ソフトウェアの再配布。
- live write/remote run/stopを既存MCPに混ぜること。安全境界が濁る。

## 実プロジェクト分析への効き方

- 既存設備の「なぜ動かない」を見るには、`rung citation + xref + live read` が一番効く。
- 改造レビューには、`lint rule id + evidence + graph` が効く。
- 他ツール連携には、`device-dictionary.json` が効く。
- Windows現場導入には、`doctor` が依存関係と入力形式をはっきり出すことが効く。

