# TODO: health エンドポイント追加

> 運用契約: このファイルを再作成しない。in-place 更新のみ。再開時は本ファイル全文と正本計画書の不変条件・Hard Gate・現在 Step を再読し、現在位置を宣言してから作業する。

- 正本計画書: `valid-full-plan.md`
- goal prompt: `valid-full-goal-prompt.md`
- 計画検証日時: 2026-07-11 12:00（base: main@abc1234）
- アクティブタスク: Step 1 未着手
- ステータス凡例: `[ ]` 未着手 / `[x]` 完了 / `[!]` blocked（理由必須） / `[-]` cancelled（理由必須）
- 完了条件: 全行が `[x]` または `[-]` に消し込まれていること。
- cancelled の扱い: `[-]` にはユーザー指示または計画改訂に紐づく理由が必要。
- 非冪等タスクは実行直前にタスク行へ「実行開始」を追記してから実行する。

## Hard Gate 一覧

| Step | Command | Expect | 結果 |
| --- | --- | --- | --- |
| 1 | `go test ./internal/api` | exit 0 | 未実行 |

## Unclear Points 要約

- 未解決: None

## タスク

- [ ] Step 1: `internal/api/health.go` handler 追加（Gate: `go test ./internal/api`）
- [ ] 実環境動作確認: `curl -s localhost:8080/health`
