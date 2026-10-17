<!-- plan-mode: full -->
# 実装計画書: health エンドポイント追加

- 作成日時: 2026-07-11 12:00（base: main@abc1234）
- 進捗台帳: `valid-full-todo.md`
- goal prompt: `valid-full-goal-prompt.md`
- 改訂履歴: 2026-07-11 12:00 初版

## 1. 目的

API サーバーに死活監視用の GET /health を追加する。

- In scope: /health エンドポイントとそのテスト
- Out of scope: 認証、メトリクス出力

## 2. 事前調査結果

| 調査 | コマンド / 対象 | 結果（実測） |
| --- | --- | --- |
| ベースライン | `go test ./...` | exit 0 |

- 未実施調査と理由: なし

## 3. 既存実装の理解

- 現状フロー: main.go → router 登録 → handler
- 既存制約: ルーティングは chi を使用
- 既存テストと欠け: handler 単体テストあり、E2E なし
- 読了ファイル一覧: 付録Aに記載

## 4. 設計判断

### 4.1 変更のメンタルモデル（星取り表）

| 軸 | モデル1: 薄いハンドラ追加 | モデル2: 監視サブシステム新設 |
| --- | --- | --- |
| 要求・不変条件・安全性を満たすか | PASS 死活監視の要求を満たし認可要件なし | PASS ただし Out of scope のメトリクスに踏み込む |
| 消える特殊ケース・分岐 | ◎ 認証・状態・依存の考慮が不要 | △ 基盤の抽象が増える |
| 変更増幅（触る箇所） | ◎ 1ファイル | × ディレクトリ新設 |
| 既存構造との一致 | ○ 既存 router の流儀のまま | △ 新しい層を持ち込む |
| 監視要件との適合 | ○ 死活のみ | ◎ 将来のメトリクスまで一元化 |
| 歪み（無理が出る部分） | 依存先ヘルスチェックは表現できない | 今回の要求には過剰で Out of scope を引き込む |
| このモデルが勝つ現在条件 | In scope が死活監視のみである現状 | メトリクス出力が今回の要求に含まれる場合 |
| 判定 | 採用 | 不採用 |

- 決め手の軸: 消える特殊ケースと変更増幅。将来の拡張性を捨てるトレードオフを受け入れる
- 接ぎ木: なし

### 4.2 設計判断表

| # | 判断項目 | 採用 | 非採用案 | 理由 |
| --- | --- | --- | --- | --- |
| 1 | レスポンス形式 | 200 で `{"status":"ok"}` | 204 No Content を返す案 | 監視ツールが body を検証できるようにするため |

迷いが残る判断はない。

設計セルフチェック:

- [x] 重要判断に本気の非採用案がある
- [x] インターフェースは最頻の使い方が最も簡単
- [x] 同じ設計判断が複数モジュールに映っていない
- [x] 頼まれていない機能を入れていない
- [x] エラーは意味論の再定義で消せないか検討済み

## 5. 不変条件・逸脱禁止

- 触らないパス: `internal/auth/**`
- 変えない挙動・UI: 既存ルートのレスポンス
- やらないこと: メトリクス出力の追加
- 隣接コードの「ついで改善」をしない
- 許可パスリスト: `internal/api/**` — 実装中の diff がこの外に出たら停止して報告

## 6. Hard Gate

- 着手前 Gate（Definition of Ready）:
  1. なし（未確定仕様なし）
- Step 完了 Gate 一覧:

| Step | Command | Expect |
| --- | --- | --- |
| 1 | `go test ./internal/api` | exit 0 |

- Gate 不通過時の扱い: 次 Step に進まない。todo.md を更新して原因を記録する。

## 7. 変更対象ファイル

| パス | 新規/更新 | 目的 | 対応 Step |
| --- | --- | --- | --- |
| `internal/api/health.go` | 新規 | handler 追加 | 1 |

## 8. 実装ステップ

### Step 1: health handler の追加

- 変更ファイル: `internal/api/health.go`
- 実装内容:
  1. GET /health で 200 と `{"status":"ok"}` を返す handler を追加
- 完了条件 (DoD): `go test ./internal/api` が exit 0
- 実行コマンド: `go test ./internal/api`
- Hard Gate:
  - Command: `go test ./internal/api`
  - Expect: exit 0

## 9. テスト計画

- 自動テスト: `go test ./internal/api`
- 手動テスト: なし
- 実環境動作確認: `curl -s localhost:8080/health` が 200 を返す

## 10. リスクと対策

| リスク | 対策 | 対応する Step / Todo |
| --- | --- | --- |
| ルート衝突 | router 登録一覧を目視確認 | Step 1 |

## 11. Unclear Points

`Unclear Points: None（実装外として除外した論点: なし）`

## 12. Resolved Points

| # | 論点 | 決定 | 根拠 | 反映先 |
| --- | --- | --- | --- | --- |
| 1 | ステータスコード | 200 | ユーザー回答「200で」 | §4 |

## 13. TodoList

正本は todo.md。

- [ ] Step 1: `internal/api/health.go` handler 追加（Gate: `go test ./internal/api`）
- [ ] 実環境動作確認: `curl -s localhost:8080/health`

## 14. 図

図の省略理由: 単一 handler の追加で分岐・外部依存がないため。

## 15. Definition of Done

1. todo.md の全行が消し込まれている
2. `go test ./internal/api` が exit 0
3. `curl -s localhost:8080/health` が 200 を返す

## 付録A. 読了ファイル一覧

<details><summary>読了ファイル 2 件</summary>

1. `internal/api/router.go` - ルート登録
2. `internal/api/health_test.go` - 既存テストの流儀

</details>
