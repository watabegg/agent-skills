<!-- plan-mode: scaled-phase -->
<!-- plan-schema: causal-v1 -->
# Phase 2: writer 統合 — owner-integrity

- Overview: `valid-causal-scaled-overview.md`
- 状態: detailed
- 設計ハブ: false
- 診断・モデル正本: `valid-causal-detailed-phase.md`

## 1. このフェーズの目的と前提

既存の membership writer を reconcile helper へ統合し、直接 projection update を廃止する。

- 対象 LCU: LCU-01
- 依存: Phase 1 の reconcile helper と単体テストが PASS

## 2. 設計判断（このフェーズ分）

| 判断 | 採用 | 根拠 | LCU |
| --- | --- | --- | --- |
| writer 移行順 | command ごとに置換し同じ Gate で閉じる | LCU Split の最終 postcondition を満たす | LCU-01 |

## 3. 変更対象ファイル

| パス | 新規/更新 | 目的 | 対応 Step |
| --- | --- | --- | --- |
| `internal/ownership/commands.go` | 更新 | 全 writer を reconcile helper へ統合 | 1 |

## 4. 実装ステップ

### Step 1: 全 writer を reconcile helper へ統合する

- Primary LCU: LCU-01
- 変更ファイル: `internal/ownership/commands.go`
- 実装内容:
  1. delete、replace、bulk command の直接 projection update を削除する
  2. transaction 終端で reconcile helper を呼ぶ
- 完了条件 (DoD): `go test ./internal/ownership/...` が exit 0
- 実行コマンド: `go test ./internal/ownership/...`
- Hard Gate:
  - Command: `go test ./internal/ownership/...`
  - Expect: exit 0

## 5. テスト計画

- 自動テスト: `go test ./internal/ownership/...`
- 実環境動作確認: `go run ./cmd/ownership-smoke` が exit 0

## 6. リスクと対策

| リスク | 対策 | 対応する Step / Todo |
| --- | --- | --- |
| 一部 writer の移行漏れ | variant query の writer 集合を AST test で固定する | Step 1 |

## 7. フェーズ Todo

- [ ] Step 1 (LCU-01): `internal/ownership/commands.go` writer 統合（Gate: `go test ./internal/ownership/...`）
