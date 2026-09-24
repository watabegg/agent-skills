#!/usr/bin/env python3
"""Install maintained personal skills and link their Claude counterparts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("agy-writer", "ealps-moodle-operator", "luna-impl", "pencil-pencli", "prepare-invoice-email", "prepare-pr",
          "semantic-commit-ja", "shinshu-portal-auth", "sync-teams-attendance")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def digest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def replace_skill(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".skill-install-", dir=target.parent) as temp:
        staging = Path(temp) / "new"
        backup = Path(temp) / "old"
        shutil.copytree(source, staging, ignore=IGNORE)
        if digest(source) != digest(staging):
            raise RuntimeError(f"Staging verification failed: {source.name}")
        had_target = target.exists() or target.is_symlink()
        if had_target:
            target.rename(backup)
        try:
            staging.rename(target)
        except BaseException:
            if had_target:
                backup.rename(target)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--shared-skills", type=Path, default=Path.home() / ".agents/skills")
    parser.add_argument("--claude-home", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--apply", action="store_true", help="install; without this, print the plan")
    parser.add_argument("--only", nargs="+", choices=SKILLS, help="sync only the named skills")
    parser.add_argument("--writer-runtime", action="store_true", help="also sync the agy-writer launcher and workspace")
    parser.add_argument("--writer-workspace", type=Path)
    parser.add_argument("--writer-bin-dir", type=Path)
    parser.add_argument("--agy-config", type=Path)
    args = parser.parse_args()
    if args.writer_runtime and "agy-writer" not in (args.only or SKILLS):
        parser.error("--writer-runtime requires agy-writer in the selected skills")
    rows = []
    for name in args.only or SKILLS:
        source = ROOT / "skills" / name
        if not (source / "SKILL.md").is_file():
            raise RuntimeError(f"Missing source skill: {name}")
        target = (args.shared_skills if name == "semantic-commit-ja" else args.codex_home / "skills") / name
        target = target.absolute()
        link = args.claude_home / "skills" / name
        if args.apply:
            replace_skill(source, target)
            link.parent.mkdir(parents=True, exist_ok=True)
            if not (link.is_symlink() and link.resolve() == target.resolve()):
                if link.is_symlink() or link.is_file():
                    link.unlink()
                elif link.exists():
                    shutil.rmtree(link)
                link.symlink_to(target)
            if digest(source) != digest(target) or link.resolve() != target.resolve():
                raise RuntimeError(f"Installed verification failed: {name}")
        rows.append({"skill": name, "source": str(source), "target": str(target), "claude": str(link)})
    result = {"applied": args.apply, "skills": rows}
    if args.writer_runtime:
        installed = args.codex_home / "skills/agy-writer"
        runner = installed if args.apply else ROOT / "skills/agy-writer"
        command = [sys.executable, str(runner / "scripts/install_runtime.py"), "--skill", str(installed)]
        if not args.apply:
            command.extend(["--source", str(runner)])
        for flag, value in [("--workspace", args.writer_workspace), ("--bin-dir", args.writer_bin_dir), ("--agy-config", args.agy_config)]:
            if value is not None:
                command.extend([flag, str(value)])
        if args.apply:
            command.append("--apply")
        output = subprocess.run(command, capture_output=True, text=True, check=True)
        result["writer_runtime"] = json.loads(output.stdout)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
