# Distribution provenance

This repository forked `skills/codex-review-multi-v2` from `fullerene-inc/agent-skills` commit `49fd08742f7666efa1fd4775989317d2da73f077` on 2026-07-22.

The fork keeps the upstream review contract and adds local release hardening:

- reject generated Python bytecode from immutable companion distributions;
- bind attestation to a start-of-review Git and worktree fingerprint, including branch-mode dirty bytes and untracked file kinds, include those bytes in the declared review scope, force Git to reveal ignored submodule state, and reject dirty submodules whose inner bytes are not pinned by the superproject;
- run the model in a dedicated child directory while keeping the private `0700` official run and trusted validation directories outside the model workspace;
- build the Codex CLI with `exec` before its subcommand options and clear inherited additional workspace-write roots;
- snapshot the skill, schema, validator, renderer, and profile before prompt construction, so prompt generation and attestation use the same trusted bytes;
- validate a no-follow regular-file snapshot of `review.json`, render privately, then transactionally publish independent `0600` artifact inodes without following or replacing model-created paths;
- reject shared-default, future-approved, repository-external, and symlinked product profiles as demotion evidence;
- exclude unverified model-authored reviewer and summary sidecars from the attested report.

`skills/herdr-dev-loop/release-dependencies.json` records the immutable distribution commit of this fork, the HLoop adapter version, and the `sha256-tree-v1` payload digest. The digest excludes only the capability manifest that embeds the digest itself. To avoid a self-referential Git commit, the manifest uses the repository URL plus payload digest as its content-addressed adapter source; the distribution commit remains a separate exact pin in the HLoop record. The manifest remains subject to an exact adapter-record comparison.
