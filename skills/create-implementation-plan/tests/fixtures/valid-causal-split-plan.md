<!-- plan-mode: full -->
<!-- plan-schema: causal-v1 -->
# 実装計画書: owner projection と audit timestamp の独立修復

- 作成日時: 2026-07-13 18:00（base: main@789abcd）
- 入力タイプ: corrective-bundle(2)
- 診断・モデル正本: `valid-causal-split-plan.md`
- 進捗台帳: `valid-causal-split-todo.md`
- goal prompt: `valid-causal-split-goal-prompt.md`
- 改訂履歴: 2026-07-13 18:00 初版

## 1. 目的

owner projection の残留と audit timestamp の欠落を、それぞれの authority と postcondition に沿って修復する。

- In scope: ownership projection と audit event writer
- Out of scope: 権限体系と audit retention

## 2. 事前調査結果

| 調査 | コマンド / 対象 | 結果（実測） |
| --- | --- | --- |
| owner writer | `rg -n 'CurrentOwnerID' internal/ownership` | 3件 |
| audit writer | `rg -n 'OccurredAt|AuditEvent' internal/audit` | 2件 |

- 未実施調査と理由: なし

## 3. 既存実装の理解

- 現状フロー: membership command と audit command は別 repository で完結する
- 既存制約: owner projection と audit event に transaction 共有はない
- 既存テストと欠け: owner 削除と timestamp 未指定の回帰ケースがない
- 読了ファイル一覧: 付録Aに記載

## 4. 因果分析

### 4.1 Finding 台帳

| Finding | Input Ref | 観測された failure | 期待動作 | 出典・再現証拠 |
| --- | --- | --- | --- | --- |
| F-01 | I-01 | 最後の owner membership 削除後も current owner が残る | current owner が空になる | `ownership_test.go:88` |
| F-02 | I-02 | timestamp 未指定の audit event が時刻ゼロで保存される | server clock の時刻で保存される | `audit_test.go:52` |

### 4.2 Cause 仮説と判定

#### Cause C-01: owner projection の delete writer が欠落している

- 関連 Finding: F-01
- error state / 発生機構: membership が空でも current owner ID が旧値のまま残る
- 近接原因: delete command が owner projection の導出処理を呼ばない
- 違反した不変条件: current owner は active owner membership から導出される
- 設計原因・寄与条件: projection writer が command ごとに分散している
- 証拠: `rg -n 'CurrentOwnerID =' internal/ownership` で delete writer の欠落を確認
- falsifier: delete transaction 終端で既に共通 reconcile が必ず呼ばれると call graph で確認できた場合
- 判定: 採用

#### Cause C-02: audit 時刻の補完責務が caller に分散している

- 関連 Finding: F-02
- error state / 発生機構: OccurredAt が zero value の event が永続化される
- 近接原因: API caller が timestamp を省略し、repository が補完しない
- 違反した不変条件: 保存済み audit event は確定した発生時刻を持つ
- 設計原因・寄与条件: server clock の owner が定義されず、入力値をそのまま保存している
- 証拠: `go test ./internal/audit -run TestMissingOccurredAt` が fail
- falsifier: repository が zero value を server clock で補完すると実装と test で確認できた場合
- 判定: 採用

## 5. 解決の概念モデル

#### Model M-01: membership を正とする owner projection

- 対象 Cause: C-01
- authoritative source: active owner membership の集合
- derived projection: aggregate の current owner ID
- 責務の owner: ownership repository
- identity / boundary: tenant と aggregate の組
- state / time semantics: transaction 完了時点の active membership を current とする
- 成立させる不変条件: current owner は membership から導出した expected owner と一致する

#### Model M-02: server clock を正とする audit occurrence

- 対象 Cause: C-02
- authoritative source: audit repository が保持する server clock
- derived projection: audit event の OccurredAt
- 責務の owner: audit repository
- identity / boundary: 一つの audit event ID
- state / time semantics: caller 指定時刻がなければ保存 transaction 内の server time を採用する
- 成立させる不変条件: 保存済み event の OccurredAt は zero value でない

## 6. 修復モデル

#### Repair R-01: owner transaction reconcile

- 対象 Model: M-01
- authoritative / observed state: active membership と保存済み current owner ID
- mechanism: expected owner を導出し、差分があれば projection を更新する
- trigger: owner membership write transaction の終端
- enforcement point: ownership repository の reconcile helper
- transaction boundary: membership 更新と projection 更新を同じ transaction で確定する
- postcondition: current owner ID が expected owner ID と一致する
- idempotency: 一致時は update 0件になる
- concurrency control: aggregate row lock 後に membership を読む
- cleanup / migration: 対象外: online write path の修復だけを行うため
- retired paths: command ごとの直接 projection update を削除する

#### Repair R-02: audit timestamp normalization

- 対象 Model: M-02
- authoritative / observed state: caller の OccurredAt と repository の server clock
- mechanism: zero value の場合だけ server clock で OccurredAt を補完する
- trigger: audit event insert の直前
- enforcement point: audit repository の normalize helper
- transaction boundary: 補完後の event insert 一件を同じ transaction で確定する
- postcondition: 保存された OccurredAt が zero value でない
- idempotency: 指定済み時刻は変更せず、再実行しても同じ値を保持する
- concurrency control: 対象外: event ID ごとの append-only insert であるため
- cleanup / migration: 対象外: 既存 event の履歴時刻は推定更新しないため
- retired paths: API caller ごとの timestamp 補完を削除する

## 7. 論理的変更単位

### 7.1 LCU マップ

| LCU | Primary Finding | Cause | Target Model | Repair | 併合・分離の根拠 | 実装先 |
| --- | --- | --- | --- | --- | --- | --- |
| LCU-01 | F-01 | C-01 | M-01 | R-01 | 分離: membership projection の整合 postcondition だけで閉じる | Step 1 |
| LCU-02 | F-02 | C-02 | M-02 | R-02 | 分離: audit event の時刻確定 postcondition だけで閉じる | Step 2 |

### 7.2 同型探索

#### Variant V-01: owner projection writer

- Seed: C-01
- 探索軸: writer と command entrypoint
- Query / scope: `rg -n 'CurrentOwnerID|SetCurrentOwner' internal/ownership`
- 結果・証拠: delete を含む3 writer を確認
- 扱い: include: F-01 — 同じ projection postcondition の再現経路である
- LCU: LCU-01

#### Variant V-02: audit event writer

- Seed: C-02
- 探索軸: writer と timestamp predicate
- Query / scope: `rg -n 'OccurredAt|AuditEvent' internal/audit`
- 結果・証拠: repository insert 一入口だけを確認
- 扱い: include: F-02 — 同じ zero timestamp postcondition の再現経路である
- LCU: LCU-02

## 8. 解決アプローチ比較（必要な場合のみ）

比較不要: 二つの Finding は authority、owner、postcondition が異なり、既存 repository 境界から各方式が一つに定まるため

## 9. 設計判断

| 判断 | 採用 | 根拠 | LCU |
| --- | --- | --- | --- |
| owner enforcement point | ownership transaction 終端 | 全 membership writer が通る | LCU-01 |
| audit clock owner | audit repository | 全 event insert が通る | LCU-02 |

## 10. 不変条件・逸脱禁止

- 触らないパス: `internal/auth/**`
- 変えない挙動・UI: membership API と audit API の response
- やらないこと: 権限変更と既存 audit event の推定 backfill
- 隣接コードのついで改善をしない
- 許可パスリスト: `internal/ownership/**`, `internal/audit/**` — 計画成果物と開始前 dirty は照合対象外

## 11. Hard Gate

- 着手前 Gate（Definition of Ready）:
  1. なし（両 Cause と authority は確定済み）
- Step 完了 Gate 一覧:

| Step | Command | Expect |
| --- | --- | --- |
| 1 | `go test ./internal/ownership/...` | exit 0 |
| 2 | `go test ./internal/audit/...` | exit 0 |

- Gate 不通過時: 次 Step に進まず todo を blocked にする。

## 12. 変更対象ファイル

| パス | 新規/更新 | 目的 | 対応 Step |
| --- | --- | --- | --- |
| `internal/ownership/reconcile.go` | 新規 | owner projection を再導出する | 1 |
| `internal/audit/repository.go` | 更新 | zero timestamp を補完する | 2 |

## 13. 実装ステップ

### Step 1: owner projection を reconcile する

- Primary LCU: LCU-01
- 変更ファイル: `internal/ownership/reconcile.go`
- 実装内容:
  1. expected owner を導出し、membership write の transaction 終端から呼ぶ
- 完了条件 (DoD): `go test ./internal/ownership/...` が exit 0
- 実行コマンド: `go test ./internal/ownership/...`
- Hard Gate:
  - Command: `go test ./internal/ownership/...`
  - Expect: exit 0

### Step 2: audit timestamp を repository で補完する

- Primary LCU: LCU-02
- 変更ファイル: `internal/audit/repository.go`
- 実装内容:
  1. zero value の OccurredAt だけ server clock で補完する
- 完了条件 (DoD): `go test ./internal/audit/...` が exit 0
- 実行コマンド: `go test ./internal/audit/...`
- Hard Gate:
  - Command: `go test ./internal/audit/...`
  - Expect: exit 0

## 14. テスト計画

- 自動テスト: `go test ./internal/ownership/... ./internal/audit/...`
- 手動テスト: owner 削除と timestamp 省略 event を API から実行する
- 実環境動作確認: `go run ./cmd/integrity-smoke` が exit 0

## 15. リスクと対策

| リスク | 対策 | 対応する Step / Todo |
| --- | --- | --- |
| owner lock 順序の逆転 | 既存 aggregate lock の内側で reconcile する | Step 1 |
| caller 指定時刻の上書き | zero value の場合だけ補完する test を置く | Step 2 |

## 16. Unclear Points

`Unclear Points: None（実装外として除外した論点: audit backfill）`

## 17. Resolved Points

| # | 論点 | 決定 | 根拠 | 反映先 |
| --- | --- | --- | --- | --- |
| 1 | owner authority | membership | schema と writer の確認 | §5 M-01 |
| 2 | audit clock owner | repository | insert path の確認 | §5 M-02 |

## 18. TodoList

- [ ] Step 1 (LCU-01): `internal/ownership/reconcile.go` owner reconcile（Gate: `go test ./internal/ownership/...`）
- [ ] Step 2 (LCU-02): `internal/audit/repository.go` timestamp 補完（Gate: `go test ./internal/audit/...`）
- [ ] 実環境動作確認: `go run ./cmd/integrity-smoke`

## 19. 図

図の省略理由: 二つの repository 境界が独立し、相互の呼び出しや状態遷移がないため。

## 20. Definition of Done

1. todo の全行が消し込まれている
2. `go test ./internal/ownership/... ./internal/audit/...` が exit 0
3. `go run ./cmd/integrity-smoke` が exit 0
4. 完了報告に新規 variant、LCU 境界、postcondition の変更有無がある

## 付録A. 読了ファイル一覧

<details><summary>読了ファイル 4 件</summary>

1. `internal/ownership/repository.go` - owner transaction
2. `internal/ownership/ownership_test.go` - owner 再現 test
3. `internal/audit/repository.go` - audit insert
4. `internal/audit/audit_test.go` - timestamp 再現 test

</details>
