# herdr-dev-loop review convergence protocol

Status: proposed review policy for 0.5.3 recovery and 0.5.4

## 目的

品質を落とさず、`review -> fix -> review` の直列往復を減らす。review の回数を減らすこと自体を目的にせず、同じ code 状態で得られる観点を先に全部集め、修正を原因別にまとめる。

## なぜ単純な反復が遅かったか

旧 run では38回の Patch Review のうち34回が `fix_required` だった。T010 は9回、T041 は12回、T042 は6回、T043 は6回の Patch Review を行った。requeue が history を消すため、task 単位の上限は実質的に働かなかった。

また、112件の unresolved fingerprint occurrence に対し exact duplicate は3組だけだった。つまり、同じ finding を何度も見つけたのではなく、cleanup、identity binding、durability、fail-closed boundary という同じ failure class の別の穴が修正後に見つかり続けた。

したがって、fingerprint の重複排除だけでは収束しない。最初に変更 surface と failure class を広く調べ、修正後にも fresh regression challenge を残す必要がある。

## 二つの review level

### task-local Patch Review

目的は、Worker candidate が task contract と preserved invariant を満たすか確認することである。

- exact candidate SHA、attempt、task contract digest、candidate artifact digest に固定する。
- medium/high risk task だけ必須にする。
- normal cap は task lifetime で2回。
- user が一回だけ許可した場合に限り round 3 を開く。
- requeue や successor rename で cap をリセットしない。
- scope 外 finding は follow-up にし、自動 task 化しない。

### release-level audit epoch

目的は、同じ integration SHA の実装品質と acceptance coverage を独立に監査することである。

- Reviewer swarm と Gap swarm を別 Coordinator とする。
- 両方を同じ immutable SHA に固定する。
- Coordinator 内で lane、verifier、challenge を計画する。
- 全 participant identity と artifact digest を manifest に記録する。
- 全 result が揃うまで fix を開始しない。

Patch Review と release-level audit は代替関係ではない。前者は task-local regression を統合前に減らし、後者は task 間と release acceptance の抜けを調べる。

## discovery epoch

### Reviewer swarm

推奨6 lane:

1. correctness and state-machine transitions
2. durability, crash recovery, idempotency
3. identity, authentication, replay, scope boundary
4. compatibility, migration, schema parity
5. tests, release evidence, exact-SHA binding
6. operator UX, failure recovery, lifecycle cleanup

各 lane は独立した finding を出す。Coordinator は finding を消さず、重複候補を semantic issue key と failure class でまとめる。

### Gap swarm

推奨4 lane:

1. requirement-to-task and task-to-code coverage
2. caller/consumer/failure-path coverage
3. test/evidence/release-gate coverage
4. scope, decision, migration, operator-path coverage

別 participant が coverage challenge を行い、「実装済み」と自己申告された条件から反証候補を探す。

### independent verifier

confirmed P0/P1 は、発見 lane と異なる identity の verifier が trigger を再現する。P2 は policy に応じて sampling できるが、release-blocking にするなら verifier を必須にする。

## collection barrier

次が揃うまで triage を始めない。

- immutable PLAN
- expected participant identities
- 全 lane artifact
- challenge artifact
- verifier artifact
- MANIFEST
- Coordinator FINAL
- target SHA と artifact digest
- timeout/incomplete participant の明示状態

timeout participant を成功扱いにしない。successful artifact だけを successor epoch へ継承し、未完了 process は fresh identity で補完する。

## finding normalization

各 finding は次を持つ。

- source execution、lane、artifact digest
- exact target SHA
- severity
- trigger
- product impact
- affected path/symbol
- violated invariant
- failure class
- origin: introduced / diff-expanded-pre-existing / unrelated-pre-existing
- contract relation: in-scope / adjacent / out-of-scope
- proposed fix
- regression test
- semantic fingerprint

### semantic issue key

fingerprint は artifact identity と exact wording の安定化に使う。意味的な再発判定には、次の tuple を併用する。

```text
(failure_class, violated_invariant, affected_surface_prefix, trigger_shape)
```

自動で二つの finding を統合しない。Coordinator が merge proposal を出し、Manager が source evidence を見て確定する。

## 一括 triage

すべての finding を次の軸で一度に分類する。

1. fact status: confirmed / refuted / needs-spec / insufficient-evidence
2. origin: introduced / diff-expanded-pre-existing / unrelated-pre-existing
3. contract relation: in-scope / adjacent / out-of-scope
4. disposition: fix-now / follow-up / accepted-risk / no-action / user-decision
5. release effect: blocking / non-blocking

自動 fix task にできるのは、原則として次を全部満たす finding だけである。

- confirmed
- in-scope
- fix-now
- release-blocking
- locked requirement または approved scope ref がある
- remediation budget が残る

unrelated pre-existing finding は requirement ref が付いていても自動 materialize しない。

## root-cause batching

task を file 単位や Reviewer 単位で分けない。次の順でまとめる。

1. violated invariant
2. transaction/lifecycle boundary
3. shared write surface
4. required regression test

同じ根本原因を複数 file で直すなら一つにする。別 lifecycle を中央 CLI の同じ file で触るだけなら分ける。

各 remediation task は次を持つ。

- source finding 全件
- failure class
- exact trigger
- preserved invariant
- negative regression
- positive adjacent regression
- write allow/deny
- dependency
- task-lifetime budget usage
- scope ref

## fix 後の二本立て review

### known-finding closure

前 epoch の全 source finding を対象にする。

- trigger test が期待どおり変わったか。
- fix commit が finding へ結び付くか。
- sibling call site に同じ穴が残っていないか。
- artifact と schema が runtime と一致するか。
- authorization、cleanup、history が final gate まで残るか。

### mandatory regression challenge

前 epoch の finding list を主入力にせず、変更 surface から新しい regression を探す。

- adjacent callers/consumers
- negative/replay/concurrency cases
- migration and legacy compatibility
- cleanup and retry paths
- exact-SHA release evidence
- operator recovery path

fresh challenge は省略しない。旧 run では exact fingerprint の再発が少なく、既知 fingerprint closure だけでは新しい穴を見逃すためである。

二つは同じ SHA へ並行実行できる。両方が終わってから次の triage を行う。

## bounded remediation policy

release-level remediation は最大2 round を提案する。

### round 0

pre-existing audit で既に確認済みの finding を closeout plan として修正する。これは recovery の planned implementation であり、final discovery epoch の round を消費しない。ただし task-local Patch Review cap は適用する。

### round 1

final discovery epoch の confirmed in-scope blocking finding を全て原因別に修正する。

### round 2

round 1 の変更で発生または露出した in-scope blocking regression を修正する。

### upper bound

round 2 後に blocking finding が残れば user decision へ停止する。自動的に quality bar を下げない。

選択肢は次を提示する。

- extra round を exact finding set へ一回だけ許可する。
- feature を release scope から外す。
- candidate を rollback する。
- 0.5.3 release を延期する。

accepted risk は release-blocking correctness/durability/security finding の便利な逃げ道にしない。

## evidence reuse

validation evidence は次の identity が全て同じ場合だけ再利用する。

- exact source SHA
- ordered command
- working directory semantics
- runtime/dependency digest
- relevant environment capability
- test selection

同じ SHA で Reviewer と Gap が別々に full suite を走らせる必要はない。Manager-owned L3 catalog を参照し、Reviewer は必要な special verification と反証 probe に時間を使う。

source SHA が変わったら code-sensitive evidence は stale になる。ただし immutable external dependency handshake など、code 非依存 evidence は別 identity で再利用できる。

## stop conditions

- collection barrier が incomplete
- target SHA drift
- same-SHA の別 active lineage が存在
- task/release remediation budget exhausted
- finding inventory が triage 前に mutation された
- scope relation が undecided
- verifier と discovery lane の identity が同じ
- review artifact が code を変更した
- full validation が failure のまま audit を開始しようとしている

## 成功指標

- final discovery epoch 数
- remediation round 数
- first epoch で見つかった confirmed finding の割合
- fix 後 challenge で見つかった新 regression 数
- exact SHA あたり full-suite 実行回数
- task あたり Patch Review round 数
- user decision に停止した時点で保存された unresolved finding の完全性
- wall time と総 agent time を分けた値

目標は finding を0件に見せることではない。少ない epoch で全観点を集め、未解決を隠さず、release 可能かを判断できる状態へ収束することである。
