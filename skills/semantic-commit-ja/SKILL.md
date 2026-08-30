---
name: semantic-commit-ja
description: Create, review, amend, or validate Japanese Conventional Commit messages from the actual Git changes. Use this by default whenever the user asks to commit, prepare a commit message, amend, revert, split commits, or review commit-message format, unless the user explicitly requests another language or convention.
---

# Semantic Commit Ja

Use this skill as the default commit-message policy for Codex and Claude Code. Follow repository-specific rules and explicit user instructions when they are more specific; otherwise use Japanese for the subject and body and English Conventional Commits tokens for the machine-readable prefix.

## Format

Use this structure:

```text
type(scope): 日本語の件名

日本語の本文。必要な場合だけ、変更内容と変更理由を書く。
```

Use `scope` only when it adds stable, useful context. Otherwise omit it:

```text
fix: 日本語の件名
```

For breaking changes, use the Conventional Commits marker:

```text
feat(api)!: 互換性のない認証 API 変更
```

Keep `type`, `scope`, `!`, and footer tokens such as `BREAKING CHANGE` in their standard machine-readable form. Write the description and body in Japanese, except for code identifiers, product names, URLs, issue references, and other terms that are clearer unchanged.

## Type selection

Choose the primary type from the actual purpose of the change:

- `feat`: ユーザーや利用者に見える機能・能力の追加
- `fix`: 誤った挙動や不具合の修正
- `docs`: ドキュメントのみの変更
- `test`: テストの追加・変更
- `refactor`: 挙動を変えない内部構造の整理
- `style`: 挙動を変えないフォーマット・空白の変更
- `perf`: パフォーマンス改善
- `ci`: CI/CD の変更
- `build`: ビルド、依存関係、パッケージングの変更
- `chore`: 上記に当てはまらない保守作業
- `revert`: 過去のコミットの取り消し

Do not choose a type from the filename alone. Prefer the user-visible or operational intent of the diff. If multiple unrelated purposes are mixed, recommend splitting the changes before writing a single message.

## Message rules

- Keep the subject short, concrete, and focused on one purpose.
- Write the subject in Japanese, without a trailing period.
- Use a concise imperative or natural noun-phrase style consistent with the repository's existing commits.
- Explain why in the body when the subject alone does not make the motivation, risk, migration, data behavior, authorization, billing, deployment, or user impact clear.
- Do not invent tests, behavior, issue numbers, or rationale that are not supported by the diff or session context.
- Keep the body in Japanese and use short paragraphs or bullets only when they improve scanability.

Good examples:

```text
feat(booking): 予約一覧に日付範囲フィルターを追加
```

```text
fix(auth): セッション期限切れ後のリダイレクトループを修正

期限切れのセッションをログイン画面へ戻す際に、元の遷移先を再利用して
リダイレクトが循環していた。期限切れ時に遷移先をクリアして停止する。
```

Avoid vague messages such as `chore: いろいろ修正`, `fix: バグ修正`, or `feat: 機能追加`.

## Workflow

1. Read the applicable repository instructions and any explicit user request about language, format, scope, staging, or commit behavior.
2. Inspect the real changes before deciding the message. At minimum, use `git status --short` and the relevant `git diff`; use `git diff --cached` for staged changes and `git log -10 --oneline` when repository style matters.
3. Decide whether the changes represent one coherent purpose. If not, identify the split and do not hide unrelated work behind one message.
4. Select `type` and optional `scope` from the change intent.
5. Draft the Japanese Conventional Commit message and verify its prefix, language, subject, body, and breaking-change notation.
6. If the user asked only for a message or review, do not change the index or create a commit.
7. If the user explicitly asked to commit, ensure the staged files match the intended scope before committing. Do not stage unrelated files merely to make a commit possible.
8. After committing, verify the resulting message with `git log -1 --pretty=raw` or an equivalent read-only Git command.

When constructing a multi-paragraph commit from the shell, use separate `-m` arguments or a message file. Do not put literal `\n` or `\n\n` text inside one `-m` argument.

## Explicit overrides

Skip or adapt this skill only when the user explicitly requests another language, another commit convention, an exact message, a squash/release-tool format, or no commit-message generation. Continue to follow repository rules and safety constraints in all cases.
