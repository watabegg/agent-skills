---
name: implementation-plan
description: 既存コードベースを調査し、観測事象から原因、不変条件、解決の概念モデル、修復モデル、論理的変更単位、同型variantまで追跡可能な実装計画書を作る。ユーザーが「実装計画書」「実装プラン」「修正計画」「fix plan」「開発計画」「設計方針を作って」「実装計画を立てて」「計画かいて」など計画作成を依頼したときに使う。計画書・TODO台帳・goal promptを生成し、可能な調査を先に実施し、残る不明点を Unclear Points として提示する。
---

# Implementation Plan

実装計画は、報告された場所を修正手順へ変換する文書ではない。
観測された failure または未充足要求を、証拠付きの Cause、Target Model、Repair、Logical Change Unit（LCU）へ変換し、同じ原因を持つ未知の variant まで探索する。

成果物は原則3点セットとする。

- plan.md: 設計契約の正本。
- todo.md: 実装中に更新する進捗台帳。
- goal-prompt.md: 後続エージェントの起動契約。

lite だけは plan.md + todo.md を標準とし、goal-prompt.md は要求された場合に作る。

## 大原則

1. 推測だけで計画を書かない。主要経路を読み、実行可能な調査を先に終える。
2. failure、内部 error state、近接原因、設計原因を混同しない。
3. 実装パターンを先に選ばない。authoritative source、derived state、owner、不変条件を先に定める。
4. 因果関係は多対多でよい。Primary Finding の LCU 所属だけを排他的にする。
5. 既知の報告だけを直さず、同じ predicate、writer、状態遷移、入口、旧経路を探索する。
6. 現実的な複数案が残る判断だけ比較する。帳票を埋めるための対案を捏造しない。
7. ジュニアエンジニアが実装と検証を一意に進められる粒度へ分解する。
8. 実装フェーズへ届ける規律は todo.md と goal-prompt.md にも埋め込む。
9. 提出前に finalize を通す。不合格の計画書は納品しない。

## モードと schema

新規計画は冒頭5行以内に次の2行を置く。

```markdown
<!-- plan-mode: full -->
<!-- plan-schema: causal-v1 -->
```

過去の marker なし計画は legacy として従来 lint を維持する。新規計画で legacy を選ばない。

| モード | 条件 | テンプレート |
| --- | --- | --- |
| lite | 変更3ファイル以内かつ diff を一文で説明できる | `references/plan-lite-template.md` |
| full | 上記以外。迷ったらこれ | `references/plan-template.md` |
| scaled | Step 8以上、Todo 26件以上、変更31ファイル以上のいずれか | `references/scale-mode.md` |

schema、認証・権限、決済、データ削除・移行、公開 API 契約に触れる変更は lite 禁止とする。
複数報告を束ねる `corrective-bundle(N)` も、入力被覆と LCU マップが必要なため lite 禁止とする。

scaled の mode は overview が `scaled-overview`、phase が `scaled-phase` である。`plan-mode: scaled` は使わない。

## 実行フロー

### 1. 成果物パスと入力を固定する

1. `rg --files` と `rg -n` で影響範囲の当たりを付け、モードを暫定判定する。
2. リポジトリの docs 規約に従って成果物パスを決める。規約がなければ `docs/plans/` を使う。
3. 入力タイプを `corrective-single`、`corrective-bundle(N)`、`feature-gap` から宣言する。bundle の N は2以上とする。
4. ユーザー要求を1〜3文で言い換え、In scope と Out of scope を明示する。

feature-gap の Finding は「要求された workflow が現行 system では成立しない」という未充足要求である。架空の障害を作らない。

### 2. 既存実装と実測を把握する

1. 入口、主要処理、write path、read path、保存・出力、既存テストを追う。
2. 影響範囲の主要ファイルは抜粋でなく全文を読む。
3. ベースラインテストを実行する。
4. 外部依存は正常、異常、境界を最低1件ずつ実測する。
5. 永続化が絡む場合は schema、authority、writer、移行要否を確認する。
6. 読了ファイルと未実施調査の理由を計画へ残す。

secret、token、個人データを実測記録へ書かない。

### 3. Finding と Cause を確定する

`references/design-principles.md` を全文読んでから分析する。

1. 独立して再現または確認できる failure / 未充足要求を Finding 台帳へ正規化する。
2. 初期入力の独立して検証できる主張へ I-01..I-N を振り、Input Ref から Finding への全件被覆を残す。
3. 各 Finding について、error state、近接原因、違反不変条件、設計原因・寄与条件を分ける。
4. Cause ごとに証拠と falsifier を置き、採用、棄却、未確定を判定する。
5. 未確定 Cause が実装を左右する場合は Unclear Points へ送る。

`because` の収束は Cause 候補の生成に使ってよい。ただし「所有権の問題」のような傘語だけで Cause を確定しない。

### 4. 解決の概念モデルと Repair を定める

Cause からコード変更へ飛ばず、Target Model を先に書く。

- authoritative source。
- derived projection。
- owner / writer。
- identity / consistency boundary。
- state / time semantics。
- 成立させる不変条件。

次に Repair を定める。

- mechanism、trigger、enforcement point。
- transaction boundary、postcondition。
- idempotency、concurrency control。
- cleanup / migration、retired paths。

reconcile、状態機械、台帳と投影、単一 writer は Repair の候補であり、Target Model の代わりではない。

### 5. LCU を確定して variant を探索する

同じ不変条件、Target Model、Repair postcondition で閉じ、一つの変更単位として検証できる Finding を同じ LCU にする。
場所や傘語だけで併合しない。
独立している場合は M=N のまま分離する。

各 Finding は Primary LCU にちょうど1回だけ属する。Cause、Model、Repair は複数 LCU から参照してよい。

各 LCU について、次から該当する軸を実際に検索する。

- 同じ predicate / current 判定。
- 同じ状態への writer。
- 同じ状態遷移。
- 同じ判断の複製。
- 別 endpoint、job、event consumer。
- 新しい enforcement point を迂回する旧経路。

検索コマンド、scope、結果、include / exclude の理由を残す。include した事象は Finding 台帳と LCU へ戻す。

### 6. 必要な判断だけ比較する

要求と既存契約から方式が一つに定まる場合は、`比較不要:` にこの案件固有の理由を書く。

現実的な案が複数残る場合は、判断または LCU ごとに星取り表で比較する。
要求適合、差が出る軸、歪み、勝つ現在条件、判定を含める。
Pros / Cons 表は使わない。

本文を書き切る前にユーザー判断が必要な方式差がある場合だけ、方式サマリをチャットで先出しする。
実装詳細で解消できる論点のために作業を止めない。

### 7. Step と Hard Gate を作る

各 Step は次を持つ。

- Primary LCU。共有基盤 Step は理由付き Shared LCU を追加する。
- 変更ファイル。
- 実装内容。
- 完了条件（DoD）と実行コマンド。
- Hard Gate の Command と機械判定可能な Expect。

通常 Step は一つの Primary LCU を担当する。scaled の次 phase 詳細化 Step だけは `plan-maintenance` を使う。

Hard Gate は Repair postcondition または variant coverage を検証する。
Expect に「正しく動く」などの判定不能語を書かない。
テスト計画には実環境動作確認を最低1件入れる。

### 8. スコープと Unclear Points を固定する

不変条件・逸脱禁止には、触らないパス、変えない挙動、やらないこと、許可パスリストを書く。隣接コードのついで改善を禁止する。

Unclear Points は Point / Why / Impact / Need / Fallback / 推奨で提示する。納品時は番号と推奨値を先出しし、ユーザーが「3で」のように返せる形にする。

### 9. todo.md と goal-prompt.md を作る

`references/todo-template.md` と `references/goal-prompt-template.md` に従う。

- todo.md に分析本文を複写しない。診断・モデル正本への参照と、正本に完全一致する Primary LCU tag を持たせる。
- 未登録 variant や LCU 境界変更を発見したら、実装を勝手に広げず `[!]` にして plan を改訂する。
- goal-prompt.md に許可パス、正本参照、preflight、再開手順、逸脱報告契約を含める。

### 10. 図を必要な場合だけ作る

API・非同期は sequence、依存・データフローは flowchart、状態遷移は state diagram、移行は flowchart と対応表を使う。
Repair の trigger、enforcement point、postcondition を図へ反映する。

Mermaid source は本文へインラインで置く。分岐・依存がなければ `図の省略理由:` を書く。

### 11. finalize して納品する

```bash
~/.codex/skills/create-implementation-plan/scripts/lint_plan.sh --finalize <plan.md>
```

scaled は `00-overview.md` を渡す。finalize は bundle の相互参照、phase、LCU の正本、Gate、許可パスを検査し、PASS 後に HTML view を生成する。

納品メッセージには成果物パス、Unclear Points、計画書一式を単独 commit する提案を含める。未 commit の計画は別 checkout や cloud agent に届かない。

## scaled の設計正本

scaled では最初の detailed phase を `設計ハブ: true` にし、Finding、Cause、Model、Repair、LCU、variant の唯一の正本にする。
overview は一文の解決モデルと正本参照だけを持つ。

後続 phase は `設計ハブ: false` とし、分析を複写しない。新しい Finding や variant を発見したら、最初の detailed phase を改訂して finalize し直す。

フェーズ境界は LCU 境界へ合わせる。一つの LCU を複数 phase へ分ける場合は依存理由と最終 postcondition Gate を正本へ書く。

## 実装フェーズとの接続

- 実装は `$codex-impl` に委ねる。
- 進捗は todo.md だけを更新する。
- plan.md は Cause、Model、Repair、LCU、variant、スコープなどの契約変更時だけ改訂する。
- 完了報告の「計画書からの逸脱」に、新規 variant、LCU 境界変更、Repair postcondition 変更を含める。

## 提出前の目視確認

1. Finding から Step / Gate まで ID で追えるか。
2. Cause が傘語で止まらず、証拠と falsifier を持つか。
3. authoritative source、projection、owner が一貫するか。
4. Repair postcondition が全 Finding の期待動作を覆うか。
5. variant 探索が報告箇所以外へ広がっているか。
6. LCU が過併合されず、M=N も正当な結果として残せているか。
7. Step、todo、Gate が同じ LCU を参照するか。
8. 後半 Step まで変更ファイルとコマンドが具体的か。
9. japanese-tech-writing の規約に従い、長い表セルをブロックへ分解したか。
