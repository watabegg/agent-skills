# herdr-dev-loop 0.5.2 release checklist

## Release identity

- Skill version: `0.5.2`
- State format: `3`
- Schema revision: `2`
- Minimum Python: `3.11`
- Legacy `.ai/loop`: ignored
- Release shape: bounded review convergence with explicit manual-final certification
- Version tag:作成しない。配布対象のcleanなcandidate SHAを外部で固定する

candidate SHAを固定する前のworking tree検証は、release evidenceとして再利用しない。各環境でbaseline SHA、candidate SHA、実行時のHEADを記録し、証跡はtracked fileやnamespaceのlocal-only artifactへ書き戻さない。

## 0.5.2の変更範囲

0.5.2は、0.5.1のreport transportと同一UID cooperative threat modelを維持したまま、review-driven scope expansionを抑えるためのrelease policyを追加する。

- 新規loopのreview cadenceは`batch`。`review_after_merges`と`gap_after_merges`は、明示的なmerge-count policyまたはlegacy loopの互換設定として扱う
- release scopeをsource digest、`scope_revision`、`source_snapshot_revision`とともにlockし、task作成にprovenanceを要求する
- findingを事実性、重要度、発生時期、contract関係、判断要否、処置、release判定の独立軸で保存する
- follow-upを`fu:v1:sha256:<digest>`の安定issue keyでfirst-class stateへ保存し、review fingerprintや対象SHAだけが変わった再発見を重複登録しない
- convergence reviewのfix roundを最大2回にboundedし、固定SHAでverified actionable findingが0件になるまでmanual finalへ進めない
- manual final reviewはPLAN、MANIFEST、report、lane完了、verification shortfall、manifest completeness、verified actionable finding数を再計算する
- manual finalの失敗、対象SHA drift、またはround上限後の再開は、明示的なuser inputを伴う原子的な`hloop review reopen`だけで行う

0.5.1のrelease evidenceと、0.5.1以前のinvocation key・migration・同一UID境界に関する互換記述は、歴史的証跡として変更しない。

## Migration and compatibility

0.5.2 runtimeは次のmigration chainを使用する。

```text
format-1.revision-0
  -> format-2.revision-0
  -> format-3.revision-0
  -> format-3.revision-1
  -> format-3.revision-2
```

既存namespaceは、同じruntimeとnamespaceで次を順に実行する。

```bash
$HLOOP version
$HLOOP migrate --dry-run
$HLOOP migrate --apply
$HLOOP status --raw-state
```

format-3 revision 1からのmigrationは`run_id`、既存の`review_after_merges`、既存taskを保持する。既存taskは`legacy-unclassified`として読み取り、legacy loopには新しいmanual-final gateを暗黙に要求しない。future revisionはread-only inspectionだけ許可し、downgradeやmutationを拒否する。

## New-loop operator flow

新規loopでは、dispatch前にrelease scopeを固定する。

```bash
$HLOOP release-scope lock \
  --source MISSION.md --source PLAN.md \
  --plan-item-ref P004d --requirement-ref R001 \
  --scope-ref release-scope-contract
$HLOOP release-scope status --json
```

scopeを変える必要がある場合は、editorial、clarification、scope-changeを区別して`release-scope amend`へ記録する。意味を変える`scope-change`にはuser inputと更新後のrevisionが必要であり、未記録のsource driftはreadinessを止める。

lock後のtaskは、`planned`、`finding`、`user-amendment`、`operational`のいずれかのoriginと、対応するplan、requirement、finding、input、またはoperational reasonを持つ。`hloop task new`、triage、pump、conductorのtask作成経路は同じauthorization preflightを通る。

停止または最終認証待ちでは新しいroleをfreezeする。

```bash
$HLOOP dispatch freeze --reason 'awaiting manual final review' --user-input-id U0001
$HLOOP dispatch status --json
$HLOOP dispatch unfreeze --user-input-id U0002
```

freeze中もvalidation、harvest、merge、follow-up記録、report、pauseは実行できるが、新しいtask・Worker・Reviewer・Gap Auditor・Advisorの起動は拒否される。

## Review convergence and manual final

実装batchの統合とvalidation後、Managerは固定SHAのpre-final convergenceを実行する。

```bash
$HLOOP review readiness --json
$HLOOP review convergence prepare --mode swarm --json
# ReviewerまたはManagerが、準備済み固定SHAのMANIFEST.jsonへ証跡を記録
$HLOOP review convergence record --fix-round 0 --json
```

manifestが不完全、またはverified actionable findingが残る場合は、状態を記録して同一round内のremediationへ戻る。roundは自動で2回を超えない。上限到達後に続行するには、user inputを伴う`hloop review reopen --action ...`が必要である。

convergenceが`converged`になったら、fresh contextのmanual final reviewを準備する。

```bash
$HLOOP final-review prepare --mode swarm --json
# PLAN.json、MANIFEST.json、reportを手動reviewの実結果で埋める
$HLOOP final-review record --json
$HLOOP final-review status --json
```

manual finalは、finding数が0という自己申告だけでは合格しない。全laneの完了、必要な独立verification、shortfallがないこと、PLAN/MANIFESTのidentityとdigest、固定target SHA、scope snapshot、reportの存在を検証し、verified actionable findingが0件であることを要求する。finishはmanual finalが`passed`でない限り拒否される。

手動review後にHEADが変わった場合や、failed/incompleteになった場合は、旧certificationを再利用せず、user inputを記録した原子的なreopenから再収束する。

## Public schema entry points

manual finalの公開schemaは次のrepository copyを正本とする。

- `schemas/final-review-plan.schema.json`
- `schemas/final-review-manifest.schema.json`

両ファイルはcanonicalな`references/schemas/`定義への公開entry pointであり、PLAN/MANIFESTのdigest、base/target SHA、scope revision、lane plan、verification policy、completeness、verified actionable finding、patch verdictを検証する。

## Required local validation

repository rootから次を実行し、出力をcandidate SHA keyedなlocal-only release evidenceへ保存する。provider E2Eを実行していない場合は成功と記録せず、明示的に`not_run`または`skipped`とする。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest skills/herdr-dev-loop/tests/test_config_v05.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s skills/herdr-dev-loop/tests -p 'test_*.py'
PYTHONDONTWRITEBYTECODE=1 python3 skills/herdr-dev-loop/scripts/hloop selftest
QUICK_VALIDATE="$(find "${CODEX_HOME:-$HOME/.codex}" "$HOME/.claude" \
  -iname quick_validate.py -print -quit 2>/dev/null || true)"
test -n "$QUICK_VALIDATE"
PYTHONDONTWRITEBYTECODE=1 python3 "$QUICK_VALIDATE" skills/herdr-dev-loop
PYTHONDONTWRITEBYTECODE=1 python3 skills/herdr-dev-loop/tests/run_synthetic_e2e.py --json
git diff --check
```

provider E2Eは認証済みの使い捨てsessionがある場合だけ実行する。

```bash
python3 skills/herdr-dev-loop/tests/run_provider_e2e.py \
  --provider codex --allow-skip \
  --skip-reason 'authenticated provider session is unavailable' --json
```

skipは非実行の証拠であり、live provider passではない。Claudeについても同じ扱いにする。

## Evidence table

| Gate | Evidence | Result |
|---|---|---|
| Version and JSON parsing | `VERSION`, public final-review schemas | pending |
| Targeted config/artifact tests | `test_config_v05.py` | pending |
| Full unit/integration tests | unittest discovery | pending |
| Skill selftest | `hloop selftest` | pending |
| Skill quick validator | `quick_validate.py` | pending |
| Synthetic E2E | structured JSON | pending |
| Codex provider E2E | live JSON or explicit skipped JSON | not_run |
| Claude provider E2E | live JSON or explicit skipped JSON | not_run |
| Installed Codex/Claude copies | outside this repository task | not_run |

このtaskではinstalled Codex/Claude skill copyを同期・変更しない。配布時のbackup、parity、fresh discovery、rollbackは[Migration And Install Parity](../references/migration-install.md)の手順で別途実施する。

## Residual-risk boundary

0.5.2はreview scope、task provenance、manual-final completeness、same-UID cooperative trust modelを明示する。悪意あるsame-UID processからのsecret分離、暗号学的Manager認証、OS-level ACK write isolation、強いsandbox boundary、新しいroleやtransportはこのreleaseの保証範囲ではない。
