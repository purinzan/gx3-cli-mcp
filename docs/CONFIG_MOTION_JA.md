# シンプルモーション（RD77MS）の見方

ラダー側からのアクセスは公式ラベル付きで完全に読める。軸パラメータ本体は
まだ読めない。その境界を書く。

実プロジェクトで確認済み（RD77MS16、バッファメモリアクセス 60 件）。

## バッファメモリアクセス

```bash
gx3-cli motion-rd77 --root <project>
```

xref が必要（先に `gx3-cli xref build --root <project>`）。

ラダーが触っている `U<番号>\G<オフセット>` を、ユニット型名と
**三菱の公式 G ラベル**に対応づけて出す:

```
<pou>  st<step>  TO  unit=D<n>(unit address) offset=G4300+Z2
                     data=D<m>(positioning command number)
                     RD77.stnAxCtrl1[0].uPositioningStartNo  axis=1+Z/100 (RD77MS16)
```

読めるもの:

- 位置決め開始番号、サーボOFF要求などの制御ワード
- インデックスレジスタで軸を切り替えている場合の軸番号の式（`axis=1+Z/100`）
- `FROM`/`TO`/`DFRO`/`DTO` の転送元・転送先とオフセット

CSV は `outputs/project_motion_fromto_access.csv`、
`outputs/project_motion_ug_access.csv`、`outputs/project_motion_labels.csv`。

## 専用命令

同じコマンドが末尾で数える:

```
dedicated motion instructions:
  G.INPUT: 8
  G.OUTPUT: 8
  GP.ERRCLEAR: 3
  ZP.CSET: 8
```

## インデックス修飾に注意

モーション制御は軸ごとにオフセットを変えるため、`G4300+Z2` のように
インデックスレジスタで軸を選ぶ書き方が多い。**実行時にどの軸を指すかは
静的には決まらない**。`where-used` はこの点を出力に明示する:

```
Note: N D occurrences are index-modified (M of them writes). Which address
those reach is only known while the program runs, so this list can be incomplete.
```

軸を特定したいときは、インデックスレジスタに何が入るかを先に追う:

```bash
gx3-cli trace-device Z2 --root <project> --max-depth 3 --compact
```

## 軸パラメータ（未対応）

`*_RD77MS*.iut`（数百KB）に入っている。

```bash
gx3-cli iut-probe --root <project>
```

現状はコンテナの中身を数えるだけ:

```json
{
  "iut_files": 1,
  "lenpref_strings": 592,
  "data_name_entries": 106,
  "data_name_unique_total": 53,
  "numeric_path_entries": 484,
  "numeric_path_unique_total": 242
}
```

`motion-rd77` の末尾にも `not decoded` と出る。

**読めないもの**: 原点復帰方式、加減速時定数、ソフトウェアリミット、
サーボアンプ設定、位置決めデータテーブル。

これらが必要な場合は、現状 GX Works3 側で確認するしかない。名前付き
セクションと数値パスは取り出せているので、GX Works3 の画面と 1 例
突き合わせられれば解読の足がかりになる。

関連: [CPU・ユニット構成](CONFIG_CPU_UNITS_JA.md) /
[モジュール設定](CONFIG_MODULES_JA.md)
