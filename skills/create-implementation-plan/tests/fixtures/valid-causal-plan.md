<!-- plan-mode: full -->
<!-- plan-schema: causal-v1 -->
# 実装計画書: current owner の整合性修復

- 作成日時: 2026-07-13 12:00（base: main@def5678）
- 入力タイプ: corrective-bundle(2)
- 診断・モデル正本: `valid-causal-plan.md`
- 進捗台帳: `valid-causal-todo.md`
- goal prompt: `valid-causal-goal-prompt.md`
- 改訂履歴: 2026-07-13 12:00 初版

## 1. 目的

membership の変更後も current owner projection が authoritative membership と一致するようにする。

- In scope: owner 更新と解除の write path
- Out of scope: membership UI と権限体系の変更

## 2. 事前調査結果

| 調査 | コマンド / 対象 | 結果（実測） |
| --- | --- | --- |
| ベースライン | `go test ./internal/ownership/...` | exit 0 |
| write path | `rg -n 'CurrentOwnerID|Membership' internal` | 4件 |

- 未実施調査と理由: なし

## 3. 既存実装の理解

- 現状フロー: membership command → repository write → owner projection の条件付き更新
- 既存制約: membership と owner は同一 transaction で更新できる
- 既存テストと欠け: 追加経路はあるが削除と交代の回帰テストがない
- 読了ファイル一覧: 付録Aに記載

## 4. 因果分析

### 4.1 Finding 台帳

| Finding | Input Ref | 観測された failure | 期待動作 | 出典・再現証拠 |
| --- | --- | --- | --- | --- |
| F-01 | I-01 | 最後の owner membership を削除しても current owner が残る | current owner が空になる | `ownership_test.go:88` |
| F-02 | I-02 | owner membership を交代すると旧 owner が残る | 新 owner だけが current になる | `ownership_test.go:121` |

### 4.2 Cause 仮説と判定

#### Cause C-01: current owner を各 command が個別更新している

- 関連 Finding: F-01, F-02
- error state / 発生機構: membership と current owner projection が異なる人物を指す
- 近接原因: delete と replace の command が owner projection 更新を呼ばない
- 違反した不変条件: current owner は authoritative membership から一意に導出される
- 設計原因・寄与条件: projection の writer が command ごとに分散し、同じ導出判断を複製している
- 証拠: `rg -n 'CurrentOwnerID =' internal/ownership` で4つの writer を確認
- falsifier: 全 membership write path が共通の導出関数を必ず呼ぶことを call graph と test で確認できた場合
- 判定: 採用

## 5. 解決の概念モデル

#### Model M-01: membership を正とする owner projection

- 対象 Cause: C-01
- authoritative source: active な owner role membership の集合
- derived projection: aggregate の current owner ID
- 責務の owner: ownership repository の transaction helper
- identity / boundary: tenant と aggregate の組を整合性境界にする
- state / time semantics: active membership のうち優先順位が最上位の1件を current とする
- 成立させる不変条件: transaction 完了時に current owner は membership から再計算した値と一致する

## 6. 修復モデル

#### Repair R-01: transaction 内 reconcile

- 対象 Model: M-01
- authoritative / observed state: active owner membership と保存済み current owner ID
- mechanism: membership から expected owner を導出し、差分があれば projection を更新する
- trigger: owner role membership を変更する全 command の transaction 終端
- enforcement point: ownership repository の reconcile helper
- transaction boundary: membership 更新と projection 更新を同じ transaction で確定する
- postcondition: current owner ID が expected owner ID と一致し、候補なしなら空である
- idempotency: 同じ membership 状態で再実行した場合は update 0件になる
- concurrency control: aggregate row lock の取得後に membership を読む
- cleanup / migration: 起動時 backfill は別 LCU にせず今回の command 修復対象外とする
- retired paths: command ごとの直接 CurrentOwnerID 代入を削除する

## 7. 論理的変更単位

### 7.1 LCU マップ

| LCU | Primary Finding | Cause | Target Model | Repair | 併合・分離の根拠 | 実装先 |
| --- | --- | --- | --- | --- | --- | --- |
| LCU-01 | F-01, F-02 | C-01 | M-01 | R-01 | 併合: 同じ projection 不変条件と reconcile postcondition で閉じる | Step 1 |

### 7.2 同型探索

#### Variant V-01: current owner の全 writer

- Seed: C-01
- 探索軸: writer と別 command entrypoint
- Query / scope: `rg -n 'CurrentOwnerID|SetCurrentOwner' internal/ownership`
- 結果・証拠: 4 writer のうち2件が同じ不変条件を迂回する
- 扱い: include: F-01, F-02 — 既存 Finding の再現経路として同じ postcondition で閉じる
- LCU: LCU-01

## 8. 解決アプローチ比較（必要な場合のみ）

比較不要: membership が既存契約で唯一の authoritative source と確定しており、projection の導出方式に自由度がないため

## 9. 設計判断

| 判断 | 採用 | 根拠 | LCU |
| --- | --- | --- | --- |
| enforcement point | repository transaction 終端 | 全 write path が通り、原子的に postcondition を強制できる | LCU-01 |

## 10. 不変条件・逸脱禁止

- 触らないパス: `internal/auth/**`
- 変えない挙動・UI: membership API の request と response
- やらないこと: 起動時 backfill と権限体系変更
- 隣接コードのついで改善をしない
- 許可パスリスト: `internal/ownership/**` — 計画成果物と開始前 dirty は照合対象外

## 11. Hard Gate

- 着手前 Gate（Definition of Ready）:
  1. なし（Cause と authority は確定済み）
- Step 完了 Gate 一覧:

| Step | Command | Expect |
| --- | --- | --- |
| 1 | `go test ./internal/ownership/...` | exit 0 |

- Gate 不通過時: 次 Step に進まず todo を blocked にする。

## 12. 変更対象ファイル

| パス | 新規/更新 | 目的 | 対応 Step |
| --- | --- | --- | --- |
| `internal/ownership/reconcile.go` | 新規 | owner projection を一元導出する | 1 |

## 13. 実装ステップ

### Step 1: owner projection を reconcile する

- Primary LCU: LCU-01
- 変更ファイル: `internal/ownership/reconcile.go`
- 実装内容:
  1. expected owner を導出し、全 command の transaction 終端から呼ぶ
  2. 直接代入する旧 writer を削除する
- 完了条件 (DoD): `go test ./internal/ownership/...` が exit 0
- 実行コマンド: `go test ./internal/ownership/...`
- Hard Gate:
  - Command: `go test ./internal/ownership/...`
  - Expect: exit 0

## 14. テスト計画

- 自動テスト: `go test ./internal/ownership/...`
- 手動テスト: owner 削除と交代を API から実行する
- 実環境動作確認: `curl -fsS localhost:8080/test-owner-reconcile` が HTTP 200

## 15. リスクと対策

| リスク | 対策 | 対応する Step / Todo |
| --- | --- | --- |
| lock 順序の逆転 | 既存 aggregate lock の後に membership を読む | Step 1 |

## 16. Unclear Points

`Unclear Points: None（実装外として除外した論点: 起動時 backfill）`

## 17. Resolved Points

| # | 論点 | 決定 | 根拠 | 反映先 |
| --- | --- | --- | --- | --- |
| 1 | authoritative source | membership | repository と schema の確認 | §5 |

## 18. TodoList

- [ ] Step 1 (LCU-01): `internal/ownership/reconcile.go` reconcile 実装（Gate: `go test ./internal/ownership/...`）
- [ ] 実環境動作確認: `curl -fsS localhost:8080/test-owner-reconcile`

## 19. 図

図の省略理由: transaction 内の単一 repository helper で完結するため。

## 20. Definition of Done

1. todo の全行が消し込まれている
2. `go test ./internal/ownership/...` が exit 0
3. `curl -fsS localhost:8080/test-owner-reconcile` が HTTP 200
4. 完了報告に新規 variant、LCU 境界変更、postcondition 変更の有無がある

## 付録A. 読了ファイル一覧

<details><summary>読了ファイル 3 件</summary>

1. `internal/ownership/repository.go` - transaction 境界
2. `internal/ownership/commands.go` - write path
3. `internal/ownership/ownership_test.go` - 再現 test

</details>
