# TODO: owner projection と audit timestamp の独立修復

> 運用契約: このファイルを再作成しない。in-place 更新のみ。再開時は本ファイル全文と正本計画書の不変条件・Hard Gate・現在 Step を再読する。

- 正本計画書: `valid-causal-split-plan.md`
- 診断・モデル正本: `valid-causal-split-plan.md`
- goal prompt: `valid-causal-split-goal-prompt.md`
- 計画検証日時: 2026-07-13 18:00（base: main@789abcd）
- アクティブタスク: Step 1 未着手
- ステータス凡例: `[ ]` 未着手 / `[x]` 完了 / `[!]` blocked / `[-]` cancelled
- 完了条件: 全行が `[x]` または `[-]` に消し込まれていること。
- cancelled の扱い: `[-]` には計画改訂またはユーザー指示の理由が必要。
- 非冪等タスクは実行開始を記録し、副作用を確認してから再実行する。
- 未登録 variant または LCU 境界変更はタスクを blocked にして計画を改訂する。

## Hard Gate 一覧

| Step | Command | Expect | 結果 |
| --- | --- | --- | --- |
| 1 | `go test ./internal/ownership/...` | exit 0 | 未実行 |
| 2 | `go test ./internal/audit/...` | exit 0 | 未実行 |

## Unclear Points 要約

- 未解決: None

## タスク

- [ ] Step 1 (LCU-01): `internal/ownership/reconcile.go` owner reconcile（Gate: `go test ./internal/ownership/...`）
- [ ] Step 2 (LCU-02): `internal/audit/repository.go` timestamp 補完（Gate: `go test ./internal/audit/...`）
- [ ] 実環境動作確認: `go run ./cmd/integrity-smoke`
