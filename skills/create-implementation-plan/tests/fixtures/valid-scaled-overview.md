<!-- plan-mode: scaled-overview -->
# 実装計画書 (scaled): 通知システム拡張 — Overview

- 作成日時: 2026-07-11 12:00（base: main@abc1234）
- scaled 判定根拠: Step 8以上
- 進捗台帳: `valid-scaled-todo.md`
- goal prompt: `valid-scaled-goal-prompt.md`
- 改訂履歴: 2026-07-11 12:00 初版

## 1. 目的

通知チャネルを email から email + Slack に拡張する。

- In scope: Notifier 抽象と Slack 実装
- Out of scope: webhook チャネル
- 採用メンタルモデル: チャネル抽象+実装差し替え — 正準語彙: Notifier / Send / channel

## 2. 不変条件・逸脱禁止（全フェーズ共通）

- 触らないパス: `internal/auth/**`
- 変えない挙動・UI: 既存 email 通知の文面と送信タイミング
- やらないこと: webhook チャネルの実装
- 許可パスリスト: `internal/notify/**` — この外に出たら停止。計画成果物と開始前 dirty は照合対象外

## 3. フェーズ一覧

| # | フェーズ | 状態 | ファイル | 完了 Gate |
| --- | --- | --- | --- | --- |
| 1 | チャネル基盤 | detailed | `valid-detailed-phase.md` | `go test ./internal/notify` → exit 0 |
| 2 | Slack 実装 | outline | `valid-outline-phase.md` | `go test ./internal/notify` → exit 0 |

## 4. Unclear Points

`Unclear Points: None（実装外として除外した論点: なし）`

## 5. Resolved Points

| # | 論点 | 決定 | 根拠 | 反映先 |
| --- | --- | --- | --- | --- |
| 1 | チャネル抽象の粒度 | Send のみ | ユーザー回答「Sendだけで」 | Phase 1 §2 |

## 6. TodoList（フェーズ単位）

正本は todo.md。

- [ ] Phase 1: チャネル基盤（Gate: `go test ./internal/notify`）
- [ ] Phase 2: Slack 実装（Gate: `go test ./internal/notify`）

## 7. Definition of Done

1. todo.md の全行が消し込まれている
2. cancelled（`[-]`）でない全フェーズの完了 Gate が PASS
3. `go run ./cmd/notify-smoke` が成功する
