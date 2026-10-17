<!-- plan-mode: scaled-phase -->
<!-- plan-schema: causal-v1 -->
# Phase [N]: [name] — [topic]

- Overview: `[docs/plans/<prefix>-<topic>/00-overview.md]`
- 状態: [detailed / outline]
- 設計ハブ: [true / false]
- 入力タイプ: [設計ハブだけ overview と同じ値。後続 phase はこの行を削除]
- 診断・モデル正本: `[最初の detailed phase。設計ハブ自身なら self]`

## 1. このフェーズの目的と前提

[このフェーズで達成すること。1〜2文]

- 対象 LCU: [LCU-01]
- 依存: [前フェーズの完了 / Unclear Point の解消]

## 2. 分析と設計（このフェーズ分）

最初の detailed phase は `設計ハブ: true` とし、次の §2.1〜§2.7 をすべて持つ。後続 phase は `設計ハブ: false` とし、分析本文を複写せず、この節にはその phase 固有の判断表だけを残す。

### 2.1 因果分析

初期入力の単位は独立して検証できる主張とし、一つの報告に複数の failure があれば先に分割する。
bundle の N は2以上とする。
I-01..I-N を重複なく全件割り当て、複数入力を一つの Finding に正規化する場合はカンマ区切りにし、探索起源は `variant: V-01` と書く。

| Finding | Input Ref | 観測された failure / 未充足要求 | 期待動作 | 出典・再現証拠 |
| --- | --- | --- | --- | --- |
| F-01 | I-01 | [観測可能な事象] | [期待する状態] | `[issue、テスト、ログ、path:line]` |

#### Cause C-01: [原因の名前]

- 関連 Finding: F-01
- error state / 発生機構: [内部 error state]
- 近接原因: [直接の発火条件]
- 違反した不変条件: [常に成立すべき条件]
- 設計原因・寄与条件: [データ所有、境界、状態表現、writer]
- 証拠: `[path:line、検索コマンド、テスト、ログ]`
- falsifier: [仮説を反証できる観測]
- 判定: 採用

### 2.2 解決の概念モデル

#### Model M-01: [モデル名]

- 対象 Cause: C-01
- authoritative source: [正となる事実]
- derived projection: [派生状態。非該当なら `対象外: 理由`]
- 責務の owner: [更新責務]
- identity / boundary: [同一性・整合性境界]
- state / time semantics: [current・履歴・競合の意味]
- 成立させる不変条件: [Target Model の不変条件]

### 2.3 修復モデル

#### Repair R-01: [メカニズム]

- 対象 Model: M-01
- authoritative / observed state: [desired / actual]
- mechanism: [修復処理]
- trigger: [起動契機]
- enforcement point: [不変条件の強制点]
- transaction boundary: [境界。非該当なら `対象外: 理由`]
- postcondition: [完了後の状態]
- idempotency: [再実行時の意味]
- concurrency control: [競合制御。非該当なら `対象外: 理由`]
- cleanup / migration: [既存データ。非該当なら `対象外: 理由`]
- retired paths: [廃止する旧経路。非該当なら `対象外: 理由`]

### 2.4 論理的変更単位

| LCU | Primary Finding | Cause | Target Model | Repair | 併合・分離の根拠 | 実装先 |
| --- | --- | --- | --- | --- | --- | --- |
| LCU-01 | F-01 | C-01 | M-01 | R-01 | 単独: [理由] | Phase 1 |

一つの LCU を複数 phase に分ける場合、実装先を `Phase 1, Phase 2` のように列挙し、分割理由と最終 postcondition Gate を記載する。

複数 phase に分ける LCU ごとに次のブロックを追加する。単一 phase の LCU では追加しない。

<!--
#### LCU Split LCU-01: [分割名]

- 分割理由: [一つの phase で閉じられない依存理由]
- 中間 postcondition: Phase 1=[次 phase が依存できる機械判定可能な状態]
- 最終 Gate: `P2-S3` [LCU 全体の Repair postcondition を閉じる Gate]
-->

最終 phase が outline の間も未来の Gate ID を予約する。
詳細化後は、その ID の Hard Gate と Primary LCU がこの契約に一致しなければならない。

### 2.5 同型探索

#### Variant V-01: [探索対象]

- Seed: F-01
- 探索軸: [predicate / writer / state transition / duplicate decision / entrypoint / bypass]
- Query / scope: `[rg -n '...' path]`
- 結果・証拠: [件数と対象]
- 扱い: [include: F-01 — 理由 / exclude: 理由]
- LCU: LCU-01

### 2.6 解決アプローチ比較（必要な場合のみ）

比較不要: [現実的な案が一つに定まる具体的理由]

複数案が残る場合は上の行を削除し、full テンプレートと同じ星取り表を判断または LCU ごとに置く。

### 2.7 設計判断

| 判断 | 採用 | 根拠 | LCU |
| --- | --- | --- | --- |
| [判断] | [決定] | [不変条件・証拠との接続] | LCU-01 |

## 3. 変更対象ファイル

| パス | 新規/更新 | 目的 | 対応 Step |
| --- | --- | --- | --- |
| `[path]` | 更新 | [目的] | 1 |

## 4. 実装ステップ

### Step 1: [名前]

- Primary LCU: LCU-01
- Shared LCU: [LCU-02 — 共有理由。共有しない場合はこの行を削除]
- 変更ファイル: `[path]`
- 実装内容:
  1. [作業]
- 完了条件 (DoD): `[command]` が [機械判定可能な値]
- 実行コマンド: `[command]`
- 失敗時の切り分け: [任意]
- Hard Gate:
  - Command: `[command]`
  - Expect: [exit 0 / 0件]

### Step [最終番号]: 次フェーズの詳細化（最後のフェーズでは削除）

- Primary LCU: plan-maintenance
- 変更ファイル: `[docs/plans/<prefix>-<topic>/20-phase2-<name>.md]`
- 実装内容:
  1. outline を detailed に更新する
  2. todo.md に Step と Hard Gate を追記する
  3. 新しい Finding / Cause / variant があれば診断・モデル正本を先に改訂する
  4. overview の改訂履歴を更新する
- 完了条件 (DoD): `~/.codex/skills/create-implementation-plan/scripts/lint_plan.sh --require-detailed [次フェーズファイル]` が exit 0
- 実行コマンド: `~/.codex/skills/create-implementation-plan/scripts/lint_plan.sh --require-detailed [次フェーズファイル]`
- Hard Gate:
  - Command: `~/.codex/skills/create-implementation-plan/scripts/lint_plan.sh --require-detailed [次フェーズファイル]`
  - Expect: exit 0

## 5. テスト計画

- 自動テスト: `[command]`
- 実環境動作確認: `[command]`

## 6. リスクと対策

| リスク | 対策 | 対応する Step / Todo |
| --- | --- | --- |
| [リスク] | [対策] | [Step 1 / todo #3] |

## 7. 図（必要な場合のみ）

Repair の trigger、enforcement point、postcondition を図へ反映する。分岐・依存がなければ省略理由を書く。

## 8. フェーズ Todo（todo.md への転記元）

- [ ] Step 1 (LCU-01): `[path]` [やること]（Gate: `[command]`）
- [ ] Step 2 (plan-maintenance): 次フェーズ詳細化（Gate: lint exit 0）
