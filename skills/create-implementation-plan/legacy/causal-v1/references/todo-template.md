# TODO: [topic]

> 運用契約: このファイルを再作成しない。in-place 更新のみ。コンテキスト圧縮後・再開時は、まず本ファイル全文と正本計画書の「不変条件・逸脱禁止」「Hard Gate」および現在 Step の本文を再読し、現在位置を1行で宣言してから作業する。

- 正本計画書: `[リポジトリ相対パス。scaled の場合は 00-overview.md]`
- 診断・モデル正本: `[full は正本計画書、scaled は最初の detailed phase]`
- goal prompt: `[リポジトリ相対パス。lite で作らない場合は「なし（lite）」]`
- 計画検証日時: [YYYY-MM-DD HH:MM]（base: [branch]@[short-sha]。初版は作成日時と同値。鮮度チェックを実施したら更新する）
- アクティブタスク: [着手中のタスクを1行で。Step/Phase ID 付き。進行に合わせて更新する]
- ステータス凡例: `[ ]` 未着手 / `[x]` 完了 / `[!]` blocked（理由必須） / `[-]` cancelled（理由必須）
- 完了条件: 全行が `[x]` または `[-]` に消し込まれていること。未消し込みのままの完了報告は禁止。
- cancelled の扱い: `[-]` にはユーザー指示または計画改訂に紐づく理由が必要。`[-]` が1件でもあれば完了報告は「完了（一部取消あり）」とし、取消一覧を明記する。
- 非冪等タスク（migration・データ更新・外部 API 書き込み）は、実行直前にタスク行へ「実行開始」を追記してから実行する。再開時、先頭の未完了タスクが非冪等なら副作用の有無を確認してから再実行する。
- 未登録 variant、LCU 境界変更、Repair postcondition 変更を発見したら、実装範囲を黙って広げない。該当タスクを `[!]` にし、診断・モデル正本を改訂してから再開する。

## Hard Gate 一覧

| Step | Command | Expect | 結果 |
| --- | --- | --- | --- |
| 1 | `[command]` | [expect] | [PASS/FAIL/未実行] |

Gate を実行したら「結果」列を更新する。FAIL のまま次の Step に進まない。
Step 列は、lite では Todo 行番号、scaled では `P1-S1` のようなフェーズ修飾付き ID で書く。
Expect には Gate コマンドの機械判定値（exit 0・件数など）を書き下す（lite の `（Gate: cmd）` 表記から復元する）。

## Unclear Points 要約

- 未解決: [N] 件 — [各1行で。ブロッカーには「ブロッカー」と明記] ／ または `None`

## タスク

対象の全量を省略せず列挙する（「主要なもののみ」への要約は禁止）。1タスク = 1成果。

- [ ] Step 1 (LCU-01): `[path]` [やること]（Gate: `[command]`）
- [ ] Step 2 (LCU-02): `[path]` [やること]（Gate: `[command]`）
- [ ] Phase 3 (LCU-03): [outline phase の成果]（Gate: `[overview の完了 Gate]`）
- [ ] テスト: `[command]`
- [ ] 実環境動作確認: `[command]`
- [ ] ドキュメント反映: `[path]`

括弧内は Primary LCU 一つ、または outline phase の対象 LCU 全件と完全一致させる。
説明中へ余分な LCU を足して照合を迂回しない。
