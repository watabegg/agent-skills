# Distribution provenance

This repository forked `skills/codex-review-multi-v2` from `fullerene-inc/agent-skills` commit `49fd08742f7666efa1fd4775989317d2da73f077` on 2026-07-22.

The fork keeps the upstream review contract and adds local release hardening:

- reject generated Python bytecode from immutable companion distributions;
- bind attestation to a start-of-review Git and worktree fingerprint, including branch-mode dirty bytes, and reject dirty submodules whose inner bytes are not pinned by the superproject;
- run the model with only its artifact directory writable and keep trusted validation files outside that directory;
- validate and render a parent-owned regular-file snapshot of `review.json`, then atomically publish generated artifacts without following or replacing model-created paths;
- reject shared-default, future-approved, repository-external, and symlinked product profiles as demotion evidence;
- exclude unverified model-authored reviewer and summary sidecars from the attested report.

`skills/herdr-dev-loop/release-dependencies.json` records the immutable distribution commit of this fork, the HLoop adapter version, and the `sha256-tree-v1` payload digest. The digest excludes only the capability manifest that embeds the digest itself. To avoid a self-referential Git commit, the manifest uses the repository URL plus payload digest as its content-addressed adapter source; the distribution commit remains a separate exact pin in the HLoop record. The manifest remains subject to an exact adapter-record comparison.
