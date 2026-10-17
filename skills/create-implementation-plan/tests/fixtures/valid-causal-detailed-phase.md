<!-- plan-mode: scaled-phase -->
<!-- plan-schema: causal-v1 -->
# Phase 1: reconcile 導入 — owner-integrity

- Overview: `valid-causal-scaled-overview.md`
- 状態: detailed
- 設計ハブ: true
- 入力タイプ: corrective-single
- 診断・モデル正本: `valid-causal-detailed-phase.md`

## 1. このフェーズの目的と前提

membership write の transaction 終端へ owner reconcile を導入する。

- 対象 LCU: LCU-01
- 依存: なし

## 2. 分析と設計

### 2.1 因果分析

| Finding | Input Ref | 観測された failure | 期待動作 | 出典・再現証拠 |
| --- | --- | --- | --- | --- |
| F-01 | I-01 | membership 削除後も旧 current owner が残る | current owner が membership と一致する | `owner_test.go:77` |

#### Cause C-01: projection writer の分散

- 関連 Finding: F-01
- error state / 発生機構: membership と current owner が異なる人物を指す
- 近接原因: delete command が projection を更新しない
- 違反した不変条件: current owner は active owner membership から導出される
- 設計原因・寄与条件: projection writer と導出 predicate が command ごとに複製されている
- 証拠: `rg -n 'CurrentOwnerID =' internal/ownership` で3 writer を確認
- falsifier: delete を含む全 writer が共通導出 helper を呼ぶことを call graph で確認できた場合
- 判定: 採用

### 2.2 解決の概念モデル

#### Model M-01: membership を正とする projection

- 対象 Cause: C-01
- authoritative source: active owner membership
- derived projection: current owner ID
- 責務の owner: ownership repository
- identity / boundary: tenant と aggregate の組
- state / time semantics: transaction 完了時点の active membership を current とする
- 成立させる不変条件: current owner ID は membership から導出した expected ID と一致する

### 2.3 修復モデル

#### Repair R-01: transaction reconcile

- 対象 Model: M-01
- authoritative / observed state: active membership と保存済み current owner ID
- mechanism: expected ID を導出して差分 update する
- trigger: membership write transaction の終端
- enforcement point: repository reconcile helper
- transaction boundary: membership と projection を同じ transaction で確定する
- postcondition: current owner ID が expected ID と一致する
- idempotency: 一致時は update 0件になる
- concurrency control: aggregate row lock 後に membership を読む
- cleanup / migration: 対象外: 今回は online write path の修復だけを行うため
- retired paths: command ごとの直接 projection update を削除する

### 2.4 論理的変更単位

| LCU | Primary Finding | Cause | Target Model | Repair | 併合・分離の根拠 | 実装先 |
| --- | --- | --- | --- | --- | --- | --- |
| LCU-01 | F-01 | C-01 | M-01 | R-01 | 単独: 一つの Finding を一つの postcondition で閉じる | Phase 1, Phase 2 |

#### LCU Split LCU-01: reconcile 導入と writer 統合

- 分割理由: helper を先に固定してから既存 writer を段階的に移す必要があるため
- 中間 postcondition: Phase 1=reconcile helper の単体テストが PASS し、新規 writer から利用できる
- 最終 Gate: `P2-S1` 全 writer が reconcile helper を通り owner 整合性テストが PASS

### 2.5 同型探索

#### Variant V-01: projection writer

- Seed: C-01
- 探索軸: writer と bypass entrypoint
- Query / scope: `rg -n 'CurrentOwnerID|SetCurrentOwner' internal/ownership`
- 結果・証拠: 3 writer はすべて F-01 の再現経路に含まれる
- 扱い: include: F-01 — 同じ Finding の別 entrypoint として一つの postcondition で閉じる
- LCU: LCU-01

### 2.6 解決アプローチ比較（必要な場合のみ）

比較不要: membership が既存 schema 上の唯一の authoritative source であり導出方法が一つに定まるため

### 2.7 設計判断

| 判断 | 採用 | 根拠 | LCU |
| --- | --- | --- | --- |
| enforcement point | repository transaction 終端 | 全 writer が通る一つの境界である | LCU-01 |

## 3. 変更対象ファイル

| パス | 新規/更新 | 目的 | 対応 Step |
| --- | --- | --- | --- |
| `internal/ownership/reconcile.go` | 新規 | projection reconcile | 1 |

## 4. 実装ステップ

### Step 1: reconcile helper を導入する

- Primary LCU: LCU-01
- 変更ファイル: `internal/ownership/reconcile.go`
- 実装内容:
  1. expected ID の導出と差分 update を実装する
  2. 全 membership writer から transaction 終端で呼ぶ
- 完了条件 (DoD): `go test ./internal/ownership/...` が exit 0
- 実行コマンド: `go test ./internal/ownership/...`
- Hard Gate:
  - Command: `go test ./internal/ownership/...`
  - Expect: exit 0

### Step 2: 次フェーズを詳細化する

- Primary LCU: plan-maintenance
- 変更ファイル: `valid-causal-phase2.md`
- 実装内容:
  1. writer 統合 phase の Step、Gate、Todo を詳細化する
- 完了条件 (DoD): `lint_plan.sh --require-detailed valid-causal-phase2.md` が exit 0
- 実行コマンド: `lint_plan.sh --require-detailed valid-causal-phase2.md`
- Hard Gate:
  - Command: `lint_plan.sh --require-detailed valid-causal-phase2.md`
  - Expect: exit 0

## 5. テスト計画

- 自動テスト: `go test ./internal/ownership/...`
- 実環境動作確認: `go run ./cmd/ownership-smoke` が exit 0

## 6. リスクと対策

| リスク | 対策 | 対応する Step / Todo |
| --- | --- | --- |
| lock 順序逆転 | 既存 aggregate lock の内側で実行する | Step 1 |

## 7. 図（必要な場合のみ）

図の省略理由: 一つの transaction helper で完結するため。

## 8. フェーズ Todo

- [ ] Step 1 (LCU-01): `internal/ownership/reconcile.go` 実装（Gate: `go test ./internal/ownership/...`）
- [ ] Step 2 (plan-maintenance): Phase 2 詳細化（Gate: `lint_plan.sh --require-detailed valid-causal-phase2.md`）
