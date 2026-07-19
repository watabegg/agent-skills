# herdr-dev-loop long-running Manager fast fix

Status: operational proposal

## 問題

長い run では、Manager が skill の存在を忘れるというより、次の細かい規範を順番に取りこぼす。

- broker を先に drain せず pane を直接見る。
- batch close と requirement progress 更新を忘れる。
- task contract の必須 field を欠いたまま start する。
- start 直後に contract を変え、attempt identity を壊す。
- conflict graph に載った task を並行 dispatch する。
- review 上限へ達した task を requeue し、上限を実質リセットする。
- installed runtime、repository runtime、run version のずれを見落とす。
- 同じ TUI delivery failure を別 message で繰り返す。
- `manager_context` や progress artifact が存在しても、空または古いまま使われない。

今回の旧 run では、`manager_context` が空、T050/T051 は conflict graph に載ったまま同時 start、T044 から T049 は scope ref 欠落で連続 abort、T043 は start 後に contract を変更、終盤は broker wake より pane fallback に依存した。これは長時間作業中の注意力だけに責任を置けない。重要な規範が mutation の前提として強制されていなかった。

## fast fix の狙い

0.5.4 の repository memory を待たず、次の run から簡単に使える薄い仕組みを入れる。

1. Manager が再開時に読む情報を一つの brief にする。
2. Manager セッションを batch 単位で交代できるようにする。
3. 同じ failure class を二度繰り返したら mutation を止める。
4. runtime と scope の identity を mutation の直前に再検証する。
5. 旧 thread の会話記憶を正本にしない。

## Manager brief

`hloop manager brief --json` と `hloop manager brief --markdown` を提案する。

brief は STATE の巨大な dump ではない。次の canonical source から deterministic に投影する。

- MISSION
- PLAN と current planning identity
- accepted requirements と scope refs
- accepted decisions と未解決 decision
- runtime version、runtime digest、run version、schema revision
- namespace、run ID、phase、pause/freeze reason
- integration branch と exact SHA
- active task、attempt、write scope、contract digest
- queued task と dependency
- review/gap epoch と target SHA
- unresolved confirmed finding
- Patch Review/remediation の task-lifetime usage
- missing pane、dirty worktree、cleanup failure、runtime drift
- recovery manifest と predecessor run
- 次に許された mutation を最大3件
- 明示的に禁止された mutation

例:

```markdown
# Manager Brief

- Runtime: 0.5.3 / digest sha256:...
- Run: recovery-canary / phase dispatching / SHA abc123
- User control: implementation resumed for approved 0.5.3 scope only
- Active: none
- Next safe action: start SC01 after dry-run
- Forbidden: resume old T050/T051 attempts; parallelize central hloop tasks
- Circuit: TUI transport closed after 2 unknown events
- Review budget: SC01 0/2; release remediation 0/2
- Read before mutation: task SC01, decision D008, recovery manifest
```

## mandatory pre-mutation preflight

すべての mutation command は、同じ transaction の前半で次を検査する。

1. runtime digest が run pin と一致する。
2. Manager session lease が current である。
3. user pause または dispatch freeze がない。
4. command が current phase で許可される。
5. task contract schema、requirement refs、scope refs が完全である。
6. start 対象 task の base SHA が current integration SHA と一致する。
7. active write-scope conflict がない。
8. predecessor attempt の unresolved cleanup がない。
9. task-lifetime review/remediation budget を超えない。
10. exact command intent が brief の next-safe-actions に含まれるか、明示的 override decision がある。

検査後と mutation の間に STATE が変わらないよう、同じ loop lock と compare-and-swap identity を使う。

## Manager session rotation

長い一つの会話 thread を Manager の唯一の実行主体にしない。交代単位は wall clock ではなく安全な batch 境界とする。

### rotation trigger

- batch が close した。
- broad review epoch が reported になった。
- remediation round が完了した。
- Manager の連続運転が90分を超えた。
- context compaction が発生した。
- 同じ operational warning を二度見た。
- user が status を3回以上求める間、同じ task が進んでいない。

90分は timeout ではない。安全な境界に達するまで少し延長できるが、新しい task は start しない。

### handoff capsule

交代前に `hloop manager handoff create` が次を保存する。

- handoff ID と時刻
- current brief digest
- 完了した action
- 未完了 action
- active external process と待機理由
- 失敗した command と再実行可否
- exact next command の dry-run
- user へ未提示の decision 候補
- known risks と circuit state

新 Manager は `SKILL.md` を読み、version、doctor、selftest、brief、conductor、broker drain、handoff digest を確認してから lease を取得する。

## repeated-failure circuit breaker

fingerprint の完全一致ではなく failure class を数える。

### failure class

- `transport.tui-submit-unknown`
- `transport.tui-busy`
- `lifecycle.missing-pane`
- `lifecycle.cleanup-failed`
- `contract.missing-scope-ref`
- `contract.start-then-rebind`
- `dispatch.write-scope-conflict`
- `runtime.version-drift`
- `review.budget-reset-attempt`
- `review.same-sha-serial-repeat`
- `git.worktree-owner-mismatch`
- `metrics.nonterminal-accrual`

### rule

同じ run で同じ class が2回発生したら circuit を open にする。

- 3回目の同種 mutation を拒否する。
- dispatch freeze を立てる。
- brief の先頭へ原因と recovery action を表示する。
- user decision が不要な既知 recovery なら Manager が一度だけ実行できる。
- recovery 後に exact regression check を通した場合だけ close する。
- force override は理由、scope、期限、decision ref を要求する。

TUI `unknown` は自動再送禁止なので、同じ pane への新 message も同じ failure class として扱う。内容が違うという理由で circuit を避けない。

## immediate no-code procedure

製品機能を実装する前でも、0.5.3 recovery では次を必須にする。

### 各 Manager turn の開始

1. `SKILL.md` の mandatory preflight を読む。
2. exact helper path、runtime version、namespace、run ID を一つの user-visible progress message に書く。
3. `dashboard` と `conductor --no-fail` を実行する。
4. `manager next` を一度だけ実行し、古い event を無差別に ACK しない。
5. recovery inventory と current closeout task を読む。
6. 今回の turn で行う mutation を最大3件に限定する。

### 各 task start 前

1. task file を全文読む。
2. base SHA と integration SHA を比較する。
3. write scope conflict を確認する。
4. acceptance、preserved invariant、regression check を Worker prompt に含める。
5. `--dry-run` の出力を保存する。
6. start 後は contract を変えない。変える必要があれば attempt を開始せず task revision を作る。

### 待機時

- pane polling を通常運用にしない。
- broker wait または `hloop wait ... --harvest` を使う。
- 60秒 sleep を数百回繰り返さない。
- 待機中に新しい review や重複 task を start しない。

### batch 終了時

- batch close
- requirement reconcile
- validation evidence catalog 更新
- finding ledger 更新
- Manager handoff note 更新
- progress percentage と残る gate を user へ報告

## progress reporting

「何%」は wall time の推測ではなく、weighted gate で出す。

| gate | weight |
| --- | ---: |
| recovery freeze/manifest | 5 |
| fast fix | 15 |
| candidate import | 10 |
| known finding closure | 35 |
| exact-SHA validation | 10 |
| broad Reviewer/Gap epoch | 10 |
| bounded remediation | 10 |
| release/install/finish | 5 |

各 gate は `not_started`、`running`、`passed`、`blocked` のみを持つ。task の途中時間を根拠なく50%としない。報告には次を含める。

- 現在の weighted progress
- 完了した gate
- active task と開始時刻
- 次の observable completion
- 残る user decision
- earliest/best guess ではなく、残る直列 gate 数と実測中央値による幅

## 0.5.4 への接続

fast fix で作る brief、handoff、circuit の record は、[repository memory design](2026-07-19-v0.5.4-repository-memory-design.md) の projection と incident event へ移行できる形にする。

ただし 0.5.3 では SQLite memory、repository UUID、semantic near-duplicate、user-gated promotion まで実装しない。小さい recovery guard が膨らみ、0.5.3 の scope を再び拡大しないためである。

## 受入条件

1. 新 Manager が旧会話 thread を読まずに brief と handoff から next safe action を再現できる。
2. brief は exact source digest を持ち、STATE 変更で stale になる。
3. runtime drift、pause、scope conflict、missing scope ref を mutation 前に拒否する。
4. 同じ failure class の3回目を自動実行できない。
5. circuit close には recovery evidence が必要である。
6. rotation 中に二人の Manager が同じ lease で mutation できない。
7. handoff は raw user input、credential、session secret を含まない。
8. progress percentage は gate evidence から再計算できる。
9. memory や brief は MISSION、PLAN、task contract より優先されない。
10. old run の stale role state を next safe action として提案しない。

## 非目標

- Manager の判断を自動化し切らない。
- 長い task を90分で強制終了しない。
- warning が一度出ただけで run を止めない。
- repository memory の全機能を 0.5.3 に入れない。
- user の自然な途中指示を制限しない。
- scope 外 finding を memory を理由に実装しない。
