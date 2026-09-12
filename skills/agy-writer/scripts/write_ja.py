#!/usr/bin/env python3
"""Write Japanese documents with agy-writer; stdout is a compact result, not prose."""

import argparse
import difflib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timezone


MODEL = "gemini-3.8-flash-high"


def read_text(path):
    text = path.read_bytes().decode("utf-8")
    if not text.strip():
        raise ValueError(f"Empty input: {path}")
    return text


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(text.encode("utf-8"))
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def locate_findings(draft, findings):
    if not isinstance(findings, list) or not findings:
        raise ValueError("findings must be a non-empty JSON array")
    spans = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict) or set(finding) != {"before", "evidence", "issue"}:
            raise ValueError(f"Finding {index}: require before, evidence, issue")
        if any(not isinstance(v, str) or not v.strip() for v in finding.values()):
            raise ValueError(f"Finding {index}: fields must be non-empty strings")
        before = finding["before"]
        start = draft.find(before)
        if start < 0 or draft.find(before, start + 1) >= 0:
            raise ValueError(f"Finding {index}: before must occur exactly once")
        spans.append((start, start + len(before), index))
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise ValueError("Finding passages must not overlap")
    return spans


def decode_repair(body, count):
    """Accept one answer, including identical repeats from the CLI's finish events."""
    decoder = json.JSONDecoder()
    remaining = body.strip()
    if remaining.startswith("```json\n") and remaining.endswith("```"):
        remaining = remaining[8:-3].strip()
    elif remaining.startswith("```\n") and remaining.endswith("```"):
        remaining = remaining[4:-3].strip()
    answers = []
    while remaining:
        item, end = decoder.raw_decode(remaining)
        remaining = remaining[end:].strip()
        if not isinstance(item, dict):
            raise ValueError("Repair response must contain a JSON object")
        # Some agy versions append transport metadata to an otherwise identical answer.
        item = {key: value for key, value in item.items() if key not in {"toolAction", "toolSummary"}}
        if set(item) != {"replacements", "needs_input"} or not isinstance(item["needs_input"], str):
            raise ValueError("Invalid repair response fields")
        if not isinstance(item["replacements"], list):
            raise ValueError("replacements must be an array")
        answers.append(item)
    if not answers or any(answer != answers[0] for answer in answers[1:]):
        raise ValueError("Missing or conflicting repair answers; inspect response.json")
    answer = answers[0]
    if answer["needs_input"].strip():
        if answer["replacements"]:
            raise ValueError("needs_input response must not also provide replacements")
        return answer
    replacements = answer["replacements"]
    if len(replacements) != count:
        raise ValueError("Repair response has an unexpected number of replacements")
    indexed = {}
    for replacement in replacements:
        if not isinstance(replacement, dict) or set(replacement) != {"id", "text"}:
            raise ValueError("Each replacement must have id and text")
        index = replacement["id"]
        if type(index) is not int or index in indexed or not isinstance(replacement["text"], str):
            raise ValueError("Invalid or duplicate replacement id/text")
        indexed[index] = replacement["text"]
    if set(indexed) != set(range(count)):
        raise ValueError("Repair response has unexpected replacement ids")
    return {"replacements": indexed, "needs_input": ""}


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("draft", "repair"):
        command = commands.add_parser(operation)
        command.add_argument("--model", default=MODEL)
        command.add_argument("--timeout", type=int, default=1200, help="seconds; default 1200")
        command.add_argument("--writer", default="agy-writer", help="writer executable, without shell arguments")
        command.add_argument("--profile", type=Path, help="optional extra style instructions for this request")
        if operation == "draft":
            command.add_argument("--packet", type=Path, required=True)
            command.add_argument("--out", type=Path, required=True)
        else:
            command.add_argument("--run", type=Path, required=True)
            command.add_argument("--findings", type=Path, required=True)
    return parser


def execute(args, run, record):
    if args.timeout <= 0:
        raise ValueError("timeout must be positive")
    spans = []
    if args.operation == "draft":
        packet = read_text(args.packet)
        output = args.out.expanduser().resolve()
        if output == args.packet.resolve():
            raise ValueError("Output must differ from packet")
        previous = output.read_bytes() if output.exists() else None
        draft = ""
        findings = []
    else:
        base = json.loads((args.run / "run.json").read_text(encoding="utf-8"))
        if not isinstance(base, dict) or base.get("status") != "needs_review":
            raise ValueError("The base run did not produce a draft")
        if not isinstance(base.get("output"), str) or not base["output"]:
            raise ValueError("The base run has no output path")
        packet = read_text(args.run / "packet.md")
        draft = read_text(args.run / "draft.md")
        output = Path(base["output"])
        previous = output.read_bytes()
        if previous != draft.encode("utf-8"):
            raise ValueError("Output changed since the base run; do not repair an obsolete draft")
        findings = json.loads(args.findings.read_text(encoding="utf-8"))
        spans = locate_findings(draft, findings)
        save_json(run / "findings.json", findings)
        (run / "before.md").write_bytes(previous)
    if output.is_relative_to(run):
        raise ValueError("Output must be outside the run directory")
    record["output"] = str(output)
    (run / "packet.md").write_bytes(packet.encode("utf-8"))
    prompt = "この workspace の GEMINI.md に従って執筆してください。必要な材料はこの依頼に含まれています。ツールは不要です。"
    if args.profile:
        prompt += "\n今回の追加の語彙設定:\n" + read_text(args.profile)
    prompt += "\n\n原資料:\n" + packet
    if args.operation == "draft":
        prompt += "\n\n出力: 完成稿の Markdown 本文だけ。文書全体をコードブロックで囲まないでください。"
    else:
        prompt += "\n\n修正対象の原稿:\n" + draft
        prompt += "\n\n意味の指摘（配列の先頭を id=0 とする）:\n" + json.dumps(findings, ensure_ascii=False)
        prompt += """\n\n指摘された意味だけを直し、周囲の文体と構成を保ってください。置き換え後の文章はあなたが書いてください。
出力は次の JSON オブジェクト一つだけです。id は指摘の配列位置、text は before 全体の置き換えです。
{"replacements":[{"id":0,"text":"置き換える本文"}],"needs_input":""}
すべての id を一度ずつ含めます。原資料だけで修正できない場合は replacements を空配列にし、needs_input に不足を記載してください。
説明、コードフェンス、完了通知、JSON の繰り返しは不要です。"""
    (run / "prompt.md").write_text(prompt, encoding="utf-8")
    command = [args.writer, "--new-project", "--model", args.model, "--effort", "high",
               "--output-format", "json", "--print-timeout", f"{args.timeout}s", "-p", prompt]
    record.update(status="running", model_requested=args.model, effort="high")
    save_json(run / "run.json", record)
    with (run / "response.json").open("wb") as stdout, (run / "stderr.log").open("wb") as stderr:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                timeout=args.timeout + 15, check=False)
    if result.returncode:
        raise ValueError(f"agy-writer exited with {result.returncode}; inspect stderr.log")
    response = json.loads((run / "response.json").read_text(encoding="utf-8"))
    if not isinstance(response, dict) or response.get("status") != "SUCCESS":
        raise ValueError("agy-writer did not report SUCCESS; inspect response.json")
    body = response.get("response")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("agy-writer returned no document")
    record.update(conversation_id=response.get("conversation_id"), usage=response.get("usage"))
    if args.operation == "repair":
        answer = decode_repair(body, len(findings))
        if answer["needs_input"]:
            record.update(status="needs_input", reason=answer["needs_input"])
            return 2
        save_json(run / "replacements.json", answer["replacements"])
        body = draft
        for start, end, index in reversed(spans):
            body = body[:start] + answer["replacements"][index] + body[end:]
        changes = "".join(difflib.unified_diff(draft.splitlines(keepends=True), body.splitlines(keepends=True),
                                               fromfile="before.md", tofile="draft.md"))
        (run / "changes.diff").write_text(changes, encoding="utf-8")
        record["changes"] = str(run / "changes.diff")
    if not body.strip():
        raise ValueError("Result would be an empty document")
    # Another writer may have edited the same destination while Gemini was running.
    current = output.read_bytes() if output.exists() else None
    if current != previous:
        raise ValueError("Output changed while Gemini was running; generated response is saved")
    (run / "draft.md").write_bytes(body.encode("utf-8"))
    atomic_write(output, body)
    record["status"] = "needs_review"
    return 0


def main():
    args = make_parser().parse_args()
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "agy-writer/runs"
    state.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-"), dir=state))
    record = {"operation": args.operation, "run_dir": str(run), "status": "failed"}
    try:
        code = execute(args, run, record)
    except subprocess.TimeoutExpired:
        record.update(status="failed", error="agy-writer timed out; inspect the saved response and stderr.log")
        code = 1
    except (OSError, ValueError) as error:
        record.update(status="failed", error=str(error))
        code = 1
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_json(run / "run.json", record)
    fields = ("status", "operation", "output", "run_dir", "changes", "reason", "error")
    print(json.dumps({key: record[key] for key in fields if key in record}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
