#!/usr/bin/env python3
"""Install an already-installed agy-writer skill's launcher and workspace files."""

import argparse
import json
from pathlib import Path
import shlex
import shutil


def sync_file(source, target):
    if (target.is_file() and not target.is_symlink() and target.read_bytes() == source.read_bytes()
            and target.stat().st_mode & 0o777 == source.stat().st_mode & 0o777):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()
    shutil.copy2(source, target)
    return True


def sync_link(target, source):
    if target.is_symlink() and target.resolve() == source.resolve():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if target.is_dir() and not target.is_symlink():
            raise ValueError(f"Expected a file, found a directory: {target}")
        target.unlink()
    target.symlink_to(source)
    return True


def hook_definition(policy):
    return {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command",
            "command": "python3 " + shlex.quote(str(policy)), "timeout": 5}]}]}


def load_hooks(path):
    hooks = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(hooks, dict):
        raise ValueError(f"Expected a hooks object: {path}")
    return hooks


def save_hooks(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.is_file() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def install(skill, workspace, bin_dir, agy_config, apply=False, source=None):
    skill, workspace, bin_dir, agy_config = [p.expanduser().resolve() for p in (skill, workspace, bin_dir, agy_config)]
    scripts = skill / "scripts"
    source = source.expanduser().resolve() if source else skill
    files = [(source / "assets/GEMINI.md", workspace / "GEMINI.md"),
             (source / "assets/WORKFLOW.md", workspace / "WORKFLOW.md"),
             (source / "scripts/document_writer_policy.py", workspace / "bin/document_writer_policy.py"),
             (source / "scripts/agy-doc-count", workspace / "bin/agy-doc-count")]
    links = [(bin_dir / "agy-writer", scripts / "agy-writer"),
             (bin_dir / "agent-write-ja", scripts / "write_ja.py"),
             (workspace / "style-profile.md", workspace / "GEMINI.md")]
    local_path = workspace / ".agents/hooks.json"
    local_hooks = load_hooks(local_path)
    local_hooks["document-writer-policy"] = hook_definition(workspace / "bin/document_writer_policy.py")
    old_path = agy_config / "hooks.json"
    old_hooks = load_hooks(old_path)
    old_policy = agy_config / "hooks/document_writer_policy.py"
    if old_policy.exists() or old_policy.is_symlink():
        links.append((old_policy, workspace / "bin/document_writer_policy.py"))
    migrate = old_hooks.get("document-writer-policy") == hook_definition(old_policy)
    if "document-writer-policy" in old_hooks and not migrate:
        raise ValueError("Global writer hook differs from the known definition; inspect before migrating it")
    for source, _ in files:
        if not source.is_file():
            raise ValueError(f"Missing runtime source: {source}")
    changed = []
    if apply:
        for source, target in files:
            if sync_file(source, target):
                changed.append(str(target))
        for target, source in links:
            if sync_link(target, source):
                changed.append(str(target))
        if save_hooks(local_path, local_hooks):
            changed.append(str(local_path))
        # Install the workspace policy before removing its old global registration.
        if migrate:
            del old_hooks["document-writer-policy"]
            save_hooks(old_path, old_hooks)
            changed.append(str(old_path))
    return {"applied": apply, "workspace": str(workspace), "files": [str(p) for _, p in files],
            "links": {str(p): str(s) for p, s in links}, "workspace_hooks": str(local_path),
            "migrate_global_writer_hook": migrate, "changed": changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source", type=Path, help="payload source for an installation preview")
    parser.add_argument("--workspace", type=Path, default=Path.home() / "agy-writer")
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    parser.add_argument("--agy-config", type=Path, default=Path.home() / ".gemini/config")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.skill, args.workspace, args.bin_dir, args.agy_config, args.apply, args.source), ensure_ascii=False))
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
