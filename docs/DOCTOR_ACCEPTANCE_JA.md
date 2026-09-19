# Doctor 初期受入台帳

この台帳は Issue #135 の初期受入12項目について、実装・根拠テスト・残件を対応させるためのものです。

現在の状態は次のコマンドで確認できます。

```bash
gx3-cli doctor-acceptance
gx3-cli doctor-acceptance --format json
```

`Closeable: yes` は、初期版の受入項目に対応する実装と公開CIで回る非機密テストが揃っていることを示します。Doctorは保守性・追跡性・変更安全性・異常解析性を診断する補助であり、PLCプログラムの機能正しさや設備安全を断定するものではありません。

## 判定

| 項目 | 実装/根拠 |
|---|---|
| workspace health と project-health の区別 | `doctor --project-health` と `mode=project-health` |
| 1コマンド診断 | `doctor --project-health --root <project>` / `collect_project_health` |
| 5観点 | 既存公開スコア名の Maintainability / Traceability / Change safety / Troubleshootability / Documentation に対応 |
| 点数以外の根拠 | `checks`、`count`、`top_risks`、`reason`、`human_check` |
| Top risks | severity順に上位N件 |
| 既存結果の再利用 | lint/dead-logic等の既存チェックをDoctor表示へ再構成 |
| 未評価を問題なしにしない | core inconclusive は未採点 |
| 重要I/Oコメント欠落 | `io-comment-gap` とbad fixtureで検証 |
| findingの根拠 | check record由来のevidence/sourceを保持 |
| JSON/MCP向け | `--format json` でscores/checks/top_risks/analysisを返す |
| bad fixture | コメント欠落、重複出力、ワード複数writer、SET/RST分離、曖昧コメント、未使用残骸 |
| good/bad回帰 | good/bad fixture比較でbad側のscore低下とfinding検出を確認 |
