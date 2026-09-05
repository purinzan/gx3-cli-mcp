# CPU・ユニット構成・デバイスメモリの見方

プロジェクトが「どのCPUで、どのユニットが、どのスロットに、どの先頭I/Oで」
構成されているかを読む手順。ラダーを読む前に、まずここを押さえる。

実プロジェクトで確認済み（iQ-R / R32ENCPU、27ユニット・4ベース構成）。

## ユニット構成

```bash
gx3-cli exec-config --root <project>
```

出力の後半 `units from UnitConfig.dat` に、ベース番号・スロット番号・先頭I/O・
局番・型名が並ぶ。CSV は `outputs/project_exec_units.csv`。

読めるもの:

| 項目 | 例 | 備考 |
|---|---|---|
| CPU 型名 | `R32ENCPU` | `Config.xml` の `Unit=` にも入っている |
| 増設ベース | `R65B`, `R312B` | ケーブル `RC12B(1.2m)` 等も含む |
| 電源 | `R64P`, `R61P` | |
| I/O ユニット | `RX42C4`, `RY42PT1P` | 先頭I/Oが 16進で出る |
| インテリジェント機能ユニット | `RD77MS16`, `RD81MES96N` | 先頭I/Oがバッファメモリ `U<先頭I/O÷16>\G...` の U 番号になる |

**先頭I/O とバッファメモリの対応**: 先頭I/O `0x300` のユニットは
ラダー上で `U30\G...` としてアクセスされる（`0x300 / 0x10 = 0x30`）。
`motion-rd77` はこの対応を使ってユニットを特定している。

## CPU パラメータ

`CPU.PRM` はプログラム実行設定を持つ。同じコマンドの前半に出る:

```bash
gx3-cli exec-config --root <project>
```

```
program files in CPU.PRM execution-setting order (19):
  PF000, PF001, ...
program groups (19), POUs (70):
  [0] PF000   dir=...  pous=5 steps~22639
```

実行順はスキャンの順序そのものなので、`scan-order` が指摘する
「書く前に読んでいる」候補の根拠になる。

CSV は `outputs/project_exec_programs.csv`。

## プロジェクト全体の形式

```bash
gx3-cli inspect --root <project>
gx3-cli version --root <project>
gx3-cli doctor --root <project>
```

- `inspect` — 読める/編集できるファイルの分類
- `version` — GX Works3 の保存・変換・書き込みバージョン
- `doctor` — 索引や xref の有無、次に実行すべきコマンド

## デバイスメモリ

```bash
gx3-cli dm-probe --root <project>
```

`*_DM.db`（デバイスメモリの初期値・保持値）を読む。

**プロジェクトによっては存在しない。** デバイスメモリを保存していない
プロジェクトでは `no *_DM.db files found` と出る。これは不具合ではなく、
そのプロジェクトにデータが無いという意味。

## ラベル

```bash
gx3-cli label-probe --root <project>
```

`LabelData.db` のグローバルラベル、配列、デバイス割付を読む。
ラベル主体のプロジェクトでは、`xref build` がここを使ってラベル参照
（ヘッダの `_lid/...`）を実名に解決する。解決した数はビルド時に表示される。

## 生ファイルの場所

コマンドが未対応の項目を自分で確かめたいとき:

| ファイル | 内容 |
|---|---|
| `UnitConfig.dat` | ユニット構成（sqlite） |
| `CPU.PRM` | プログラム実行設定（バイナリ） |
| `UNIT.PRM` | ユニットパラメータ（バイナリ、IP や接続方式の文字列を含む） |
| `SYSTEM.PRM` | システムパラメータ（バイナリ、文字列を含まない） |
| `Config.xml` | CPU 型名、アーカイブ種別、セキュリティ版数 |
| `<数字列>.db` | インテリジェント機能ユニットのパラメータ。`gx3-cli module-params` で読む（[モジュール設定](CONFIG_MODULES_JA.md)） |
| `_Project.txc` | 暗号化されたプロジェクト本体。エントロピー 8.000 で解読不能。`inspect` も `encrypted/high-entropy project body` と分類する |

関連: [ネットワーク設定](CONFIG_NETWORK_JA.md) /
[モジュール設定](CONFIG_MODULES_JA.md) /
[シンプルモーション](CONFIG_MOTION_JA.md)
