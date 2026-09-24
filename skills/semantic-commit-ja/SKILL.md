---
name: semantic-commit-ja
description: Create or review Japanese Conventional Commit messages from real Git changes; use for commit, amend, revert and commit-splitting requests unless another language or convention is specified.
---

Use the repository's more specific rules and the user's chosen scope. Otherwise write `type(scope): 日本語の件名`; omit an unhelpful scope. Keep Conventional Commit tokens, identifiers and `BREAKING CHANGE` in their standard form. Use `!` for a breaking change.

Inspect `git status --short` and the relevant diff before writing. For a staged commit, use `git diff --cached`; do not mix in unrelated unstaged changes. Consult recent commits only when repository style is unclear. Choose the type from the change's purpose, not its filename. Identify independent changes rather than hiding them under one vague subject.

Write a short, concrete Japanese subject without a final period. Use the body when the reason, behavior, migration or impact needs explanation. Do not invent tests, issue numbers or rationale. Common types are feat, fix, docs, test, refactor, style, perf, ci, build, chore and revert.

A message-only or review request does not authorize staging or committing. For an authorized commit, confirm the staged scope and use existing repository validation/hooks. Do not stage unrelated work, rewrite earlier commits or repair unrelated failures without task authorization. For a multiline message use a temporary body file and `git commit -F` so shell substitutions cannot alter the text.

Report the proposed message, or the created commit and relevant validation, according to the actual request.
