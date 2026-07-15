# herdr-dev-loop 有界レビュー収束と手動最終認証の実装計画

- 作成日：2026-07-16
- 対象：herdr-dev-loop 0.5.2
- 状態：実装前、0.5.0 Manager postmortem反映済み
- 関連機能：HLoop Native Review、`codex-review-multi-v2`、final gate、triage、最終報告

## 目的

herdr-dev-loopの実装速度を維持しながら、最終的に独立した`codex-review-multi-v2`を手動実行し、検証済みの修正対象findingが0件である状態へ収束させる。

この計画でいう**finding 0件**は、候補や懸念が一つも存在しないことではない。Coordinatorが実コードで確認し、今回の変更範囲で修正すべきと判定した**検証済みactionable finding**が0件であることを指す。新しい能力、対応環境、脅威モデル、運用保証を要求する改善は、findingを隠さずfollow-upとして最終報告へ残す。

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

## 用語

- **release scope contract**：今回のreleaseで実装または保証する範囲を決める正本。MISSION、PLAN、accepted requirement、task acceptance、対応platform、明文化されたtrust boundary、releaseから外してよい機能から構成する。
- **release scope lock**：release scope contractのsourceとdigestを固定する状態。planned taskとin-scope remediationは実行できる。
- **dispatch freeze**：user指示、review収束、停止処理のため、新規taskと新規roleの起動を禁止する運用状態。release scope lockとは別に管理する。
- **review candidate**：Reviewer laneが報告した未検証の問題候補。
- **verified actionable finding**：CoordinatorまたはManagerが発生経路を実コードで確認し、今回のrelease scope contractに違反すると判定した修正対象。
- **scope-expanding candidate**：妥当な改善ではあるが、新しい能力、platform、脅威モデル、運用保証、互換保証を要求する候補。
- **follow-up**：今回のfinishを止めず、重要度、根拠、影響、推奨対応、見送り理由を残す後続作業候補。
- **accepted risk**：今回shipする挙動に残る具体的なriskを、権限を持つ主体が理由付きで受け入れた記録。未実装作業を保存するfollow-upとは区別する。
- **convergence review**：実装HEADを最終レビュー可能な状態へ収束させるpre-finalレビュー。
- **certification review**：収束済みHEADへfreshな文脈から手動実行する最終`codex-review-multi-v2`。
- **review fix round**：一つの固定SHAから確認したin-scope findingを、一つのremediation batchで修正し、再検証する単位。

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

固定後のrelease scope contractは、単なる文書の存在ではなく、対象ファイルとdigestの組としてSTATEへ記録する。user inputによる明示変更以外でrelease scopeを拡張しない。明示変更ではinput ID、変更理由、旧revision、新revision、影響taskを記録する。

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

### dispatch freeze

release scope lockはplanned taskの実行を許可する。userが停止を指示した場合、review収束後に最終認証を待つ場合、またはremediation上限へ達した場合は、別の`dispatch freeze`を有効にする。

dispatch freeze中は、`task new`、Worker start、Reviewer start、Gap Auditor start、Advisor startをCLIが拒否する。すでにrunningのroleは、freeze recordが許可したIDだけを安全な境界まで継続できる。validation、harvest、merge、follow-up記録、最終報告、pauseは許可する。dashboardとconductorはfreeze中に新しいrole起動をnext actionとして提案しない。

## findingの分類

severityだけで「今回直すか」を決めない。review candidateは次の独立した軸を持つ。

| 軸 | 値 |
|---|---|
| 事実性 | `confirmed`、`refuted`、`insufficient_evidence`、`needs_spec` |
| 重要度 | `P0`、`P1`、`P2`、`P3` |
| scopeとの関係 | `original_requirement`、`current_regression`、`preexisting`、`outside_release` |
| 処置 | `fix_now`、`defer_follow_up`、`disable_feature`、`mark_experimental`、`user_decision`、`accepted_risk`、`discard` |
| release判定 | `blocking`、`non_blocking` |

Managerはreview candidateごとに、次の順序で各軸を確定する。

1. 発生経路を現在の対象SHAで再現またはコード上で証明できるか。
2. 今回のdiffが問題を導入または拡大したか。
3. release scope contractのどの項目に違反するか。
4. 修正が現在の契約を回復するものか、新しい保証を追加するものか。
5. 問題のある機能をreleaseから外すか、experimental化できるか。
6. user decisionがなくても、現在の契約内で安全に処置できるか。

処置は次の規則に従う。

| 条件 | 原則処置 | release判定 |
|---|---|---|
| `refuted` | `discard` | `non_blocking` |
| `insufficient_evidence`かつ`preexisting`または`outside_release` | `defer_follow_up`または`discard` | `non_blocking` |
| `insufficient_evidence`または`needs_spec`で、現在のacceptanceまたは安全性を判定できない | `user_decision` | `blocking` |
| confirmedな`preexisting`問題 | patch verdictから除外し、必要なら`defer_follow_up` | `non_blocking` |
| 今回のdiffが導入したP1 | `fix_now` | `blocking` |
| 元のacceptanceを破るP1 | `fix_now` | `blocking` |
| 到達可能なsecurityまたはdata lossのP0とP1 | `fix_now`、`disable_feature`、`user_decision`のいずれか | 解決まで`blocking` |
| confirmedな`outside_release` P1 | `defer_follow_up` | `non_blocking` |
| 現在のcontractを満たす追加保証 | `defer_follow_up` | `non_blocking` |
| 局所修正できず、機能を外せる | `disable_feature`または`mark_experimental` | 処置完了まで`blocking` |
| spec選択が必要だが、選択なしでも現在のacceptanceを満たせる | `defer_follow_up` | `non_blocking` |
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
        +---- verified actionable findings > 0 ---> certification failed
        |
        v
manual final finding count = 0
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
- release scope contractのsource digestがlock時から変わっていない
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

この時点でWorker、Reviewer、Gap Auditorを自動起動しない。手動最終レビューが終わるまでintegration HEADを変更しない。

### 実行

Managerまたはuserは、通常の`codex-review-multi-v2`を明示的に実行する。HLoop schedulerから自動起動せず、freshなreviewer contextを使う。pre-final findingや期待する件数をReviewer promptへ渡さない。

追加focusには、次だけを含める。

- review modeとbase...HEAD
- release scope contractの正本
- 対応platformとtrust boundary
- generated fileの扱い
- repository固有の検証command

Reviewerはcodeを変更しない。Coordinatorはcandidateを実コードで検証し、actionable finding、residual risk、open questionを分離して日本語で報告する。

### 記録

Managerは手動レビュー後、`reviews/FINAL.md`へ次を記録する。

- protocol：`codex-review-multi-v2`
- base SHA
- target SHA
- lane数とlane名
- Coordinator session ID
- reviewed fileまたはdiff inventory
- verified actionable finding数
- findings
- residual risks
- follow-up参照
- patch verdict
- completed at

HLoopはartifactのtarget SHAが現在のintegration HEADと一致することを確認する。`verified_actionable_findings: 0`かつ`patch_verdict: passed`の場合だけmanual final review gateをpassedにする。

最終レビューが1件以上のverified actionable findingを報告した場合、認証はfailedとなる。HLoopは自動修正しない。Managerはscope分類と残りのround budgetを示し、userの指示を待つ。

### 失効

manual final review後に次のいずれかが起きた場合、認証を失効させる。

- integration HEADが変わる
- release scope contractのsource digestが変わる
- validation対象のlockfile、toolchain、config snapshotが変わる
- accepted decisionが実装挙動を変える

文書だけの変更でもdiffへ含まれる場合はtarget SHAが変わるため、再レビューを必要とする。レビュー後に最終報告だけを更新する場合は、local-only loop artifactとしてintegration commitへ含めない。

## first-class follow-up

follow-upの正本は、Git管理文書ではなく`.ai/herdr-dev-loop/loops/<namespace>/follow-ups/FNNN.md`とする。一項目は次の情報を持つ。

- ID、title、status
- source review、gap、task、finding ID
- semantic fingerprintとduplicate relation
- discovered HEAD
- evidence、impact、affected pathまたはsymbol
- fact status、severity、scope relation、release判定
- requirement IDまたはrelease scope contractとの関係
- recommended actionとdeferred reason
- target versionまたはmilestone
- reconsider condition
- created at、updated at

同じsemantic fingerprintのfollow-upを重複作成しない。重複候補は既存artifactへsource evidenceを追記する。後のloopがfollow-upを採用する場合は、新しいuser inputまたはMISSIONによってrelease scopeへ入った事実を記録し、taskへ昇格したIDを相互参照する。

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
    "revision": 1,
    "last_user_input_id": ""
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
    "final_required": "zero_verified_actionable_findings",
    "lane_count": "auto"
  },
  "review_convergence": {
    "status": "pending",
    "target_sha": "",
    "fix_round": 0,
    "verified_actionable_findings": null,
    "artifact_refs": []
  },
  "manual_final_review": {
    "status": "pending",
    "target_sha": "",
    "artifact": "",
    "verified_actionable_findings": null
  },
  "follow_ups": {
    "next_id": 1,
    "open_count": 0,
    "artifact_refs": [],
    "fingerprints": {}
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
final_required = "zero_verified_actionable_findings"
lane_count = "auto"
```

既存の`review_after_merges`は、`cadence = "merge-count"`の互換設定として維持する。新規loopは`batch`を既定とし、migrated loopは現在の値から`merge-count`を選んで既存挙動を保つ。

`current_regression`をfollow-upへ送る、securityまたはdata lossのblocking findingを黙ってcloseする、follow-upだけでfinal gateを失効させる、といった安全性に関わる規則はconfigurableにしない。

### phase

次のphaseまたはsubstateを追加する。

- `review_readiness`
- `review_convergence`
- `review_convergence_exhausted`
- `awaiting_manual_final_review`
- `manual_final_review_failed`
- `ready_for_manager_qa`

既存phaseとの互換を優先し、トップレベルphase追加が不要なら`review_convergence.status`と`manual_final_review.status`で表現する。実装前にstate transition表を更新し、同じ意味をphaseとsubstateへ二重保持しない。

## observabilityとpostmortem

dashboard、progress report、final reportは、少なくとも次を同じrun IDへ紐付けて表示する。

- Managerのprovider、model、reasoning effort
- planned task数とremediation task数
- review fix round数
- candidate数、confirmed数、各disposition数
- confirmed findingからtask、follow-up、disable、user decisionへ進んだ比率
- ReviewerとGap Auditorのcompleted、stale、aborted、timeout数
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
hloop final-review prepare
hloop final-review record
hloop final-review status
```

`hloop review convergence prepare`は自動でReviewerを起動せず、既存`hloop reviewer start`へ渡す固定SHAとprotocolを準備する。`hloop final-review prepare`は手動`codex-review-multi-v2`用contextを表示し、target SHAを固定する。

`hloop final-review record`は、structured artifactが存在し、target SHA、protocol、finding count、verdictが有効な場合だけ状態を更新する。chat出力だけを根拠にpassedへしない。

`dispatch freeze`の判定は各commandへ個別実装せず、task作成とrole起動が必ず通る共通preflightへ置く。CLI、pump、triage、conductorのどこから呼ばれても同じ拒否結果になるようにする。`dispatch unfreeze`はuser input IDまたは明示的な再開理由を必須とし、pause解除の副作用では実行しない。

## artifact変更

次のartifactを追加または拡張する。

- `PLAN.md`：release scope contract、対応環境、非対象、review policy
- `STATE.json`：release scope lock、dispatch freeze、review convergence、manual final review、follow-up inventory、execution metrics
- `reviews/<review-id>.md`：candidateの各分類軸とManager disposition
- `reviews/FINAL.md`：手動最終レビュー結果
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
fact_status: confirmed | refuted | insufficient_evidence | needs_spec
severity: P0 | P1 | P2 | P3
scope_relation: original_requirement | current_regression | preexisting | outside_release
disposition: fix_now | defer_follow_up | disable_feature | mark_experimental | user_decision | accepted_risk | discard
release_effect: blocking | non_blocking
requirement_refs:
  - MISSION.md#Done-when
why_fix_now: "現在のacceptanceを破るため"
remediation_round: 1
duplicate_of: ""
```

`fix_now`から作るtaskには、`source_finding`、非空の`requirement_refs`または明示的なrelease scope参照、`scope_relation`、`why_fix_now`、`release_effect`、`remediation_round`を必須とする。`outside_release`から`fix_now`へ変更する場合はuser input IDとrelease scope revisionを必須とし、reviewだけを根拠に昇格できないようにする。

## gateの失効規則

- `fix_now`、`disable_feature`、`mark_experimental`によるcodeまたはrelease artifact変更はintegration HEADを変えるため、review readiness、convergence、manual final reviewを失効させる。
- `defer_follow_up`はsource candidateのtriageをterminalとしてcloseする。記録、重複排除、Git exportはlocal-only namespaced artifactとして扱い、それだけでは再reviewを要求せず、final gateも失効させない。
- `accepted_risk`は権限主体によるdecisionを必要とする。decisionがrelease scope contractを変える場合だけscope revisionを上げ、該当gateを失効させる。
- taskを作成した事実だけで無条件に全gateを落とさず、taskのdispositionと対象SHAへの影響から失効対象を決める。
- `dispatch freeze`中はgate状態にかかわらず、新規taskとrole起動を拒否する。

## 実装構成

### Phase 0：fixtureと状態遷移

- 現在のreview cadence、final gate、triage、finishをfixture化する。
- 直交したfinding分類matrix、follow-up、manual final reviewのartifact schemaを追加する。
- state transition表へconvergenceとmanual final reviewを追加する。
- migrated loopが現在のmerge-count cadenceを維持するtestを作る。

### Phase 1：release scopeとfirst-class follow-up

- scope sourceとdigestを固定する処理を実装する。
- review candidateのscope classification validatorを実装する。
- `fix_now` taskへfinding、requirement、scope relation、理由、release effectを必須化する。
- `follow-ups/FNNN.md`の作成、一覧、参照、fingerprint重複排除を実装する。
- optionalなGit follow-up exportとfinal report projectionを実装する。
- user inputなしのscope-expanding task作成を拒否する。

### Phase 2：dispatch freezeとtriage guard

- task作成と全role起動が通る共通dispatch preflightを実装する。
- user stop、convergence、round exhaustedからdispatch freezeへ遷移する。
- dashboardとconductorからfreeze中のrole起動提案を除く。
- `triage --create-tasks`を`fix_now`だけに限定する。
- follow-up登録ではfinal gateを失効させず、code変更taskだけを対象SHAに応じて失効させる。

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

- `final-review prepare`でtarget SHAとcontextを固定する。
- `reviews/FINAL.md` schemaとrecord commandを実装する。
- verified actionable finding 0件だけをpassedにする。
- HEADまたはscope source変更時に認証を失効させる。
- finish gateへmanual final reviewを追加する。

### Phase 6：報告、migration、文書

- reports/FINALへfinding disposition、follow-up参照、round、performance、Manager invocationを追加する。
- format 3のschema revision migrationを実装する。
- SKILL.md、manager-loop、reviewer-contract、review-swarm、configurationを更新する。
- CodexとClaudeのinstall copyを同期し、parityを確認する。
- 実リポジトリの小規模dogfoodでmanual final finding 0件まで確認する。

## テスト計画

### unit test

- candidate分類matrix
- scope source digestの生成とdrift検出
- confirmedな`outside_release` P1からのtask作成拒否
- `current_regression` P1が`fix_now`かつ`blocking`になること
- 到達可能なsecurityまたはdata lossのP0を`defer_follow_up`だけでcloseできないこと
- user inputによるscope拡張の明示許可
- follow-up fingerprintの重複排除
- follow-upのfinal report projection
- follow-up登録だけではfinal gateを失効させないこと
- accepted riskとfollow-upが別artifactになること
- dispatch freezeがtask、Worker、Reviewer、Gap Auditor、Advisorの起動を拒否すること
- freeze中のdashboardがrole startを提案しないこと
- review fix round上限
- batch cadenceとmerge-count互換
- effective parallelism計算
- manual final review artifact validator
- target SHA不一致による認証拒否
- HEAD変更による認証失効

### integration test

- batch内の複数mergeでReviewerを起動せず、batch close後に一度だけgateを開く
- in-scope P1 findingをremediation taskへ変換する
- confirmedなscope外P1をfollow-upへ送り、taskを作らない
- unrelated pre-existing issueをpatch verdictから除外する
- `triage --create-tasks`が`fix_now`以外をtask化しない
- user stop後にpumpと直接CLIの両方から新規roleを起動できない
- freeze中のdashboardとconductorがReviewerまたはGap Auditorをnext actionに出さない
- pre-final finding 0件からmanual final待機へ遷移する
- manual final finding 1件でfinishを拒否する
- manual final finding 0件でManager QAへ進む
- final review後のmergeで認証を失効させる
- round上限後に`review_convergence_exhausted`で停止する
- migrationした既存loopが従来cadenceを保つ

### synthetic E2E

次の四シナリオを追加する。

1. 3 Workerを並列実行し、batch reviewで一つのin-scope findingを確認し、一つのremediation batchで修正し、pre-finalとmanual finalが0件で完了する。
2. reviewが妥当なscope-expanding candidateを報告し、taskを作らず`follow-ups/FNNN.md`へ記録し、follow-upによってfinal gateを失効させず、manual final finding 0件で完了する。
3. 2 round後もin-scope findingが残り、自動で3 round目へ進まず停止する。
4. userが停止を指示した後、active roleだけを安全な境界まで完了させ、新規task、Reviewer、Gap Auditorを起動せずfinal reportへ到達する。

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
- `manual_final_review.status = "not-required-for-legacy-run"`
- `release_scope.status = "legacy-unlocked"`
- `dispatch_freeze.status = "inactive"`
- `follow_ups`は空inventoryから開始する

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
Manager final QA: passed
Manager invocation: <provider>/<model>/<reasoning-effort>
Tasks: <planned> planned, <remediation> remediation
Review fix rounds: <n>/2
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
