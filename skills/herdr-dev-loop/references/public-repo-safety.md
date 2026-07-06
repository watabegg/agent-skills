# Public Repo Safety

This skill repository is public-oriented. Keep it generic and free of private operational data.

Do not commit:

- real `.ai/loop` artifacts from product repositories
- pane transcripts
- tokens, cookies, API keys, credentials, browser profiles, or env files
- customer, company, student, or production data
- private repository URLs or internal hostnames
- live production runbooks unless already approved for public release

It is acceptable to document generic command shapes and local CLI assumptions, but prefer `hloop doctor` for current environment facts instead of hard-coding machine-specific paths.
