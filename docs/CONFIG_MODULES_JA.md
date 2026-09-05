# MES・レコーディング・データロギング・EtherNet/IP・シリアルの見方

インテリジェント機能ユニットの設定。**設定値は読めます。** 読めないのは
「その番号が何の設定か」という名前の対応だけ。その線引きを先に書く。

実プロジェクトで確認済み（12ユニット: MES、レコーディング、データロギング、
EtherNet/IP、シリアル、シンプルモーション、アナログ入力、DI/DO）。

## まずこれを実行する

```bash
gx3-cli module-params --root <project>
gx3-cli module-params --root <project> --unit RD81RC96
gx3-cli module-params --root <project> --format csv -o modules.csv
```

ユニットごとに次を出す:

- 型名、ベース、スロット、先頭I/O、**ラダーから触るときの `U<番号>\G...`**
- 既定値から変更されている設定（＝技術者が設定したもの）
- パラメータカタログ（記述子）のテーブル名
- リフレッシュ／ハンドシェイク設定の行数

## パラメータDBの構造

ユニットごとに `<数字列>.db` が1つある。どれも同じ形で、`ProfileTableInfo`
が各テーブルの種別を持つ:

| 種別 | 意味 |
|---|---|
| `DEVICEINFO` | ユニット自身。型名・先頭I/O・ベース・スロット・占有点数 |
| `CARDINFO` | **技術者が入力した設定**。`PARAM_*Setting` に入る |
| `BASICPARAMETER` | パラメータのカタログ（記述子）。値ではない |
| `_UNITPARAM` | リフレッシュ・ハンドシェイク設定。アドレス単位で1行 |

**ここを取り違えると「読めない」と誤解する。** `RecordingBuffer` や
`IPAddress` という名前のテーブルは設定値を持っていそうに見えるが、中身は
`Prm1=10, Prm2=31504, Prm3=257`。この `Prm3=257` は**プロファイルの版数**で、
全モジュールの全記述子テーブルで同一。`Prm2` はそのテーブルのID。
`module-params` はこの署名で記述子と設定値を判別している。

実際の設定値は `PARAM_BasicSetting` にあり、一部はそのまま文字列で読める:

```
BasePrm3 = <IPアドレス>
BasePrm4 = <サブネットマスク>
BasePrm5 = <デフォルトゲートウェイ>
BasePrm6 = 800
```

`DataArrayIndexX` は**チャンネル番号または軸番号**。8chアナログ入力は同じ
設定が8行、16軸モーションは16行並ぶ。

## モジュール別の状況

| ユニット | 読めるもの |
|---|---|
| EtherNet/IP | IP・サブネット・ゲートウェイ、初期動作設定31項目、IPフィルタ、割り込み設定 |
| レコーディング | IP・サブネット・ゲートウェイ・ポート、論理スイッチ、応用設定 |
| データロギング | スイッチ設定（`BasePrm1..8`） |
| シリアル | 接続形態、`BasicSetting` 151行・`AppliedSetting` 422行・`DeviceSetting` 261行 |
| アナログ入力 | `AppliedSetting` を8ch分（`BasicParameter2/4/5`、`BasicParameter_Common` も） |
| DI/DO | 応答時間などの基本設定 |

**残る不明点は `BasePrm<n>` / `AppliedPrm<n>` の n が何の設定かの対応表だけ。**
これはモジュールプロファイル（GX Works3 が別途インストールする CSPP）側にあり、
プロジェクトには入っていない。1モジュールでも GX Works3 の設定画面と
突き合わせられれば、同じ手法で全モジュール分の対応が起こせる。

## MES のジョブ定義は、プロジェクトに入っていない

`RD81MES96N` のパラメータDBを全行確認した結果:

```
DeviceInfo                  18行  I/O割付・型名・ベンダ・版数
CardParameter                1行
LogicalSwitch_BasicSetting  12行  スイッチ設定
_UnitParam                   0行  空
ProfileTableInfo             4行  メタ情報
```

ジョブ定義・DB接続先・送信項目に相当するテーブルも文字列も**存在しない**。
MESインタフェース設定ツールで設定し、モジュールのSDカードへ書き込む方式の
ため、`.gx3` 側には I/O 割付とスイッチ設定しか残らない。

**解析できないのではなく、データが無い。** `RD81DL96`（データロギング）も
同じ構成。`Rec/` ディレクトリはプロジェクトにあっても空のことがある。

## ラダー側から攻める

設定が読めない項目でも、**ラダーがそのユニットをどう扱っているか**は完全に
読める。`module-params` が出す `U<番号>` を使って:

```bash
gx3-cli xref where-used "U<番号>\G<オフセット>" --db <xref>
gx3-cli query-instruction TO --root <project>
gx3-cli query-instruction FROM --root <project>
```

`motion-rd77` は RD77 についてこれを自動でやっている
（[シンプルモーション](CONFIG_MOTION_JA.md)）。

関連: [CPU・ユニット構成](CONFIG_CPU_UNITS_JA.md) /
[ネットワーク設定](CONFIG_NETWORK_JA.md)
