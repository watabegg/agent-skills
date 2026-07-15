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
- Candidate SHA：配布対象HEADから取得し、tracked noteではなくSHA keyed local manifestへ固定する

candidate SHAを固定する前のworking tree検証はrelease passとして再利用しない。gate開始時にcleanなHEADを固定し、全結果、installed source SHA、配布時のHEADを同じSHAへ揃える。tracked noteへSHAや結果を書き戻してcandidateを変えない。

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

`hloop agent report`は、論理reportごとにcallerが生成する`--invocation-id`を受け取る。新しい論理reportのkeyはASCII英数字で始め、以降もASCII英数字または`.`、`_`、`:`、`/`、`-`だけを使う。応答が不明な同一reportのretryは同じkeyを使い、retryは新しい論理reportより先に実行する。このgrammarは、生成promptに表示したshell commandへkeyを安全に埋め込める範囲へ限定している。

role-local outboxは、最初に保存した完全なenvelopeをpendingまたはconfirmedの状態にかかわらず再利用する。この再利用には元のevent ID、timestamp、digestが含まれる。同じkeyを異なるsemantic contentへ使うとbrokerへ到達する前にidempotency conflictとなり、異なるkeyを持つ同内容reportは別eventとして保存される。`--invocation-id`と`--event-id`は同時指定できない。

0.5.0が保存した旧visible-ASCII keyは、pendingまたはconfirmedのentryがoutboxに残っており、同じkeyと同じsemantic contentでretryされた場合に限って再利用できる。旧keyを含むentryが存在しても、新しいshell-safe keyのreportは別entryとして作成できる。現在grammar外のkeyで新規entryを作ることや、retentionから消えた旧keyを再作成することは拒否する。

outboxはrun、role、attemptごとに最新64件だけを保持する。invocation idempotencyは元のentryが保持されている間に限られ、eviction後のretryでは新しいeventが作られる場合がある。この契約はbounded client retryであり、任意の期間にわたるgeneral exactly-once deliveryではない。brokerとManager inboxの配信契約は引き続きat-least-onceであり、Managerはevent IDとlease generationで処理を冪等にする。

T502のfault-injection testは、broker commit後かつoutbox confirm前、outbox confirm後の例外、CLI success表示直前の停止を分けて検証する。いずれも同じinvocation IDでretryしたとき、event、inbox、wakeは一件だけになる。別keyを持つ同内容reportは二件になる。

## SQLite接続のcleanup

brokerの`_connect()`は、`sqlite3.connect()`成功後のrow factoryまたはPRAGMA初期化で例外が発生した場合、作成済みconnectionをcloseしてから元の例外を再送出する。close自体が失敗しても初期化時の例外を置き換えない。

回帰testは実際の`sqlite3.Connection` subclassでPRAGMA失敗を発生させ、closeの呼出しを確認した後に参照を解放して`gc.collect()`を実行する。`ResourceWarning`が記録されないことまで確認し、mock connectionの呼出し確認だけで完了とはしない。

## Providerとinstallの検証境界

利用者指示により、今回はCodexのlive availability/read-only marker probeだけを実施する。このprobeは一時Git repositoryからCodex CLIを起動し、固定markerの応答とGit不変性を確認する。HLoop role起動、Herdr連携、rendered role prompt、`agent report --invocation-id`経路のlive E2Eではない。Claude live provider E2Eとfresh Claude discoveryは実施せず、成功を主張しない。この限定によって、Claude provider設定、Claude install path、一般的なClaude対応記述を削除するわけではない。

repositoryとCodex版またはClaude Code版の`diff -qr`は、配布fileの静的parityを検証する。各installed directoryの`hloop selftest`は、そのdirectoryにあるPython runtimeの自己検証である。静的parityとPython selftestは、Claude CLIを起動するlive provider E2Eやfresh Claude discoveryの代替証拠ではない。

## Validation commands

すべてのrelease gateは、T503をcommitして固定した同じcandidate SHAで実行する。raw log、probe result JSON、最終manifestは、`$(git rev-parse --git-common-dir)/herdr-dev-loop-release-evidence/0.5.1/<candidate-sha>/`へlocal-only evidenceとして保存する。tracked fileやworktree内の`.ai`へ検証logを書かない。

### Repository validation

```bash
set -euo pipefail
: "${EXPECTED_BASE_SHA:?set the externally reviewed full baseline SHA}"
: "${EXPECTED_CANDIDATE_SHA:?set the externally reviewed full candidate SHA}"
case "$EXPECTED_BASE_SHA" in
  *[!0-9a-f]*|'') exit 2 ;;
esac
case "$EXPECTED_CANDIDATE_SHA" in
  *[!0-9a-f]*|'') exit 2 ;;
esac
test "${#EXPECTED_BASE_SHA}" -eq 40
test "${#EXPECTED_CANDIDATE_SHA}" -eq 40
require_candidate_identity() {
  ACTUAL_CANDIDATE_SHA="$(git rev-parse HEAD)"
  test "$(git rev-parse --verify "${EXPECTED_BASE_SHA}^{commit}")" = "$EXPECTED_BASE_SHA"
  test "$(git rev-parse --verify "${EXPECTED_CANDIDATE_SHA}^{commit}")" = "$EXPECTED_CANDIDATE_SHA"
  test "$ACTUAL_CANDIDATE_SHA" = "$EXPECTED_CANDIDATE_SHA"
  test "$(git merge-base "$EXPECTED_BASE_SHA" "$ACTUAL_CANDIDATE_SHA")" = "$EXPECTED_BASE_SHA"
}
require_candidate_identity
EVIDENCE_PARENT="$(git rev-parse --git-common-dir)/herdr-dev-loop-release-evidence/0.5.1"
EVIDENCE_ROOT="$EVIDENCE_PARENT/$EXPECTED_CANDIDATE_SHA"
test "$(basename "$EVIDENCE_ROOT")" = "$ACTUAL_CANDIDATE_SHA"
mkdir -p "$EVIDENCE_PARENT"
test ! -L "$EVIDENCE_ROOT"
test ! -e "$EVIDENCE_ROOT"
mkdir -m 700 "$EVIDENCE_ROOT"
WARNING_LOG="$EVIDENCE_ROOT/unit.log"
umask 077
set -o noclobber
PYCACHE_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$PYCACHE_DIR"
}
trap cleanup EXIT
require_clean_worktree() {
  local worktree_status
  worktree_status="$(git status --porcelain --untracked-files=all)"
  test -z "$worktree_status"
}
require_clean_worktree

PYTHONPYCACHEPREFIX="$PYCACHE_DIR" python3 -m py_compile \
  skills/herdr-dev-loop/scripts/hloop \
  skills/herdr-dev-loop/tests/run_synthetic_e2e.py \
  skills/herdr-dev-loop/tests/run_provider_e2e.py \
  >"$EVIDENCE_ROOT/compile.stdout.log" \
  2>"$EVIDENCE_ROOT/compile.stderr.log"
printf 'passed\n' >"$EVIDENCE_ROOT/compile.status"

PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS='always::ResourceWarning' \
  python3 -m unittest discover -s skills/herdr-dev-loop/tests -v \
  >"$WARNING_LOG" 2>&1
if rg -n 'ResourceWarning|Exception ignored while finalizing database connection' \
  "$WARNING_LOG"; then
  warning_scan_status=0
else
  warning_scan_status=$?
fi
case "$warning_scan_status" in
  0) exit 1 ;;
  1) ;;
  *) exit "$warning_scan_status" ;;
esac

PYTHONDONTWRITEBYTECODE=1 python3 skills/herdr-dev-loop/scripts/hloop version --json \
  >"$EVIDENCE_ROOT/version.json" \
  2>"$EVIDENCE_ROOT/version.stderr.log"
PYTHONDONTWRITEBYTECODE=1 python3 skills/herdr-dev-loop/scripts/hloop selftest \
  >"$EVIDENCE_ROOT/selftest.log" \
  2>"$EVIDENCE_ROOT/selftest.stderr.log"
QUICK_VALIDATE="$(find "${CODEX_HOME:-$HOME/.codex}" "$HOME/.claude" \
  -iname quick_validate.py -print -quit 2>/dev/null || true)"
test -n "$QUICK_VALIDATE"
PYTHONDONTWRITEBYTECODE=1 python3 "$QUICK_VALIDATE" skills/herdr-dev-loop \
  >"$EVIDENCE_ROOT/quick-validate.log" \
  2>"$EVIDENCE_ROOT/quick-validate.stderr.log"
PYTHONDONTWRITEBYTECODE=1 \
  python3 skills/herdr-dev-loop/tests/run_synthetic_e2e.py \
  --output "$EVIDENCE_ROOT/synthetic.json" \
  >"$EVIDENCE_ROOT/synthetic.stdout.log" \
  2>"$EVIDENCE_ROOT/synthetic.stderr.log"
git diff --check "$EXPECTED_BASE_SHA...$EXPECTED_CANDIDATE_SHA" \
  >"$EVIDENCE_ROOT/diff-check.log" \
  2>"$EVIDENCE_ROOT/diff-check.stderr.log"
require_candidate_identity
require_clean_worktree
printf 'passed\n' >"$EVIDENCE_ROOT/repository-gates.status"
```

`EXPECTED_BASE_SHA`と`EXPECTED_CANDIDATE_SHA`は、このblockを実行するcheckoutから導出せず、review対象として外部に固定したfull SHAを渡す。blockは開始時と終了時のHEAD、commit object、merge-base、固定SHA間のdiff、evidence directory名をその値と照合し、別のcleanなcheckoutや途中で移動したrefの結果をcandidate evidenceとして受理しない。candidate evidence directoryは既存file、directory、symlinkを受理しないone-shot directoryとして作成し、`noclobber`で既存または差し替えられた`unit.log`を上書きしない。再実行する場合は既存証跡を退避してから、空のpathに対して明示的に開始する。

`PYTHONWARNINGS=error`だけでは、SQLite finalizer内の例外が`Exception ignored`として出力されてもtest processが0で終了する場合がある。このため、deterministicな回帰testに加えてfull suiteのstderrを検査する。

T503の未commit差分を検査するときは、candidate gateとは別に次を実行する。

```bash
git diff --check
```

### Codex live availability/read-only marker probe

```bash
set -euo pipefail
: "${EXPECTED_BASE_SHA:?set the externally reviewed full baseline SHA}"
: "${EXPECTED_CANDIDATE_SHA:?set the externally reviewed full candidate SHA}"
test "${#EXPECTED_BASE_SHA}" -eq 40
test "${#EXPECTED_CANDIDATE_SHA}" -eq 40
test "$(git rev-parse --verify "${EXPECTED_BASE_SHA}^{commit}")" = "$EXPECTED_BASE_SHA"
test "$(git rev-parse --verify "${EXPECTED_CANDIDATE_SHA}^{commit}")" = "$EXPECTED_CANDIDATE_SHA"
test "$(git rev-parse HEAD)" = "$EXPECTED_CANDIDATE_SHA"
test "$(git merge-base "$EXPECTED_BASE_SHA" "$EXPECTED_CANDIDATE_SHA")" = "$EXPECTED_BASE_SHA"
EVIDENCE_ROOT="$(git rev-parse --git-common-dir)/herdr-dev-loop-release-evidence/0.5.1/$EXPECTED_CANDIDATE_SHA"
test -d "$EVIDENCE_ROOT"
test ! -L "$EVIDENCE_ROOT"
test ! -e "$EVIDENCE_ROOT/codex-marker-probe.json"
umask 077
set -o noclobber
PYTHONDONTWRITEBYTECODE=1 python3 \
  skills/herdr-dev-loop/tests/run_provider_e2e.py \
  --provider codex \
  --output "$EVIDENCE_ROOT/codex-marker-probe.json" \
  >"$EVIDENCE_ROOT/codex-marker-probe.stdout.log" \
  2>"$EVIDENCE_ROOT/codex-marker-probe.stderr.log"
test "$(git rev-parse HEAD)" = "$EXPECTED_CANDIDATE_SHA"
test "$(git merge-base "$EXPECTED_BASE_SHA" "$EXPECTED_CANDIDATE_SHA")" = "$EXPECTED_BASE_SHA"
printf 'passed\n' >"$EVIDENCE_ROOT/provider-gates.status"
```

`--allow-skip`は診断には使えるが、0.5.1 release passには使わない。このresultをHLoop、Herdr、role prompt、report経路のlive E2Eへ読み替えない。runnerのlegacy filenameには`e2e`が残るが、result JSONの`runner`、`probe_kind`、`coverage`、`excluded_coverage`はmarker probeの限定された証明範囲を明示する。Claude live provider E2Eを実行しないことは`not run`として残し、Codexの結果やstatic install parityからClaudeの成功を推測しない。

### Install parityとPython selftest

```bash
set -euo pipefail
: "${EXPECTED_BASE_SHA:?set the externally reviewed full baseline SHA}"
: "${EXPECTED_CANDIDATE_SHA:?set the externally reviewed full candidate SHA}"
test "${#EXPECTED_BASE_SHA}" -eq 40
test "${#EXPECTED_CANDIDATE_SHA}" -eq 40
test "$(git rev-parse --verify "${EXPECTED_BASE_SHA}^{commit}")" = "$EXPECTED_BASE_SHA"
test "$(git rev-parse --verify "${EXPECTED_CANDIDATE_SHA}^{commit}")" = "$EXPECTED_CANDIDATE_SHA"
test "$(git rev-parse HEAD)" = "$EXPECTED_CANDIDATE_SHA"
test "$(git merge-base "$EXPECTED_BASE_SHA" "$EXPECTED_CANDIDATE_SHA")" = "$EXPECTED_BASE_SHA"
SKILL_DIR="skills/herdr-dev-loop"
CODEX_SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/herdr-dev-loop"
CLAUDE_SKILL_DIR="${CLAUDE_SKILLS_HOME:-$HOME/.claude/skills}/herdr-dev-loop"
EVIDENCE_ROOT="$(git rev-parse --git-common-dir)/herdr-dev-loop-release-evidence/0.5.1/$EXPECTED_CANDIDATE_SHA"
test -d "$EVIDENCE_ROOT"
test ! -L "$EVIDENCE_ROOT"
test "$(git rev-parse HEAD)" = "$EXPECTED_CANDIDATE_SHA"
umask 077
set -o noclobber

diff -qr "$SKILL_DIR" "$CODEX_SKILL_DIR" \
  >"$EVIDENCE_ROOT/codex-install-diff.log" \
  2>"$EVIDENCE_ROOT/codex-install-diff.stderr.log"
diff -qr "$SKILL_DIR" "$CLAUDE_SKILL_DIR" \
  >"$EVIDENCE_ROOT/claude-install-diff.log" \
  2>"$EVIDENCE_ROOT/claude-install-diff.stderr.log"

PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_SKILL_DIR/scripts/hloop" version --json \
  >"$EVIDENCE_ROOT/codex-installed-version.json" \
  2>"$EVIDENCE_ROOT/codex-installed-version.stderr.log"
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_SKILL_DIR/scripts/hloop" version --json \
  >"$EVIDENCE_ROOT/claude-installed-version.json" \
  2>"$EVIDENCE_ROOT/claude-installed-version.stderr.log"
PYTHONDONTWRITEBYTECODE=1 python3 "$CODEX_SKILL_DIR/scripts/hloop" selftest \
  >"$EVIDENCE_ROOT/codex-installed-selftest.log" \
  2>"$EVIDENCE_ROOT/codex-installed-selftest.stderr.log"
PYTHONDONTWRITEBYTECODE=1 python3 "$CLAUDE_SKILL_DIR/scripts/hloop" selftest \
  >"$EVIDENCE_ROOT/claude-installed-selftest.log" \
  2>"$EVIDENCE_ROOT/claude-installed-selftest.stderr.log"
test "$(git rev-parse HEAD)" = "$EXPECTED_CANDIDATE_SHA"
test "$(git merge-base "$EXPECTED_BASE_SHA" "$EXPECTED_CANDIDATE_SHA")" = "$EXPECTED_BASE_SHA"
python3 - "$EXPECTED_CANDIDATE_SHA" "$CODEX_SKILL_DIR" "$CLAUDE_SKILL_DIR" \
  >"$EVIDENCE_ROOT/installed-source.json" \
  2>"$EVIDENCE_ROOT/installed-source.stderr.log" <<'PY'
import json
import sys

print(json.dumps({
    "status": "passed",
    "source_sha": sys.argv[1],
    "codex_skill_dir": sys.argv[2],
    "claude_skill_dir": sys.argv[3],
}, sort_keys=True))
PY
printf 'passed\n' >"$EVIDENCE_ROOT/install-gates.status"
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

## Evidence contract

次の表はgateの契約templateであり、実行結果をtracked fileへ書き戻さない。結果を書き戻してcommitするとcandidate SHAが変わり、元のSHAで取得した証跡を流用できなくなるためである。

| Gate | 主張 | Evidence | Required result |
|---|---|---|---|
| Release identity | versionとcandidate SHAが一つに固定されている | `VERSION`、`hloop version --json`、candidate SHA | `passed` |
| T501 implementation | Manager consumer誤用guardと同一UID threat modelが実装されている | commits、negative tests | `passed` |
| T502 invocation idempotency | 三つのfault boundaryとsame-content/new-keyを区別する | commits、runtime tests | `passed` |
| T502 retention | role-local outboxが既定で最新64件へ制限される | default retention test | `passed` |
| T502 SQLite cleanup | 接続初期化失敗時にcloseし、元例外とResourceWarning不在を確認する | real connection、BaseException、`gc.collect()` tests | `passed` |
| Python compileとunit | current candidateのfull suiteがwarningなしで成功する | compile output、unittest output、warning scan | `passed` |
| Repository selftest | repository copyのPython selftestが成功する | command output | `passed` |
| Skill validator | skill構造が有効である | quick validation output | `passed` |
| Diff hygiene | validation前後にworktreeがcleanである | `git diff --check`、`git status --porcelain` | `passed` |
| Synthetic E2E | release scenariosが成功する | structured JSON | `passed` |
| Codex marker probe | live Codex CLIが固定markerを返しGitを変更しない | structured JSON | `passed` |
| Claude live provider E2E | 今回は実行しない | manifestの明示値 | `not_run` |
| Static Codex install parity | repositoryとCodex版のfileが一致する | `diff -qr` output | `passed` |
| Static Claude install parity | repositoryとClaude Code版のfileが一致する | `diff -qr` output | `passed` |
| Codex installed selftest | Codex install pathのPython selftestが成功する | command output | `passed` |
| Claude installed selftest | Claude install pathのPython selftestが成功する | command output | `passed` |
| Fresh Codex discovery | fresh Codex sessionが0.5.1を発見する | session evidence | `passed` |
| Fresh Claude discovery | 今回は実行しない | manifestの明示値 | `not_run` |
| Gap Audit | plan、follow-up、release note、codeが一致する | fixed-SHA artifact | `passed` |
| Manual review | verified actionable findingが0件である | fixed-SHA review artifact | `passed` |
| Rollback readiness | backupと復元手順を確認している | backup path、version、selftest、doctor | `passed` |

実行結果は、full candidate SHAをdirectory名にしたlocal-only `manifest.json`へ保存する。最低限、`candidate_sha`、`base_sha`、`runtime_version`、`installed_source_sha`、各gateの`status`とevidence file名を持たせる。`claude_live_provider_e2e`と`fresh_claude_discovery`は文字列`not_run`とし、Codex marker probeは`codex_marker_probe`として記録する。最終配布前に、manifestの`candidate_sha`、`installed_source_sha`、`git rev-parse HEAD`が一致することを確認する。

repository、probe、installのcommandは、上記blockのfile名でone-shot directoryへ出力する。Managerは同じdirectoryへ`install-backups.json`、`codex-discovery.json`、`gap-audit.txt`、`review-correctness.txt`、`review-security.txt`、`review-cli-docs.txt`、`review-tests.txt`を保存する。Gapと各review fileは`base_sha: <full SHA>`、`candidate_sha: <full SHA>`、最後にそれぞれ`GAP AUDIT: PASS`または`REVIEW RESULT: CLEAN`の3行だけを持つ。古いcandidateの結果をコピーしない。全gate完了後、次のfinalizerで必須file、JSON内容、warning不在、SHA付きGap PASS、4 lane CLEANを完全一致で検査し、最後に`manifest.json`を原子的に確定する。

```bash
set -euo pipefail
: "${EXPECTED_BASE_SHA:?set the externally reviewed full baseline SHA}"
: "${EXPECTED_CANDIDATE_SHA:?set the externally reviewed full candidate SHA}"
EVIDENCE_ROOT="$(git rev-parse --git-common-dir)/herdr-dev-loop-release-evidence/0.5.1/$EXPECTED_CANDIDATE_SHA"
test "${#EXPECTED_BASE_SHA}" -eq 40
test "${#EXPECTED_CANDIDATE_SHA}" -eq 40
test "$(git rev-parse --verify "${EXPECTED_BASE_SHA}^{commit}")" = "$EXPECTED_BASE_SHA"
test "$(git rev-parse --verify "${EXPECTED_CANDIDATE_SHA}^{commit}")" = "$EXPECTED_CANDIDATE_SHA"
test "$(git rev-parse HEAD)" = "$EXPECTED_CANDIDATE_SHA"
test "$(git merge-base "$EXPECTED_BASE_SHA" "$EXPECTED_CANDIDATE_SHA")" = "$EXPECTED_BASE_SHA"
test "$(git rev-parse master)" = "$EXPECTED_CANDIDATE_SHA"
test "$(git rev-parse origin/master)" = "$EXPECTED_CANDIDATE_SHA"
python3 - "$EVIDENCE_ROOT" "$EXPECTED_BASE_SHA" "$EXPECTED_CANDIDATE_SHA" <<'PY'
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
base_sha = sys.argv[2]
candidate_sha = sys.argv[3]
root_stat = os.lstat(root)
if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != os.geteuid():
    raise SystemExit("evidence root must be a current-user directory, not a symlink")

required_nonempty = {
    "compile.status",
    "unit.log",
    "version.json",
    "selftest.log",
    "quick-validate.log",
    "synthetic.json",
    "synthetic.stdout.log",
    "repository-gates.status",
    "codex-marker-probe.json",
    "codex-marker-probe.stdout.log",
    "provider-gates.status",
    "codex-installed-version.json",
    "claude-installed-version.json",
    "codex-installed-selftest.log",
    "claude-installed-selftest.log",
    "installed-source.json",
    "install-gates.status",
    "install-backups.json",
    "codex-discovery.json",
    "gap-audit.txt",
    "review-correctness.txt",
    "review-security.txt",
    "review-cli-docs.txt",
    "review-tests.txt",
}
required_regular = required_nonempty | {
    "compile.stdout.log",
    "compile.stderr.log",
    "version.stderr.log",
    "selftest.stderr.log",
    "quick-validate.stderr.log",
    "synthetic.stderr.log",
    "diff-check.log",
    "diff-check.stderr.log",
    "codex-marker-probe.stderr.log",
    "codex-install-diff.log",
    "codex-install-diff.stderr.log",
    "claude-install-diff.log",
    "claude-install-diff.stderr.log",
    "codex-installed-version.stderr.log",
    "claude-installed-version.stderr.log",
    "codex-installed-selftest.stderr.log",
    "claude-installed-selftest.stderr.log",
    "installed-source.stderr.log",
}
for name in sorted(required_regular):
    path = root / name
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise SystemExit(f"unsafe evidence file: {name}")
    if name in required_nonempty and metadata.st_size == 0:
        raise SystemExit(f"empty evidence file: {name}")

unit_text = (root / "unit.log").read_text(encoding="utf-8")
unit_match = re.search(r"Ran ([0-9]+) tests", unit_text)
if unit_match is None or "\nOK\n" not in unit_text:
    raise SystemExit("unit suite did not finish successfully")
if re.search(r"ResourceWarning|Exception ignored while finalizing database connection", unit_text):
    raise SystemExit("resource warning found in unit evidence")
if (root / "compile.status").read_text(encoding="utf-8").strip() != "passed":
    raise SystemExit("compile gate did not pass")
if (root / "repository-gates.status").read_text(encoding="utf-8").strip() != "passed":
    raise SystemExit("repository gate did not pass")
if (root / "provider-gates.status").read_text(encoding="utf-8").strip() != "passed":
    raise SystemExit("provider gate did not pass")
if (root / "install-gates.status").read_text(encoding="utf-8").strip() != "passed":
    raise SystemExit("install gate did not pass")
for name in (
    "diff-check.log",
    "diff-check.stderr.log",
    "codex-install-diff.log",
    "codex-install-diff.stderr.log",
    "claude-install-diff.log",
    "claude-install-diff.stderr.log",
):
    if (root / name).read_bytes():
        raise SystemExit(f"expected empty success evidence: {name}")
if (root / "selftest.log").read_text(encoding="utf-8").strip() != "selftest ok":
    raise SystemExit("repository selftest mismatch")
if "Skill is valid!" not in (root / "quick-validate.log").read_text(encoding="utf-8"):
    raise SystemExit("skill validator mismatch")
for name in ("codex-installed-selftest.log", "claude-installed-selftest.log"):
    if (root / name).read_text(encoding="utf-8").strip() != "selftest ok":
        raise SystemExit(f"installed selftest mismatch: {name}")

version = json.loads((root / "version.json").read_text(encoding="utf-8"))
synthetic = json.loads((root / "synthetic.json").read_text(encoding="utf-8"))
marker = json.loads((root / "codex-marker-probe.json").read_text(encoding="utf-8"))
codex_version = json.loads((root / "codex-installed-version.json").read_text(encoding="utf-8"))
claude_version = json.loads((root / "claude-installed-version.json").read_text(encoding="utf-8"))
backups = json.loads((root / "install-backups.json").read_text(encoding="utf-8"))
discovery = json.loads((root / "codex-discovery.json").read_text(encoding="utf-8"))
installed_source = json.loads((root / "installed-source.json").read_text(encoding="utf-8"))
if version.get("runtime_skill_version") != "0.5.1":
    raise SystemExit("repository version mismatch")
if any(item.get("runtime_skill_version") != "0.5.1" for item in (codex_version, claude_version)):
    raise SystemExit("installed version mismatch")
if synthetic.get("status") != "passed" or synthetic.get("scenario_count") != 9:
    raise SystemExit("synthetic gate mismatch")
if marker.get("status") != "passed" or marker.get("runner") != "herdr-dev-loop-provider-marker-probe":
    raise SystemExit("Codex marker probe mismatch")
if backups.get("status") != "passed" or discovery.get("status") != "passed":
    raise SystemExit("backup or Codex discovery evidence mismatch")
if discovery.get("source_sha") != candidate_sha:
    raise SystemExit("Codex discovery source SHA mismatch")
if installed_source.get("status") != "passed" or installed_source.get("source_sha") != candidate_sha:
    raise SystemExit("installed source SHA mismatch")
gap_lines = (root / "gap-audit.txt").read_text(encoding="utf-8").splitlines()
expected_gap_lines = [
    f"base_sha: {base_sha}",
    f"candidate_sha: {candidate_sha}",
    "GAP AUDIT: PASS",
]
if gap_lines != expected_gap_lines:
    raise SystemExit("Gap Audit did not pass")
review_files = [
    "review-correctness.txt",
    "review-security.txt",
    "review-cli-docs.txt",
    "review-tests.txt",
]
expected_review_lines = [
    f"base_sha: {base_sha}",
    f"candidate_sha: {candidate_sha}",
    "REVIEW RESULT: CLEAN",
]
for name in review_files:
    if (root / name).read_text(encoding="utf-8").splitlines() != expected_review_lines:
        raise SystemExit(f"manual review is not clean or SHA-bound: {name}")

manifest = {
    "schema_version": 1,
    "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "base_sha": base_sha,
    "candidate_sha": candidate_sha,
    "installed_source_sha": candidate_sha,
    "runtime_version": "0.5.1",
    "gates": {
        "repository_unit": {"status": "passed", "count": int(unit_match.group(1)), "evidence": "unit.log"},
        "python_compile": {"status": "passed", "evidence": "compile.status"},
        "repository_selftest": {"status": "passed", "evidence": "selftest.log"},
        "skill_validator": {"status": "passed", "evidence": "quick-validate.log"},
        "diff_hygiene": {"status": "passed", "evidence": "diff-check.log"},
        "resource_warning_scan": {"status": "passed", "evidence": "unit.log"},
        "synthetic_e2e": {"status": "passed", "scenario_count": 9, "evidence": "synthetic.json"},
        "codex_marker_probe": {"status": "passed", "evidence": "codex-marker-probe.json"},
        "claude_live_provider_e2e": "not_run",
        "codex_install_parity": {"status": "passed", "evidence": "codex-install-diff.log"},
        "claude_install_parity": {"status": "passed", "evidence": "claude-install-diff.log"},
        "codex_installed_version": {"status": "passed", "evidence": "codex-installed-version.json"},
        "claude_installed_version": {"status": "passed", "evidence": "claude-installed-version.json"},
        "codex_installed_selftest": {"status": "passed", "evidence": "codex-installed-selftest.log"},
        "claude_installed_selftest": {"status": "passed", "evidence": "claude-installed-selftest.log"},
        "installed_source": {"status": "passed", "evidence": "installed-source.json"},
        "fresh_codex_discovery": {"status": "passed", "evidence": "codex-discovery.json"},
        "fresh_claude_discovery": "not_run",
        "gap_audit": {"status": "passed", "evidence": "gap-audit.txt"},
        "manual_review": {"status": "passed", "findings": 0, "evidence": review_files},
        "rollback_readiness": {"status": "passed", "evidence": "install-backups.json"},
    },
}
manifest_path = root / "manifest.json"
temporary_path = root / ".manifest.json.tmp"
if os.path.lexists(manifest_path) or os.path.lexists(temporary_path):
    raise SystemExit("manifest path already exists")
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(temporary_path, flags, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())
os.replace(temporary_path, manifest_path)
directory_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(directory_descriptor)
finally:
    os.close(directory_descriptor)
PY
test "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["candidate_sha"])' "$EVIDENCE_ROOT/manifest.json")" = "$EXPECTED_CANDIDATE_SHA"
```
