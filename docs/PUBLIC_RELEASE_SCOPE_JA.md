# GitHub 公開範囲

このリポジトリでは、GX Works3 プロジェクトをローカルで読み取り解析する CLI と stdio MCP サーバーを公開対象にします。

## 含めるもの

- `gx3cli/`: MCP サーバーと読み取り解析 CLI。
- `docs/`: MCP 設定例、ユーザーマニュアル、エージェント向けガイド、セキュリティ説明。
- `tests/`: 合成データだけを使う回帰テスト。
- `scripts/release_gate.py`: 公開前の混入チェック。
- `pyproject.toml`、`MANIFEST.in`、`README.md`、`LICENSE.txt`。
- `.github/workflows/ci.yml`: Windows 上でテスト、release gate、wheel build を実行する CI。

## 含めないもの

- 顧客または現場由来の `.gx3`、`.gtx`、`.db`、`.csv`、`.xlsx`、`.pdf`、ZIP などのデータ。
- 解析キャッシュ、インデックス、出力物、サポート bundle。
- ライセンス発行や課金用の秘密鍵、token、スクリプト。
- プロジェクトを書き換える CLI の公開入口。
- MCP からの合成デモ生成入口。
- GUI アプリ、オンライン送信 UI、デスクトップ配布用ビルド成果物。

## 公開前チェック

```powershell
python run_tests.py
python scripts\release_gate.py .
python -m build --wheel
python scripts\release_gate.py dist\gx3_cli_mcp-*.whl
```

会社名、設備名、案件コードなどの禁止語はソースに直書きしません。必要な場合は公開前に外部ファイルで渡します。

```powershell
$env:GX3_RELEASE_FORBIDDEN_TERMS_FILE="D:\path\forbidden_terms.txt"
python scripts\release_gate.py .
```
