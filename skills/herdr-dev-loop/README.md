# herdr-dev-loop の使い方

`herdr-dev-loop` は、Herdr 上で複数の Codex または Claude agent に実装、仕様との突合、レビュー、修正を分担させるための Skill です。Manager が `.ai/herdr-dev-loop/loops/<namespace>` を管理し、Worker が実装し、Gap Auditor が元の計画や仕様との差分を調べ、Reviewer が統合後の変更を確認します。

このREADMEは運用の入口です。artifactの形式や状態遷移の厳密な契約は、[Managerのチェックリスト](references/manager-loop.md)、[状態遷移](references/state-machine.md)、[ブランチ方針](references/branch-policy.md)、[Worker契約](references/worker-contract.md)、[Gap Auditor契約](references/gap-contract.md)、[Reviewer契約](references/reviewer-contract.md)、[artifact形式](references/artifact-contract.md)、[validation方針](references/validation-policy.md)、[`/goal`のプロファイル例](references/profile-examples.md)を参照してください。

## 最初に確認すること

このSkillはHerdrの中で使うことが前提です。通常のCodexセッションで `HERDR_ENV=1` が設定されていない場合、paneやagentを起動するところで停止します。

```bash
echo "$HERDR_ENV"
herdr --help
git status --short --branch
```

既存ループを再開するときは、スレッドの記憶ではなくリポジトリ上の `.ai/herdr-dev-loop/loops/<namespace>` を基準にします。namespaceは省略せず、セッション中の全コマンドで同じ値を使います。旧 `.ai/loop` は古い別形式として無視され、自動移行もされません。

```bash
HLOOP="python3 /home/watabegg/.codex/skills/herdr-dev-loop/scripts/hloop --namespace <namespace>"
$HLOOP namespaces
$HLOOP version
$HLOOP doctor
$HLOOP dashboard
$HLOOP conductor --no-fail
```

`hloop` がPATHにないこと自体は問題ではありません。Skillの絶対パスを使えます。

## バージョンとセッションの識別

Skillを使うManagerは、ほかの調査や変更より先に `$HLOOP version` を実行し、最初の進捗メッセージで `herdr-dev-loop <runtime-version> を使用します` と表示します。既存loopでは同時に `loop_skill_version` と `run_id` も表示します。これにより、Codexのセッション履歴だけを見ても、そのセッションがどの版のHLoop契約で動いたかを判別できます。

新しいloopでは、初期化時の版を `STATE.json.skill_version` に固定します。Worker、Reviewer、Gap Auditor、Advisorは起動時の版を各agent状態とartifactの `skill_version` に記録し、最初の進捗にも版とrole IDを出します。`hloop doctor` はインストール済みの版とloopに固定された版が異なる場合に警告し、harvestはrole起動時の版とartifactの版が異なる場合に拒否します。

```text
herdr-dev-loop 0.4.0 / namespace <namespace> を使用します（loop_skill_version: 0.4.0, run_id: 20260712T...-goal）
```

`hloop namespaces` は同居するloopを列挙し、旧 `.ai/loop` が存在する場合は `legacy ignored` と表示します。

## 永続化とworktree初期化経験

既定の `persistence` は `local-only` です。Managerのloop stateはrole worktreeへコピーされ、integration branchへloop artifactをcommitしなくても起動できます。Workerのproduct変更をsquash mergeするときは、namespace配下のartifactをstageから外してproduct commitへ混ぜません。loop artifact自体をbranch履歴へ残すリポジトリだけ `--persistence branch-history` を選びます。0.3.xのstateを再開するときは、`hloop migrate --dry-run`で確認してから`hloop migrate --apply`を実行します。

worktreeごとに必要な依存導入や生成処理は、初期化時に繰り返し指定できます。

```bash
$HLOOP init ... \
  --worker-setup-command 'pnpm install --frozen-lockfile' \
  --worker-setup-command 'pnpm generate' \
  --reviewer-setup-command 'pnpm install --frozen-lockfile'
```

実行結果はnamespace外の `.ai/herdr-dev-loop/experience/worktree-setup.json` に最大200件蓄積されます。保存するのはcommand、成否、return code、所要時間、role/run識別子だけで、stdout/stderrは秘密値混入を避けるため保存しません。成功した経験を次回の既定値にする場合は次を使います。

```bash
$HLOOP experience recommend --command 'pnpm install --frozen-lockfile'
$HLOOP experience show
```

明示的なsetup commandを付けずに次のloopを初期化すると、recommended commandsが引き継がれます。

## Artifactなしで止まったroleの復旧

roleがartifactを書かず終了しても、artifactを捏造せず終了・再投入できます。

```bash
$HLOOP agent abort R002 --reason 'Reviewer exited before artifact'
$HLOOP agent requeue R002 --reason 'Retry with supported model'
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
HLOOP="python3 /home/watabegg/.codex/skills/herdr-dev-loop/scripts/hloop --namespace <namespace>"
$HLOOP version
$HLOOP selftest
$HLOOP doctor
```

`selftest` はSkill内のschemaとartifact契約を検査します。Skill更新後は必ず実行します。`doctor` はHerdr、git、agent CLIなどを確認します。

### 2. ループを初期化する

```bash
$HLOOP --repo <repo> init \
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

### 3. batchとtaskを作る

```bash
$HLOOP --repo <repo> batch start "Initial implementation batch"
$HLOOP --repo <repo> task new "<担当範囲の実装>" \
  --write-allow 'src/foo/**' --write-allow 'tests/foo/**'
```

`write-allow` はWorkerが変更してよい範囲です。並列Workerの範囲が重ならないように分割します。

契約変更にはtaskファイルと`STATE.json`の手編集ではなく、次を使います。`local-only`では変更後のcheckpointは不要です。`branch-history`を選んだ場合だけ、Worker起動前にcheckpointします。

```bash
$HLOOP --repo <repo> task update T001 \
  --add-write-allow 'src/shared/**' \
  --add-acceptance '共有処理の回帰テストが通る'
```

### 4. bounded tickから始める

```bash
$HLOOP --repo <repo> dashboard
$HLOOP --repo <repo> tick --once --max-workers 3 --stop-on-user-decision
```

初回は `tick --once` でWorkerの起動、artifact、pane、worktreeの対応を確認します。安定してからpumpへ進みます。

```bash
$HLOOP --repo <repo> pump \
  --max-transitions 20 --max-workers 3 --stop-on-triage
```

`waiting` で止めたいときは `--stop-on-waiting` を付けます。ReviewerやGap Auditorが統合ブランチを読んでいる間はmergeしません。

## 個別操作とtriage

通常は `tick` または `pump` に任せます。確認や手動介入が必要な場合だけ次を使います。

```bash
$HLOOP worker watch T001
$HLOOP reviewer watch R001
$HLOOP gap watch G001
$HLOOP wait next --harvest

$HLOOP worker message T001 --file prompt.md
$HLOOP reviewer message R001 --file review-followup.md
$HLOOP gap message G001 --file gap-followup.md
```

Workerはproduct変更をcommitした後、成果物を次のように確定します。branch、base SHA、変更ファイル、`run_id`、`merge_ready`はhloopが生成します。

```bash
$HLOOP worker finalize T001 \
  --validation-command 'pnpm test --filter target' \
  --validation-result passed \
  --validation-summary 'targeted test passed'
```

`wait --harvest`、`tick`、`pump`は、Worker成果物がHEADへcommitされるまでreadyと扱いません。Reviewer、Gap Auditor、Advisorは`run_id`と監査対象`head_sha`が一致する場合だけ回収されます。

直接 `herdr pane run` を使わず、hloopのmessageを使います。起動前の確認には `worker start`、`reviewer start`、`gap start` の `--dry-run` を使います。

レビューまたはGap Auditorのartifactは、先にfix-task draftへ変換します。

```bash
$HLOOP triage review R001
$HLOOP triage gap G001
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
├── tasks/ results/ reviews/ gaps/ advice/ triage/
├── qa/              # Manager最終QA
└── reports/         # 最終報告
```

Managerは mission、plan、profile、state、decisionを所有します。Workerは自分のブランチと自分のresult artifactだけを書きます。Reviewerは `reviews/`、Gap Auditorは `gaps/`、Advisorは `advice/` の自分のartifactだけを書きます。

Workerの結果をManagerが書き換えて `done` にすることはできません。`partial`、`blocked`、`failed`、`merge_ready: false` の場合は、原因を記録して再実行またはfix taskへ進みます。

## 停止したときの対処

- **`HERDR_ENV=1` がない**：Herdrの管理下で再実行します。通常のCodexセッションでpane操作を代替しません。
- **dirty fileで止まる**：`git status --short` で対象外の変更を確認し、既存作業を壊さないよう別worktreeや専用ブランチへ移します。
- **role起動前にcheckpointを要求される**：taskやManager-owned loop入力が対象HEADと一致していません。表示された`hloop checkpoint`を実行してから再起動します。失敗時点では新しいworktreeは作られません。
- **paneがない、agentが固まった**：まず `$HLOOP conductor --no-fail` を実行し、表示された状態に対応する `watch`、`message`、`harvest` を使います。
- **レビュー指摘がある**：`triage` 後に、修正、仕様判断、accepted risk、false positiveのいずれかをManagerが決めます。
- **仕様判断が必要**：`DECISIONS.md` に候補と根拠を記録し、ユーザー判断が必要なら `USER_ACTION_REQUIRED.md` に分けて停止します。
- **validationが失敗した**：失敗コマンドと統合ブランチの状態を確認します。rollbackが自明でない場合は勝手に戻しません。

状態が不明なままpaneやgitを手作業で操作すると、`STATE.json` と実際のpane、worktree、artifactの対応が崩れます。先に `dashboard` と `conductor --no-fail` を実行してください。

## 完了前の確認

```bash
$HLOOP --repo <repo> dashboard
$HLOOP --repo <repo> conductor --no-fail
$HLOOP --repo <repo> validate
$HLOOP --repo <repo> report
```

`manager_qa_profile` が `none` 以外なら、最終QAを `qa/FINAL.md` に記録します。最終報告は `reports/FINAL.md` に残します。

## Skillを更新した後のinstall

このリポジトリのSkillを編集したら、検証してからインストール済みコピーを同期します。

```bash
SKILL_DIR="skills/herdr-dev-loop"
INSTALLED_DIR="${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop"

python3 /home/watabegg/.codex/skills/.system/skill-creator/scripts/quick_validate.py "$SKILL_DIR"
python3 "$SKILL_DIR/scripts/hloop" selftest
rsync -a --delete "$SKILL_DIR/" "$INSTALLED_DIR/"
diff -qr "$SKILL_DIR" "$INSTALLED_DIR"
python3 "$INSTALLED_DIR/scripts/hloop" selftest
python3 /home/watabegg/.codex/skills/.system/skill-creator/scripts/quick_validate.py "$INSTALLED_DIR"
```

この同期は既存の同名Skillを上書きします。反映されない場合はCodexを再起動します。

## 公開時の注意

Skillのソース、README、参照文書、スクリプトは公開できます。ただし、実プロジェクトで生成された `.ai/herdr-dev-loop/loops/<namespace>`、pane transcript、秘密値、cookie、社内URL、本番環境の運用情報はこのリポジトリへ持ち込みません。
