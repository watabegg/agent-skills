# goal prompt: 通知システム拡張

## /goal 貼り付け用（この短文だけを使う）

```
通知システム拡張を実装してください。
- 作業の正: valid-scaled-overview.md（scaled。フェーズ一覧から着手フェーズを特定）
- 進捗台帳: valid-scaled-todo.md（再作成禁止・in-place 更新・再開時はヘッダのアクティブタスクから）
- 「不変条件・逸脱禁止」節に従うこと。許可パスリスト外の変更は停止して報告。
- 完了条件: todo 全行消し込み + 全 Hard Gate PASS + 完了報告に「計画書からの逸脱」セクション必須。
- 実装は $codex-impl を使用。
```

## 正本参照ブロック

- 計画書: valid-scaled-overview.md
- 進捗台帳: valid-scaled-todo.md
- 許可パスリスト: `internal/notify/**` — 計画成果物と開始前 dirty は照合対象外
- コミット状態: 未コミット（テスト用最小例）

## 事前回答（preflight）

- gap-check 回数: 未回答
- レビューループ上限: 未回答
- QA 環境: staging

## 再開手順（別セッション・コンテキスト圧縮後）

1. todo.md を全文読み、ヘッダのアクティブタスクから再開位置を特定する
2. 鮮度チェック: 計画検証日時から24時間超、または base branch が計画時点の sha から進んでいる場合、keep / redesign / close で再検証する

## 実装後フロー

1. PR 作成
2. QA: post-implementation-verify を staging で
