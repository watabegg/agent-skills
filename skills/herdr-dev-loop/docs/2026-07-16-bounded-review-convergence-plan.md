# herdr-dev-loop 有界レビュー収束と手動最終認証の実装計画

- 作成日：2026-07-16
- 対象：herdr-dev-loop 0.5.2
- 状態：実装前、0.5.0 Manager postmortem反映済み
- 関連機能：HLoop Native Review、`codex-review-multi-v2`、final gate、triage、最終報告

## 目的

herdr-dev-loopの実装速度を維持しながら、最終的に独立した`codex-review-multi-v2`を手動実行し、検証済みの修正対象findingが0件である状態へ収束させる。

この計画でいう**finding 0件**は、候補や懸念が一つも存在しないことではない。Coordinatorが実コードで確認し、今回の変更範囲で修正すべきと判定した**検証済みactionable finding**が0件であることを指す。新しい能力、対応環境、脅威モデル、運用保証を要求する改善は、findingを隠さずfollow-upとして最終報告へ残す。

finding 0件だけではreview完了を意味しない。必須laneまたは独立verificationが未完了なら、0件は「問題がなかった」ではなく「確認を完了できなかった」結果である。manual final reviewはmanifest completenessも合格条件に含める。

レビュー結果を0件に見せるため、具体的な不具合をscope外へ追い出すことはしない。今回のdiffが既存の対応シナリオで発生させる不具合は、修正量にかかわらずin-scope findingとして扱う。

## 背景

0.5.0の実装履歴では、T001からT013までの13タスクが82分、実効並列度2.28で完了した。一方、T016からT035までのレビュー修正は466分を要し、実効並列度は0.93だった。20タスク中15タスクが同じ`skills/herdr-dev-loop/scripts/hloop`を変更したため、3 Worker構成でも処理がほぼ直列になった。

停止後のManager postmortemでは、run全体は約23時間、全53 taskであり、T016以降の38 taskがreview、gap、dogfood由来のremediationだったと整理された。特にT035からT053までの19 taskだけで約9時間を使い、9 Reviewer中4件、8 Gap Auditor中2件がabortedになった。初期実装の遅さだけではなく、planned work完了後もreview findingがtaskを再生成し続けたことが、長時間化の中心である。

最終dual-swarmレビューR009では、Codexの4 laneが12候補を報告したが、Claudeの4 laneが1800秒以内に完了せず、独立verificationを開始できなかった。結果としてconfirmed findingは0件だったが、12候補すべてが`insufficient_evidence`となり、品質ゲートとして完結しなかった。

この結果から、次の問題を分けて解決する。

1. 実装中の頻繁な重いレビューが、並列Workerの統合を止めている。
2. レビューfindingが、元要件との関係を確認されないまま新規タスクへ変換される。
3. 同じ中核ファイルを複数タスクが変更し、レビュー修正が直列化する。
4. full test、selftest、synthetic E2Eが各Workerで繰り返される。
5. 最終レビューの前に、統合HEADがレビュー可能な状態へ収束していない。
6. review候補、検証済みfinding、follow-up、accepted riskの境界が曖昧である。

0.5.0 Managerのpostmortemでは、厳密なreasoning modelよりも、P0とP1を原則fix-taskへ送るtriage規則と、follow-upへ閉じる機械的な出口の不在が主因と判定された。xhighは二次的な失敗条件まで探索するため修正連鎖を増幅したが、reasoning effortを下げるだけでは出口不足を解消できない。STATEにはManagerのmodelとreasoning effortが記録されておらず、寄与率を事後に測れなかった。Reviewerの精度を維持し、release判断を構造化規則へ移す必要がある。

現行実装にも、この因果が残っている。`references/manager-loop.md`はP0とP1から通常fix taskを作るよう指示する。`references/schemas/review-manifest.schema.json`は`ignore_status: may_defer`を持つ一方、`recommended_action`にはfollow-upがない。`hloop triage --create-tasks`はconfirmed candidateからtaskを作る際に、元要件またはrelease scopeとの関係を要求しない。`create_loop_task`は新しいtaskの種類にかかわらずarmed final gateを失効させる。dashboardは非terminal phaseで`needs_review`または`needs_gap_check`が残れば、loopがpause中でもrole startをnext actionへ追加できる。

停止時に作成した`docs/2026-07-15-v0.5.0-follow-ups.md`は、重要度、根拠、影響、推奨対応、見送り理由を保存できた。一方、この文書はuserがdispatchを停止した後に手作業で作られている。次版では、同じ情報をreview triage時点からnamespaced stateへ保存し、必要な場合だけGit管理文書へexportする。

## 成功条件

次の条件をすべて満たしたとき、この変更を完成とする。

1. 新規loopは、mergeごとではなくbatch境界で統合レビューを行う。
2. Managerは、findingを元要件と照合し、独立した分類軸からdispositionを決定する。
3. scopeを拡大するfindingからremediation taskを自動作成しない。
4. レビュー修正ラウンドに明示的な上限があり、上限後は自動継続しない。
5. pre-final reviewが、固定SHAに対して検証済みactionable finding 0件へ収束する。
6. final gateは、独立した手動`codex-review-multi-v2`を待つ状態を表現できる。
7. 手動最終レビューは、固定SHA、base、scope source、lane数、結果artifactを記録する。
8. 手動最終レビューに検証済みactionable findingが1件以上あれば、finishを拒否する。
9. follow-upが残っていても、現在の契約を満たし、検証済みactionable findingが0件ならfinishできる。
10. final review後にintegration HEADが変わった場合、認証結果を失効させる。
11. 既存loopのreview cadenceをmigrationで暗黙変更しない。
12. 新しいAgent role、broker transport、汎用workflow engineを追加しない。
13. follow-upをfirst-class stateとして保存し、同じfindingを重複登録しない。
14. severity、scopeとの関係、処置、release判定を独立した軸として記録する。
15. 修正連鎖が上限へ達した機能を、追加修正だけでなくdisable、experimental化、user decisionから選べる。
16. dispatch freeze中は、新規taskと新規roleの起動をCLIが拒否し、dashboardも起動を提案しない。
17. Managerのprovider、model、reasoning effortと、findingの処置結果を記録する。
18. manual final reviewは、finding数だけでなくlane完了、verification shortfall、manifest completenessを検証し、不完全なreviewを合格させない。
19. release scope lock後に作るすべてのtaskは、locked PLAN、requirement、finding、user amendmentのいずれかへ追跡できるprovenanceを持つ。
20. manual final reviewの失敗またはreview fix round上限到達後は、user inputを伴う原子的なreopen操作でのみtask作成可能な状態へ戻る。
21. review candidateの発生時期、release contractとの関係、仕様判断の要否を別々の軸で記録する。
22. follow-upはreview内fingerprintとは別の安定したissue keyで重複排除し、修正案、severity、対象SHAの違いだけでは別項目にしない。

## 用語

- **release scope contract**：今回のreleaseで実装または保証する範囲を決める正本。MISSION、PLAN、accepted requirement、task acceptance、対応platform、明文化されたtrust boundary、releaseから外してよい機能から構成する。
- **release scope lock**：release scope contractのsource、内容digest、意味的revisionを固定する状態。planned taskとin-scope remediationは実行できる。
- **scope revision**：releaseで保証する意味を変更した回数。保証範囲の拡張または縮小ではuser inputを必要とする。
- **source snapshot revision**：release scope contractを構成する文書内容の版。意味を変えない誤記修正でも更新するが、それだけではscope revisionを上げない。
- **dispatch freeze**：user指示、review収束、停止処理のため、新規taskと新規roleの起動を禁止する運用状態。release scope lockとは別に管理する。
- **review candidate**：Reviewer laneが報告した未検証の問題候補。
- **verified actionable finding**：CoordinatorまたはManagerが発生経路を実コードで確認し、今回のrelease scope contractに違反すると判定した修正対象。
- **scope-expanding candidate**：妥当な改善ではあるが、新しい能力、platform、脅威モデル、運用保証、互換保証を要求する候補。
- **follow-up**：今回のfinishを止めず、重要度、根拠、影響、推奨対応、見送り理由を残す後続作業候補。
- **accepted risk**：今回shipする挙動に残る具体的なriskを、権限を持つ主体が理由付きで受け入れた記録。未実装作業を保存するfollow-upとは区別する。
- **convergence review**：実装HEADを最終レビュー可能な状態へ収束させるpre-finalレビュー。
- **certification review**：収束済みHEADへfreshな文脈から手動実行する最終`codex-review-multi-v2`。
- **review fix round**：一つの固定SHAから確認したin-scope findingを、一つのremediation batchで修正し、再検証する単位。
- **review fingerprint**：一つの固定SHAに対するreview candidateを重複排除する識別子。対象位置、発生条件、影響、修正案を含められる。
- **follow-up issue key**：複数review、SHA、修正案をまたいで同じ未解決問題を識別するversion付きのsemantic key。review fingerprintとは別に管理する。

## release scope contractの固定

### 固定する情報

最初のWorkerを起動する前に、Managerは次の情報を固定する。

- MISSIONのgoal、constraints、non-goals、done criteria
- PLANの実装項目と明示的な非対象
- accepted requirementとacceptance
- 対応OS、runtime、provider、repository形態
- trust boundary
- migrationと後方互換の保証範囲
- releaseから外してよい機能と、experimental化できる機能
- review fix roundの上限
- budget超過時に許可する処置
- spec sourceのpathと内容digest
- PLAN itemとaccepted requirementの安定したID

固定後のrelease scope contractは、単なる文書の存在ではなく、対象ファイル、digest、`scope_revision`、`source_snapshot_revision`の組としてSTATEへ記録する。user inputによる明示変更以外でrelease scopeを拡張しない。意味を変える変更ではinput ID、変更理由、旧revision、新revision、影響taskを記録する。

### 固定後に許可する変更

次の変更はscope拡大ではない。

- acceptanceを満たすために必要な局所修正
- 今回のdiffが導入した回帰の修正
- 対応対象として明記済みの環境で起きる不具合の修正
- validationを成立させるためのテスト修正
- 誤記や相互矛盾を直し、既存の意味を明確にするspec更新

次の変更はscope拡大として扱う。

- 未対応platformへの対応
- より強いtrust boundaryの導入
- 未要求の汎用化
- 新しいAgent roleやtransportの追加
- 現在のfailureを直すために不要な耐障害性の一般化
- 将来の利用例だけを根拠にしたarchitecture変更

### source amendment

source変更は、次の三種類へ分類する。

| amendment kind | 意味 | 必要な権限 | revision |
|---|---|---|---|
| `editorial` | 誤字、リンク、表現だけを直し、保証内容を変えない | Managerが変更前後の同値性を記録 | source snapshotだけを更新 |
| `clarification` | locked sourceから一意に導ける意味を明記し、保証範囲を変えない | Managerが根拠となるsource箇所を記録。不一致があればuser decision | source snapshotだけを更新 |
| `scope-change` | 能力、対応環境、trust boundary、互換保証、非目標を変更する | user input IDが必須 | scopeとsource snapshotを更新 |

`hloop release-scope amend`を通さずsource digestが変わった場合は、未記録のdriftとしてreadinessを拒否する。amendを記録した場合も、Reviewerへ渡したsource snapshotが変わるため、そのsnapshotを対象にしたreview readiness、convergence、manual final reviewは失効させる。user inputを必要とするのはscopeの意味を変える場合であり、意味を変えないsource修正すべてではない。

### dispatch freeze

release scope lockはplanned taskの実行を許可する。userが停止を指示した場合、review収束後に最終認証を待つ場合、またはremediation上限へ達した場合は、別の`dispatch freeze`を有効にする。

dispatch freeze中は、`task new`、Worker start、Reviewer start、Gap Auditor start、Advisor startをCLIが拒否する。すでにrunningのroleは、freeze recordが許可したIDだけを安全な境界まで継続できる。validation、harvest、merge、follow-up記録、最終報告、pauseは許可する。dashboardとconductorはfreeze中に新しいrole起動をnext actionとして提案しない。

### task provenanceと作成認証

release scope lock後に作るtaskは、`kind`とは別に次の`task_origin`を持つ。

| task origin | 作成根拠 | 必須参照 |
|---|---|---|
| `planned` | locked PLANまたはaccepted requirementに含まれる作業 | `plan_item_refs`または`requirement_refs` |
| `finding` | confirmedなin-scope findingのremediation | `source_finding`、`requirement_refs`、`why_fix_now` |
| `user-amendment` | userがscopeへ追加した作業 | `authorization_input_id`、更新後の`release_scope_revision` |
| `operational` | product挙動を変えない調査、validation、artifact整備 | `operational_reason`。codeまたはrelease artifactを変更するtaskには使用不可 |

すべてのtaskは作成時の`release_scope_revision`を記録する。`hloop task new`、triage、pump、conductor、将来追加する作成経路は、一つの`authorize_task_creation` preflightを通る。CLIが`created_from: PLAN.md`と自己申告するだけでは`planned`と認めず、参照IDがlocked contractに実在することを確認する。task updateはprovenanceを削除または別originへ変更できない。変更が必要な場合は、元taskをcloseし、新しい根拠でtaskを作る。

legacy runでは既存taskを`legacy-unclassified`として読み取り、従来のfinish条件を変えない。新policyを有効にしたnamespaceでは`legacy-unclassified` taskを新規作成できない。

## findingの分類

severityだけで「今回直すか」を決めない。review candidateは次の独立した軸を持つ。既存artifactの`origin`と`requires_spec_decision`を捨てず、発生時期、契約との関係、判断要否を別々に保存する。

| 軸 | 値 |
|---|---|
| 事実性 | `confirmed`、`refuted`、`insufficient_evidence` |
| 重要度 | `P0`、`P1`、`P2`、`P3` |
| 発生時期 | `introduced`、`diff-expanded-pre-existing`、`unrelated-pre-existing`、`unknown` |
| contractとの関係 | `in_scope`、`outside_release`、`ambiguous` |
| 判断要否 | `none`、`spec`、`user` |
| 処置 | `fix_now`、`defer_follow_up`、`disable_feature`、`mark_experimental`、`user_decision`、`accepted_risk`、`discard` |
| release判定 | `blocking`、`non_blocking` |

Managerはreview candidateごとに、次の順序で各軸を確定する。

1. 発生経路を現在の対象SHAで再現またはコード上で証明できるか。
2. 今回のdiffが問題を導入または拡大したか。
3. release scope contractのどの項目に違反するか。
4. 事実確認とは別に、仕様またはuser判断が必要か。
5. 修正が現在の契約を回復するものか、新しい保証を追加するものか。
6. 問題のある機能をreleaseから外すか、experimental化できるか。
7. user decisionがなくても、現在の契約内で安全に処置できるか。

処置は次の規則に従う。

| 条件 | 原則処置 | release判定 |
|---|---|---|
| `refuted` | `discard` | `non_blocking` |
| `insufficient_evidence`かつ`unrelated-pre-existing`または`outside_release` | `defer_follow_up`または`discard` | `non_blocking` |
| `insufficient_evidence`または`ambiguous`で、現在のacceptanceまたは安全性を判定できない | `user_decision` | `blocking` |
| `decision_requirement: spec`または`user`で、判断なしでは現在のacceptanceを判定できない | `user_decision` | `blocking` |
| confirmedな`unrelated-pre-existing`問題 | patch verdictから除外し、必要なら`defer_follow_up` | `non_blocking` |
| `introduced`または`diff-expanded-pre-existing`で`in_scope`のP1 | `fix_now` | `blocking` |
| requirement参照を持つ`in_scope` P1 | `fix_now` | `blocking` |
| 到達可能なsecurityまたはdata lossのP0とP1 | `fix_now`、`disable_feature`、`user_decision`のいずれか | 解決まで`blocking` |
| confirmedかつ`outside_release`のP1 | `defer_follow_up` | `non_blocking` |
| 現在のcontractを満たす追加保証 | `defer_follow_up` | `non_blocking` |
| 局所修正できず、機能を外せる | `disable_feature`または`mark_experimental` | 処置完了まで`blocking` |
| `decision_requirement: spec`だが、選択なしでも現在のacceptanceを満たせる | `defer_follow_up` | `non_blocking` |
| user decisionなしでは安全に進めない | `user_decision` | `blocking` |
| 既知の制約として契約に明記済み | `accepted_risk` | 契約に従う |

`accepted_risk`は、今回shipする挙動のriskを受け入れる判断であり、未実装作業を保存する`defer_follow_up`の代用にしない。到達可能なsecurityまたはdata lossのP0を、follow-upだけでnon-blockingにできない。

residual riskは最終報告上の投影区分であり、dispositionではない。`accepted_risk`、`insufficient_evidence`、外部依存による未検証事項から、今回残るriskだけを最終報告へ投影する。

Reviewerは分類の提案を行えるが、remediation task作成を決定しない。Managerはreview artifactを読み、根拠を確認してから分類を確定する。Managerが`fix_now`を選ぶ場合、findingとrelease scopeの対応を記録する。

## 実行フロー

```text
release scope lock
        |
        v
parallel Worker batches
        |
        v
targeted Worker validation
        |
        v
batch integration + one full validation
        |
        v
review readiness gate
        |
        v
pre-final codex-review-multi-v2
        |
        +---- verified in-scope findings ----> one remediation batch
        |                                           |
        |                                           v
        |                                  one full validation
        |                                           |
        +-------------------------------------------+
        |
        v
convergence finding count = 0
        |
        v
freeze integration SHA
        |
        v
await manual final review
        |
        v
fresh codex-review-multi-v2
        |
        +---- lane or verification incomplete ----> certification incomplete
        |                                                  |
        |                                                  v
        |                                         user-authorized retry
        |                                                  |
        +<-------------------------------------------------+
        |
        +---- verified actionable findings > 0 ---> certification failed
        |                                                  |
        |                                                  v
        |                                      user-authorized reopen
        |                                                  |
        |                                 remediation / scope action
        |                                                  |
        +--------------------< convergence review <--------+
        |
        v
manifest complete + manual final finding count = 0
        |
        v
Manager final QA + finish
```

## Workerとbatchの速度制約

### タスク分割

Managerは、Worker taskを一回の実装、self-review、targeted validationが30分から45分程度で終わる大きさへ分割する。60分を超えて進捗がないWorkerを無期限に待たず、milestone、diff、blockerを確認して、継続、分割、requeueのいずれかを選ぶ。

一つのtask acceptanceへ複数の独立domainを列挙しない。config、broker、decision、review、requirementsのように別moduleへ分けられる変更を、一つの「統合task」へ集約しない。

### write scope

batch作成時にwrite scopeの衝突graphを計算する。同じ中核ファイルを変更するtaskは同時起動しない。二つ以上のtaskが同じ中核ファイルを必要とする場合は、次のどちらかを選ぶ。

1. 先に共有interfaceを固定し、各実装を別moduleへ分ける。
2. 中核ファイルを変更する一つのintegration taskと、独立module taskを分ける。

新しいreview policy実装は`hloop`へ直接蓄積せず、`scripts/hloop_lib/review_policy.py`のような専用moduleへ置く。`hloop`にはargument parsingと薄い呼び出しだけを追加する。

### validation cadence

Workerは担当範囲のtargeted test、型検査、構文検査だけを必須とする。full test、selftest、synthetic E2Eはbatchの統合HEADに対して一度実行する。final review前とfinish前にも、それぞれ固定SHAへ一度実行する。

同じSHA、同じcommand、同じdependency identityで成功したvalidationは再利用できる。HEAD、lockfile、toolchain、config snapshotのいずれかが変わった場合だけ再実行する。

### 並列性の監視

batch終了時に、次をSTATEとprogress reportへ記録する。

- wall time
- Worker runtime合計
- effective parallelism
- longest Worker time
- validation time
- review wait time

二つ以上のWorkerを含むbatchでeffective parallelismが1.5未満の場合、Managerは次batchを開始する前にwrite scopeと依存関係を見直す。これはfinish blockerではなく、遅い計画をそのまま反復しないためのreplan triggerとする。

## review cadence

### 実装中のレビュー

mergeごとにfull Reviewerを起動しない。Workerはtask-local self-reviewを行い、Managerはbatch統合時に変更ファイル、acceptance、validation evidenceを確認する。

batch中のレビューは、次の条件に限って起動できる。

- security、migration、data lossのP0相当riskを先に確認する必要がある
- interface決定を誤ると、複数Workerの実装を作り直すことになる
- userが明示的に途中レビューを要求した

このレビューは対象SHAを固定し、結果を次batchの入力へ使う。レビュー中のHEADへmergeしない。

### batch境界

通常のreview cadenceは`batch`とする。batch内の全taskをmergeし、統合validationが通り、batchをcloseした後にだけreview gateを開ける。

ただし、すべてのbatchで`codex-review-multi-v2`を実行しない。通常batchはHLoop Native ReviewまたはManagerのreadiness checkで済ませ、`codex-review-multi-v2`はpre-finalと手動finalへ限定する。

### dual provider

dual-swarmを既定にしない。CodexとClaudeの両方を使う場合も、全laneの重複実行ではなく、仕様判断または特定findingの追加確認へ限定する。provider timeoutはレビュー全体を無期限に止めず、明示的なshortfallとして終了する。

## review readiness gate

pre-final reviewを始める前に、Managerは次の条件を確認する。

- current batchがclosedである
- running Worker、Reviewer、Gap Auditor、Advisorが存在しない
- integration checkoutがcleanである
- 予定したin-scope taskがすべてmergedである
- 現在のacceptanceを妨げるpending decisionがない
- integration validationが現在のHEADでpassedである
- pending fix-task draftがない
- release scope contractのscope revision、source snapshot revision、source digestが記録済みのlockまたはamendと一致する
- diff inventoryが生成済みである
- changed fileとvalidationの対応表がある
- migration、schema、public docs、security boundaryを変更した場合、その検証証跡がある
- `defer_follow_up`と判定した候補がfirst-class follow-up artifactへ記録されている

readiness gateが失敗した場合、Reviewerを起動しない。Managerは不足を現在のscope内で直すか、follow-upへ分類してから再判定する。

## pre-final convergence review

pre-final reviewには`codex-review-multi-v2`を使う。通常は4 laneとし、変更domainが広い場合、security、concurrency、migration、performanceのいずれかを含む場合、またはdiffが大きい場合は6 laneを使う。8 laneはuserが明示した場合だけ使う。reviewer modelはuserが明示しない限り指定しない。

CoordinatorはReviewerを兼ねず、次の処理だけを行う。

1. base、HEAD、diff、scope source、generated boundaryを確認する。
2. laneを並列起動する。
3. candidateを重複排除する。
4. candidateの発生経路を対象SHAで検証する。
5. fact status、severity、scope relationを独立して確定する。
6. release scope contractと照合する。
7. dispositionとrelease判定を確定する。

pre-final Reviewerへ「findingを0件にする」という期待を伝えない。通常のreview promptへscope sourceと対応環境だけを追加する。

## remediation batch

pre-final reviewで確認したverified actionable findingは、reviewごとに一つのremediation batchへまとめる。candidateごとに即座にtaskを作らない。

Managerはfinding全体を見て、同じ原因を直すtaskを統合し、write scopeが重ならないtaskだけを並列起動する。各taskはfinding ID、requirement IDまたはrelease scope参照、発生条件、`why_fix_now`、releaseへの影響、acceptance、write scope、targeted validationを持つ。

自動的なreview fix roundは最大2回とする。各roundは次の順序で進める。

1. verified actionable findingを確定する。
2. 一つのremediation batchを作る。
3. Workerを並列実行する。
4. batchをmergeする。
5. full validationを一度実行する。
6. 固定した新HEADへconvergence reviewを再実行する。

2回目の修正後もverified actionable findingが残る場合、loopは自動でtaskを追加しない。`review_convergence_exhausted`として停止し、残件、原因、推奨方針をuserへ報告する。

`defer_follow_up`、`accepted_risk`、`discard`と判定したcandidateからremediation taskを作らない。`disable_feature`または`mark_experimental`は、追加能力を完成させるtaskではなく、release contractへ戻す最小taskだけを許可する。`user_decision`がblockingの場合はtaskを増やさず停止する。

## 手動最終認証

### 準備

convergence reviewが0件になった時点で、Managerはintegration SHAを固定し、`dispatch freeze`を有効にして、phaseを`awaiting_manual_final_review`へ移す。HLoopは、手動レビュー用に次の情報を表示する。

- repository path
- base refとbase SHA
- integration refとtarget SHA
- branch-style diff command
- scope source
- generated file boundary
- 推奨lane数
- validation evidence

`hloop final-review prepare`は、これらの情報にcertification ID、scope revision、source snapshot revision、必須lane、verification policyを加えたimmutableな`reviews/final/PLAN.json`を作り、そのcanonical digestをSTATEへ記録する。この時点でWorker、Reviewer、Gap Auditorを自動起動しない。手動最終レビューが終わるまでintegration HEADを変更しない。

### 実行

Managerまたはuserは、通常の`codex-review-multi-v2`を明示的に実行する。HLoop schedulerから自動起動せず、freshなreviewer contextを使う。pre-final findingや期待する件数をReviewer promptへ渡さない。

追加focusには、次だけを含める。

- review modeとbase...HEAD
- release scope contractの正本
- 対応platformとtrust boundary
- generated fileの扱い
- repository固有の検証command

Reviewerはcodeを変更しない。Coordinatorはcandidateを実コードで検証し、actionable finding、residual risk、open questionを分離して日本語で報告する。

通常の`codex-review-multi-v2`は人間向け報告を返すため、HLoopは`reviews/final/MANIFEST.json`のtemplateをprepare時に生成する。このmanifestは既存の`review-manifest.schema.json`が持つlane、finding、verification、shortfall、completenessを再利用し、certification IDとprepared plan digestを追加する。CoordinatorまたはManagerは、実際のlane結果とverification結果をこのmanifestへ転記する。HLoopはchat本文、自己申告のfinding数、patch verdictだけを認証根拠にしない。

### 記録

Managerは手動レビュー後、`reviews/final/FINAL.md`へ次を記録する。

- protocol：`codex-review-multi-v2`
- certification IDとprepared plan digest
- base SHA
- target SHA
- scope revisionとsource snapshot revision
- lane数とlane名
- laneごとの`completed`、`failed`、`timeout`
- Coordinator session ID
- reviewed fileまたはdiff inventory
- candidate fingerprintとverification record
- verification shortfallとincomplete finding
- manifest completeness
- verified actionable finding数
- findings
- residual risks
- follow-up参照
- patch verdict
- completed at

`hloop final-review record`は、prepared planとmanifestのidentityを照合し、manifest completenessを再計算する。次の条件をすべて満たす場合だけmanual final review gateをpassedにする。

- certification ID、prepared plan digest、base SHA、target SHAがprepare時の値と一致する
- target SHAが現在のintegration HEADと一致する
- scope revision、source snapshot revision、source digestがprepare時から変わっていない
- 必須laneがすべて`completed`で、finding countがlane artifactと一致する
- candidateごとに必要な独立verificationが完了している
- verification shortfall、missing lane、incomplete findingが0件である
- `manifest_complete: true`
- `verified_actionable_findings: 0`
- `patch_verdict: passed`

lane失敗、timeout、verification不足があるreviewは、actionable findingが0件でも`incomplete`とする。`incomplete`は`passed`でも`failed`でもなく、認証を閉じないterminalな試行結果である。同じSHAでreviewだけを再実行するには、user inputを記録したreopen操作を必要とする。

最終レビューが1件以上のverified actionable findingを報告した場合、認証はfailedとなる。HLoopは自動修正しない。Managerはscope分類と残りのround budgetを示し、userの指示を待つ。`failed`または`incomplete`ではdispatch freezeを維持し、通常の`dispatch unfreeze`だけでtask作成可能な状態へ戻さない。

### 失敗後のreopen

`review_convergence_exhausted`、`manual_final_review_failed`、`manual_final_review_incomplete`からの復帰には、`hloop review reopen`を使う。このcommandはuser input IDとactionを必須とし、状態変更を一つのtransactionとして行う。

| action | 用途 | 遷移 |
|---|---|---|
| `remediate` | in-scope findingを修正する | certificationを失効し、freezeを解除して`review_convergence`へ戻す |
| `disable-feature` | 問題機能をrelease contractから外す最小変更を行う | scope revisionを更新し、freezeを解除して`review_convergence`へ戻す |
| `mark-experimental` | 保証範囲を縮小する変更を行う | scope revisionを更新し、freezeを解除して`review_convergence`へ戻す |
| `scope-amend` | user判断でrelease scopeを変更する | amendmentを記録し、freezeを解除して`review_readiness`へ戻す |
| `retry-certification` | codeとscopeを変えず、不完全reviewだけをやり直す | HEADをfreezeしたまま古い認証試行をcloseし、`awaiting_manual_final_review`へ戻す |
| `abort` | releaseを続行しない | freezeを維持して`paused`または`blocked_user_decision`へ移す |

自動round上限を消費済みの状態で`remediate`、`disable-feature`、`mark-experimental`を選ぶ場合は、`authorized_extra_rounds`をuser inputで明示する。追加roundは元の`max_fix_rounds`を書き換えず、誰が何roundを許可したかをJOURNALとSTATEへ記録する。reopen途中でvalidationまたはartifact更新に失敗した場合は、元のfreezeと失敗phaseを維持し、半端にdispatch可能な状態を残さない。

reopenはsource phaseとactionの組も検証する。`retry-certification`は`manual_final_review_incomplete`だけで許可し、`remediate`はconfirmedなin-scope findingがある場合だけ許可する。scopeを変えるactionでは同じtransaction内に有効なscope amendmentが必要である。

### 失効

manual final review後に次のいずれかが起きた場合、認証を失効させる。

- integration HEADが変わる
- release scope contractのscope revision、source snapshot revision、source digestが変わる
- validation対象のlockfile、toolchain、config snapshotが変わる
- accepted decisionが実装挙動を変える

文書だけの変更でもdiffへ含まれる場合はtarget SHAが変わるため、再レビューを必要とする。scope sourceをamendした場合はintegration HEADが同じでもreviewer contextが変わるため、再レビューを必要とする。レビュー後に最終報告だけを更新する場合は、local-only loop artifactとしてintegration commitへ含めない。

## first-class follow-up

follow-upの正本は、Git管理文書ではなく`.ai/herdr-dev-loop/loops/<namespace>/follow-ups/FNNN.md`とする。一項目は次の情報を持つ。

- ID、title、status
- source review、gap、task、finding ID
- follow-up issue key、issue key version、source review fingerprints、duplicate relation
- discovered HEAD
- evidence、impact、affected pathまたはsymbol
- fact status、severity、origin、contract relation、decision requirement、release判定
- requirement IDまたはrelease scope contractとの関係
- recommended actionとdeferred reason
- target versionまたはmilestone
- reconsider condition
- created at、updated at

follow-up issue keyは、version、影響を受けるcomponent、発生条件のclass、product impact、確認できたroot causeをcanonical JSONへ正規化してSHA-256で計算する。提案された修正方法、severity、review title、対象SHA、行番号はissue identityではないためkeyへ含めない。root causeが未確定の場合は、component、trigger class、product impactから暫定keyを作り、`provisional: true`を記録する。

既存のreview fingerprintは一つのreview内でcandidateを重複排除するために使い、follow-up issue keyの代用にしない。同じissue keyのfollow-upを重複作成せず、既存artifactへsource fingerprintとevidenceを追記する。表現差、symbol移動、root cause判明によって別keyになった候補は、Managerが`duplicate_of`または`supersedes`を記録して統合できる。統合履歴と旧key aliasは削除しない。後のloopがfollow-upを採用する場合は、新しいuser inputまたはMISSIONによってrelease scopeへ入った事実を記録し、taskへ昇格したIDを相互参照する。

必要な場合だけ、Git管理用の`docs/YYYY-MM-DD-<release>-follow-ups.md`へexportする。`reports/FINAL.md`はnamespaced follow-upの件数と参照を投影し、Git exportを正本にしない。

follow-up件数はmanual final finding数へ加算しない。ただし、現在のrelease scope contract違反をfollow-upへ誤分類してはならない。Managerは分類根拠を残す。`accepted_risk`はfollow-upへ変換せず、権限主体、対象risk、期限または再考条件を別のdecision recordとして保存する。

## 状態と設定

### STATE.json

既存のstate format 3を維持し、schema revisionを一つ上げる。次の状態を追加する。

```json
{
  "release_scope": {
    "status": "locked",
    "locked_at": "...",
    "source_refs": ["MISSION.md", "PLAN.md"],
    "source_digests": {},
    "scope_revision": 1,
    "source_snapshot_revision": 1,
    "last_user_input_id": "",
    "amendment_refs": []
  },
  "dispatch_freeze": {
    "status": "inactive",
    "reason": "",
    "frozen_at": "",
    "source_input_id": "",
    "allowed_running_role_ids": []
  },
  "review_policy": {
    "cadence": "batch",
    "pre_final_protocol": "codex-review-multi-v2",
    "manual_final_protocol": "codex-review-multi-v2",
    "max_fix_rounds": 2,
    "scope_expansion_action": "follow_up",
    "final_required": "complete_zero_verified_actionable_findings",
    "lane_count": "auto"
  },
  "review_convergence": {
    "status": "pending",
    "target_sha": "",
    "fix_round": 0,
    "authorized_extra_rounds": 0,
    "extra_round_authorization_refs": [],
    "verified_actionable_findings": null,
    "artifact_refs": []
  },
  "manual_final_review": {
    "status": "pending",
    "certification_id": "",
    "target_sha": "",
    "prepared_plan": "",
    "prepared_plan_digest": "",
    "manifest": "",
    "report": "",
    "manifest_complete": null,
    "shortfall_count": null,
    "verified_actionable_findings": null,
    "attempt_history": []
  },
  "follow_ups": {
    "next_id": 1,
    "open_count": 0,
    "artifact_refs": [],
    "issue_keys": {},
    "issue_key_aliases": {}
  },
  "manager_invocation": {
    "provider": "codex",
    "model": "",
    "reasoning_effort": "",
    "recorded_at": ""
  },
  "execution_metrics": {
    "planned_task_count": 0,
    "remediation_task_count": 0,
    "task_origin_counts": {},
    "review_fix_rounds": 0,
    "finding_disposition_counts": {},
    "stale_review_count": 0,
    "aborted_review_count": 0,
    "stale_gap_count": 0,
    "aborted_gap_count": 0,
    "scope_expansion_started_at": "",
    "effective_parallelism": null
  }
}
```

`manager_invocation`はManagerの能力を制限するためではなく、postmortemでmodelまたはreasoning effortの寄与を検証できるようにする監査情報である。値を取得できないbackendでは空値と取得不能理由をJOURNALへ残す。

### config.toml

新規loopの既定値は次の形とする。

```toml
[defaults.review]
cadence = "batch"
pre_final_protocol = "codex-review-multi-v2"
manual_final_protocol = "codex-review-multi-v2"
max_fix_rounds = 2
scope_expansion_action = "follow_up"
final_required = "complete_zero_verified_actionable_findings"
lane_count = "auto"
```

既存の`review_after_merges`は、`cadence = "merge-count"`の互換設定として維持する。新規loopは`batch`を既定とし、migrated loopは現在の値から`merge-count`を選んで既存挙動を保つ。

`introduced`または`diff-expanded-pre-existing`かつ`in_scope`のregressionをfollow-upへ送る、securityまたはdata lossのblocking findingを黙ってcloseする、不完全なmanual final reviewを合格させる、follow-upだけでfinal gateを失効させる、といった安全性に関わる規則はconfigurableにしない。

### phase

次のphaseまたはsubstateを追加する。

- `review_readiness`
- `review_convergence`
- `review_convergence_exhausted`
- `awaiting_manual_final_review`
- `manual_final_review_failed`
- `manual_final_review_incomplete`
- `ready_for_manager_qa`

既存phaseとの互換を優先し、トップレベルphase追加が不要なら`review_convergence.status`と`manual_final_review.status`で表現する。実装前にstate transition表を更新し、同じ意味をphaseとsubstateへ二重保持しない。

## observabilityとpostmortem

dashboard、progress report、final reportは、少なくとも次を同じrun IDへ紐付けて表示する。

- Managerのprovider、model、reasoning effort
- planned task数とremediation task数
- task origin別件数とscope revision別件数
- review fix round数
- candidate数、confirmed数、origin、contract relation、decision requirement、各disposition数
- confirmed findingからtask、follow-up、disable、user decisionへ進んだ比率
- ReviewerとGap Auditorのcompleted、stale、aborted、timeout数
- manual final reviewのlane完了数、shortfall数、incomplete試行数
- 最初にscope拡大候補が現れた時刻と、taskへ昇格した場合のuser input ID
- requirementごとのplanned、implemented、validated、deferred状態
- phase別wall time、validation time、review wait time、effective parallelism

planned task完了後にremediation taskが増え続ける、staleまたはaborted review比率が高い、2 Worker以上でeffective parallelismが1.5未満、といった状態はpostmortem warningとして表示する。warningだけで自動停止はしないが、round上限とdispatch freezeは機械的に強制する。

Managerのreasoning effortを下げることは、この変更の主要な収束手段にしない。ReviewerとVerifierは高精度設定を維持できる。Managerのmodelまたはeffortを変更する場合も、前後のtask数、finding処置、所要時間を比較できる記録を先に整える。

## CLI変更

既存commandを再利用し、追加CLIは次の範囲へ限定する。

```text
hloop release-scope lock
hloop release-scope status
hloop release-scope amend
hloop dispatch freeze
hloop dispatch status
hloop dispatch unfreeze
hloop follow-up add
hloop follow-up list
hloop follow-up show
hloop follow-up export
hloop review readiness
hloop review convergence prepare
hloop review convergence record
hloop review reopen
hloop final-review prepare
hloop final-review record
hloop final-review status
```

`hloop review convergence prepare`は自動でReviewerを起動せず、既存`hloop reviewer start`へ渡す固定SHAとprotocolを準備する。`hloop final-review prepare`は手動`codex-review-multi-v2`用contextを表示し、target SHAを固定する。

`hloop final-review record`は、`reviews/final/PLAN.json`とstructured manifestが存在し、plan identity、target SHA、protocol、lane completion、verification completeness、finding count、verdictが有効な場合だけ状態を更新する。chat出力だけを根拠にpassedへしない。

`dispatch freeze`の判定は各commandへ個別実装せず、task作成とrole起動が必ず通る共通preflightへ置く。task作成では同じpreflightでtask provenanceも検査する。CLI、pump、triage、conductorのどこから呼ばれても同じ拒否結果になるようにする。`dispatch unfreeze`はuser input IDまたは明示的な再開理由を必須とし、pause解除の副作用では実行しない。review失敗phaseでは`hloop review reopen`だけがfreeze解除とphase遷移を同時に行える。

## artifact変更

次のartifactを追加または拡張する。

- `PLAN.md`：安定したplan item ID、release scope contract、対応環境、非対象、review policy
- `STATE.json`：release scope lock、dispatch freeze、review convergence、manual final review、follow-up inventory、execution metrics
- `release-scope/amendments/ANNN.json`：amendment kind、変更前後のdigestとrevision、根拠、user input参照
- `tasks/<task-id>.md`：task origin、scope revision、planまたはrequirement参照、必要なauthorization
- `reviews/<review-id>.md`：candidateの各分類軸とManager disposition
- `reviews/final/PLAN.json`：手動最終レビューのimmutable planとcontext digest
- `reviews/final/MANIFEST.json`：lane結果、candidate、verification、shortfall、recomputed completeness
- `reviews/final/FINAL.md`：手動最終レビューの人間向け結果
- `follow-ups/FNNN.md`：後続候補のnamespaced正本
- `docs/YYYY-MM-DD-<release>-follow-ups.md`：必要な場合だけ作るGit export
- `reports/FINAL.md`：finding 0件、follow-up件数、manual final review evidence
- `JOURNAL.md`：classification、round消費、freeze、停止理由、Manager invocation

Reviewer artifactのfindingへ次の項目を追加する。

```yaml
finding_id: FND-001
source_artifact: reviews/R001.md
source_candidate_id: C03
fingerprint: sha256:...
target_sha: abcdef0
fact_status: confirmed | refuted | insufficient_evidence
severity: P0 | P1 | P2 | P3
origin: introduced | diff-expanded-pre-existing | unrelated-pre-existing | unknown
contract_relation: in_scope | outside_release | ambiguous
decision_requirement: none | spec | user
disposition: fix_now | defer_follow_up | disable_feature | mark_experimental | user_decision | accepted_risk | discard
release_effect: blocking | non_blocking
requirement_refs:
  - MISSION.md#Done-when
why_fix_now: "現在のacceptanceを破るため"
remediation_round: 1
duplicate_of: ""
```

`fix_now`から作るtaskには、`task_origin: finding`、`source_finding`、非空の`requirement_refs`または明示的なrelease scope参照、`origin`、`contract_relation`、`why_fix_now`、`release_effect`、`release_scope_revision`、`remediation_round`を必須とする。`outside_release`から`fix_now`へ変更する場合はuser input IDと更新後のrelease scope revisionを必須とし、reviewだけを根拠に昇格できないようにする。

task artifactは、既存の`kind`と別に次のprovenanceを持つ。

```yaml
task_origin: finding | planned | user-amendment | operational
release_scope_revision: 2
plan_item_refs: []
requirement_refs:
  - REQ-004
source_finding: FND-001
authorization_input_id: ""
why_fix_now: "現在のacceptanceを破るため"
```

## gateの失効規則

- `fix_now`、`disable_feature`、`mark_experimental`によるcodeまたはrelease artifact変更はintegration HEADを変えるため、review readiness、convergence、manual final reviewを失効させる。
- `defer_follow_up`はsource candidateのtriageをterminalとしてcloseする。記録、重複排除、Git exportはlocal-only namespaced artifactとして扱い、それだけでは再reviewを要求せず、final gateも失効させない。
- `accepted_risk`は権限主体によるdecisionを必要とする。decisionがrelease scope contractを変える場合だけscope revisionを上げ、該当gateを失効させる。
- source snapshot amendmentはscope revisionが同じでもreviewer contextを変えるため、既存のreadiness、convergence、manual final reviewを失効させる。
- taskを作成した事実だけで無条件に全gateを落とさず、taskのdispositionと対象SHAへの影響から失効対象を決める。
- `dispatch freeze`中はgate状態にかかわらず、新規taskとrole起動を拒否する。
- review失敗後のreopenは、認証失効、必要なscopeまたはround authorization、freeze解除、phase遷移を一つのtransactionで行う。

## 実装構成

### Phase 0：fixtureと状態遷移

- 現在のreview cadence、final gate、triage、finishをfixture化する。
- fact status、origin、contract relation、decision requirementを分けたfinding分類matrixを追加する。
- follow-up、manual final review plan、manifest、reportのartifact schemaを追加する。
- state transition表へconvergence、manual final review、failedまたはincompleteからのreopenを追加する。
- migrated loopが現在のmerge-count cadenceを維持するtestを作る。

### Phase 1：release scopeとfirst-class follow-up

- scope source、scope revision、source snapshot revision、digestを固定し、amendment kind別に更新する処理を実装する。
- review candidateのscope classification validatorを実装する。
- 全taskへtask origin、scope revision、PLAN、requirement、finding、user inputのprovenanceを必須化する。
- `fix_now` taskへfinding、requirement、origin、contract relation、理由、release effectを必須化する。
- `follow-ups/FNNN.md`の作成、一覧、参照、version付きissue key重複排除、alias統合を実装する。
- optionalなGit follow-up exportとfinal report projectionを実装する。
- 直接`task new`を含む全経路で、user inputなしのscope-expanding task作成を拒否する。

### Phase 2：dispatch freezeとtriage guard

- task認証と全role起動が通る共通dispatch preflightを実装する。
- user stop、convergence、round exhaustedからdispatch freezeへ遷移する。
- dashboardとconductorからfreeze中のrole起動提案を除く。
- `triage --create-tasks`を`fix_now`だけに限定する。
- follow-up登録ではfinal gateを失効させず、code変更taskだけを対象SHAに応じて失効させる。
- failedまたはincomplete reviewから原子的に復帰する`review reopen`と追加round認証を実装する。

### Phase 3：batch cadenceと速度計測

- 新規loopのreview cadenceをbatchへ変更する。
- batch close後だけ通常review gateを開く。
- Manager identity、planned/remediation task、finding disposition、stale/aborted role、effective parallelism、longest Worker、validation time、review waitを記録する。
- full validation evidenceを同一SHAで再利用する。
- task作成時にwrite scope衝突を表示する。

### Phase 4：pre-final convergence

- readiness gateを実装する。
- `codex-review-multi-v2`互換のpre-final contextを生成する。
- review結果を一つのremediation batchへまとめる。
- max 2 roundを強制する。
- budget exhausted時に新規taskを作らず停止する。

### Phase 5：手動最終認証

- `final-review prepare`でtarget SHA、scope snapshot、lane plan、verification policyとplan digestを固定する。
- `reviews/final/PLAN.json`、`MANIFEST.json`、`FINAL.md`のschemaとrecord commandを実装する。
- lane completenessとverification completenessをHLoopで再計算する。
- completeかつverified actionable finding 0件だけをpassedにする。
- incomplete reviewをfinding 0件として合格させず、同一SHAでretryできるreopen経路を実装する。
- HEAD、scope revision、source snapshot変更時に認証を失効させる。
- finish gateへmanual final reviewを追加する。

### Phase 6：報告、migration、文書

- reports/FINALへfinding disposition、follow-up参照、round、performance、Manager invocationを追加する。
- format 3のschema revision migrationを実装する。
- SKILL.md、manager-loop、reviewer-contract、review-swarm、configurationを更新する。
- CodexとClaudeのinstall copyを同期し、parityを確認する。
- 実リポジトリの小規模dogfoodでmanual final manifestがcompleteかつfinding 0件になるまで確認する。

## テスト計画

### unit test

- candidate分類matrix
- `introduced`かつ`in_scope`のregressionを両軸で保持できること
- confirmedかつ`decision_requirement: spec`を同時に表現できること
- scope source digestの生成とdrift検出
- editorialまたはclarification amendmentではsource snapshotだけが上がること
- scope-change amendmentではuser inputなしにscope revisionを上げられないこと
- confirmedな`outside_release` P1からのtask作成拒否
- `introduced`または`diff-expanded-pre-existing`かつ`in_scope`のP1が`fix_now`かつ`blocking`になること
- 到達可能なsecurityまたはdata lossのP0を`defer_follow_up`だけでcloseできないこと
- user inputによるscope拡張の明示許可
- `task new`がlocked PLANまたはrequirement参照のない`planned` taskを拒否すること
- task updateがprovenanceを削除または変更できないこと
- triageと直接CLIが同じtask authorizationを通ること
- 修正案、severity、SHAだけが異なるfollow-upを同じissue keyで重複排除すること
- 暫定issue keyを確定keyへ統合し、旧key aliasを保持すること
- follow-upのfinal report projection
- follow-up登録だけではfinal gateを失効させないこと
- accepted riskとfollow-upが別artifactになること
- dispatch freezeがtask、Worker、Reviewer、Gap Auditor、Advisorの起動を拒否すること
- freeze中のdashboardがrole startを提案しないこと
- review fix round上限
- batch cadenceとmerge-count互換
- effective parallelism計算
- manual final review plan、manifest、reportのidentity validator
- missing、failed、timeout laneがあるfinding 0件reviewを`incomplete`にすること
- verification shortfallまたはincomplete findingがあるreviewを合格させないこと
- chat本文または`FINAL.md`だけではmanual final reviewをpassedにできないこと
- target SHA不一致による認証拒否
- HEAD、scope revision、source snapshot変更による認証失効
- review reopenが認証失効、追加round認証、freeze解除、phase遷移を原子的に行うこと
- reopen失敗時に元のfreezeとphaseを維持すること

### integration test

- batch内の複数mergeでReviewerを起動せず、batch close後に一度だけgateを開く
- in-scope P1 findingをremediation taskへ変換する
- confirmedなscope外P1をfollow-upへ送り、taskを作らない
- unrelated pre-existing issueをpatch verdictから除外する
- `triage --create-tasks`が`fix_now`以外をtask化しない
- release scope lock後の直接`task new`でscope外taskを作れない
- user stop後にpumpと直接CLIの両方から新規roleを起動できない
- freeze中のdashboardとconductorがReviewerまたはGap Auditorをnext actionに出さない
- pre-final finding 0件からmanual final待機へ遷移する
- manual final finding 1件でfinishを拒否する
- manual final finding 0件かつcompleteなmanifestでManager QAへ進む
- manual final finding 0件でもlane timeoutまたはverification shortfallがあればfinishを拒否する
- final review後のmergeで認証を失効させる
- round上限後に`review_convergence_exhausted`で停止する
- manual final finding後にuser-authorized reopenでremediationへ戻り、追加roundを記録する
- incomplete manual finalを同一SHAでretryし、古い試行を履歴へ残す
- migrationした既存loopが従来cadenceを保つ

### synthetic E2E

次の六シナリオを追加する。

1. 3 Workerを並列実行し、batch reviewで一つのin-scope findingを確認し、一つのremediation batchで修正し、pre-finalが0件、manual finalがcompleteかつ0件で完了する。
2. reviewが妥当なscope-expanding candidateを報告し、taskを作らず`follow-ups/FNNN.md`へ記録し、follow-upによってfinal gateを失効させず、manual finalがcompleteかつfinding 0件で完了する。
3. 2 round後もin-scope findingが残り、自動で3 round目へ進まず停止する。
4. userが停止を指示した後、active roleだけを安全な境界まで完了させ、新規task、Reviewer、Gap Auditorを起動せずfinal reportへ到達する。
5. manual finalのlaneがtimeoutし、finding 0件でも`incomplete`としてfinishを拒否し、同一SHAのretryでcompleteになって完了する。
6. manual finalが新しいin-scope findingを確認し、userが追加roundを許可してreopenし、remediation、convergence、再認証へ戻る。

### performance acceptance

synthetic E2Eでは、次を確認する。

- 3つの非衝突Workerが同時にrunningになれる
- batch内mergeごとにfull Reviewerを起動しない
- full validation回数がWorker数ではなくbatch数に比例する
- review修正taskが一つのremediation batchへ集約される
- review wait中に安全なWorker成果をharvestできる

時間そのものはCI環境で変動するため、固定秒数をpass条件にしない。起動数、validation回数、review回数、状態遷移を測定可能な代理指標として使う。

## migration

既存loopを新しい既定へ暗黙移行しない。migrationは次の値を設定する。

- `review_policy.cadence = "merge-count"`
- `review_policy.max_fix_rounds = 2`
- `review_policy.scope_expansion_action = "follow_up"`
- `review_convergence.status = "not-started"`
- `review_convergence.authorized_extra_rounds = 0`
- `manual_final_review.status = "not-required-for-legacy-run"`
- `release_scope.status = "legacy-unlocked"`
- `release_scope.scope_revision = 0`
- `release_scope.source_snapshot_revision = 0`
- `dispatch_freeze.status = "inactive"`
- 既存taskは`task_origin = "legacy-unclassified"`として読み取る
- `follow_ups`は空のissue key inventoryから開始する

新しいmanual final gate、release scope lock、dispatch freezeを既存running loopへ突然追加するとfinish条件が変わるため、legacy runでは自動有効化しない。必要な場合は、新しいreview policyを持つnamespaceを作成する。新規loopはmanual final gateとrelease scope lockを既定で有効にし、dispatch freezeは停止条件に入ったときだけ有効にする。

## rollout

この変更は0.5.0の未完taskにも、0.5.1のrelease hardeningにも含めない。0.5.1をreleaseした後、0.5.2の独立した改善として実装する。0.5.1は信頼境界、report idempotency、release evidenceの収束に限定し、本計画のdogfood対象にしない。

1. unit、integration、synthetic E2Eを通す。
2. `hloop selftest`とskill quick validationを通す。
3. temporary repositoryで新規loopとmigrated loopを確認する。
4. 小規模な実装をdogfoodし、pre-final review、follow-up分類、manual final reviewを実行する。
5. repo copyとCodex install copyを同期する。
6. Claude install copyを同期する。
7. `diff -qr`でinstall parityを確認する。
8. 実行時間、planned/remediation task数、review回数、stale/aborted数、validation回数、finding disposition、Manager invocationをpostmortemへ記録する。

## 非目標

- finding数を減らすためにReviewer promptを弱めない。
- release scope contractに違反する具体的な不具合をfollow-upへ送らない。
- dual-swarmを通常の最終gateにしない。
- mergeごとに`codex-review-multi-v2`を実行しない。
- review candidateからfix-taskを自動生成しない。
- Reviewerへcode修正権限を与えない。
- manual final reviewをschedulerから自動実行しない。
- 新しいAgent role、broker、wake protocol、worktree transactionを追加しない。
- review convergenceのために汎用issue trackerを実装しない。
- performance改善を理由にvalidation evidenceを省略しない。
- Managerのreasoning effortを下げることだけで収束問題を解決したことにしない。
- この改善のために0.5.0へ新しい実装taskを追加しない。

## 完了時の最終報告

最終報告は次の形を持つ。

```text
Implementation scope: verified
Integration validation: passed at <sha>
Pre-final convergence review: 0 verified actionable findings
Manual codex-review-multi-v2: 0 verified actionable findings at <sha>
Manual review completeness: complete, shortfalls 0
Manual review plan: <certification-id>/<plan-digest>
Manager final QA: passed
Manager invocation: <provider>/<model>/<reasoning-effort>
Tasks: <planned> planned, <remediation> remediation
Review fix rounds: <n>/2, user-authorized extra <n>
Follow-ups: <n>
Residual risks: <n>
Role shortfalls: reviewers <stale>/<aborted>, gaps <stale>/<aborted>
Effective parallelism: <value>
Push/install status: <status>
```

follow-upとresidual riskの本文は省略せず、今回のfinishを妨げない理由を各項目へ記録する。

## 未決事項

実装前に次の二点だけを確定する。

1. manual final reviewを全新規loopで必須にするか、configで有効化したloopだけにするか。
2. broad/high-risk diffで`codex-review-multi-v2`を6 laneへ増やす判定を、定量条件で固定するか、Manager判断として理由だけ記録するか。

どちらもruntimeの中核設計を変えない。既定案は、manual final reviewを新規loopで必須とし、lane数は4を既定、6への増加はManagerが理由を記録する方式とする。
