# MES・レコーディング・データロギング・EtherNet/IP・シリアルの見方

インテリジェント機能ユニットの設定。**ここは現状いちばん読めていない領域**で、
「どの設定が変更されているか」までは分かるが「それが何の設定か」は分からない。
その線引きを先に書く。

## どのユニットがどの DB を持つか

```bash
gx3-cli comm-refresh --root <project>
```

`project_comm_units.csv` の `parameter_db` 列に、ユニットごとの
パラメータDB ファイル名（`<数字列>.db`）が入る。まずこの対応表を作る。

`gx3-cli exec-config --root <project>` のユニット一覧と突き合わせれば、
型名・スロット・先頭I/O とファイル名が結びつく。

## パラメータDB の中身

どのモジュールも同じ形の sqlite で、テーブルごとに次の列を持つ:

| 列 | 意味 |
|---|---|
| `Label` | 設定項目名（`Prm1`, `OperateSet2` のような汎用名のことが多い） |
| `Data` | 実際の値 |
| `DataDefault` | 既定値 |
| `ParamGroup` | 所属グループ |

**`Data != DataDefault` の行が、技術者が変更した設定**。これが現状いちばん
確実に取り出せる情報:

```bash
python -c "
import sqlite3
con = sqlite3.connect('file:<project>/<数字列>.db?mode=ro', uri=True)
tables = [r[0] for r in con.execute(\"select name from sqlite_master where type='table'\")]
for t in tables:
    rows = con.execute(f'select Label, Data, DataDefault from [{t}]').fetchall()
    changed = [r for r in rows if r[1] is not None and r[1] != r[2]]
    if changed:
        print(f'[{t}] {len(changed)}/{len(rows)}')
        for label, data, default in changed[:20]:
            print('   ', label, '=', data)
"
```

テーブル名は意味を持つことがある（`IPAddress`, `GatewayAddress`, `DNS`,
`BasicSetting`, `AppliedSetting`, `DeviceSetting`, `LogicalSwitch_BasicSetting`)。
`DeviceInfo` はどのモジュールにもあり、型名・先頭I/O・占有点数・
リフレッシュ設定が入る。

## 分かっている範囲

| ユニット | 読めるもの | 読めないもの |
|---|---|---|
| EtherNet/IP（RJ71EIP91） | IP・サブネット・ゲートウェイ（`ip-map`）| コネクション設定、Assembly インスタンス、RPI |
| レコーディング（RD81RC96） | IP・DNS、変更された設定の位置 | 記録トリガ、対象デバイス、保存条件 |
| MES インタフェース（RD81MES96N） | 変更された設定の位置 | ジョブ定義、DB接続先、送信項目 |
| データロギング（RD81DL96） | 変更された設定の位置 | ロギング設定、トリガ |
| シリアル（RJ71C24-R2） | 接続形態（`project_comm_units.csv`）、`BasicSetting`/`AppliedSetting`/`DeviceSetting` の変更行 | 通信プロトコル、手順、フレーム定義 |

`Rec/` ディレクトリはプロジェクトにあっても空のことがある。レコーディングの
設定はモジュールのパラメータDB 側に入る。

## ラダー側から攻める

設定そのものが読めなくても、**ラダーがそのユニットをどう扱っているか**は
完全に読める。先頭I/O から U 番号を出して:

```bash
gx3-cli xref where-used "U<番号>\G<オフセット>" --db <xref>
gx3-cli query-instruction TO --root <project>
gx3-cli query-instruction FROM --root <project>
```

バッファメモリの読み書き位置が分かれば、どのアドレスが何に使われているかは
マニュアルと突き合わせて特定できる。`motion-rd77` は RD77 について
これを自動でやっている（[シンプルモーション](CONFIG_MOTION_JA.md)）。

## 対応を進めるには

3 つのモジュールとも同じ形のテーブルなので、**1 モジュール分でも
GX Works3 の設定画面と `Label` の対応が取れれば**、残りも同じ手法で
解読できる。必要なのは推測ではなく、画面と DB を並べた 1 例。

関連: [CPU・ユニット構成](CONFIG_CPU_UNITS_JA.md) /
[ネットワーク設定](CONFIG_NETWORK_JA.md)
