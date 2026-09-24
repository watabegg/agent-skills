#!/usr/bin/env python3
"""Summarize eALPS/Moodle JSON evidence from browser automation runs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def load_jsons(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.glob("*.json") if p.name != "summary.json")


def classify(data: dict) -> str:
    href = data.get("finalUrl") or data.get("href") or ""
    text = data.get("text") or data.get("summary", {}).get("text") or ""
    if "/mod/quiz/" in href or "受験" in text or "受験概要" in text:
        return "quiz"
    if "/mod/assign/" in href or "提出ステータス" in text or "ファイル提出" in text:
        return "assignment"
    return "page"


def assignment_status(data: dict) -> dict:
    text = compact(data.get("text") or data.get("summary", {}).get("text") or "")
    filled = data.get("filled")
    file_match = re.search(r"ファイル提出\s+([^ ]+)", text)
    updated_match = re.search(r"最終更新日時\s+(.+?)(?:\s+ファイル提出|\s+オンラインテキスト|\s+提出コメント|$)", text)
    return {
        "ok": "評定のために提出済み" in text,
        "state": "submitted" if "評定のために提出済み" in text else "draft" if "下書き" in text else "not_submitted" if "未提出" in text else "unknown",
        "file": file_match.group(1) if file_match else "online-text" if "オンラインテキスト" in text else "",
        "updated": updated_match.group(1) if updated_match else "",
        "filled": filled if isinstance(filled, dict) else {},
    }


def quiz_status(data: dict) -> dict:
    text = compact(data.get("text") or data.get("summary", {}).get("text") or "")
    href = data.get("finalUrl") or data.get("href") or ""
    completed_match = re.search(r"完了日時\s+(.+?)(?:\s+継続時間|\s+問題|$)", text)
    return {
        "ok": "review.php" in href and "ステータス 終了" in text,
        "state": "completed" if "review.php" in href and "ステータス 終了" in text else "in_progress" if "進行中" in text else "unknown",
        "completed": completed_match.group(1) if completed_match else "",
        "review": "review.php" in href,
    }


def summarize(path: Path) -> list[dict]:
    rows = []
    for json_path in load_jsons(path):
        try:
            data = json.loads(json_path.read_text())
        except Exception as exc:
            rows.append({"file": json_path.name, "type": "error", "status": f"read_error:{exc}"})
            continue
        if not isinstance(data, dict) or not isinstance(data.get("summary", {}), dict):
            rows.append({"file": json_path.name, "type": "error", "status": "invalid_evidence"})
            continue
        kind = classify(data)
        name = data.get("name") or data.get("finalTitle") or data.get("title") or json_path.stem
        href = data.get("finalUrl") or data.get("href") or data.get("summary", {}).get("href") or ""
        if kind == "assignment":
            status = assignment_status(data)
            rows.append({
                "file": json_path.name,
                "type": kind,
                "name": name,
                "ok": status["ok"],
                "state": status["state"],
                "detail": status["file"],
                "time": status["updated"],
                "href": href,
            })
        elif kind == "quiz":
            status = quiz_status(data)
            rows.append({
                "file": json_path.name,
                "type": kind,
                "name": name,
                "ok": status["ok"],
                "state": status["state"],
                "detail": "review" if status["review"] else "not-review",
                "time": status["completed"],
                "href": href,
            })
        else:
            text = compact(data.get("text") or data.get("summary", {}).get("text") or "")
            rows.append({
                "file": json_path.name,
                "type": kind,
                "name": name,
                "ok": bool(text),
                "detail": text[:80],
                "time": "",
                "href": href,
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="JSON file or directory containing Moodle evidence JSON")
    parser.add_argument("--json", action="store_true", help="emit the legacy row array instead of a table")
    parser.add_argument("--verify", action="store_true", help="emit a status envelope; exit 2 when evidence is missing, 1 when invalid")
    args = parser.parse_args()

    try:
        rows = summarize(args.path)
    except (OSError, ValueError, TypeError, AttributeError):
        rows = [{"file": args.path.name, "type": "error", "status": "invalid_evidence"}]
    if args.verify:
        if not args.path.exists():
            status, reason, code = "needs_input", "missing_input", 2
        elif not rows:
            status, reason, code = "needs_input", "empty_evidence", 2
        elif any(row["type"] == "error" for row in rows):
            status, reason, code = "failed", "invalid_evidence", 1
        else:
            status, reason, code = "completed", "evidence_summarized", 0
        print(json.dumps({"status": status, "reason": reason, "changed": False,
                          "artifacts": [str(args.path.resolve())] if args.path.exists() else [],
                          "rows": rows}, ensure_ascii=False, indent=2))
        return code
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print("type\tok\tname\tdetail\ttime\tfile")
    for row in rows:
        print("\t".join(str(row.get(k, "")) for k in ["type", "ok", "name", "detail", "time", "file"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
