# crv2 出力フォーマット正本(renderer / agent-native 共通)

review.json(schemas/review.schema.json)から最終日本語レビューを生成する側の正本。attested 実行では assets/validate_review.py + assets/render_review.py がこの写像を機械適用する。agent-native 直実行も、可能なら `python3 assets/render_review.py` を呼び、呼べない場合のみ本書の表を機械的に適用して(感覚で調整しない)最終行に UNATTESTED を付ける。レビューエージェント(モデル)は Action・P・verdict・件数・OK 行を書かない。

## Action 導出表(固定写像)

base = impact × exposure.trigger_class:

| impact \ trigger_class | routine          | conditional      | adversarial |
| ---------------------- | ---------------- | ---------------- | ----------- |
| critical               | block            | fix-before-merge | follow-up   |
| high                   | fix-before-merge | fix-before-merge | follow-up   |
| medium                 | fix-before-merge | follow-up        | follow-up   |
| low                    | follow-up        | follow-up        | omit        |

調整ルール(base に対して番号順に適用):

1. `operational_rate: rare` → 1 段降格。条件は両方必須: (a) 有効な context_effect がある(実在 profile_field への JSON Pointer + 降格適格な source_type + 期限内 as_of + causal_link + counterfactual)、(b) defect_class が非降格クラス(authz-boundary / destructive-data-loss / irreversible-external-effect / production-credential)ではない。impact が critical / high の指摘は follow-up より下へは下げない。
2. `operational_rate: frequent` → 1 段昇格。ただし block へ上げられるのは impact: critical のみ。
3. `operational_rate: unknown / occasional` → 調整なし。unknown(情報が無い)を理由に下げることは禁止(fail-closed)。
4. `mitigation: easy-recovery` → 非降格クラス外でのみ 1 段降格。easy-recovery の主張には SKILL.md の 4 条件(検知可能・一意特定可能・文書化された復旧手順・有界コスト)が必要。
5. `uncertainty.kind: behavior` → Action を clarify-spec に上書きし、「残留リスク / 未確認点」へ質問として置く。
6. `confidence: low`(evidence uncertainty)→ 導出済み Action を保持したまま「残留リスク / 未確認点」へ移す(Impact・Action は改変しない)。

P 写像(固定): block→P0 / fix-before-merge→P1 / follow-up→P2 / clarify-spec→P なし(残留リスク行き) / omit→表示しない。omit を含む全候補と `action_without_context` / `action_with_context` の両方を監査 artifact に残す。セッション内の相対比較・レビューラウンド・confidence 単独で Action を調整しない。

`sentinel: true` の高 Impact 候補が文脈適用(調整 1・4)で omit または降格になった場合、renderer は監査フラグを立てる。表示は調整後だけでよいが、artifact からは消さない。

## 表示規則(renderer output contract)

- 検証を通過した指摘は全件フル形式で出力する。表示上限による要約・省略・「他N件は割愛」集約はしない(ノイズ抑制は生成側で行い、件数は validator の COUNT_INFO warning で監視するだけ)。
- 新規または拡大した指摘: Action の重い順(block → fix-before-merge → follow-up)に全件フル表示。原因クラスタは親 1 件として子症状を構造化して保持する。
- 残留リスク / 未確認点: clarify-spec 質問・confidence: low 指摘・assurance gap を全件表示。各項目は具体的な質問または確認手順で終える。
- 既存の無関係な問題: 該当分を全件表示。
- Profile gaps: review.json には全件保存し、人間向け表示は 3 件まで(`action_changes_if_answered: true` を優先)。全件は `--gaps-out` の JSON に保存する。
- どのセクションも該当がなければ「なし」と書く。「なし」は失敗ではなく良い結果である。

## 最終 Markdown 構成

```markdown
## Reviewed by
- 実際に使ったレビュアーを列挙し、各 1 行で何を見たか・何に依拠したかを書く(sentinel レーンは context-blind と明記)。

## 新規または拡大した指摘
- この差分に起因するものだけ。種別は「新規バグ」または「既存問題の露出拡大」に限定。
- Action の重い順に並べる。各項目先頭の [P0-P2] は P 写像の導出値(手で選ばない)。

## 総評
- 平易な日本語で 3〜6 行。この差分起因の問題だけをもとに、パッチが全体として安全そうかを述べる。
- どこに最も自信があり、どこに最も自信がないかを述べる。
- 指摘が複数あるときは、最後に「原因は実質何個か」を 1 行で書く(例: 指摘9件のうち6件は in-flight 応答管理の欠如1つに帰着)。

## 既存の無関係な問題
- 差分と無関係にレビュー中明確に確認できた既存問題のみ。overall 評価に含めないことを各項目で明記する。

## 残留リスク / 未確認点
- 次のいずれかに該当する項目を質問・確認手順の形式で置く(P は付けない): behavior uncertainty の clarify-spec 質問 / confidence: low(evidence uncertainty)の指摘(導出 Action は併記して保持) / assurance gap(検証困難化)。

## Profile gaps
- operational uncertainty で記録された欠落 profile 事実。JSON Pointer + 質問案を表示 3 件まで。

## Patch verdict
- overall: correct / mostly correct / incorrect / cannot judge without spec
- reason: 新規バグと露出拡大のみに基づく 1 段落。
```

## 指摘の表示テンプレート(全セクション共通)

```markdown
### [P0-P2] タイトル
- 一言要約: 何が起きる問題かを 1-2 文でわかりやすく。
- 種別: 新規バグ / 既存問題の露出拡大 / 既存の無関係な問題
- 症状/原因: 原因そのもの / <原因名>の症状
- Impact / Exposure / Mitigation: impact、trigger_class + operational_rate、mitigation を 1 行で。
- defect_class / uncertainty: schema の値をそのまま書く。
- Confidence: high / medium / low + 根拠種別(reproduced / code-trace / inference)
- どんなときに起きるか: ユーザー操作とデータ条件の時系列で書き、永続化層(DB / メモリ / DOM)を明示する。security は caller capability(ordinary-authenticated / privileged-role / compromised-component / unauthenticated)と reachability(normal-ui / browser-devtools-or-http-client / non-default-feature / implausible-sequence)をここに記録する。
- 何が困るか: ユーザー、運用者、開発者のどこにどんな不利益が出るかを書く。
- なぜ起きるか: 発生機序を A→B→C のステップ連鎖で書き、壊れる前提を invariant type(domain-invariant / local-contract / unknown)付きで 1 文添える。
- 反証チェック: 指摘を無効化しうるガード・制約・仕様・既存テストを探した場所(file:line または文書名)と結果。
- (降格主張時のみ)context_effect: 引用した profile_field / source_type / as_of / causal_link / counterfactual を 1-2 行で。
- 根拠:
  /plain/path/to/file.ext:line
- どう直すか: 原因側の修正を主、応急処置(症状側)は従として分けて書く。
- Action: renderer 導出値(block / fix-before-merge / follow-up / clarify-spec)
```

クラスタ親は統一テンプレートに次の節を足す(子 finding を構造として消さない):

```markdown
### [P1] 【原因】in-flight RPC の鮮度・再入を管理する共通機構がない
- この原因による症状(子 finding の id / impact / trigger を保持する):
  1. F-01 (high): 求人の並行追加で古い snapshot が詳細表示を巻き戻す
  2. F-02 (medium): 保存中のモーダルを閉じると選択状態が後から戻される
- どう直すか(原因側): 応答適用前に世代トークンを検証する共通フックを導入する。子症状は同時に解消する。
```

## Codebase audit 時のセクション読み替え

- `新規または拡大した指摘` → `優先して直すべきコードベース指摘`。種別は `現行コードベースの修正候補` / `設計・運用リスク` / `未確認点`。
- なぜ起きるか → 現行コードのどの構造・分岐・契約・運用前提が原因かを書く。
- 総評 → 現行コードベース全体のリスクと、次に直すべき優先領域を述べる。
- `Patch verdict` → `Codebase audit verdict`: overall は healthy / mostly healthy / risky / cannot judge without deeper runtime evidence。reason は最重要のコードベース指摘と残留リスクに基づく 1 段落。
- codebase audit のみ、wontfix 級の提案を backlog 項目として保持してよい(通常レビューでは omit)。
