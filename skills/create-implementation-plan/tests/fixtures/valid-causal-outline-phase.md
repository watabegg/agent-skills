<!-- plan-mode: scaled-phase -->
<!-- plan-schema: causal-v1 -->
# Phase 2: writer 統合 — owner-integrity

- Overview: `valid-causal-scaled-overview.md`
- 状態: outline
- 設計ハブ: false
- 診断・モデル正本: `valid-causal-detailed-phase.md`

## 1. このフェーズの目的と前提

既存の membership writer を reconcile helper へ統合し、直接 projection update を廃止する。

- 対象 LCU: LCU-01
- 依存: Phase 1 の reconcile helper と単体テストが PASS

## 2. 変更対象ファイル

| パス | 新規/更新 | 目的 |
| --- | --- | --- |
| `internal/ownership/commands.go` | 更新 | 全 writer を reconcile helper へ統合 |
