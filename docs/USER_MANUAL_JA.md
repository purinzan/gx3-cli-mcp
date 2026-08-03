# gx3-cli-mcp ユーザーマニュアル

このツールは、GX Works3 (`.gx3`) プロジェクトをローカルで読み取り解析する CLI と stdio MCP サーバーです。GX Works3 のプロジェクトファイルを書き換える機能は MCP から公開していません。

## インストール

GitHub からインストール:

```powershell
python -m pip install git+https://github.com/purinzan/gx3-cli-mcp.git
gx3-cli --version
gx3-mcp-server --version
```

ローカル開発用:

```powershell
python -m pip install -e .
```

## 最初に実行する確認

```powershell
gx3-cli doctor --root C:\path\to\project.gx3
gx3-cli index-lite build --root C:\path\to\project.gx3
gx3-cli xref build --root C:\path\to\project.gx3
```

`.gx3` を指定した場合は `.gx3_cache\<sha256>\` に展開し、そのキャッシュを解析対象として使います。

## よく使うコマンド

| 目的 | コマンド |
|---|---|
| 解析対象と補助 DB を確認する | `gx3-cli doctor --root project.gx3` |
| SQLite インデックスを作る | `gx3-cli index-lite build --root project.gx3` |
| デバイス情報を見る | `gx3-cli query-device M100 --root project.gx3` |
| コメント語句から探す | `gx3-cli query-comment "起動" --root project.gx3` |
| 外部入力、HMI、通信境界を見る | `gx3-cli query-external --root project.gx3` |
| サイクル、ステップ、状態系候補を見る | `gx3-cli query-cycle --root project.gx3` |
| 使用デバイス範囲と空き領域を見る | `gx3-cli device-map --root project.gx3 --types M,D,W --min-free 100` |
| writer/reader と POU/step を見る | `gx3-cli xref where-used M100 --root project.gx3` |
| コイル成立条件を追う | `gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact` |
| 2 つのコイルが同時 ON 可能か静的確認する | `gx3-cli interlock-check M100 M200 --root project.gx3` |
| 静的チェックを走らせる | `gx3-cli lint project.gx3` |
| GX 印刷風のラダー根拠を見る | `gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M100` |
| 解析信頼度を確認する | `gx3-cli reliability-report --root project.gx3 -o reliability.md` |
| サポート用の診断 ZIP を作る | `gx3-cli support-bundle --root project.gx3 -o support.zip` |

## MCP で使う

MCP クライアント設定例は `docs/mcp_client_config.json` を参照してください。

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

このリリースでは MCP サーバーの利用に license token は不要です。

## デモデータ

公開資料、動作確認、スクリーンショットには顧客プロジェクトを使わず、合成プロジェクトを使ってください。

```powershell
gx3-cli synthetic-project demo.gx3 --overwrite
gx3-cli doctor --root demo.gx3
gx3-cli reliability-report --root demo.gx3 -o demo_reliability.md
```

`synthetic-project` はローカル CLI 専用です。MCP からは実行できません。
