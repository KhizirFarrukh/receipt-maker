"""Render a Claude Code session log as a readable conversation.

Produces SESSION-TRANSCRIPT.md beside this file. Every user and assistant turn
in full; tool calls appear as one line each -- what was run and why -- but not
their output. The raw log is around 17 MB, nearly all of it file contents and
test results, and all of that is reconstructible from the commits. What is not
reconstructible is what was asked, what was decided, and what was rejected.

This shop's own identifying data is redacted in the same pass that writes the
file, so an unredacted copy never exists inside the repository. Commit f4e137b
took that data out of the tracked files deliberately, and a transcript is a
tracked file.

Usage:

    python claude_chat/export_conversation.py <session.jsonl> claude_chat/SESSION-TRANSCRIPT.md

The session logs live in %USERPROFILE%\\.claude\\projects\\<slugified project path>\\.
"""
import json
import os
import re
import sys
from datetime import datetime

# Longest / most specific first, so a broader rule never eats a narrower one.
# The middle rules catch the number written as a regex, e.g. "339 ?282 ?5523",
# which a literal match misses -- it did, on the first pass.
REDACTIONS = [
    (r"support@chawlatech\.pk", "[shop-email]"),
    (r"chawlatech\.pk", "[shop-domain]"),
    (r"\+92[^0-9]{0,4}339[^0-9]{0,4}282[^0-9]{0,4}5523", "[shop-phone]"),
    (r"339[^0-9]{0,4}282[^0-9]{0,4}5523", "[shop-phone]"),
    (r"339[^0-9]{0,4}282", "[shop-phone]"),
    (r"3392825523", "[shop-phone]"),
    (r"ChawlaTechSignature", "[shop]Signature"),
    (r"Chawla\s*Tech", "[shop]"),
    (r"chawlatech", "[shop]"),
    (r"\bChawla\b", "[shop]"),
]

#: Arguments worth showing for a tool call, in preference order.
SUMMARY_KEYS = ("description", "command", "file_path", "pattern", "prompt",
                "path", "skill", "url", "query")

MAX_SUMMARY = 160


def redact(text):
    for pattern, replacement in REDACTIONS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def ts(raw):
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(raw)


def one_line(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text[:MAX_SUMMARY] + ("…" if len(text) > MAX_SUMMARY else "")


def summarise_tool(block):
    name = block.get("name", "tool")
    args = block.get("input", {}) or {}
    if isinstance(args, dict):
        for key in SUMMARY_KEYS:
            if args.get(key):
                return "%s — %s" % (name, one_line(args[key]))
        if args:
            first = sorted(args)[0]
            return "%s — %s" % (name, one_line(args[first]))
    return name


def blocks_of(message):
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def main(log_path, dest):
    records = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue

    turns = [r for r in records if r.get("type") in ("user", "assistant")]
    stamps = [r.get("timestamp") for r in turns if r.get("timestamp")]
    branches = sorted({r.get("gitBranch") for r in turns if r.get("gitBranch")})

    out = ["# Session transcript — receipt-maker", ""]
    out.append("The conversation behind this branch, exported from the Claude Code session log.")
    out.append("")
    out.append("| | |")
    out.append("|---|---|")
    out.append("| Session | `%s` |" % (turns[0].get("sessionId", "") if turns else ""))
    out.append("| From | %s |" % ts(stamps[0] if stamps else ""))
    out.append("| To | %s |" % ts(stamps[-1] if stamps else ""))
    out.append("| Branches | %s |" % ", ".join("`%s`" % b for b in branches))
    out.append("")
    out.append("**What this is.** Every message in order, in full. Tool calls are listed by name "
               "and purpose; their output is not, because the raw log is 17 MB of file contents "
               "and test results and all of it is reconstructible from the commits. Claude's "
               "internal reasoning is not included.")
    out.append("")
    out.append("> **Redacted.** This shop's business name, domain, support address and phone "
               "number are replaced with `[shop]`, `[shop-domain]`, `[shop-email]` and "
               "`[shop-phone]`. Commit `f4e137b` took that data out of the tracked files on "
               "purpose, and a transcript is a tracked file. No key material or passphrase value "
               "appears anywhere in this log.")
    out.append("")

    users = assistants = tools = 0
    for record in records:
        if record.get("type") not in ("user", "assistant"):
            continue
        message = record.get("message") or {}
        role = message.get("role")

        texts, calls = [], []
        for block in blocks_of(message):
            kind = block.get("type")
            if kind == "text":
                body = (block.get("text") or "").strip()
                if body:
                    texts.append(body)
            elif kind == "tool_use":
                calls.append(summarise_tool(block))

        if not texts and not calls:
            continue

        side = " · subagent" if record.get("isSidechain") else ""
        who = "User" if role == "user" else "Claude"
        if role == "user":
            users += 1
        else:
            assistants += 1

        out.append("---")
        out.append("")
        out.append("### %s%s  <sub>%s</sub>" % (who, side, ts(record.get("timestamp"))))
        out.append("")
        for body in texts:
            out.append(body)
            out.append("")
        if calls:
            tools += len(calls)
            for call in calls:
                out.append("- `%s`" % call.replace("`", "'"))
            out.append("")

    text = redact("\n".join(out).rstrip() + "\n")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    # Re-scan the finished file rather than trusting the substitutions: a rule
    # that fires zero times is indistinguishable from a rule that is wrong.
    leaks = [p for p, _ in REDACTIONS if re.search(p, text, re.IGNORECASE)]
    print("user turns      : %d" % users)
    print("assistant turns : %d" % assistants)
    print("tool calls      : %d" % tools)
    print("size            : %.0f KB" % (os.path.getsize(dest) / 1024))
    print("residue         : %s" % (", ".join(leaks) if leaks else "none"))
    return 1 if leaks else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
