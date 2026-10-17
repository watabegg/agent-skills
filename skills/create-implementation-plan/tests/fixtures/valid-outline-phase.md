<!-- plan-mode: scaled-phase -->
# Phase 2: Slack チャネル — notification-v2

- Overview: `valid-scaled-overview.md`
- 状態: outline

## 1. このフェーズの目的と前提

Slack チャネル実装を追加する。Phase 1 の Notifier interface に依存。

## 2. 変更対象ファイル

| パス | 新規/更新 | 目的 |
| --- | --- | --- |
| `internal/notify/slack.go` | 新規 | Slack 送信実装 |
