# herdr-dev-loop の使い方

`herdr-dev-loop` は、Herdr 上で複数の Codex または Claude agent に実装、仕様との突合、レビュー、修正を分担させるための Skill です。Manager が `.ai/herdr-dev-loop/loops/<namespace>` を管理し、Worker が実装し、Gap Auditor が元の計画や仕様との差分を調べ、Reviewer が統合後の変更を確認します。

このREADMEは0.5.2運用の入口です。設定は[Configuration Contract](references/configuration.md)、報連相とManagerの待機は[Agent Report And Manager Wake Contract](references/report-protocol.md)、要件と判断は[Requirements, Decisions, And Outcomes](references/requirements-decisions-outcomes.md)、厳格なreviewは[Review Swarm And Dual Review Contract](references/review-swarm.md)、移行とinstallは[Migration And Install Parity](references/migration-install.md)を参照してください。artifactの形式や状態遷移の厳密な契約は、[Managerのチェックリスト](references/manager-loop.md)、[状態遷移](references/state-machine.md)、[ブランチ方針](references/branch-policy.md)、[Worker契約](references/worker-contract.md)、[Gap Auditor契約](references/gap-contract.md)、[Reviewer契約](references/reviewer-contract.md)、[artifact形式](references/artifact-contract.md)、[validation方針](references/validation-policy.md)に分けています。release checklistは[`docs/RELEASE-0.5.2.md`](docs/RELEASE-0.5.2.md)です。

## 最初に確認すること

このSkillはHerdrの中で使うことが前提です。通常のCodexセッションで `HERDR_ENV=1` が設定されていない場合、paneやagentを起動するところで停止します。

```bash
echo "$HERDR_ENV"
herdr --help
git status --short --branch
```

既存ループを再開するときは、スレッドの記憶ではなくリポジトリ上の `.ai/herdr-dev-loop/loops/<namespace>` を基準にします。namespaceは省略せず、セッション中の全コマンドで同じ値を使います。旧 `.ai/loop` は古い別形式として無視され、自動移行もされません。

```bash
hloop() {
  python3 "${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop/scripts/hloop" --namespace <namespace> "$@"
}
hloop namespaces
hloop version
hloop doctor
hloop dashboard
hloop conductor --no-fail
```

`hloop` がPATHにないこと自体は問題ではありません。Skillの絶対パスを使えます。

## バージョンとセッションの識別

Skillを使うManagerは、ほかの調査や変更より先に `hloop version` を実行し、最初の進捗メッセージで `herdr-dev-loop <runtime-version> を使用します` と表示します。既存loopでは同時に `loop_skill_version` と `run_id` も表示します。これにより、Codexのセッション履歴だけを見ても、そのセッションがどの版のHLoop契約で動いたかを判別できます。

新しいloopでは、初期化時の版を `STATE.json.skill_version` に固定します。Worker、Reviewer、Gap Auditor、Advisorは起動時の版を各agent状態とartifactの `skill_version` に記録し、最初の進捗にも版とrole IDを出します。`hloop doctor` はインストール済みの版とloopに固定された版が異なる場合に警告し、harvestはrole起動時の版とartifactの版が異なる場合に拒否します。

```text
herdr-dev-loop 0.5.2 / namespace <namespace> を使用します（loop_skill_version: 0.5.2, run_id: 20260716T...-goal）
```

`hloop namespaces` は同居するloopを列挙し、旧 `.ai/loop` が存在する場合は `legacy ignored` と表示します。

## `config.toml` の設定

0.5.2はPython 3.11以上を要求し、標準ライブラリの`tomllib`で設定を読みます。設定ファイルがなくても既定値で動作します。利用中のパスと解決結果は次のコマンドで確認できます。

```bash
hloop config path --json
hloop config validate --json
hloop config explain --repo <repo> --json
```

探索順は`$HLOOP_CONFIG_HOME/config.toml`、`$XDG_CONFIG_HOME/herdr-dev-loop/config.toml`、`~/.config/herdr-dev-loop/config.toml`です。最初に見つかった1ファイルだけを読み、複数ファイルをmergeしません。

`[defaults]`にWorkerとReviewerのprovider、model、effort、同時Worker数、session cleanupを設定できます。`[[scope]]`は既定でcanonicalなrepository rootに一致し、同じrepository内のsubdirectoryから起動しても結果が変わりません。起動directory固有の設定だけ`match = "cwd"`を明示します。設定例は[`examples/config.toml`](examples/config.toml)にあります。

新規loopの`[defaults.review]`は、`cadence = "batch"`、`pre_final_protocol = "codex-review-multi-v2"`、`manual_final_protocol = "codex-review-multi-v2"`、`max_fix_rounds = 2`、`scope_expansion_action = "follow_up"`、`final_required = "complete_zero_verified_actionable_findings"`、`lane_count = "auto"`です。manual-finalは実装済みの`codex-review-multi-v2`だけを受理し、`native`は黙って置換せず拒否します。legacy loopをmigrationしても、保存済みのmerge-count cadenceや既存のfinish semanticsは暗黙に変更されません。

解決順はbuilt-in default、`[defaults]`、浅いscopeから深いscope、task override、role start overrideです。`init`は設定元と解決値を`STATE.json`へsnapshotするため、global configを書き換えても既存loopは暗黙に変わりません。credential、token、任意shell commandは設定ファイルへ書きません。

## 永続化とworktree初期化経験

既定の `persistence` は `local-only` です。Managerのloop stateはrole worktreeへコピーされ、integration branchへloop artifactをcommitしなくても起動できます。Workerのproduct変更をsquash mergeするときは、namespace配下のartifactをstageから外してproduct commitへ混ぜません。loop artifact自体をbranch履歴へ残すリポジトリだけ `--persistence branch-history` を選びます。format 2、format 3 revision 0/1のstateを再開するときは、`hloop migrate --dry-run`で確認してから`hloop migrate --apply`を実行します。0.5.2の新規stateはformat 3、revision 2です。

worktreeごとに必要な依存導入や生成処理は、初期化時に繰り返し指定できます。

```bash
hloop init ... \
  --worker-setup-command 'pnpm install --frozen-lockfile' \
  --worker-setup-command 'pnpm generate' \
  --reviewer-setup-command 'pnpm install --frozen-lockfile'
```

実行結果はnamespace外の `.ai/herdr-dev-loop/experience/worktree-setup.json` に最大200件蓄積されます。保存するのはcommand、成否、return code、所要時間、role/run識別子だけで、stdout/stderrは秘密値混入を避けるため保存しません。成功した経験を次回の既定値にする場合は次を使います。

```bash
hloop experience recommend --command 'pnpm install --frozen-lockfile'
hloop experience show
```

明示的なsetup commandを付けずに次のloopを初期化すると、recommended commandsが引き継がれます。

## 0.5.2のrelease scopeとbounded review

新規loopでは、実装をdispatchする前にrelease scope contractを固定します。scopeはsource fileのdigest、`scope_revision`、`source_snapshot_revision`、plan item、requirement、対応環境、trust boundaryを含む正本です。

```bash
hloop release-scope lock \
  --source MISSION.md --source PLAN.md \
  --plan-item-ref P004d --requirement-ref R001 \
  --scope-ref release-scope-contract
hloop release-scope status --json
```

lock後に意味を変える場合は、`release-scope amend --kind editorial|clarification|scope-change`で理由と参照を記録します。`scope-change`にはuser inputが必要です。source digestの未記録変更はreview readinessを止めます。

lock後のtaskは、作成根拠を`task_origin`として保存します。`planned`はPLANまたはrequirement、`finding`はconfirmedなin-scope finding、`user-amendment`はuser input、`operational`はproduct挙動を変えない調査・validation・artifact整備だけに使います。task作成、triage、pump、conductorは同じauthorization preflightを通るため、CLIの自己申告だけでscope外taskを作成できません。

review収束またはmanual final待ちでは、dispatchを独立してfreezeできます。

```bash
hloop dispatch freeze --reason 'awaiting manual final review' --user-input-id U0001
hloop dispatch status --json
hloop dispatch unfreeze --user-input-id U0002
```

freeze中もvalidation、harvest、merge、follow-up記録、report、pauseは可能ですが、新しいtaskと新しいWorker/Reviewer/Gap Auditor/Advisorの起動は拒否されます。

### Findingの分類とfollow-up

Reviewerのcandidateは、severityだけで処置しません。`fact_status`（confirmed/refuted/insufficient_evidence）、`severity`（P0–P3）、`origin`（introduced/diff-expanded-pre-existing/unrelated-pre-existing/unknown）、`contract_relation`（in_scope/outside_release/ambiguous）、`decision_requirement`（none/spec/user）、`disposition`（fix_now/defer_follow_up/disable_feature/mark_experimental/user_decision/accepted_risk/discard）、`release_effect`（blocking/non_blocking）を独立して確定します。今回のdiffが導入または拡大したin-scope P0/P1はfollow-upへ隠さず、fix、disable、experimental化、またはblocking user decisionにします。

scope外または今回止めないcandidateは、first-class follow-upとして記録できます。issue keyはcomponent、trigger class、product impact、既知のroot causeだけから生成され、review fingerprint、対象SHA、severity、修正案、タイトルの変化では重複しません。

```bash
hloop follow-up add \
  --title '次版で扱う統合改善' \
  --component 'review pipeline' \
  --trigger-class 'scope expanding candidate' \
  --product-impact '次版の運用判断が必要' \
  --impact '現在のrelease acceptanceには影響しない' \
  --affected-path 'skills/herdr-dev-loop/scripts/hloop' \
  --fact-status insufficient_evidence \
  --origin unknown \
  --contract-relation outside_release \
  --decision-requirement none \
  --release-effect non_blocking \
  --disposition defer_follow_up \
  --source-review-fingerprint sha256:<64 hex> \
  --evidence reviews/R001.md \
  --deferred-reason '現在のrelease scope外' \
  --reconsider-condition '次のscope lockで対象化する'
hloop follow-up list --json
hloop follow-up export --output docs/follow-ups.md
```

### Convergence reviewとmanual final

通常のReviewer起動はpre-final convergenceを暗黙に開始しません。統合batchとvalidationが安定したら、固定SHAを準備し、MANIFESTを記録します。

```bash
hloop review readiness --json
hloop review convergence prepare --mode swarm --json
hloop review convergence record --fix-round 0 --json
```

manifestが不完全、またはverified actionable findingが残る場合は、状態を保存してremediationへ戻ります。新規loopのautomatic fix round上限は2回です。上限後やmanual finalの失敗から再開する場合は、user inputを伴う原子的な`hloop review reopen --action ... --user-input-id U0002`だけを使います。これにより、追加task作成、scope amendment、extra round authorization、certification invalidationを一つの状態遷移で記録します。

convergenceが`converged`になったら、freshなmanual final reviewを準備します。

```bash
hloop final-review prepare --mode swarm --json
# 固定SHA、PLAN.json、MANIFEST.json、reportへ手動review結果を記録
hloop final-review record --json
hloop final-review status --json
```

manual finalは、finding数が0という自己申告だけでは合格しません。全lane完了、必要な独立verification、shortfallなし、manifest completeness、PLAN/MANIFESTのidentityとdigest、scope snapshot、固定target SHA、report存在を検証し、verified actionable findingが0件であることを要求します。complete-zeroにならないmanual finalは`finish`を通過できません。

PLAN/MANIFESTの公開schemaは[`schemas/final-review-plan.schema.json`](schemas/final-review-plan.schema.json)と[`schemas/final-review-manifest.schema.json`](schemas/final-review-manifest.schema.json)です。

## Artifactなしで止まったroleの復旧

roleがartifactを書かず終了しても、artifactを捏造せず終了・再投入できます。

```bash
hloop agent abort R002 --reason 'Reviewer exited before artifact'
hloop agent requeue R002 --reason 'Retry with supported model'
```

paneは閉じられ、再投入時は古いworktreeを整理します。product差分が残るworktreeは誤消去を避けて停止し、Managerが本当に破棄すると判断した場合だけ `--force-cleanup` を付けます。

## 用語

### 役割

- **Manager**：現在の親agent。目的、計画、タスク、merge、validation、triage、最終判断を管理します。
- **Worker**：担当範囲のコードを変更し、テスト、QA、`result.md` の作成、commitまで行う実装agentです。
- **Gap Auditor**：元のplanまたはspecと統合ブランチを比較します。一般的なコードレビューではなく、要求漏れや仕様とのずれを調べます。
- **Reviewer**：統合ブランチをレビューします。動作、リスク、書き込み範囲、mergeの安全性、validationとQAの証拠を確認します。
- **Advisor**：仕様判断や修正方針を相談するagentです。明示的に起動した場合だけ動き、mergeやgateを決める権限はありません。

### ループとブランチ

- **integration branch**：Managerが管理する統合用ブランチです。Workerブランチを順にmergeし、そのHEADをReviewerとGap Auditorが確認します。
- **pr-per-task**：WorkerごとにPRまたはプロダクト側のhandoffを行う方式です。hloopは自動mergeせず、`branch_handoff` で停止します。
- **custom**：プロダクト固有の運用です。merge、release、deploy、QAの流れを `PLAN.md` に書いてから使います。
- **worktree**：各agentを分離して動かすgitの作業ディレクトリです。
- **bounded tick**：安全な状態遷移を一度だけ進めます。初回や不安定な状態で使います。
- **pump**：bounded tickを指定回数まで繰り返します。無制限には動きません。

### 設定と証跡

- **MISSION.md**：目的、制約、非目標、完了条件。
- **PLAN.md**：タスク分割、依存関係、ブランチ引き渡し、validation、QA、reviewの計画。
- **PROFILE.md**：ブランチ方式、protocol、agentの種類、review lane、QA profile。
- **STATE.json**：phase、タスク、agent、pane、worktreeの機械可読な状態。
- **DECISIONS.md**：元の仕様だけでは決められない仕様判断。
- **USER_ACTION_REQUIRED.md**：ユーザーの判断がないと進められない事項。
- **result artifact**：Workerの `.ai/herdr-dev-loop/loops/<namespace>/results/<task-id>/result.md`。
- **review artifact**：Reviewerの `.ai/herdr-dev-loop/loops/<namespace>/reviews/<review-id>.md`。
- **gap artifact**：Gap Auditorの `.ai/herdr-dev-loop/loops/<namespace>/gaps/<gap-id>.md`。

### protocol、provider、model

この3つは別の設定です。

- **protocol**：agentの作業契約です。通常は `native` を使います。
- **provider**：agentを起動するCLIです。`codex` または `claude` を指定します。
- **model**：provider内で使うモデルです。`auto` はCLIの既定値です。

たとえば `worker_protocol: native` と `worker_agent_provider: codex` は別々に指定します。`$codex-impl` と `$codex-review-multi-v2` は互換protocolで、通常の既定値ではありません。

### QAの2段階

- **worker_qa_profile**：各Workerが担当タスクについて行うQAです。
- **manager_qa_profile**：統合、Reviewer、Gap Auditorの後にManagerが行う最終QAです。

どちらも `repo-default`、`local`、`preview`、`staging`、`custom`、`none` を選べます。Workerにはローカル確認をさせ、Managerの最終QAを省略する場合は `worker_qa_profile: local` と `manager_qa_profile: none` にします。

### Review mode

Reviewer protocolとreview modeは別の設定です。`single`は1 provider、1 laneです。`swarm`は1 providerで4本から8本、`dual`はCodexとClaudeで1本ずつ、`dual-swarm`はproviderごとに4本から8本のdiscovery laneを使います。

全laneとVerifierは同じhead SHAへ固定されます。CodexとClaudeが同じsemantic fingerprintを報告したfindingは`consensus`、一方だけなら`unique`です。どちらも二次確認へ進みます。P0、P1、仕様判断候補は2回の独立検証を必要とし、予算や独立Verifierが足りないfindingは`insufficient_evidence`として残ります。

```bash
hloop reviewer start --mode swarm --dry-run
hloop reviewer start --mode dual-swarm --dry-run
```

詳細は[Review Swarm And Dual Review Contract](references/review-swarm.md)を参照してください。

## 利用者入力、要件、判断

利用者から新しい指示を受けたら、taskを変える前に入力を保存し、observableな受入条件へ変換します。

```bash
hloop input record --source manager-chat --text '<利用者の指示>'
hloop requirement new \
  --source-input U0001 \
  --acceptance '<観測可能な完了条件>' \
  --priority P1
```

raw inputは自動redactionされたlocal-only artifactで、checkpointやproduct commitには入りません。要件は`not_started`、`in_progress`、`implemented_unverified`、`verified`の順に進みます。`verified`には同じhead SHAのartifactとpassing testまたはQA evidenceが必要です。Agentの自己申告だけでは検証済みにできません。

元のmissionやplanから決まらない選択は`decision new`で記録します。`blocking-user`には影響taskを必ず指定し、そのtaskと未mergeの依存taskだけを止めます。ほかの安全な作業が残る間はloop全体をblockedにしません。利用者回答は`decision respond`、Managerが確認した確定結果は`decision resolve`で分けて記録します。

```bash
hloop decision new \
  --title '<平易な質問>' --class blocking-user \
  --affects T004 \
  --option '<選択肢1>' --option '<選択肢2>' \
  --recommend-option opt_1 --recommend-rationale '<根拠>'
```

## Agent報告とevent-driven Manager

0.5.2のlong-running roleは`ack`、`milestone`、`attention`、`completion`を`hloop agent report`で送ります。各論理reportでは新しい`--invocation-id`を生成し、応答が不明な同一reportのretryでは同じ値を使います。invocation IDはASCII英数字で始め、以降もASCII英数字または`.`、`_`、`:`、`/`、`-`だけを使います。retryは新しい論理reportより先に行います。outboxは最新64件のbounded retentionであり、保持期間外のexactly-onceを保証しません。`--invocation-id`を省略したlegacy pending retryと、`--event-id`による互換retryも維持します。`ack`はmaterial edit前のgoal、scope、acceptance、approachを固定します。`milestone`は通常inbox-only、`attention`はManager対応、`completion`はartifactとSHAの検証開始を知らせます。completion report自体は完了証拠ではありません。

Managerはpaneを巡回する前にdurable inboxを処理します。

```bash
hloop inbox list
hloop manager next
```

対応事項がなければwake leaseを登録します。

```bash
hloop manager sleep --ttl-seconds 3600
```

report brokerはat-least-onceでwakeを記録するため、Managerはevent IDとlease generationで重複を除き、処理後に`hloop inbox ack <event-id>`を実行します。brokerを利用できないreportはrun専用spoolへ退避され、`hloop broker recover`で冪等に再生します。paneの確認は無言終了やcrashのfallbackであり、通常進捗のpollingには使いません。

## 同一UIDの信頼境界

HLoopは、同じOS UIDで動くAgentを信頼済みの協調主体として扱います。attempt-scoped credentialが保証するのは、reportの誤配送、stale attempt、role identityの取り違えを拒否することです。credential fileのmode `0600`は、別のOSユーザーと意図しない公開からtokenを守りますが、同じUIDのprocessから秘密を分離するものではありません。HLoopは、悪意あるsame-UID processに対する秘密分離、暗号学的なManager認証、強いsandbox境界を保証しません。

subordinate roleの起動commandは、`HLOOP_ROLE_CONTEXT=1`、`HLOOP_ROLE_ID`、`HLOOP_ROLE_ATTEMPT_ID`、`HLOOP_MANAGER_REPO`をbest-effort contextとして継承します。このcontextを検出した場合、`hloop inbox list|show|ack`と`hloop manager next|sleep`は実行を拒否し、可能なら`HLOOP_MANAGER_REPO`の既存`JOURNAL.md`へ記録します。監査記録に失敗しても拒否は維持します。ただし、同じUIDのprocessは環境変数を削除または変更できるため、このguardはsubordinate roleによる誤操作を減らすpreflightであり、security boundaryではありません。

semantic ACKはintegration gateです。未承認のattemptはfinalize、harvest、mergeを通過できませんが、ACK前の最初のfilesystem writeをOS権限で防ぐ機構ではありません。

## `/goal` のプロンプト例

### 標準的な統合ブランチ

```text
/goal <機能を実装する>。

Use $herdr-dev-loop.

Loop profile:
- branch_strategy: integration
- worker_protocol: native
- review_protocol: native
- worker_agent_provider: codex
- reviewer_agent_provider: codex
- gap_agent_provider: codex
- worker_qa_profile: repo-default
- manager_qa_profile: none

Requirements:
- Workerは担当範囲を分離し、統合ブランチへ直接変更を加えない。
- ManagerはWorkerの結果を確認してからmergeする。
- Reviewerは検証済みの統合後に起動する。
- Gap Auditorは完了前に元のplan/specと実装の対応を確認する。
```

### WorkerごとにPRを作る

```text
/goal <機能を独立したPR単位に分割して実装する>。

Use $herdr-dev-loop.

Loop profile:
- branch_strategy: pr-per-task
- worker_protocol: native
- review_protocol: native
- worker_qa_profile: repo-default
- manager_qa_profile: preview

Requirements:
- Workerブランチは独立したPRとして公開できる単位にする。
- merge-readyになったら自動mergeせず、PR handoffで止める。
- Preview URLが利用可能になったらManagerが最終QAを記録する。
```

### 必要なときだけAdvisorを使う

```text
/goal <機能を実装する>。レビューやGap Auditorの結果に修正方針の相談が必要な場合だけAdvisorを使う。

Use $herdr-dev-loop.

Loop profile:
- branch_strategy: integration
- advisor_enabled: true
- advisor_mode: dialogue
- advisor_agent_provider: claude
- advisor_agent_model: opus

Advisor policy:
- Advisorを自動起動しない。
- 必要なときだけCodexとClaudeのdialogueを作る。
- 採用した判断はManagerがDECISIONS.mdに記録するかfix taskにする。
- Advisorにgateの終了、task作成、merge、最終決定をさせない。
```

詳細なプロファイル例は [profile-examples.md](references/profile-examples.md) にあります。

## 初回セットアップ

以下では、`<repo>` を対象リポジトリ、`main` をベースブランチとします。

### 1. Skillと環境を検査する

```bash
hloop() {
  python3 "${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop/scripts/hloop" --namespace <namespace> "$@"
}
hloop version
hloop selftest
hloop doctor
```

`selftest` はSkill内のschemaとartifact契約を検査します。Skill更新後は必ず実行します。`doctor` はHerdr、git、agent CLIなどを確認します。

### 2. ループを初期化する

```bash
hloop --repo <repo> init \
  --goal-id <goal-id> \
  --goal "<完了条件を含む具体的な目標>" \
  --base main --create-branch \
  --persistence local-only \
  --worktree-root ../wt/<goal-id> \
  --branch-strategy integration --merge-mode squash \
  --worker-protocol native --review-protocol native \
  --worker-qa-profile repo-default --manager-qa-profile none \
  --worker-runner tui --gap-runner tui --reviewer-runner tui \
  --max-workers 3 --max-reviewers 1 --max-gap-auditors 1
```

`--create-branch` はintegration branchの準備も行います。未commitの変更がある場合は、先に状態を確認してください。ループ以外のdirty fileがあるとmutating commandが停止することがあります。

`--worktree-root` を指定すると、Worker、Reviewer、Gap Auditor、Advisorのworktreeがすべてその配下へ作られます。相対パスは対象リポジトリを基準に解決されます。`init --force`で再初期化した場合、旧loopは`.ai/herdr-dev-loop/archive/<namespace>/`へ退避され、新しい`run_id`が発行されます。

### 3. Quick Start: 要件からbatchとtaskを作る

```bash
hloop --repo <repo> input record --source manager-chat --text '<利用者の指示>'
hloop --repo <repo> requirement new \
  --source-input U0001 \
  --acceptance '<観測可能な完了条件>' \
  --priority P1
hloop --repo <repo> release-scope lock \
  --source MISSION.md --source PLAN.md \
  --requirement-ref REQ-001 \
  --scope-ref release-scope-contract
hloop --repo <repo> batch start "Initial implementation batch"
hloop --repo <repo> task new "<担当範囲の実装>" \
  --requirement-ref REQ-001 \
  --write-allow 'src/foo/**' --write-allow 'tests/foo/**'
```

`write-allow` はWorkerが変更してよい範囲です。並列Workerの範囲が重ならないように分割します。

契約変更にはtaskファイルと`STATE.json`の手編集ではなく、次を使います。`local-only`では変更後のcheckpointは不要です。`branch-history`を選んだ場合だけ、Worker起動前にcheckpointします。

```bash
hloop --repo <repo> task update T001 \
  --add-write-allow 'src/shared/**' \
  --add-acceptance '共有処理の回帰テストが通る'
```

実行中Workerの契約を変更すると、新しいtask digestへreport identityが再束縛され、semantic ACK barrierが自動で再設定されます。続けて`hloop worker message`で変更内容を伝え、Workerが新digestのcorrected ACKを送り、Managerが承認するまでfinalize、harvest、mergeは進みません。

### 4. bounded tickから始める

```bash
hloop --repo <repo> dashboard
hloop --repo <repo> tick --once --max-workers 3 --stop-on-user-decision
```

初回は `tick --once` でWorkerの起動、artifact、pane、worktreeの対応を確認します。安定してからpumpへ進みます。

```bash
hloop --repo <repo> pump \
  --max-transitions 20 --max-workers 3 --stop-on-triage
```

`waiting` で止めたいときは `--stop-on-waiting` を付けます。ReviewerやGap Auditorが統合ブランチを読んでいる間はmergeしません。

## 個別操作とtriage

通常は `tick` または `pump` に任せます。確認や手動介入が必要な場合だけ次を使います。

```bash
hloop worker watch T001
hloop reviewer watch R001
hloop gap watch G001
hloop wait next --harvest

hloop worker message T001 --file prompt.md
hloop reviewer message R001 --file review-followup.md
hloop gap message G001 --file gap-followup.md
```

Workerはproduct変更をcommitした後、成果物を次のように確定します。branch、base SHA、変更ファイル、`run_id`、`merge_ready`はhloopが生成します。

```bash
hloop worker finalize T001 \
  --validation-command 'pnpm test --filter target' \
  --validation-result passed \
  --validation-summary 'targeted test passed'
```

`--validation-result`は`passed`、`failed`、`blocked`のいずれかです。`--status done`では全件`passed`でなければならず、失敗またはblockedを含む成果物はharvest時にmerge-readyと認められず、mergeでも再検査されます。harvest済みartifactの正本はManager側のnamespaced result pathです。

`wait --harvest`、`tick`、`pump`は、Worker成果物がHEADへcommitされるまでreadyと扱いません。Reviewer、Gap Auditor、Advisorは`run_id`と監査対象`head_sha`が一致する場合だけ回収されます。

直接 `herdr pane run` を使わず、hloopのmessageを使います。起動前の確認には `worker start`、`reviewer start`、`gap start` の `--dry-run` を使います。

レビューまたはGap Auditorのartifactは、先にfix-task draftへ変換します。

```bash
hloop triage review R001
hloop triage gap G001
```

Managerがdraftを確認した後、必要なものだけ `--create-tasks` でqueued taskにします。Advisorは `request`、`start`、`harvest`、`close` を明示的に実行します。`tick` と `pump` はAdvisorを自動起動しません。

## `.ai/herdr-dev-loop/loops/<namespace>` の見方

```text
.ai/herdr-dev-loop/loops/<namespace>/
├── MISSION.md       # 目的と完了条件
├── PLAN.md          # タスク、QA、reviewの計画
├── PROFILE.md       # ループ設定
├── STATE.json       # 現在の状態
├── DECISIONS.md     # 仕様判断
├── USER_ACTION_REQUIRED.md
├── inputs/          # redacted raw input。local-only
├── inbox/ broker/ broker-spool/ # local-only
├── tasks/ results/ reviews/ gaps/ advice/ triage/
├── release-scope/ follow-ups/   # scope amendments and first-class follow-ups
├── reviews/convergence/          # fixed-target PLAN/MANIFEST
├── reviews/final/                # manual-final PLAN/MANIFEST/REPORT
├── qa/              # Manager最終QA
└── reports/         # 最終報告
```

Managerは mission、plan、profile、state、decisionを所有します。Workerは自分のブランチと自分のresult artifactだけを書きます。Reviewerは `reviews/`、Gap Auditorは `gaps/`、Advisorは `advice/` の自分のartifactだけを書きます。

Accepted requirement、progress、machine-readable decisionは`STATE.json.requirements`と`STATE.json.decisions`に保存されます。`DECISIONS.md`は人が読む判断台帳です。0.5.2 CLIは`requirements/`、`progress/`、`context/`、`decisions/`の個別directoryを生成しません。release scopeとfollow-upは、それぞれ`STATE.json.release_scope`と`STATE.json.follow_ups`、およびnamespaced artifactへ保存されます。

Workerの結果をManagerが書き換えて `done` にすることはできません。`partial`、`blocked`、`failed`、`merge_ready: false` の場合は、原因を記録して再実行またはfix taskへ進みます。

## 停止したときの対処

- **`HERDR_ENV=1` がない**：Herdrの管理下で再実行します。通常のCodexセッションでpane操作を代替しません。
- **dirty fileで止まる**：`git status --short` で対象外の変更を確認し、既存作業を壊さないよう別worktreeや専用ブランチへ移します。
- **role起動前にcheckpointを要求される**：taskやManager-owned loop入力が対象HEADと一致していません。表示された`hloop checkpoint`を実行してから再起動します。失敗時点では新しいworktreeは作られません。
- **paneがない、agentが固まった**：まず `hloop conductor --no-fail` を実行し、表示された状態に対応する `watch`、`message`、`harvest` を使います。
- **レビュー指摘がある**：`triage` 後に、修正、仕様判断、accepted risk、false positiveのいずれかをManagerが決めます。
- **仕様判断が必要**：`DECISIONS.md` に候補と根拠を記録し、ユーザー判断が必要なら `USER_ACTION_REQUIRED.md` に分けて停止します。
- **validationが失敗した**：失敗コマンドと統合ブランチの状態を確認します。rollbackが自明でない場合は勝手に戻しません。

状態が不明なままpaneやgitを手作業で操作すると、`STATE.json` と実際のpane、worktree、artifactの対応が崩れます。先に `dashboard` と `conductor --no-fail` を実行してください。

## 完了前の確認

```bash
hloop --repo <repo> dashboard
hloop --repo <repo> conductor --no-fail
hloop --repo <repo> validate
hloop --repo <repo> final-gates arm
hloop --repo <repo> finish
hloop --repo <repo> report
```

`manager_qa_profile` が `none` 以外なら、最終QAを `qa/FINAL.md` に記録します。`final-gates arm`は全task merge、batch close、review triage完了、fix-task draftなしを同一SHAで確認します。新taskを作るとarmは解除されます。`finish`だけがcurrent-headのvalidation、review、gap、Manager QA、cleanup、final gateを再確認してdoneへ進めます。

## Skillを更新した後のinstall

このリポジトリのSkillを編集したら、検証してからCodexとClaude Codeの両コピーを同期します。既存directoryをtimestamp付きでbackupしてから`rsync --delete`を実行します。

```bash
SKILL_DIR="skills/herdr-dev-loop"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CODEX_SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop"
CLAUDE_SKILL_DIR="${CLAUDE_SKILLS_HOME:-$HOME/.claude/skills}/herdr-dev-loop"

QUICK_VALIDATE="$(find "${CODEX_HOME:-$HOME/.codex}" "$HOME/.claude" -iname quick_validate.py 2>/dev/null | head -n1)"
test -n "$QUICK_VALIDATE" || { echo "quick_validate.py not found under the Codex or Claude skill-creator install" >&2; exit 1; }
python3 "$QUICK_VALIDATE" "$SKILL_DIR"
python3 "$SKILL_DIR/scripts/hloop" selftest
test ! -e "$CODEX_SKILL_DIR" || cp -a "$CODEX_SKILL_DIR" "${CODEX_SKILL_DIR}.backup-${STAMP}"
test ! -e "$CLAUDE_SKILL_DIR" || cp -a "$CLAUDE_SKILL_DIR" "${CLAUDE_SKILL_DIR}.backup-${STAMP}"
rsync -a --delete "$SKILL_DIR/" "$CODEX_SKILL_DIR/"
rsync -a --delete "$SKILL_DIR/" "$CLAUDE_SKILL_DIR/"
diff -qr "$SKILL_DIR" "$CODEX_SKILL_DIR"
diff -qr "$SKILL_DIR" "$CLAUDE_SKILL_DIR"
python3 "$CODEX_SKILL_DIR/scripts/hloop" version --json
python3 "$CLAUDE_SKILL_DIR/scripts/hloop" version --json
python3 "$CODEX_SKILL_DIR/scripts/hloop" selftest
python3 "$CLAUDE_SKILL_DIR/scripts/hloop" selftest
```

通常の配布では、同期後に新しいCodexとClaude Code sessionでskill discoveryと最初の0.5.2表示を確認します。fresh provider discoveryやprovider E2Eを実施していない場合は成功扱いしません。rollbackではactive loopを止め、失敗したinstalled directoryを退避して対応するbackupを戻します。移行済みnamespaceを古いruntimeでmutateしません。詳しい手順は[Migration And Install Parity](references/migration-install.md)、release gateは[`docs/RELEASE-0.5.2.md`](docs/RELEASE-0.5.2.md)にあります。なお、今回のrepository taskではinstalled Codex/Claude copyを同期しません。

## 公開時の注意

Skillのソース、README、参照文書、スクリプトは公開できます。ただし、実プロジェクトで生成された `.ai/herdr-dev-loop/loops/<namespace>`、pane transcript、秘密値、cookie、社内URL、本番環境の運用情報はこのリポジトリへ持ち込みません。
