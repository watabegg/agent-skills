# Distribution provenance

This repository forked `skills/codex-review-multi-v2` from `fullerene-inc/agent-skills` commit `49fd08742f7666efa1fd4775989317d2da73f077` on 2026-07-22.

The fork keeps the upstream review contract and adds local release hardening:

- reject generated Python bytecode from immutable companion distributions;
- bind attestation to a start-of-review Git and worktree fingerprint;
- run the model with only its artifact directory writable and keep trusted validation files outside that directory;
- validate and render a parent-owned snapshot of `review.json`;
- reject shared-default, future-approved, and symlinked product profiles as demotion evidence.

`skills/herdr-dev-loop/release-dependencies.json` records the immutable commit of this fork, the HLoop adapter version, and the `sha256-tree-v1` payload digest. The digest excludes only the capability manifest that embeds the digest itself. The manifest remains subject to an exact adapter-record comparison.
