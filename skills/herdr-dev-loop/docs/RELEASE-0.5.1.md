# herdr-dev-loop 0.5.1 release note

## Release identity

- Skill version：`0.5.1`
- Release baseline：`master`の`631b360`
- T501 implementation：`96aec39`
- T502 implementation：`9169464`
- Release branch：`fix/herdr-dev-loop-v0.5.1`
- T503 worktree branch：`ai/v0.5.1-t503-release`
- Release target：`master`
- State format：`3`
- Schema revision：`1`
- Minimum Python：`3.11`
- Legacy `.ai/loop`：ignored
- Version tag：作成しない
- Candidate SHA：T503の変更をcommitした後に固定する

T503の成果物が未commitの間、0.5.1のrelease identityは完成していない。candidate SHAを固定する前のworking tree検証はrelease passとして再利用せず、candidate gateは同じ固定SHAに対して実行する。

## 0.5.1の変更範囲

0.5.1は、0.5.0を統合した`631b360`に対するrelease hardeningである。T501はManager consumerの誤用guardと同一UIDの信頼境界を追加し、T502はreport invocationのidempotencyとSQLite接続初期化失敗時のcleanupを追加した。review scope control、first-class follow-up、dispatch freeze、有界review convergenceは0.5.2以降の別作業であり、0.5.1には実装しない。

## 同一UIDのcooperative threat model

HLoopは、同じOS UIDで動くAgentを信頼済みの協調主体として扱う。attempt credentialが防ぐ対象は、reportの誤配送、stale attemptの再利用、role identityの取り違えである。credential fileのmode `0600`は、別のOSユーザーと意図しない公開からtokenを守る。

T501はsubordinate roleへbest-effortのrole contextを渡す。このcontextがある場合、`hloop inbox list|show|ack`と`hloop manager next|sleep`は実行を拒否し、可能ならManager checkoutの`JOURNAL.md`へ拒否を記録する。監査記録に失敗してもcommandの拒否は維持する。ただし、同じUIDのprocessは環境変数を削除または変更できるため、このguardはaccidental misuseを減らすpreflightであり、security boundaryではない。

0.5.1は、次の保証を提供しない。

- 悪意ある同一UID processからのsecret分離
- 暗号学的なManager認証
- 悪意ある同一UID processによるstate改変への耐性
- OS level ACK write isolation
- 強いsandbox boundary

semantic ACKはfinalize、harvest、mergeを閉じるintegration gateである。ACK承認前の最初のfilesystem writeをOS権限で防ぐ機構ではない。OS level ACK write isolationはprovider isolationの後続課題であり、0.5.2のreview scope controlとは分けて扱う。

## Caller-stable invocation idempotency

`hloop agent report`は、論理reportごとにcallerが生成する`--invocation-id`を受け取る。新しい論理reportは新しいvisible ASCIIかつ空白を含まないkeyを使い、応答が不明な同一reportのretryは同じkeyを使う。retryは新しい論理reportより先に実行する。

role-local outboxは、最初に保存した完全なenvelopeをpendingまたはconfirmedの状態にかかわらず再利用する。この再利用には元のevent ID、timestamp、digestが含まれる。同じkeyを異なるsemantic contentへ使うとbrokerへ到達する前にidempotency conflictとなり、異なるkeyを持つ同内容reportは別eventとして保存される。`--invocation-id`と`--event-id`は同時指定できない。

outboxはrun、role、attemptごとに最新64件だけを保持する。invocation idempotencyは元のentryが保持されている間に限られ、eviction後のretryでは新しいeventが作られる場合がある。この契約はbounded client retryであり、任意の期間にわたるgeneral exactly-once deliveryではない。brokerとManager inboxの配信契約は引き続きat-least-onceであり、Managerはevent IDとlease generationで処理を冪等にする。

T502のfault-injection testは、broker commit後かつoutbox confirm前、outbox confirm後の例外、CLI success表示直前の停止を分けて検証する。いずれも同じinvocation IDでretryしたとき、event、inbox、wakeは一件だけになる。別keyを持つ同内容reportは二件になる。

## SQLite接続のcleanup

brokerの`_connect()`は、`sqlite3.connect()`成功後のrow factoryまたはPRAGMA初期化で例外が発生した場合、作成済みconnectionをcloseしてから元の例外を再送出する。close自体が失敗しても初期化時の例外を置き換えない。

回帰testは実際の`sqlite3.Connection` subclassでPRAGMA失敗を発生させ、closeの呼出しを確認した後に参照を解放して`gc.collect()`を実行する。`ResourceWarning`が記録されないことまで確認し、mock connectionの呼出し確認だけで完了とはしない。

## Providerとinstallの検証境界

利用者指示により、今回のlive provider E2EはCodexだけを実施する。Claude live provider E2Eとfresh Claude discoveryは実施せず、成功を主張しない。この限定によって、Claude provider設定、Claude install path、一般的なClaude対応記述を削除するわけではない。

repositoryとCodex版またはClaude Code版の`diff -qr`は、配布fileの静的parityを検証する。各installed directoryの`hloop selftest`は、そのdirectoryにあるPython runtimeの自己検証である。静的parityとPython selftestは、Claude CLIを起動するlive provider E2Eやfresh Claude discoveryの代替証拠ではない。

## Validation commands

すべてのrelease gateは、T503をcommitして固定した同じcandidate SHAで実行する。raw logとprovider result JSONはlocal-only evidenceとして保存し、`.ai`配下の検証logをcommitしない。

### Repository validation

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  skills/herdr-dev-loop/scripts/hloop \
  skills/herdr-dev-loop/tests/run_synthetic_e2e.py \
  skills/herdr-dev-loop/tests/run_provider_e2e.py

WARNING_LOG="$(mktemp)"
set -o pipefail
PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS='always::ResourceWarning' \
  python3 -m unittest discover -s skills/herdr-dev-loop/tests -v \
  2>&1 | tee "$WARNING_LOG"
! rg -n 'ResourceWarning|Exception ignored while finalizing database connection' \
  "$WARNING_LOG"
rm -f "$WARNING_LOG"

python3 skills/herdr-dev-loop/scripts/hloop version --json
python3 skills/herdr-dev-loop/scripts/hloop selftest
QUICK_VALIDATE="$(find "${CODEX_HOME:-$HOME/.codex}" "$HOME/.claude" \
  -iname quick_validate.py 2>/dev/null | head -n1)"
test -n "$QUICK_VALIDATE"
python3 "$QUICK_VALIDATE" skills/herdr-dev-loop
python3 skills/herdr-dev-loop/tests/run_synthetic_e2e.py --json
git diff --check master...HEAD
```

`PYTHONWARNINGS=error`だけでは、SQLite finalizer内の例外が`Exception ignored`として出力されてもtest processが0で終了する場合がある。このため、deterministicな回帰testに加えてfull suiteのstderrを検査する。

T503の未commit差分を検査するときは、candidate gateとは別に次を実行する。

```bash
git diff --check
```

### Codex live provider E2E

```bash
python3 skills/herdr-dev-loop/tests/run_provider_e2e.py \
  --provider codex --json
```

`--allow-skip`は診断には使えるが、0.5.1 release passには使わない。Claude live provider E2Eを実行しないことは`not run`として残し、Codexの結果やstatic install parityからClaudeの成功を推測しない。

### Install parityとPython selftest

```bash
SKILL_DIR="skills/herdr-dev-loop"
CODEX_SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop"
CLAUDE_SKILL_DIR="${CLAUDE_SKILLS_HOME:-$HOME/.claude/skills}/herdr-dev-loop"

diff -qr "$SKILL_DIR" "$CODEX_SKILL_DIR"
diff -qr "$SKILL_DIR" "$CLAUDE_SKILL_DIR"

python3 "$CODEX_SKILL_DIR/scripts/hloop" version --json
python3 "$CLAUDE_SKILL_DIR/scripts/hloop" version --json
python3 "$CODEX_SKILL_DIR/scripts/hloop" selftest
python3 "$CLAUDE_SKILL_DIR/scripts/hloop" selftest
```

fresh Codex sessionではskill discoveryと最初の0.5.1 bannerを確認する。fresh Claude discoveryは今回のgateでは実行せず、`not run`と記録する。

## Rollback

installed copyを置き換える前にCodex版とClaude Code版をtimestamp付きでbackupする。同期または検証が失敗した場合は、次の順でrollbackする。

1. active loopとrole agentを停止し、runtimeの置換中にstateを変更しない。
2. 失敗したinstalled directoryを別名へ退避し、対応するtimestamp付きbackupを元のpathへ戻す。
3. 復元したcopyの`hloop version --json`、`hloop selftest`、`hloop doctor`を実行する。
4. 0.5.1 runtimeで変更したnamespaceを、古いruntimeでmutateしない。再び0.5.1へ戻すか、新しいnamespaceから開始する。
5. candidateを`master`へ統合した後にrelease gateが失敗した場合、`master`をrelease成功とは扱わず、原因修正用branchで修正して新しいcandidate SHAへ全gateを揃え直す。

rollbackはinstalled runtimeを復元する手順であり、0.5.1で生成したoutboxやloop evidenceを古い形式へ変換する手順ではない。

## Evidence table

| Gate | 主張 | Evidence | Result |
|---|---|---|---|
| Release identity | versionとcandidate SHAが一つに固定されている | `VERSION`、`hloop version --json`、candidate SHA | version 0.5.1確認済み、candidate SHA pending |
| T501 implementation | Manager consumer誤用guardと同一UID threat modelが実装されている | `96aec39`、negative tests | implemented、candidate validation pending |
| T502 invocation idempotency | 三つのfault boundaryとsame-content/new-keyを区別する | `9169464`、runtime tests | implemented、candidate validation pending |
| T502 retention | role-local outboxが最新64件へ制限される | `9169464`、outbox retention test | implemented、candidate validation pending |
| T502 SQLite cleanup | 接続初期化失敗時にcloseし、`ResourceWarning`を残さない | `9169464`、real connectionと`gc.collect()`のtest | implemented、candidate validation pending |
| Python compileとunit | current candidateのfull suiteがwarningなしで成功する | compile output、unittest output、warning scan | pending |
| Repository selftest | repository copyのPython selftestが成功する | `selftest ok` | T503 working treeでpassed、fixed-SHA rerun pending |
| Skill validator | skill構造が有効である | `Skill is valid!` | T503 working treeでpassed、fixed-SHA rerun pending |
| T503 diff hygiene | working treeにwhitespace errorがない | `git diff --check` | passed |
| Synthetic E2E | release scenariosが成功する | structured JSON | pending |
| Codex live provider E2E | live Codex sessionで成功する | structured JSON | pending |
| Claude live provider E2E | 今回は実行しない | なし | not run |
| Static Codex install parity | repositoryとCodex版のfileが一致する | `diff -qr` output | pending |
| Static Claude install parity | repositoryとClaude Code版のfileが一致する | `diff -qr` output | pending |
| Codex installed selftest | Codex install pathのPython selftestが成功する | command output | pending |
| Claude installed selftest | Claude install pathのPython selftestが成功する | command output | pending |
| Fresh Codex discovery | fresh Codex sessionが0.5.1を発見する | session evidence | pending |
| Fresh Claude discovery | 今回は実行しない | なし | not run |
| Gap Audit | plan、follow-up、release note、codeが一致する | fixed-SHA artifact | pending |
| Manual review | verified actionable findingが0件である | fixed-SHA review artifact | pending |
| Rollback readiness | backupと復元手順を確認している | backup path、version、selftest、doctor | pending |

`implemented`は、candidate gateがpassedであることを意味しない。`pending`を`passed`へ変更できるのは、同じ固定candidate SHAに対する実行結果を確認した後だけである。
