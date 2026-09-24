#!/usr/bin/env python3
"""Adapt RTK's Claude command rewrite to Codex PreToolUse output."""
import json
import subprocess
import sys


def adapt(payload, output):
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "PreToolUse" or payload.get("tool_name") != "Bash":
        return None
    command = payload.get("tool_input", {}).get("command")
    if not isinstance(command, str) or not command:
        return None
    if not isinstance(output, dict):
        return None
    hook = output.get("hookSpecificOutput", {})
    if not isinstance(hook, dict) or hook.get("hookEventName") != "PreToolUse":
        return None
    updated = hook.get("updatedInput", {})
    rewritten = updated.get("command") if isinstance(updated, dict) else None
    if not isinstance(rewritten, str) or not rewritten or rewritten == command:
        return None
    if hook.get("permissionDecision") not in (None, "allow"):
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "allow", "updatedInput": {"command": rewritten}}}


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "PreToolUse" or payload.get("tool_name") != "Bash":
            return
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
            return
        result = subprocess.run(["rtk", "hook", "claude"], input=json.dumps(payload),
                                text=True, capture_output=True, timeout=3)
        if result.returncode:
            print("RTK rewrite unavailable; command unchanged.", file=sys.stderr)
            return
        if result.stdout.strip():
            output = adapt(payload, json.loads(result.stdout))
            if output:
                print(json.dumps(output))
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        print("RTK rewrite unavailable; command unchanged.", file=sys.stderr)


if __name__ == "__main__":
    main()
