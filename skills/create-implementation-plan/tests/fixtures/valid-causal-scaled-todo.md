# TODO: owner 整合性修復

> 運用契約: このファイルを再作成しない。in-place 更新のみ。再開時は正本と現在 Step を再読する。

- 正本計画書: `valid-causal-scaled-overview.md`
- 診断・モデル正本: `valid-causal-detailed-phase.md`
- goal prompt: `valid-causal-scaled-goal-prompt.md`
- 計画検証日時: 2026-07-13 14:00（base: main@456cdef）
- アクティブタスク: P1-S1 未着手
- ステータス凡例: `[ ]` 未着手 / `[x]` 完了 / `[!]` blocked / `[-]` cancelled
- 完了条件: 全行が `[x]` または `[-]` に消し込まれていること。
- cancelled の扱い: `[-]` は理由必須。
- 非冪等タスクは実行開始を記録してから実行する。

## Hard Gate 一覧

| Step | Command | Expect | 結果 |
| --- | --- | --- | --- |
| P1-S1 | `go test ./internal/ownership/...` | exit 0 | 未実行 |
| P1-S2 | `lint_plan.sh --require-detailed valid-causal-phase2.md` | exit 0 | 未実行 |
| P2-S1 | `go test ./internal/ownership/...` | exit 0 | 未実行 |

## Unclear Points 要約

- 未解決: None

## タスク

- [ ] P1-S1 (LCU-01): `internal/ownership/reconcile.go` 実装（Gate: `go test ./internal/ownership/...`）
- [ ] P1-S2 (plan-maintenance): Phase 2 詳細化（Gate: `lint_plan.sh --require-detailed valid-causal-phase2.md`）
- [ ] P2-S1 (LCU-01): `internal/ownership/commands.go` writer 統合（Gate: `go test ./internal/ownership/...`）
