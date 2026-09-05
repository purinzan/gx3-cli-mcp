# IP・接続方法・CC-Link・リフレッシュエリアの見方

「このデバイスは他局から書かれているのか」を判断するための情報。
リフレッシュエリアに入っているデバイスは、ラダーに書き手がいなくても
ネットワーク経由で値が入る。`dead-logic` がこの区別に使っている。

実プロジェクトで確認済み（CC-Link ×2、CC IE Field ×2、Ethernet ×3、
EtherNet/IP、シリアル）。

## IP アドレス

```bash
gx3-cli ip-map --root <project>
```

登録されている IP を、ユニット・ベース・スロット・先頭I/O とともに一覧する。
どのパラメータDB のどのテーブルから読んだかも列に出るので、値の裏取りができる。

読めるもの: ノードアドレス、サブネットマスク、デフォルトゲートウェイ。

## 通信ユニットとリフレッシュエリア

```bash
gx3-cli comm-refresh --root <project>
```

CSV を 4 つ書き出す（カレントディレクトリ）:

| ファイル | 内容 |
|---|---|
| `project_comm_units.csv` | 通信ユニット一覧。接続形態、ネットワーク番号、局番、占有局数、IP |
| `project_comm_refresh_areas.csv` | リフレッシュエリア。RX/RY/RWr/RWw の割付範囲 |
| `project_comm_ethernet_slmp_device_candidates.csv` | SLMP 経由で触られるデバイスの候補 |
| `project_comm_device_comment_hints.csv` | コメント文言からの推定 |

リフレッシュエリアの行は、方向（`incoming_to_plc` / `outgoing_from_plc`）と
種別（`remote_input_RX` / `remote_output_RY` / `remote_register_RWr` /
`remote_register_RWw` / `link_relay_or_buffer`）を持つ。

`confidence` 列を必ず見ること:

- `high_string_evidence_manual_format_inference` — マニュアルの書式と一致
- `medium_string_evidence_module_format_inference` — モジュール書式からの推定

`evidence_file` と `evidence_offset_hex` が付いているので、`.w3pa` の
その位置を直接確認できる。

## 接続スレーブ

`project_comm_refresh_areas.csv` の `remote_station_module_strings` 列に、
CC-Link に接続されているリモート局の型名と台数が入る
（例: `<リモート局の型名>:9`）。

## 通信の詳細

```bash
gx3-cli comm-detail --root <project>
```

通信元の候補と AJ65BT-R2N の設定を抽出する。実行に時間がかかる（数十秒）。

```bash
gx3-cli network-map --root <project> --index-db <index>
```

IP・CC-Link・SCON・安全関係をまとめたノード/エッジの地図。

```bash
gx3-cli scon-map --root <project>
```

IAI/SCON の軸マップと POS 値。該当ユニットが無ければ `no rows`。

## 接続方法（MELSOFT / SLMP / シリアル）

コマンドは無い。`UNIT.PRM` に文字列として入っている:

```bash
python -c "
import re
b = open(r'<project>/UNIT.PRM','rb').read()
for m in re.finditer(rb'(?:[\x20-\x7e]\x00){3,}', b):
    print(m.group().decode('utf-16-le').strip())
"
```

`MELSOFT Connection Module`、`SLMP Connection Module`、`UDP`、
`Host Station (iQ-RCPU)` などが並ぶ。シリアル接続は
`project_comm_units.csv` の `connection` 列に出る。

## リフレッシュを踏まえた解析

リフレッシュエリアを渡すと、`dead-logic` は「ラダーに書き手がいないが
ネットワークから値が入るデバイス」を除外する:

```bash
gx3-cli dead-logic --root <project> --db <xref> --refresh-csv project_comm_refresh_areas.csv
```

実行後に `devices skipped as network-refreshed` の件数が出る。

関連: [CPU・ユニット構成](CONFIG_CPU_UNITS_JA.md) /
[モジュール設定](CONFIG_MODULES_JA.md)
