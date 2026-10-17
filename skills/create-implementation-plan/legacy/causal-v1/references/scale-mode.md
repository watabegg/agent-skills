# スケールモード

大規模計画は overview、調査、phase、todo、goal prompt に分割する。分割後も Finding から LCU、Step、Gate への追跡を切らさない。

## 発動閾値

次のいずれかに該当したら scaled を使う。

- 実装 Step が8以上。
- Todo が26件以上。
- 変更対象ファイルが31以上。
- 閾値未満でも、複数リリースや独立した rollout phase を跨ぐ。

判定根拠は overview 冒頭に記録する。

## ディレクトリ構造

```text
docs/plans/<prefix>-<topic>/
├── 00-overview.md
├── 00-overview.html
├── 01-research.md
├── 10-phase1-<name>.md
├── 20-phase2-<name>.md
├── <topic>-todo.md
└── <topic>-goal-prompt.md
```

リポジトリに既存の docs 規約があれば、その配下へ置く。

## 正本の分離

- `00-overview.md`: 目的、全体スコープ、一文の解決モデル、診断・モデル正本への参照、phase 一覧、Unclear Points、Definition of Done を持つ。80行以内を目安とし、120行超は lint 失格。
- `01-research.md`: 実測、既存実装、読了ファイルを持つ。lint 対象外。
- 最初の detailed phase: `設計ハブ: true` とし、Finding、Cause、Target Model、Repair、LCU、variant、必要な比較の唯一の正本になる。
- 後続 phase: `設計ハブ: false` とし、診断・モデル正本を参照する。分析本文を複写しない。
- todo.md: 全 phase 共通の進捗台帳。分析本文は載せない。

overview、todo、goal prompt へ Cause や Repair の本文をコピーしない。参照だけを持たせ、二重正本を作らない。
outline phase は目的、対象 LCU、変更対象だけを持ち、Step、Step Hard Gate、detailed 専用セクションを置かない。
Step を確定した時点で `状態: detailed` へ変更する。

## LCU と phase の境界

phase 境界は LCU 境界へ合わせる。outline phase も対象 LCU を overview で宣言する。

一つの LCU を複数 phase に分割する場合は、診断・モデル正本へ次を書く。

- 分割しなければならない依存理由。
- 各 phase の中間 postcondition。
- LCU 全体を閉じる最終 postcondition と Gate。

`scaled-phase-template.md` の `LCU Split` ブロックを使い、中間 postcondition には各中間 Phase、最終 Gate には `PN-SN` を記録する。
最終 phase が outline の間は、Gate ID が最終 phase を指すことまでを契約とする。
その phase を detailed にした時点で、Gate の実在と Primary LCU の一致を finalize で確定する。

同じファイル群や実行順だけを理由に phase を作らない。

## 二段階詳細化

- 直近の未着手 phase だけを `detailed` にする。
- それ以降は `outline` とし、目的、対象 LCU、変更対象、完了 Gate を持たせる。
- phase N の最終 Step に phase N+1 の詳細化を入れる。
- 詳細化 Step の Primary LCU は `plan-maintenance` とする。
- 詳細化 Gate は `lint_plan.sh --require-detailed <次phase>` を使う。

新しい Finding、Cause、variant を詳細化時に発見した場合は、outline だけを更新しない。診断・モデル正本の参照グラフと overview の LCU 対応を先に改訂し、finalize し直す。

## todo.md の全量

scaled の todo は次を全量とする。

- detailed phase の全 Step。
- outline phase ごとの1行。
- 全 Step / phase の Hard Gate。

各 Step と outline phase の todo 行には、正本と同じ Primary LCU を括弧タグで過不足なく転記する。

詳細化したら todo.md を in-place 更新し、Step と Gate を追記する。Hard Gate の Step ID は `P1-S1` のように phase 修飾付きで書く。

進捗は todo.md だけを更新する。Cause、Model、Repair、LCU、variant、スコープなどの契約が変わる場合だけ plan を改訂し、overview の改訂履歴へ記録する。

## テンプレートと lint

- overview: `references/scaled-overview-template.md`
- phase: `references/scaled-phase-template.md`

納品時は個別 lint ではなく overview を finalize する。

```bash
~/.codex/skills/create-implementation-plan/scripts/lint_plan.sh --finalize docs/plans/<prefix>-<topic>/00-overview.md
```
