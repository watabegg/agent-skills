# herdr-dev-loop 0.4.0 runtime hardening plan

- 作成日：2026-07-12
- 対象：`herdr-dev-loop 0.3.0`、commit `3777a5b`
- 目標版：`0.4.0`
- 状態：実装待ち

## 目的

2026-07-12のCodex Manager runとClaude Manager runで確認したruntime、状態機械、復旧経路の欠陥を修正する。修正後は、Workerが自分のbranchを完結させ、ManagerがHLoopの状態やgateを偽装せずに、失敗、再実行、競合、停止、完了へ遷移できる状態を作る。

今回の変更は、個別プロジェクトの実装を直すものではない。`scripts/hloop`、schema、Skill契約、テスト、利用者向け文書を更新し、CodexとClaudeの両方で同じlifecycle保証を提供する。

## 調査済みの事実

調査対象の親runは2件だった。片方はCodex ManagerがWorker 14件、Reviewer 4件、Gap Auditor 4件を管理し、もう片方はClaude ManagerがWorker 21件、Reviewer 13件、Gap Auditor 9件を管理した。

調査時点でrepo版、Codexインストール版、Claudeインストール版の内容は一致し、`hloop selftest`とSkill validatorも成功した。このため、問題の原因は古いインストールではなく、0.3.0の実行時契約とテスト範囲にある。

確認した主な事実は次のとおりである。

| 対象 | 観測結果 |
| --- | --- |
| linked worktree | 21 Worker中11件でGit metadataへ書き込めず、Managerがcommitまたはfinalizeを代行した |
| requeue | STATEの`base_sha`だけが更新され、古いbranchを新しいbaseとして再利用した |
| merge conflict | 正規の復旧コマンドがなく、ManagerがGit wrapperで成功判定を迂回した |
| completion | 最後のmerge後のvalidation、review、gap、Manager QAが未完了でも完了報告とpushが行われた |
| Manager message | 一方のrunで25件がpendingのまま残り、自動再送もACKも行われなかった |
| pane監視 | 調査時のHerdr server log 18,343行中16,225行が、閉じたpaneへの`pane.get`失敗だった |
| setupと容量 | 同じsetup commandが全roleへ33回実行され、`/tmp` quota不足が複数roleを止めた |
| provider設定 | Codexのreasoning effortを固定できず、ClaudeはBash承認待ちを通常のidleとして扱った |
| 状態整合性 | cleanup失敗、古いblocker、pending message、QA待ちがあっても`conductor`が0 issueを返した |

## 守る不変条件

実装は次の不変条件を破らない。

1. Workerのproduct commitとresult finalizeはWorker自身が行う。ManagerはWorker artifactの`blocked`、`failed`、`partial`を`done`へ書き換えない。
2. 一つのattemptが持つWorker base SHAは不変とする。STATEだけを新しいintegration HEADへ付け替えない。
3. validation、review、gap、Manager QAは、実施対象のcompletion target SHAと組にして記録する。別targetのgateを完了判定へ流用しない。
4. providerのmodel、effort、permission、writable pathはrole processへ明示的に渡す。Managerは`~/.codex/config.toml`やClaudeのglobal設定を書き換えない。
5. merge conflict、環境エラー、artifactのないagent終了を別の状態として扱う。通常の待機へ落とさない。
6. `done`への遷移は一つのcompletion gateを通す。レポート生成やpushはcompletion gateの代わりにならない。
7. role worktreeのsetupはrole別に選び、agent設定、権限設定、指示文書を自己変更するcommandを受け入れない。
8. `.ai/herdr-dev-loop/loops/<namespace>`を正本とし、pane、thread memory、手作業のMarkdownコピーを正本にしない。

## 今回の非目標

- プロジェクト固有のproduct codeやCIを変更しない。
- merge conflictを自動解決しない。HLoopは競合状態を保存し、安全なabort、retry、continueを提供する。
- Herdr serverの`PaneDied for unknown pane`競合そのものは直さない。HLoopから不要なpane照会を止める。
- Claude session archive機能を独自実装しない。providerが未対応なら`unsupported`を正確に記録する。
- 0.4.0では`hloop`全体を複数moduleへ分割しない。純粋関数を増やしてテスト可能にするが、大規模な構造変更は別作業とする。
- reviewとgapの既定頻度は、正しさに関する修正が安定するまで変更しない。batch単位のcadenceは選択肢として追加し、実run比較後に既定変更を判断する。

## 0.4.0で採用する設計

### state formatと移行

`STATE.json`へ`state_format_version: 2`を追加する。0.3.0 stateは読み取り時に勝手に書き換えず、read-only commandでは移行必要と表示し、mutating commandでは`hloop migrate`を要求する。

`hloop migrate --dry-run`は変更予定と判定不能項目を表示する。`hloop migrate --apply`は元のSTATEをnamespace内のmigration backupへ保存してからatomic writeする。running role、active merge transaction、dirty role worktreeがある場合はapplyを拒否する。旧formatでもread-only inspectionと`agent abort`は許可し、Managerが安全にrunning roleを閉じてからmigrationできるようにする。

移行後も`run_id`は維持し、STATEの`skill_version`と新規task、attempt、artifactのidentityを0.4.0へ更新する。移行前にharvestまたはmerge済みのartifactは、元のversionとattempt情報をhistoryとして保全するが、新しいgateへ流用しない。queued taskは0.4.0 contractへ更新する。未収穫artifactやbaseを確定できないroleは自動補完せず、fresh requeueを要求する。migration後はcurrent completion targetに対するvalidation、review、gap、Manager QAをstaleとして扱う。

### attemptとbranch

各roleに`attempt_no`と`active_attempt_id`を持たせ、過去attemptはrole配下の`attempts`へ保存する。Worker attemptには少なくとも次を記録する。

- `attempt_id`
- `attempt_no`
- `branch`
- `worker_base_sha`
- `started_at`
- `pane_id`
- `worktree`
- `agent_provider`
- `agent_model`
- `agent_effort`
- `result_status`
- `head_sha`
- cleanup結果

`agent requeue`の既定動作はfresh attemptとする。旧branchとworktreeを検査し、integrationへ未到達のcommitが一件でもあればattempt history用branchへ必ずarchiveする。旧branchを削除できるのは、変更が完全に統合済みの場合か、Managerが差分を確認して明示的なforceと理由を指定した場合だけとする。その後、現在のintegration HEADからattempt番号付きbranchを作る。同じbaseとbranchを続行する操作は`agent resume`へ分け、clean worktree、同一branch、同一base、artifact未確定を満たす場合だけ許可する。

write-scopeは`worker_base_sha..Worker HEAD`で計算する。integration HEADが先へ進んでいても、別Workerの変更を対象Workerの差分へ混ぜない。merge時には`worker_base_sha`がWorker HEADと現在のintegration HEADの双方のancestorであることを確認する。

### provider起動契約

shell文字列を直接組み立てる処理を、argv生成とshell描画へ分ける。テストは描画後の文字列ではなくargv tokenを検証する。

Codex roleでは、role worktreeから絶対Git common dirを取得し、TUIとexecの両方へ`--add-dir <git-common-dir>`を渡す。`model_reasoning_effort`はrole別の`agent_effort`からprocess-localな`-c`引数で渡す。

Claude roleでは`--effort`とpermission modeをrole別に渡す。0.4.0の既定permission modeは、local CLIが対応する場合は`auto`とし、未対応なら起動前に明示エラーとする。`bypassPermissions`、`dangerously-skip-permissions`などの全許可設定へ自動fallbackしない。必要なallowed toolsを導入する場合もrole単位の一時設定とし、projectまたはglobal設定ファイルへ書き込まない。

provider、model、effort、permission、runnerは、profile既定、task override、start overrideの順に解決し、実際に起動した値をattemptへ保存する。`doctor`は利用中CLIのcapabilityを検査し、未対応flagをrole起動前に報告する。

Gitは`init`またはmigration時に`shutil.which`で得た実体のabsolute realpath、`--version`、`st_dev`、`st_ino`、SHA-256をSTATEへ記録し、そのabsolute pathを使って呼び出す。runtime中にidentityが変わった場合はenvironment blockerとして停止する。正規のpackage更新後は、running roleとactive mergeがない状態で`hloop runtime trust-git --reason <text>`を実行し、旧identityと新identityをJOURNALへ残して更新する。Worker start時にはHLoopが管理する`integration_head_sha`を記録し、harvest時にintegration branchと保護対象refがHLoopの記録外で変化していないか検査する。Git common dirを許可しても、Worker branch以外のref変更を受け入れない。

### role別setupとdisk preflight

共通の`worktree_setup_commands`を廃止し、次のrole別設定へ移行する。

- `worker_setup_commands`
- `reviewer_setup_commands`
- `gap_setup_commands`
- `advisor_setup_commands`

0.3.0の`worktree_setup_commands`はmigration時に`worker_setup_commands`へ移す。全roleへ暗黙適用しない。複数roleへ同じsetupが必要な場合は、それぞれへ明示する。

setup commandまたは`experience recommend`が`.claude/**`、`.codex/**`、`AGENTS.md`、`CLAUDE.md`、permission設定、sandbox bypassを変更しようとした場合は拒否する。この検査は任意shell commandを完全にsandbox化するものではないため、既知markerの拒否と実行結果の監査を組み合わせる。

role start、`tick`、`pump`はworktree作成やsetupより先にdisk preflightを行う。repo、worktree root、`TMPDIR`、主要cache pathについて空きbytesとinodeを検査し、同一filesystemは一度だけ数える。filesystem quotaが空き容量へ現れない場合に備え、各対象pathで小さなcreate、write、fsync、delete probeも行う。初期既定値は空き2 GiB、空きinode 50,000とし、profileまたはCLIで変更できるようにする。`0`は明示的な無効化とし、負数や不正値は拒否する。

容量不足時はbranch、worktree、pane、taskの`running`遷移を残さない。STATEとJOURNALにはpreflight failureと`blocked_environment`だけをatomicに記録する。`doctor --json`は各pathのavailable値、required値、filesystem、write probe結果を返す。

roleごとにnamespace-scopedな`TMPDIR`とtool cacheを設定できるようにし、attemptをまたぐdownload cacheをworktree外へ置く。同じattemptをpane launch failureから再開する場合は、setup commandとlockfileのfingerprintが一致し、worktreeが残っているときだけsetup再実行を省略する。fresh attemptではsetupを省略せず、共有cacheを利用する。`hloop cleanup stale`はnamespace内の終了済みworktreeと一時cacheだけを対象にし、他namespaceや利用者のglobal cacheを削除しない。

### merge transaction

merge開始前に、task、attempt、branch、integration HEAD、indexのclean状態を`merge_attempt`として保存する。content conflictと、index lock、read-only filesystem、quota不足などのenvironment errorを別statusへ分ける。

追加する操作は次のとおりである。

- `hloop merge <task-id>`：cleanな状態からmergeを開始する。
- `hloop merge <task-id> --abort`：HLoopが記録したpre-merge HEADとindexへ戻す。
- `hloop merge <task-id> --retry`：abort済みかつcleanな状態から同じattemptを再試行する。
- `hloop merge <task-id> --continue`：Managerが解消したindexを再検証し、許可範囲内ならcommitする。

`continue`は未解消path、conflict marker、write-scope、result HEAD、Worker branch head、pre-merge HEADを再検査する。commit後はabsolute pathで呼び出したGitからHEAD、tree、index、Worker branch ref、changed pathsを再取得し、記録したpostconditionと突合して`needs_validation`を立てる。Gitのexit codeだけで成功扱いにせず、PATH wrapperによるfalse successを検出する。

`blocked_merge_conflict`から通常の`merge`を再実行することは拒否する。Managerが直接commitした場合も、記録したtransactionと一致しなければ`manual_integration_trace`としてP0にする。

### pane、wait、message

roleのterminal sentinelはrun、role、attemptのidentityを含む次の形式へ変更する。

```text
HERDR_LOOP_ROLE_DONE:<run-id>:<role-id>:<attempt-id>:<status>
```

sentinelから`done`、`blocked`、`failed`、`partial`を解析し、現在のrun、role、attemptと完全一致するものだけを採用する。agentがterminal状態なのにartifactがない場合はrole statusとconductor issueを`terminal_without_artifact`とし、loop phaseを`blocked_agent`へ移す。`wait`と`pump`はtimeoutまで待たず、次の操作としてabortまたはfresh requeueを表示する。

paneを閉じたらactive `pane_id`を消し、`closed_pane_id`とattempt historyへ移す。dashboardとconductorはrunningまたはcleanup failureのroleだけをprobeする。可能な場合は一回の`herdr pane list`からmapを作り、roleごとの`pane get`を避ける。

Claude固有のtrust prompt、Bash permission prompt、選択待ちをP1 blockerとして検出する。providerに依存しないissue codeを使い、表示文だけをprovider別にする。Claude session cleanup未対応時は`cleanup_status: unsupported`とし、`cleaned_at`を記録しない。

Manager messageにはUUID形式の`message_id`、対象run、role、attempt、本文digest、状態、送信時刻、ACK時刻を持たせる。role promptは、指示の処理を始める前に次のsentinelを返す。

```text
HERDR_LOOP_MESSAGE_ACK:<run-id>:<role-id>:<attempt-id>:<message-id>
```

送信結果は`delivered`、`undelivered`、`unknown`へ分ける。可視性確認だけに失敗した場合は`unknown`とし、未配送と断定しない。`hloop message drain`が自動再送するのは`undelivered`だけとする。`unknown`はACK、pane確認、Managerによる`retry`または`resolve`のいずれかを要求する。同一attempt内ではmessage IDによる重複送信を抑止するが、agentがACK前に外部副作用を起こして終了した場合のexactly-once実行は保証しない。role終了時は未送信messageを`superseded`としてarchiveし、完了後もpending directoryへ残さない。

### lifecycleとcompletion gate

`paused`、`blocked_agent`、`ready_to_finish` phaseと次の操作を追加する。

- `hloop pause --reason <text>`：新規dispatch、merge、gate開始を止める。running agentを自動abortしない。
- `hloop resume`：blocker、branch、dirty state、running roleを再検査して再開する。
- `hloop finish`：全completion gateを同一completion targetに対して検証し、`done`とFINALを一度に記録する。
- `hloop cleanup resolve <role-id> --status <cleaned|accepted-risk> --reason <text>`：cleanup failureを証拠付きで閉じる。
- `hloop handoff record <task-id> --head-sha <sha> --evidence <text>`：`pr-per-task`または`custom` strategyのhandoff完了を記録する。
- `hloop completion target --head-sha <sha>`：非integration strategyで最終gateが監査する単一のlocal commitを記録する。

paused中もread-only inspection、watch、wait、message、harvest、agent abort、agent requeue、cleanup、reportを許可する。ただしrequeueしたroleはqueuedのままにし、start、dispatch、merge、validation、review、gap、QA、finishは`resume`まで拒否する。

validation結果へ`head_sha`、reviewとgapのclosed gateへ`head_sha`、Manager QAへ`head_sha`を必須化する。integration HEADが変わった場合は、対象外になったgateをcompletion判定上`stale`にする。通常のgate開始はcadenceに従い、全task終了後と`finish`前だけcurrent completion targetを対象にする最終reviewとgapを強制する。

`integration` strategyではcurrent integration HEADをcompletion targetとする。`pr-per-task`と`custom`では、各taskのhandoff記録に加えて、全変更を表す単一のlocal commit SHAを`completion target`として記録する。単一commitを用意できない外部handoffは0.4.0の`finish`対象外とし、phaseを`branch_handoff`に保つ。`PLAN.md`に後続の統合、QA、完了手順を記録する。

`finish`は次をすべて満たす場合だけ成功する。

1. implementationとfix taskがすべてmergedまたは明示的なbranch handoff完了である。
2. running、artifact未収穫、未triageのroleがない。
3. completion targetを保持するManager checkoutまたはworktreeとindexがcleanである。
4. current completion targetに対するintegration validationがpassedである。
5. 有効化されているreviewとgap gateがcurrent completion targetに対してclosedである。
6. 必要なManager QAがcurrent completion targetに対してpassedまたはaccepted-riskである。
7. cleanup pendingと未解決のcleanup failureがない。
8. active blocker、未解決の`undelivered`または`unknown` message、manual integration traceがない。

cleanup不能を残して完了する場合は、`cleanup resolve`でaccepted riskと理由を記録してから`finish`する。`tick`、`pump`、`qa record`、`report`は`done`を書かず、全gateが揃ったときに`ready_to_finish`まで進める。`done`へ遷移できる公開操作は`hloop finish`だけとする。`report`は現在状態の文書化だけを行い、phaseを変更しない。`finish`成功時は`final_target_sha`と`finished_at`を保存し、`reports/FINAL.md`を生成する。二回目の`finish`は同じtargetならidempotentに成功し、別targetなら拒否する。

### validationとcadence

integration validationの未指定を`git diff --check`へ黙って置き換えない。`init`ではvalidation未設定のplanning stateを許し、設定には`hloop validation configure --command <command>`を使えるようにする。最初のimplementationまたはfix taskを開始する前と、`tick`または`pump`がdispatchする前に少なくとも一つのvalidation commandを必須にする。research-only loopは未設定を許可する。最小検査だけを意図する場合も、利用者が`git diff --check`を明示する。

validation logはcommand、開始時刻、終了時刻、exit code、対象HEADをheaderへ書く。commandがstdoutとstderrを出さなくても0 byteにしない。

0.4.0では既存の`review_after_merges`と`gap_after_merges`を維持し、`review_on_batch_close`と`gap_on_batch_close`を追加する。最終`finish`前のcurrent completion target gateは頻度設定にかかわらず必須とする。cadenceの既定変更は、0.4.0の実runでcycle数、待機時間、stale gate数を比較してから決める。

### conductorと状態衛生

`conductor`へ次のissueを追加する。

- `terminal-without-artifact`
- `attempt-base-mismatch`
- `stale-attempt-pane`
- `pending-message-orphaned`
- `cleanup-failed`
- `cleanup-pending-after-merge`
- `phase-completion-mismatch`
- `stale-validation-head`
- `stale-review-head`
- `stale-gap-head`
- `stale-manager-qa-head`
- `unsafe-setup-command`
- `manual-integration-trace`
- `migration-required`

`USER_ACTION_REQUIRED.md`はappend-onlyにせず、現在のactive blockerから再生成する。解決済みblockerはJOURNALへ履歴として残し、利用者向けの現在状態には出さない。mergeやcleanupが復旧したら、対応するtask errorとglobal errorを明示的にcloseする。

## 実装フェーズ

### Phase 0：テスト基盤とstate format

変更内容：

- `skills/herdr-dev-loop/tests/`を追加する。
- stdlibの`unittest`からextensionlessな`hloop`をloadできるfixtureを作る。
- fake `herdr`、`codex`、`claude`を追加し、argvとpane状態を記録できるようにする。
- `state_format_version: 2`、migration command、backup、schemaを実装する。
- 現行selftestはschema、parser、command renderingを5秒以内で検査する役割に限定する。

受入条件：

- 0.3.0 stateのdry-run migration結果が再現可能である。
- migration失敗時に元STATEがbyte単位で変わらない。
- running role、active merge transaction、dirty role worktreeがあるmigrationを拒否する。
- 旧formatのrunning roleを`agent abort`で安全に閉じられる。
- pre-migration artifact identityをhistoryとして保全し、新しいgateへ流用しない。

### Phase 1：provider起動とworktree preflight

変更内容：

- provider commandをargv生成へ分離する。
- CodexへGit common dirとeffortを渡す。
- Claudeへeffortとpermission modeを渡し、permission promptを検出する。
- Git executable identityと`runtime trust-git`を実装する。
- role別setup、unsafe command拒否、disk preflightを実装する。

受入条件：

- linked worktreeのCodex WorkerがManager代行なしでproduct commitとresult commitを作る。
- role processの実argvとSTATEのprovider設定が一致する。
- repoとglobalのCodex、Claude設定ファイルのhashがrun前後で変わらない。
- Worker finalizeがactive attemptのworktree、branch、base、product commitを検証する。
- 同一attemptで`blocked`、`failed`、`partial`になったartifactをManager側finalizeや編集で`done`へ昇格できない。
- 容量不足時にbranch、worktree、pane、taskの`running`遷移を残さず、blockerだけをatomicに記録する。

### Phase 2：attemptとrequeue

変更内容：

- attempt model、fresh requeue、resume、branch archiveを実装する。
- immutable Worker baseによるdiff、harvest、merge gateへ切り替える。
- stale artifact、旧attempt pane、旧session、旧pending messageを新attemptから分離する。

受入条件：

- integrationが先へ進んだ後のfresh requeueが最新HEADから新branchを作る。
- resumeがbase SHAを変えない。
- 別Workerの変更がwrite-scope違反へ混入しない。
- dirty worktreeを`--force-cleanup`なしで破棄しない。
- cleanでも未統合commitを持つ旧branchを削除せず、attempt history用branchへarchiveする。

### Phase 3：merge復旧

変更内容：

- merge transactionとcontent conflict記録を追加する。
- `merge <task-id> --abort`、`--retry`、`--continue`を追加する。
- environment errorとcontent conflictを分離する。
- commit後のtree、diff、HEAD検証を追加する。

受入条件：

- 非競合のstale parallel Workerを最新integrationへsquashできる。
- 競合をabortするとpre-merge状態へ戻る。
- continueは解消済みかつwrite-scope内のindexだけをcommitする。
- Gitのexit codeが偽装されても、HEAD、tree、index、ref、changed pathsの不一致を検出する。

### Phase 4：completion target単位gateと完了操作

変更内容：

- validation、review、gap、Manager QAへ対象HEADを保存する。
- `pause`、`resume`、`finish`を追加する。
- `cleanup resolve`、`handoff record`、`completion target`を追加する。
- `report`を状態遷移から分離する。
- completion mismatchをconductorへ追加する。

受入条件：

- 全gateが同じcurrent completion targetを対象にした場合だけ`finish`できる。
- gate完了後にcompletion targetを変更すると`finish`が失敗し、古いgateがstaleになる。
- `tick`、`pump`、`qa record`、`report`は`ready_to_finish`までしか進めない。
- `done`を書ける公開操作が`finish`だけである。
- `report`単体ではphaseが変わらない。
- `finish`成功後の同一target再実行がidempotentである。

### Phase 5：pane、message、cleanup

変更内容：

- terminal sentinelをwait、pump、conductorへ接続する。
- pane listのbatch利用、active pane IDのclear、provider別cleanup状態を実装する。
- message ID、ACK、drain、dedupe、supersedeを実装する。
- cleanup failure、orphan pending、古いblockerをconductorへ接続する。

受入条件：

- terminal sentinelがあるartifact-less roleで`wait`が即時にnonzeroを返す。
- 別run、別role、旧attemptのterminal sentinelを無視する。
- 完了済みroleが100件あってもpane lookupを行わない。
- running roleが3件の場合、pane inventory取得が一回以下である。
- Manager processを再起動してもpending messageを復元できる。
- `unknown` messageを自動再送せず、Manager判断を要求する。
- 同一attempt内で同じmessage IDの重複送信を抑止する。

### Phase 6：validation、cadence、文書

変更内容：

- validation必須化と非空log headerを実装する。
- batch closeに連動するreviewとgap gateを追加する。
- Skill契約、schema、README、root文書を0.4.0へ合わせる。
- `VERSION`とagent metadataを更新する。

受入条件：

- validation未指定のimplementationまたはfix taskをdispatchできない。
- validation未指定のresearch-only loopはplanningと実行を継続できる。
- 成功無出力のcommandでも監査可能なlogが残る。
- final gateはcadence設定にかかわらずcurrent completion targetを対象にする。
- root文書に旧`.ai/loop`、Codex-only、旧protocol前提の説明が残らない。

## 変更予定ファイル

主な変更範囲は次のとおりである。

- `skills/herdr-dev-loop/scripts/hloop`
- `skills/herdr-dev-loop/tests/test_*.py`
- `skills/herdr-dev-loop/tests/fakes/*`
- `skills/herdr-dev-loop/references/schemas/state.schema.json`
- `skills/herdr-dev-loop/references/schemas/task.schema.json`
- `skills/herdr-dev-loop/references/schemas/result.schema.json`
- `skills/herdr-dev-loop/references/artifact-contract.md`
- `skills/herdr-dev-loop/references/state-machine.md`
- `skills/herdr-dev-loop/references/branch-policy.md`
- `skills/herdr-dev-loop/references/worker-contract.md`
- `skills/herdr-dev-loop/references/manager-loop.md`
- `skills/herdr-dev-loop/references/validation-policy.md`
- `skills/herdr-dev-loop/references/cli-notes.md`
- `skills/herdr-dev-loop/references/profile-examples.md`
- `skills/herdr-dev-loop/SKILL.md`
- `skills/herdr-dev-loop/README.md`
- `skills/herdr-dev-loop/VERSION`
- `skills/herdr-dev-loop/agents/openai.yaml`
- `AGENTS.md`
- `README.md`

schema変更に伴いreview、gap、advice schemaも共通identity fieldの整合確認対象にする。変更が不要ならdiffを作らず、selftestで非変更を確認する。

## テスト戦略

### 常時実行するテスト

`hloop selftest`はschema、frontmatter、parser、prompt、argv renderingの軽量検査を担当する。実Gitを使う状態遷移は新しい`unittest`へ分ける。

```bash
python3 skills/herdr-dev-loop/scripts/hloop selftest --json
python3 -m unittest discover -s skills/herdr-dev-loop/tests -v
python3 /path/to/quick_validate.py skills/herdr-dev-loop
```

実Git fixtureでは`TemporaryDirectory`にprimary checkoutとlinked worktreeを作り、次を検証する。

1. Git common dirを含むCodex argv
2. fresh requeueとresumeのbase不変性
3. stale parallel Workerのscope計算
4. content conflictのabort、retry、continue
5. current completion target単位のfinish gate matrix
6. cleanupとbranch archive

fake CLIとfake Herdrでは次を検証する。

1. CodexとClaudeのTUI、exec別argv
2. model、effort、permission、common dirのoverride優先順位
3. trust prompt、permission prompt、terminal sentinelの分類
4. message送信、unknown結果、ACK、undelivered再送、同一IDの重複送信抑止
5. active roleだけを対象とするpane inventory
6. bytesとinode不足時に起動副作用を残さず、blockerだけを記録する遷移
7. unsafe setup commandの拒否

### 明示的に実行するHerdr E2E

実CodexまたはClaudeを起動するテストは通常のselftestへ入れず、temporary repoと専用namespaceで明示的に実行する。

1. Codex Workerがlinked worktreeでproduct commitとresult commitを作る。
2. Claude Reviewerがpermission待ちへ落ちずartifactを作る。
3. content conflictをabortし、再実行後に安全にmergeできる。
4. disk threshold未満でrole startがbranch、worktree、paneを作らず、blockerだけを記録して拒否される。
5. current completion targetの全gateを閉じた後だけ`finish`できる。

E2Eでは個人プロジェクト、秘密値、社内URL、実pane transcriptをrepoへ保存しない。

## 実装の依存関係と並列化

Phase 0は全作業の前提なので直列に完了させる。Phase 1のprovider argvとdisk/setup、Phase 5のpane/message純粋関数は、Phase 0後に別担当で実装できる。ただし、`scripts/hloop`、parser、state schemaを同時に変更するため、統合は一件ずつ行う。

Phase 2はPhase 0に依存し、Phase 3はPhase 2のattempt identityに依存する。Phase 4はPhase 2とPhase 3のstate遷移が固まってから実装する。Phase 6の文書更新は各Phaseと並行して草稿化できるが、version bumpと公開文面の確定はE2E後に行う。

実装taskの推奨順は次のとおりである。

| Task | 内容 | 依存 |
| --- | --- | --- |
| T001 | unittest基盤、fake CLI、state format 2、migration | なし |
| T002 | Codex、Claude argv、effort、permission、Git common dir | T001 |
| T003 | role別setup、unsafe command guard、disk preflight | T001 |
| T004 | attempt identity、実起動値保存、fresh requeue、resume、branch archive | T001、T002 |
| T005 | immutable baseによるharvest、scope、merge gate | T004 |
| T006 | merge transaction、abort、retry、continue | T005 |
| T007 | completion target単位gate、pause、resume、finish | T005、T006 |
| T008 | terminal sentinel、pane inventory、cleanup state | T004 |
| T009 | message ID、ACK、drain、dedupe | T004、T008 |
| T010 | conductor、active blocker再生成、state hygiene | T004、T006、T007、T008、T009 |
| T011 | validation必須化、log、batch cadence | T007 |
| T012 | references、README、AGENTS、VERSION、release | T002からT011 |

## releaseとinstall

全自動テスト成功後に`VERSION`を`0.4.0`へ更新する。release前に0.3.0 stateのcopyを使ってmigration dry-runとapplyを検証し、元ファイルを変更しないrollback確認も行う。

repo版をCodexとClaudeの各Skill install先へ同期し、次を確認する。

1. repo版、Codex版、Claude版の`diff -qr`が空である。
2. 三つのcopyで`hloop version`が0.4.0を返す。
3. 三つのcopyで`hloop selftest --json`が成功する。
4. Skill validatorがrepo版とインストール版で成功する。
5. temporary repoのHerdr E2Eが成功する。

migration済みSTATEを0.3.0へ戻して再利用しない。rollbackが必要な場合はinstallされたSkillを0.3.0へ戻し、migration前backupからSTATEも戻す。

## 完了条件

次をすべて満たした時点で、このhardeningを完了とする。

- linked worktreeのCodex WorkerがManager代行なしでcommitとfinalizeを完了する。
- 同一attemptの`blocked`、`failed`、`partial` artifactをManager操作で`done`へ昇格できない。
- Workerがintegration branchまたは保護対象refを変更した場合、harvestを拒否する。
- fresh requeueが古いbranchとbaseを新attemptへ持ち込まない。
- merge conflictをGit wrapperや手作業のSTATE編集なしでabort、retry、continueできる。
- current completion targetに対するvalidation、review、gap、Manager QAが揃うまで`finish`できない。
- artifactのないterminal roleを`wait`と`pump`が即時停止として扱う。
- pending messageがrun、role、attempt、message ID、ACKで追跡され、`unknown`を自動再送しない。
- 完了roleへのpane probeがなくなり、cleanup failureとorphan messageをconductorが報告する。
- setupとdisk preflightがrole起動より先に働き、容量不足時に起動副作用を残さずblockerを記録する。
- CodexとClaudeのglobal設定がrun前後で変化しない。
- selftest、unittest、Skill validator、install parity、明示E2Eがすべて成功する。

## 実装時に再確認する事項

次の項目は利用者の仕様判断を必要としない。実装時にlocal CLIとfixtureで確認し、結果を設計記録へ残す。

- Claudeの`auto` permission modeがTUIとexecの両方で期待どおり動くか。
- CodexへGit common dir一つを`--add-dir`すれば、linked worktreeのindexとHLoop lockを両方更新できるか。
- 初期disk thresholdの2 GiBと50,000 inodeが、三並列Workerを持つ一般的なrepoで過剰な拒否を生まないか。
- message ACKをroleの最初の返答で送っても、元の作業指示を遅延させないか。
- batch close gateを有効にしたrunで、per-merge gateよりstale監査と待機時間が減るか。

確認結果が現在の既定案を否定した場合は、危険なfallbackを選ばず、0.4.0のreleaseを止めてこの計画書へ決定理由を追記する。
