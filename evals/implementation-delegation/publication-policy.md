# Publication boundary

Build public material from an allowlist in a clean directory. Do not copy raw evidence
and then try to replace names. This policy applies to the new evaluation archive;
it does not assert that every existing repository file is anonymous.

## Allowed

- Audited synthetic specifications, starters and tests with portable paths.
- Curated historical numeric records, public model labels and methodological limits.
- Generic descriptions of private-repository observations, at month-level precision.
- Hashes of the sanitized public artifacts, public skill revisions and public references.

## Kept outside Git

- Raw conversations, model reasoning, tool logs, session/thread IDs and screenshots.
- Private repository code, original task prompts/contracts, patches and generated schemas.
- Private organization/product/customer names, URLs, repository/branch/PR/commit IDs,
  original filenames, test names, error strings, absolute paths and user identifiers.
- Private source hashes, source-to-public mappings, or symlinks to private artifacts.

Private-derived tasks cannot be published by renaming entities alone. Reauthor a
standalone generic task from non-identifying behavior, avoid distinctive combinations
of domain details, give it a new ID/version, and mark it unrun until a new evaluation.
Never transfer historical private-repository scores onto a replacement task.

Exact aggregate measurements are retained as historical observations. Names, source
links, precise execution timestamps and distinctive domain narratives are excluded.
This reduces identification risk; it cannot guarantee impossibility of correlation
with other information. Source mappings remain local and are not linked publicly.

## Review before committing

Inspect exactly the intended additions and any changed existing files: text, filenames,
embedded data, URLs, paths, hidden files and symlinks. Check domain inference manually;
a string scan alone does not prove anonymity. Do not stage private files even briefly.
Run the offline record/hash validator and relevant task calibration. Review the staged
diff and make sure raw generated evidence has not been included.

The archive consists of deliberately curated reference data, not a mirror of experiment
output directories. Do not add future submissions or logs without a separate scope and
publication review. Public grader availability must be disclosed in future studies.
