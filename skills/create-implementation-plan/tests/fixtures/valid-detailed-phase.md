<!-- plan-mode: scaled-phase -->
# Phase 1: 通知チャネル基盤 — notification-v2

- Overview: `valid-scaled-overview.md`
- 状態: detailed

## 1. このフェーズの目的と前提

通知チャネルの interface と email 実装を追加する。

- 依存: なし（最初のフェーズ）

## 2. 設計判断（このフェーズ分）

### 2.1 変更のメンタルモデル（星取り表・全フェーズ横断）

| 軸 | モデル1: チャネル抽象+実装差し替え | モデル2: チャネルごとの個別送信関数 |
| --- | --- | --- |
| 要求・不変条件・安全性を満たすか | PASS 既存 email 挙動を変えない | PASS ただし呼び出し側の分岐が増える |
| 消える特殊ケース・分岐 | ◎ 呼び出し側のチャネル分岐が消える | × チャネル追加ごとに全呼び出し側を修正 |
| 変更増幅（触る箇所） | ○ interface 導入で初回のみ広め | △ 追加のたびに増える |
| 既存構造との一致 | ○ 既存の store interface と同じ流儀 | ○ 現状の直書きと同じ |
| 歪み（無理が出る部分） | チャネル固有の rich オプションが interface を太らせうる | 分岐が呼び出し側に漏れ続ける |
| このモデルが勝つ現在条件 | チャネルが3つ以上になる今回の要求 | チャネルが email 1つのままなら十分 |
| 判定 | 採用 | 不採用 |

- 決め手の軸: 消える特殊ケース。interface が太るリスクは Send のみに絞って受ける
- 接ぎ木: なし

### 2.2 設計判断表

| # | 判断項目 | 採用 | 非採用案 | 理由 |
| --- | --- | --- | --- | --- |
| 1 | チャネル抽象 | Notifier interface に Send のみ | チャネルごとに個別関数を生やす案 | 呼び出し側をチャネル数から独立させるため |

## 3. 変更対象ファイル

| パス | 新規/更新 | 目的 | 対応 Step |
| --- | --- | --- | --- |
| `internal/notify/notifier.go` | 新規 | interface 定義 | 1 |

## 4. 実装ステップ

### Step 1: Notifier interface の定義

- 変更ファイル: `internal/notify/notifier.go`
- 実装内容:
  1. Send メソッドを持つ interface を定義
- 完了条件 (DoD): `go build ./internal/notify` が exit 0
- 実行コマンド: `go build ./internal/notify`
- Hard Gate:
  - Command: `go build ./internal/notify`
  - Expect: exit 0

### Step 2: 次フェーズの詳細化

- 変更ファイル: `valid-outline-phase.md`
- 実装内容:
  1. outline を detailed に更新し、「状態: detailed」へ変更する
- 完了条件 (DoD): `lint_plan.sh --require-detailed valid-outline-phase.md` が exit 0
- 実行コマンド: `lint_plan.sh --require-detailed valid-outline-phase.md`
- Hard Gate:
  - Command: `lint_plan.sh --require-detailed valid-outline-phase.md`
  - Expect: exit 0

## 5. テスト計画

- 自動テスト: `go test ./internal/notify`
- 実環境動作確認: `go run ./cmd/notify-smoke` で実メール1通の送信を確認

## 6. リスクと対策

| リスク | 対策 | 対応する Step / Todo |
| --- | --- | --- |
| interface 変更の波及 | 利用箇所を Phase 2 以降で段階移行 | Step 2 |

## 7. 図（必要な場合のみ）

このフェーズは interface 定義のみで分岐がないため省略。

## 8. フェーズ Todo（todo.md への転記元）

- [ ] Step 1: `internal/notify/notifier.go` interface 定義（Gate: `go build ./internal/notify`）
- [ ] Step 2: 次フェーズ詳細化（Gate: `lint_plan.sh --require-detailed valid-outline-phase.md`）
