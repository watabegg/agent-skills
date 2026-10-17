<!-- plan-mode: scaled-overview -->
<!-- plan-schema: causal-v1 -->
# 実装計画書 (scaled): owner 整合性修復 — Overview

- 作成日時: 2026-07-13 14:00（base: main@456cdef）
- 入力タイプ: corrective-single
- scaled 判定根拠: 複数リリース phase を跨ぐ
- 進捗台帳: `valid-causal-scaled-todo.md`
- goal prompt: `valid-causal-scaled-goal-prompt.md`
- 診断・モデル正本: `valid-causal-detailed-phase.md`
- 改訂履歴: 2026-07-13 14:00 初版

## 1. 目的

owner membership と current owner projection の不一致を修復する。

- In scope: ownership repository の write path
- Out of scope: membership UI
- 解決モデル: membership を authoritative source とし、transaction 終端で current owner projection を reconcile する
- LCU: 1 findings → 1 LCU

## 2. 不変条件・逸脱禁止（全フェーズ共通）

- 触らないパス: `internal/auth/**`
- 変えない挙動・UI: membership API 契約
- やらないこと: membership UI 変更
- 許可パスリスト: `internal/ownership/**` — 計画成果物と開始前 dirty は除外

## 3. フェーズ一覧

| # | フェーズ | 対象 LCU | 状態 | ファイル | 完了 Gate |
| --- | --- | --- | --- | --- | --- |
| 1 | reconcile 導入 | LCU-01 | detailed | `valid-causal-detailed-phase.md` | `go test ./internal/ownership/...` → exit 0 |
| 2 | writer 統合 | LCU-01 | detailed | `valid-causal-phase2.md` | `go test ./internal/ownership/...` → exit 0 |

## 4. Unclear Points

`Unclear Points: None（実装外として除外した論点: backfill）`

## 5. Resolved Points

| # | 論点 | 決定 | 根拠 | 反映先 |
| --- | --- | --- | --- | --- |
| 1 | authority | membership | schema と writer の調査 | Phase 1 §2 |

## 6. TodoList（フェーズ単位）

- [ ] Phase 1 (LCU-01): reconcile 導入（Gate: `go test ./internal/ownership/...`）
- [ ] Phase 2 (LCU-01): writer 統合（Gate: `go test ./internal/ownership/...`）

## 7. Definition of Done

1. todo の全行が消し込まれている
2. `go test ./internal/ownership/...` が exit 0
3. `go run ./cmd/ownership-smoke` が exit 0
