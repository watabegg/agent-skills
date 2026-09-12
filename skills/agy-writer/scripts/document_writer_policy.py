#!/usr/bin/env python3
"""Fail-closed tool policy for the dedicated Antigravity document writer."""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any


HOME_ROOT = Path.home().resolve()
TMP_ROOT = Path("/tmp").resolve()
COUNT_COMMANDS = {
    "./bin/agy-doc-count",
    str(Path(__file__).resolve().with_name("agy-doc-count")),
}

DOCUMENT_SUFFIXES = {".md", ".txt"}
READABLE_SUFFIXES = {
    ".adoc",
    ".bash",
    ".c",
    ".cc",
    ".cjs",
    ".cpp",
    ".cs",
    ".css",
    ".fish",
    ".go",
    ".gql",
    ".gradle",
    ".graphql",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsonc",
    ".jsx",
    ".kt",
    ".kts",
    ".lock",
    ".md",
    ".mjs",
    ".php",
    ".properties",
    ".proto",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}
READABLE_EXTENSIONLESS_FILES = {
    "Dockerfile",
    "Gemfile",
    "Justfile",
    "LICENSE",
    "Makefile",
    "Procfile",
    "Rakefile",
}
SENSITIVE_PARTS = {
    ".agents",
    ".aws",
    ".codex",
    ".gemini",
    ".git",
    ".gnupg",
    ".kube",
    ".ssh",
}
SENSITIVE_NAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "oauth_creds.json",
    "service-account.json",
}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
SHELL_METACHARACTERS = re.compile(r"[;&|><`$()\n\r]")
SAFE_INCLUDE_PATTERN = re.compile(
    r"^(?:\*\*/)?\*\.(?P<ext>[A-Za-z0-9]+)$"
)


def response(decision: str, reason: str) -> dict[str, str]:
    return {"decision": decision, "reason": reason}


def resolve_path(raw: Any, payload: dict[str, Any], cwd: Any = None) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        base_raw = cwd
        if not isinstance(base_raw, str) or not base_raw:
            workspaces = payload.get("workspacePaths") or []
            base_raw = workspaces[0] if workspaces else str(Path(__file__).resolve().parents[1])
        path = Path(base_raw).expanduser() / path
    try:
        return path.resolve(strict=False)
    except OSError:
        return None


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_sensitive(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    return (
        bool(lowered_parts & SENSITIVE_PARTS)
        or name == ".env"
        or name.startswith(".env.")
        or name in SENSITIVE_NAMES
        or path.suffix.lower() in SENSITIVE_SUFFIXES
    )


def safe_home_read(path: Path) -> bool:
    return is_within(path, HOME_ROOT) and not is_sensitive(path)


def safe_document(path: Path) -> bool:
    return (
        (is_within(path, HOME_ROOT) or is_within(path, TMP_ROOT))
        and not is_sensitive(path)
        and path.name not in {"AGENTS.md", "GEMINI.md"}
        and path.suffix.lower() in DOCUMENT_SUFFIXES
    )


def safe_search_includes(value: Any) -> bool:
    if isinstance(value, str):
        patterns = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        patterns = [item.strip() for item in value if item.strip()]
    else:
        return False
    if not patterns:
        return False
    for pattern in patterns:
        if pattern in READABLE_EXTENSIONLESS_FILES:
            continue
        match = SAFE_INCLUDE_PATTERN.fullmatch(pattern)
        if match is None or f".{match.group('ext').lower()}" not in READABLE_SUFFIXES:
            return False
    return True


def evaluate_read_tool(
    name: str, args: dict[str, Any], payload: dict[str, Any]
) -> dict[str, str]:
    path_keys = {
        "view_file": "AbsolutePath",
        "list_dir": "DirectoryPath",
        "find_by_name": "SearchDirectory",
        "grep_search": "SearchPath",
    }
    path = resolve_path(args.get(path_keys[name]), payload)
    if path is None or not safe_home_read(path):
        return response("deny", "読み取り対象が許可範囲外または機密パスです。")

    if name == "view_file" and path.is_file():
        if (
            path.suffix.lower() not in READABLE_SUFFIXES
            and path.name not in READABLE_EXTENSIONLESS_FILES
        ):
            return response("deny", "文書作成に必要なテキスト形式だけ読み取れます。")

    if name == "grep_search" and path.is_dir():
        if not safe_search_includes(args.get("Includes")):
            return response(
                "deny",
                "ディレクトリ検索には安全なテキスト拡張子の Includes 指定が必要です。",
            )

    return response("allow", "文書作成のための読み取り操作です。")


def evaluate_write_tool(
    args: dict[str, Any], payload: dict[str, Any]
) -> dict[str, str]:
    path = resolve_path(args.get("TargetFile"), payload)
    if path is not None and safe_document(path):
        return response("allow", ".md または .txt の文書編集です。")
    return response(
        "deny",
        "文書ライターは安全な場所にある .md と .txt だけ編集できます。",
    )


def evaluate_command(
    args: dict[str, Any], payload: dict[str, Any]
) -> dict[str, str]:
    command_line = args.get("CommandLine")
    if not isinstance(command_line, str) or SHELL_METACHARACTERS.search(command_line):
        return response("deny", "シェル構文または任意コマンドの実行は禁止されています。")
    try:
        tokens = shlex.split(command_line, posix=True)
    except ValueError:
        return response("deny", "コマンドを安全に解析できません。")
    if len(tokens) != 2 or tokens[0] not in COUNT_COMMANDS:
        return response("deny", "実行できるのは agy-doc-count のみです。")
    executable = resolve_path(tokens[0], payload, args.get("Cwd"))
    if executable != Path(__file__).resolve().with_name("agy-doc-count"):
        return response("deny", "管理された agy-doc-count だけ実行できます。")

    path = resolve_path(tokens[1], payload, args.get("Cwd"))
    if path is None or not safe_document(path):
        return response("deny", "文字数を確認できるのは .md と .txt だけです。")
    return response("allow", "固定の文字数確認コマンドです。")


def evaluate(payload: dict[str, Any]) -> dict[str, str]:
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return response("deny", "ツール呼び出し情報が不正です。")
    name = tool_call.get("name")
    args = tool_call.get("args") or {}
    if not isinstance(name, str) or not isinstance(args, dict):
        return response("deny", "ツール呼び出し情報が不正です。")

    if name in {"view_file", "list_dir", "find_by_name", "grep_search"}:
        return evaluate_read_tool(name, args, payload)
    if name in {
        "write_to_file",
        "replace_file_content",
        "multi_replace_file_content",
    }:
        return evaluate_write_tool(args, payload)
    if name == "run_command":
        return evaluate_command(args, payload)
    if name in {"ask_question", "list_permissions"}:
        return response("allow", "状態確認またはユーザーへの質問です。")
    return response("deny", "文書作成に不要なツールは無効化されています。")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = evaluate(payload)
    except Exception as exc:  # Fail closed if the hook itself receives bad input.
        result = response("deny", f"文書ライターポリシーの検証に失敗しました: {exc}")
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
