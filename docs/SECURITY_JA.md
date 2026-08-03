# セキュリティノート

`gx3-cli-mcp` は GX Works3 プロジェクトをローカルで読み取り解析する CLI と stdio MCP サーバーです。プロジェクトデータを外部サービスへ送信する機能はありません。

## データの扱い

- `.gx3` はローカルの `.gx3_cache\<sha256>\` に展開されます。
- MCP サーバーは GX Works3 プロジェクト本体を変更するコマンドを公開しません。
- プロジェクトを書き換える操作や合成デモ生成は MCP から実行できません。
- 一部の解析コマンドは、指定した出力先や一時ディレクトリに CSV、ZIP、Markdown などを生成します。
- `support-bundle` は診断情報をまとめますが、ラダー本文や顧客プロジェクトの生成ファイルは含めない設計です。

## 公開前の確認

GitHub へ公開する前に、テストとリリースゲートを実行してください。

```powershell
python run_tests.py
python scripts\release_gate.py .
python -m build --wheel
python scripts\release_gate.py dist\gx3_cli_mcp-*.whl
```

`scripts/release_gate.py` は `.gx3`、`.gtx`、DB、CAB、CSV、PDF、鍵ファイルなどの混入を検出します。現場名、設備名、案件コードなどの禁止語はソースに直書きせず、公開前に環境変数か外部ファイルで渡してください。

```powershell
$env:GX3_RELEASE_FORBIDDEN_TERMS_FILE="D:\path\forbidden_terms.txt"
python scripts\release_gate.py .
```

## 運用上の注意

- 実 PLC や生産設備への変更判断は、このツールの出力だけで行わないでください。
- 解析結果は必ず GX Works3 などの正式なエンジニアリング環境と現場の安全確認で検証してください。
- 公開サンプル、Issue、README、スクリーンショットには実プロジェクト由来の情報を載せないでください。
- このリリースでは MCP サーバーに license token は不要です。
