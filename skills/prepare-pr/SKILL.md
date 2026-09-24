---
name: prepare-pr
description: Prepare, create, or update a reviewer-friendly pull request from actual changes, including its scope, title, description, verification evidence, and reviewer focus. Use for PR creation, review-request preparation, or improving an existing PR description, not for reviewing someone else's code.
---

# Prepare a pull request

Make the change understandable to a reviewer who has not seen the conversation. Scale the description and preparation to the actual change. Do not configure or send Slack notifications.

## Establish the facts

Read applicable repository instructions and PR templates. Identify the repository, base/head branches, existing PR, commits, and actual PR diff. Distinguish uncommitted work from changes already included in that diff; include only work within the request.

Use related issues and earlier decisions to establish the problem and intended behavior. Do not invent motivation from code alone. Ask only when missing information materially affects the result and cannot be established from available evidence.

## Check scope and verification

- Judge scope by a coherent problem and its dependencies, not a fixed line-count limit. If independent purposes are mixed, propose concrete PR boundaries and their dependencies. Do not automatically split branches or rewrite published history.
- When committing is part of the authorized work, use `semantic-commit-ja` unless the user specifies another language or convention. Prefer commits that resolve a coherent task. Do not rewrite shared commits merely to improve presentation.
- Choose the smallest meaningful tests, checks, lint, or formatting validation for the change. Reuse recorded results only when they still apply to the current changes. Keep passed, failed, and unrun checks distinct. Never claim a check ran without evidence.
- Explain important coverage gaps and address them within the requested scope. Do not require new tests for documentation or trivial reversible edits. Do not automatically start a broad audit, browser QA, or multi-reviewer workflow.

## Write the title and description

For Japanese PR prose, use `agy-writer`: supply confirmed facts, let Gemini draft and edit, then compare the complete result with the diff, decisions, and actual verification evidence. Resolve these companion skills through the available skill catalog; do not hard-code installation paths. If a required companion is missing, continue independent preparation and report the missing dependency.

Follow the repository's PR template. Without one, organize the description around the following content, using headings only when they help:

- **Why:** the concrete problem, trigger, or need.
- **What:** the resulting behavior and changes that matter to the reviewer. Use a before/after example when useful.
- **Verification:** relevant commands or manual steps and their actual results; disclose material failures or unrun checks.

Add related links, reviewer focus, real UI screenshots, or API request/response examples only when useful and available. Omit empty optional sections. Keep a small change brief. Describe the final implementation, not the conversation or abandoned approaches unless they explain a material tradeoff. The title should identify the resulting change.

For code needing a localized explanation, prepare its path, current diff location, and rationale or review question. Persistent implementation rationale belongs in code when appropriate; review-specific discussion belongs in the PR. Normally include reviewer focus in the body and return useful inline-comment candidates. Post inline comments only when requested, after checking that their locations still match the diff.

## Create or update when requested

A description-only request produces text without publishing. An actual PR creation or update request authorizes the necessary operations within that scope, including relevant commits and branch pushes; do not repeatedly ask for permission already granted. Preserve unrelated local changes.

Before creating, check for an existing PR for the intended branch and repository. Use the requested draft/ready state or repository convention. If the outcome of a remote operation is unclear, inspect remote state before retrying rather than creating duplicates. Do not merge as part of this skill.

Publish the exact reviewed text using a structured tool argument or a body file that preserves newlines. Verify the resulting PR title, body, base, and head against the intended changes. Return its URL and a concise verification summary, plus any material unresolved issue. If publishing was not requested, return the prepared title/body and useful split recommendations or comment candidates instead.

## Source

Adapted from [every Tech Blog: 今すぐできるレビュワーに優しいPull Requestをつくる7つのポイント](https://tech.every.tv/entry/2021/06/08/120000) (2021-06-08). Uses its six ideas about motivation, structure, commits, scope, localized explanations, and tests. Slack integration is excluded; authorization, companion-skill integration, and proportional verification are local workflow choices.
