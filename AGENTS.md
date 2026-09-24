# Skill repository

Edit maintained skills under `skills/`; install verified changes into the active Codex skill directory. Claude uses symlinks to those installations. Do not edit installed copies as the source of truth.

Find a skill through its `SKILL.md` name and description. Resolve its scripts and references relative to that directory, independent of the current working directory. Keep the normal workflow in the entrypoint and maintenance details in references.

Validate changed skill frontmatter with the installed skill-creator `quick_validate.py`, then run the smallest relevant offline script tests. Do not contact live services merely to validate documentation or local logic. Keep credentials, cookies, private URLs, generated evidence and browser profiles outside this public repository.

The CRV2 snapshot and `herdr-dev-loop` have their own release relationship; do not synchronize them over a separately managed installation without an explicit request.
