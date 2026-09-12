"""Chapter drafting and editing; the caller retains the Codex meaning review."""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
import time

from write_ja import atomic_write, read_text, save_json


def sections(packet):
    """Split H2 sections, keeping fenced examples and the shared preamble intact."""
    preamble, current, chunks = [], [], {}
    name = None
    fence = None
    for line in packet.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(\x60{3,}|~{3,})", line)
        if fence:
            match = re.match(r"^ {0,3}(" + re.escape(fence[0]) + r"+)[ \t]*\r?\n?$", line)
            if match and len(match[1]) >= fence[1]:
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
        else:
            heading = re.match(r"^ {0,3}##[ \t]+(.*?)[ \t]*\r?\n?$", line)
            if heading:
                if name is None:
                    preamble = current
                else:
                    chunks[name] = "".join(current)
                name = re.sub(r"[ \t]+#+[ \t]*$", "", heading[1]).strip()
                if not name or name in chunks:
                    raise ValueError("Chapter plans require unique, non-empty level-two headings")
                current = [line]
                continue
        current.append(line)
    if name is None:
        raise ValueError("A chapter plan requires level-two headings in the packet")
    chunks[name] = "".join(current)
    return "".join(preamble), chunks


def prepare(packet, plan):
    if plan is None:
        return None, [{"title": None, "sections": [], "material": packet, "shared": ""}]
    if not isinstance(plan, dict) or set(plan) - {"title", "shared", "chapters"}:
        raise ValueError("Plan fields: title, optional shared, chapters")
    title, shared, chapters = plan.get("title"), plan.get("shared", []), plan.get("chapters")
    if not isinstance(title, str) or not title.strip() or "\n" in title or "\r" in title:
        raise ValueError("Plan title must be a non-empty single line")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("Plan chapters must be a non-empty array")
    preamble, chunks = sections(packet)
    assigned = set()

    def select(names):
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            raise ValueError("Section selections must be arrays of heading names")
        material = []
        for name in names:
            if name not in chunks:
                raise ValueError(f"Unknown packet heading: {name}")
            if name in assigned:
                raise ValueError(f"Packet heading assigned more than once: {name}")
            assigned.add(name)
            material.append(chunks[name])
        return "\n".join(material)

    common = preamble + "\n" + select(shared)
    tasks = []
    for chapter in chapters:
        if not isinstance(chapter, dict) or set(chapter) - {"title", "sections", "target_chars"}:
            raise ValueError("Chapter fields: title, sections, optional target_chars")
        hint, names, target = chapter.get("title"), chapter.get("sections"), chapter.get("target_chars")
        if not isinstance(hint, str) or not hint.strip() or "\n" in hint or "\r" in hint:
            raise ValueError("Chapter title must be a non-empty single line")
        if not names:
            raise ValueError("Each chapter must select at least one packet section")
        if target is not None and (type(target) is not int or target <= 0):
            raise ValueError("target_chars must be a positive integer")
        tasks.append({"title": hint, "sections": names, "material": select(names),
                      "shared": common, "target_chars": target})
    missing = set(chunks) - assigned
    if missing:
        raise ValueError("Unassigned packet headings: " + ", ".join(sorted(missing)))
    return title.strip(), tasks


def execute_chapters(args, run, record, generate):
    if args.jobs <= 0:
        raise ValueError("jobs must be positive")
    packet = read_text(args.packet)
    plan = json.loads(read_text(args.plan)) if args.plan else None
    title, tasks = prepare(packet, plan)
    profile = read_text(args.profile) if args.profile else ""
    output = args.out.expanduser().resolve()
    inputs = [args.packet, args.plan, args.profile]
    if output.is_relative_to(run) or any(output == p.expanduser().resolve() for p in inputs if p):
        raise ValueError("Output must differ from inputs and be outside the run directory")
    previous = output.read_bytes() if output.exists() else None
    (run / "packet.md").write_bytes(packet.encode("utf-8"))
    if plan is not None:
        save_json(run / "plan.json", plan)
    record.update(output=str(output), status="running", chapters=len(tasks), review_mode="continue",
                  model_requested=args.model, effort="high")
    save_json(run / "run.json", record)

    def chapter(index, task):
        folder = run / f"chapter-{index + 1:02d}"
        folder.mkdir(mode=0o700)
        evidence = "共通の背景・制約:\n" + task["shared"] + "\n\nこの章で説明する材料:\n" + task["material"]
        (folder / "packet.md").write_bytes(evidence.encode("utf-8"))
        layout = ("文書全体の Markdown 本文を書いてください。" if task["title"] is None else
                  f"文書「{title}」の一章を書いてください。章題の案: {task['title']}。\n"
                  "先頭は ## の章見出しとし、章内の見出しには ### 以下を使ってください。章題も自然な日本語に整えてかまいません。")
        target = task.get("target_chars")
        length = (f"\nこの章は {target} 字程度を目安にします。" if target else "")
        common = ("この workspace の GEMINI.md に従ってください。材料はすべて以下にあり、ツールは不要です。\n"
                  + layout + length
                  + "\n目的、課題、解決方法、理由が自然につながる文章にしてください。章の材料をすべて説明し、"
                  "共通の背景は理解と条件の維持に使います。他の章の説明や前置き、章末の要約を繰り返して増やさないでください。"
                  "情報を削って短くせず、材料にない仕様を補わないでください。\n")
        if profile:
            common += "\n今回の追加の文体設定:\n" + profile + "\n"
        bodies, phases = {}, {}
        for phase in ("write", "edit"):
            destination = folder / phase
            destination.mkdir(mode=0o700)
            phase_record = {"operation": phase, "run_dir": str(destination)}
            if phase == "write":
                instruction = "以下の原資料から執筆してください。\n\n原資料:\n" + evidence
            else:
                instruction = (
                    "あなたは初稿の執筆者とは別の校閲者です。初稿は根拠として信用せず、原資料と照合して完成稿を返してください。\n"
                    "原資料から初稿へ条件・理由・例外・未決定事項の抜けを確認し、初稿から原資料へ仕様の追加、"
                    "対象の広がりや狭まり、断定の強まりを確認して直してください。文章の自然さは保ち、"
                    "重複する説明を整理してください。材料と同じ単語に戻すだけの校閲はしません。\n\n原資料:\n"
                    + evidence + "\n\n未確認の初稿:\n" + bodies["write"])
            prompt = common + instruction + "\n\n出力は完成した Markdown 本文だけです。作業報告や文書全体を囲むコードフェンスは付けません。"
            started = time.monotonic()
            try:
                body = generate(args, destination, phase_record, prompt)
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                raise ValueError(f"Chapter {index + 1} {phase} failed; inspect {destination}") from error
            (destination / "draft.md").write_bytes(body.encode("utf-8"))
            bodies[phase] = body
            phases[phase] = {"run_dir": str(destination), "characters": len(body),
                             "elapsed_seconds": round(time.monotonic() - started, 3)}
        result = {"title_hint": task["title"], "sections": task["sections"],
                  "target_chars": target, **phases}
        save_json(folder / "chapter.json", result)
        return bodies["edit"], result

    # Each writer/editor pair is isolated; preserve plan order despite completion order.
    with ThreadPoolExecutor(max_workers=min(args.jobs, len(tasks))) as pool:
        futures = [pool.submit(chapter, i, task) for i, task in enumerate(tasks)]
        results = [future.result() for future in futures]
    body = "\n\n".join(text.strip() for text, _ in results) + "\n"
    if title:
        body = f"# {title}\n\n" + body
    if (output.read_bytes() if output.exists() else None) != previous:
        raise ValueError("Output changed while Gemini was running; chapter results are saved")
    (run / "draft.md").write_bytes(body.encode("utf-8"))
    atomic_write(output, body)
    record.update(status="needs_review", chapter_results=[result for _, result in results])
    return 0
