# Public Repo Safety

This skill repository is public-oriented. Keep it generic and free of private operational data.

Do not commit:

- real `.ai/herdr-dev-loop/loops/<namespace>` artifacts from product repositories
- `.ai/herdr-dev-loop/experience/worktree-setup.json` when commands reveal private paths, registries, hosts, or repository operations
- pane transcripts
- tokens, cookies, API keys, credentials, browser profiles, or env files
- customer, company, student, or production data
- private repository URLs or internal hostnames
- live production runbooks unless already approved for public release

It is acceptable to document generic command shapes and local CLI assumptions, but prefer `hloop doctor` for current environment facts instead of hard-coding machine-specific paths.
