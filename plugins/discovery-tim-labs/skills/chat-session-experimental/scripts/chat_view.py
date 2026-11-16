#!/usr/bin/env python3
"""Turn a Copilot chat transcript (.jsonl) into a single-page scrollable chat UI.

Usage: chat_view.py <events.jsonl> <out.html> [chatSessions answers file]
"""
import json
import os
import sys
from datetime import datetime, timezone

ARG_CAP = 2600
SUMMARY_KEYS = [
    "command", "filePath", "query", "path", "prompt", "description",
    "explanation", "newName", "symbol", "id", "urls", "file_text",
]


def load(path):
    """Read a JSONL file, skipping blank or partially written lines (live sessions)."""
    events = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and "type" in obj:
                obj.setdefault("data", {})
            events.append(obj)
    return events


def load_answers(path):
    """Pull questionCarousel answers out of the VS Code chatSessions store.

    Answers are keyed by "<toolCallId>:<questionIndex>".
    """
    if not path or not os.path.exists(path):
        return {}
    out = {}

    def walk(node):
        if isinstance(node, dict):
            if node.get("kind") == "questionCarousel" and node.get("data"):
                out.update(node["data"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    if path.endswith(".json"):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            try:
                walk(json.load(fh))
            except ValueError:
                pass
    else:
        walk(load(path))
    return out


def normalize_answer(raw):
    """Return (selected_list, freeform_str, answered_bool)."""
    if raw is None:
        return [], "", False
    if isinstance(raw, str):
        return [], raw, bool(raw.strip())
    if isinstance(raw, dict):
        sel = raw.get("selectedValues")
        if sel is None:
            one = raw.get("selectedValue")
            sel = [one] if one else []
        sel = [s for s in sel if s]
        free = raw.get("freeformValue") or ""
        return sel, free, bool(sel or free.strip())
    return [], str(raw), True


def is_system_user_msg(text):
    t = text.lstrip()
    return t.startswith("[Terminal ") or t.startswith("Continue: ")


def tool_summary(name, args):
    if not isinstance(args, dict):
        return ""
    if name == "run_in_terminal":
        cmd = str(args.get("command", "")).strip()
        cmd = cmd.replace("clear; ", "", 1).strip()
        return cmd.splitlines()[0] if cmd else args.get("explanation", "")
    if name in ("read_file",):
        fp = args.get("filePath", "")
        s = os.path.basename(str(fp))
        if "startLine" in args:
            s += "  L%s-%s" % (args.get("startLine"), args.get("endLine"))
        return s
    if name in ("create_file", "replace_string_in_file", "get_errors", "view_image"):
        fp = args.get("filePath") or args.get("filePaths") or ""
        if isinstance(fp, list):
            return ", ".join(os.path.basename(str(x)) for x in fp)
        return os.path.basename(str(fp))
    if name == "multi_replace_string_in_file":
        reps = args.get("replacements") or []
        names = []
        for r in reps:
            if isinstance(r, dict) and r.get("filePath"):
                b = os.path.basename(r["filePath"])
                if b not in names:
                    names.append(b)
        return "%d edit(s): %s" % (len(reps), ", ".join(names))
    if name == "manage_todo_list":
        todos = args.get("todoList") or []
        done = sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "completed")
        return "%d items, %d completed" % (len(todos), done)
    if name == "vscode_askQuestions":
        qs = args.get("questions") or []
        if qs and isinstance(qs[0], dict):
            return qs[0].get("question", "")
        return "%d question(s)" % len(qs)
    for key in SUMMARY_KEYS:
        if key in args and isinstance(args[key], (str, int, float)):
            return str(args[key]).splitlines()[0] if str(args[key]) else ""
        if key in args and isinstance(args[key], list):
            return ", ".join(str(x) for x in args[key])[:180]
    for v in args.values():
        if isinstance(v, str) and v.strip():
            return v.strip().splitlines()[0]
    return ", ".join(args.keys())


def pretty_args(args):
    try:
        txt = json.dumps(args, indent=2, ensure_ascii=False)
    except Exception:
        txt = str(args)
    if len(txt) > ARG_CAP:
        txt = txt[:ARG_CAP] + "\n… (truncated)"
    return txt


def build(events, answers, source=""):
    events = [e for e in events if isinstance(e, dict) and "type" in e]
    meta_ev = next((e for e in events if e["type"] == "session.start"), None)
    meta_src = meta_ev["data"] if meta_ev else {}

    tool_ok = {}
    for e in events:
        if e["type"] == "tool.execution_complete":
            tool_ok[e["data"].get("toolCallId")] = bool(e["data"].get("success"))

    items = []
    block = None
    n_answered = 0

    def flush():
        nonlocal block
        if block and block["parts"]:
            items.append(block)
        block = None

    def ensure_block(ts):
        nonlocal block
        if block is None:
            block = {"kind": "assistant", "ts": ts, "parts": []}
        return block

    def emit_ask(call_id, args, ts):
        """Render vscode_askQuestions as a question card plus a real answer bubble."""
        nonlocal n_answered
        qs = (args or {}).get("questions") or []
        cards, replies = [], []
        for i, q in enumerate(qs):
            if not isinstance(q, dict):
                continue
            opts = []
            for o in (q.get("options") or []):
                if isinstance(o, dict):
                    opts.append({
                        "label": o.get("label", ""),
                        "description": o.get("description", ""),
                        "recommended": bool(o.get("recommended")),
                    })
                else:
                    opts.append({"label": str(o), "description": "", "recommended": False})
            sel, free, answered = normalize_answer(answers.get("%s:%d" % (call_id, i)))
            for o in opts:
                o["selected"] = o["label"] in sel
            cards.append({
                "header": q.get("header", ""),
                "question": q.get("question", ""),
                "message": q.get("message", ""),
                "options": opts,
                "multi": bool(q.get("multiSelect")),
                "answered": answered,
            })
            if answered:
                n_answered += 1
                replies.append({
                    "header": q.get("header", ""),
                    "question": q.get("question", ""),
                    "selected": sel,
                    "freeform": free,
                })
        if not cards:
            return
        items.append({"kind": "ask", "ts": ts, "questions": cards})
        if replies:
            items.append({"kind": "answer", "ts": ts, "replies": replies})

    for e in events:
        typ = e["type"]
        ts = e.get("timestamp", "")
        if typ == "user.message":
            flush()
            text = e["data"].get("content", "")
            items.append({
                "kind": "user",
                "role": "system" if is_system_user_msg(text) else "human",
                "ts": ts,
                "text": text,
            })
        elif typ == "assistant.message":
            d = e["data"]
            reasoning = (d.get("reasoningText") or "").strip()
            content = (d.get("content") or "").strip()
            if reasoning:
                ensure_block(ts)["parts"].append({"t": "reasoning", "text": reasoning})
            if content:
                ensure_block(ts)["parts"].append({"t": "text", "text": content})
        elif typ == "tool.execution_start":
            d = e["data"]
            name = d.get("toolName", "tool")
            args = d.get("arguments")
            if name == "vscode_askQuestions":
                flush()
                emit_ask(d.get("toolCallId"), args, ts)
                continue
            ensure_block(ts)["parts"].append({
                "t": "tool",
                "name": name,
                "summary": tool_summary(name, args)[:260],
                "args": pretty_args(args),
                "ok": tool_ok.get(d.get("toolCallId"), None),
            })
    flush()

    times = [e["timestamp"] for e in events if e.get("timestamp")]
    start, end = (times[0], times[-1]) if times else ("", "")

    def parse(t):
        try:
            return datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None

    dur = ""
    a, b = parse(start), parse(end)
    if a and b:
        mins = int((b - a).total_seconds() // 60)
        if mins >= 48 * 60:
            dur = "%dd" % round(mins / 1440)
        else:
            dur = "%dh %02dm" % (mins // 60, mins % 60)

    n_tools = sum(1 for e in events if e["type"] == "tool.execution_start")
    n_fail = sum(1 for e in events if e["type"] == "tool.execution_complete"
                 and not e["data"].get("success"))
    n_typed = sum(1 for i in items if i["kind"] == "user" and i["role"] == "human")
    n_asks = sum(len(i["questions"]) for i in items if i["kind"] == "ask")
    n_turns = sum(1 for e in events if e["type"] == "assistant.turn_start")

    meta = {
        "sessionId": meta_src.get("sessionId", ""),
        "copilotVersion": meta_src.get("copilotVersion", ""),
        "vscodeVersion": meta_src.get("vscodeVersion", ""),
        "date": start[:10],
        "endDate": end[:10],
        "start": start[11:16],
        "end": end[11:16],
        "duration": dur,
        "tools": n_tools,
        "toolFailures": n_fail,
        "typedMessages": n_typed,
        "questions": n_asks,
        "answers": n_answered,
        "humanMessages": n_typed + n_answered,
        "turns": n_turns,
        "source": source,
    }
    return {"meta": meta, "items": items}


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<script>
  (() => {
    const param = new URLSearchParams(window.location.search).get("clawpilotTheme");
    document.documentElement.setAttribute("data-theme", param || "discovery");
  })();
</script>
<style>
:root {
  color-scheme: light;
  --cp-bg: #f7f4ef;
  --cp-bg-elevated: #fcfbf8;
  --cp-surface: #ffffff;
  --cp-surface-soft: #f5f5f5;
  --cp-border: #dedede;
  --cp-border-strong: #919191;
  --cp-text: #242424;
  --cp-text-muted: #5c5c5c;
  --cp-text-soft: #6f6f6f;
  --cp-accent: #b11f4b;
  --cp-accent-hover: #9a1a41;
  --cp-accent-soft: rgba(177, 31, 75, 0.08);
  --cp-accent-fg: #ffffff;
  --cp-success: #16a34a;
  --cp-danger: #dc2626;
  --cp-warning: #f59e0b;
  --cp-link: #0078d4;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.12);
  --cp-overlay: rgba(255, 255, 255, 0.8);
  --cp-panel: rgba(255, 255, 255, 0.86);
  --cp-panel-strong: rgba(255, 255, 255, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.55);
  --cp-highlight: rgba(177, 31, 75, 0.12);
  --cp-on-accent-soft: rgba(255, 255, 255, 0.18);
  --cp-on-accent-border: rgba(255, 255, 255, 0.42);
  --cp-on-accent-rule: rgba(255, 255, 255, 0.24);
  --cp-user-bg: var(--cp-accent);
  --cp-user-fg: var(--cp-accent-fg);
  --cp-user-border: transparent;
  --cp-user-dot: var(--cp-accent-fg);
}
html[data-theme="discovery"] {
  color-scheme: dark;
  --cp-bg: #042027;
  --cp-bg-elevated: #06282f;
  --cp-surface: #07303a;
  --cp-surface-soft: #0a3944;
  --cp-border: #14454f;
  --cp-border-strong: #2c6874;
  --cp-text: #e8f3f4;
  --cp-text-muted: #9db6bb;
  --cp-text-soft: #7b979d;
  --cp-accent: #4fd6c6;
  --cp-accent-hover: #6fe3d5;
  --cp-accent-soft: rgba(79, 214, 198, 0.13);
  --cp-accent-fg: #042027;
  --cp-success: #4ade80;
  --cp-danger: #f87171;
  --cp-warning: #fbbf24;
  --cp-link: #6fd7ff;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.42);
  --cp-overlay: rgba(4, 32, 39, 0.88);
  --cp-panel: rgba(6, 40, 47, 0.74);
  --cp-panel-strong: rgba(5, 36, 43, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.04);
  --cp-highlight: rgba(79, 214, 198, 0.12);
  --cp-on-accent-soft: rgba(79, 214, 198, 0.16);
  --cp-on-accent-border: rgba(79, 214, 198, 0.55);
  --cp-on-accent-rule: #1d5c68;
  --cp-user-bg: #0d3c46;
  --cp-user-fg: #eaf6f7;
  --cp-user-border: #1d5c68;
  --cp-user-dot: var(--cp-accent);
}
html[data-theme="dark"] {
  color-scheme: dark;
  --cp-bg: #3d3b3a;
  --cp-bg-elevated: #343231;
  --cp-surface: #292929;
  --cp-surface-soft: #2e2e2e;
  --cp-border: #474747;
  --cp-border-strong: #5f5f5f;
  --cp-text: #dedede;
  --cp-text-muted: #919191;
  --cp-text-soft: #b0b0b0;
  --cp-accent: #fd8ea1;
  --cp-accent-hover: #fb7b91;
  --cp-accent-soft: rgba(253, 142, 161, 0.14);
  --cp-accent-fg: #1a1a1a;
  --cp-success: #4ade80;
  --cp-danger: #f87171;
  --cp-warning: #fbbf24;
  --cp-link: #4da6ff;
  --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.32);
  --cp-overlay: rgba(41, 41, 41, 0.88);
  --cp-panel: rgba(41, 41, 41, 0.72);
  --cp-panel-strong: rgba(41, 41, 41, 0.96);
  --cp-sheen: rgba(255, 255, 255, 0.04);
  --cp-highlight: rgba(253, 142, 161, 0.12);
  --cp-on-accent-soft: rgba(26, 26, 26, 0.16);
  --cp-on-accent-border: rgba(26, 26, 26, 0.3);
  --cp-on-accent-rule: rgba(26, 26, 26, 0.22);
}

* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background: var(--cp-bg);
  color: var(--cp-text);
  font-family: "Segoe UI", Aptos, Calibri, -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 15px;
  line-height: 1.55;
}
code, pre, .mono { font-family: Consolas, "Courier New", Courier, monospace; }

/* ---------- layout ---------- */
.app { display: grid; grid-template-columns: 272px 1fr; height: 100vh; }
.sidebar {
  border-right: 1px solid var(--cp-border);
  background: var(--cp-bg-elevated);
  display: flex; flex-direction: column; min-height: 0;
}
.main { display: flex; flex-direction: column; min-height: 0; min-width: 0; overflow: hidden; }

/* ---------- header ---------- */
.topbar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 14px 24px;
  border-bottom: 1px solid var(--cp-border);
  background: var(--cp-panel-strong);
  backdrop-filter: blur(8px);
  position: sticky; top: 0; z-index: 5;
}
.topbar > .title { min-width: 0; margin-right: 8px; }
.topbar h1 { font-size: 1rem; margin: 0; font-weight: 600; letter-spacing: .01em; }
.topbar .sub { font-size: .78rem; color: var(--cp-text-muted); }
.spacer { flex: 1; }
.chip-btn {
  border: 1px solid var(--cp-border);
  background: var(--cp-surface);
  color: var(--cp-text-muted);
  border-radius: 0.625rem;
  padding: 5px 11px;
  font-size: .78rem;
  cursor: pointer;
  font-family: inherit;
  white-space: nowrap;
}
.chip-btn:hover { border-color: var(--cp-border-strong); color: var(--cp-text); }
.chip-btn[aria-pressed="true"] {
  background: var(--cp-accent-soft);
  border-color: var(--cp-accent);
  color: var(--cp-accent);
}

/* ---------- sidebar ---------- */
.side-head { padding: 18px 20px 12px; border-bottom: 1px solid var(--cp-border); }
.side-title { font-size: .72rem; text-transform: uppercase; letter-spacing: .09em; color: var(--cp-text-soft); }
.stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 8px; margin-top: 12px; }
.stat .n { font-size: 1.15rem; font-weight: 600; }
.stat .l { font-size: .68rem; color: var(--cp-text-soft); text-transform: uppercase; letter-spacing: .06em; }
.side-scroll { overflow-y: auto; padding: 10px 12px 28px; }
.nav-item {
  display: block; width: 100%; text-align: left;
  border: 1px solid transparent; background: none; color: var(--cp-text-muted);
  border-radius: 0.625rem; padding: 8px 10px; margin-bottom: 2px;
  font: inherit; font-size: .8rem; cursor: pointer; line-height: 1.35;
}
.nav-item:hover { background: var(--cp-surface); color: var(--cp-text); }
.nav-item.active { background: var(--cp-accent-soft); color: var(--cp-accent); border-color: var(--cp-accent); }
.nav-item .t { font-size: .68rem; color: var(--cp-text-soft); display: block; }
.nav-item.active .t { color: var(--cp-accent); opacity: .8; }
.nav-item.ask { border-left: 2px solid var(--cp-accent); border-radius: 0 0.625rem 0.625rem 0; }

/* ---------- chat ---------- */
.scroll { overflow-y: auto; scroll-behavior: smooth; flex: 1; }
.thread { max-width: 900px; margin: 0 auto; padding: 28px 28px 55vh; }

.row { display: flex; margin-bottom: 22px; }
.row.user { justify-content: flex-end; }

.bubble {
  max-width: 86%;
  border-radius: 16px;
  padding: 14px 18px;
  box-shadow: 0 0 2px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.14);
}
.bubble.user {
  background: var(--cp-user-bg);
  color: var(--cp-user-fg);
  border: 1px solid var(--cp-user-border);
  border-bottom-right-radius: 6px;
}
.bubble.user a { color: var(--cp-user-fg); }
.bubble.assistant {
  background: var(--cp-surface);
  border: 1px solid var(--cp-border);
  border-bottom-left-radius: 6px;
  max-width: 100%;
  width: 100%;
}
.bubble.system {
  background: var(--cp-surface-soft);
  border: 1px dashed var(--cp-border-strong);
  color: var(--cp-text-muted);
  border-radius: 12px;
  font-size: .85rem;
  max-width: 78%;
}

.who {
  display: flex; align-items: center; gap: 8px;
  font-size: .7rem; text-transform: uppercase; letter-spacing: .08em;
  color: var(--cp-text-soft); margin-bottom: 8px;
}
.bubble.user .who { color: var(--cp-user-fg); opacity: .78; justify-content: flex-end; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--cp-accent); }
.bubble.user .dot { background: var(--cp-user-dot); }

.md > *:first-child { margin-top: 0; }
.md > *:last-child { margin-bottom: 0; }
.md h1, .md h2, .md h3 { font-size: 1rem; margin: 16px 0 8px; font-weight: 600; }
.md h1 { font-size: 1.12rem; }
.md p { margin: 0 0 10px; }
.md ul, .md ol { margin: 0 0 10px; padding-left: 22px; }
.md li { margin: 3px 0; }
.md a { color: var(--cp-link); }
.md code {
  background: var(--cp-surface-soft);
  border: 1px solid var(--cp-border);
  border-radius: 5px; padding: 1px 5px; font-size: .88em;
}
.bubble.user .md code { background: var(--cp-on-accent-soft); border-color: var(--cp-on-accent-border); }
.md pre {
  background: var(--cp-bg-elevated);
  border: 1px solid var(--cp-border);
  border-radius: 0.625rem;
  padding: 12px 14px; overflow-x: auto; margin: 0 0 10px; font-size: .84rem;
}
.md pre code { background: none; border: 0; padding: 0; font-size: inherit; }
.md blockquote {
  margin: 0 0 10px; padding: 2px 0 2px 14px;
  border-left: 3px solid var(--cp-border-strong); color: var(--cp-text-muted);
}
.md hr { border: 0; border-top: 1px solid var(--cp-border); margin: 14px 0; }
.md table { border-collapse: collapse; margin: 0 0 10px; font-size: .86rem; width: 100%; }
.md th, .md td { border: 1px solid var(--cp-border); padding: 5px 9px; text-align: left; }
.md th { background: var(--cp-surface-soft); }

/* ---------- parts ---------- */
.part { margin-bottom: 10px; }
.part:last-child { margin-bottom: 0; }

details.reason, details.tool { border-radius: 0.625rem; }
details.reason {
  background: var(--cp-accent-soft);
  border: 1px solid var(--cp-border);
  padding: 8px 12px;
}
details.reason > summary {
  cursor: pointer; list-style: none; font-size: .76rem;
  color: var(--cp-text-muted); text-transform: uppercase; letter-spacing: .07em;
}
details.reason > summary::-webkit-details-marker { display: none; }
details.reason > summary::before { content: "▸ "; }
details.reason[open] > summary::before { content: "▾ "; }
details.reason .body {
  margin-top: 8px; font-size: .88rem; color: var(--cp-text-muted);
  white-space: pre-wrap; border-top: 1px solid var(--cp-border); padding-top: 8px;
}

details.tool {
  border: 1px solid var(--cp-border);
  background: var(--cp-bg-elevated);
  padding: 0;
}
details.tool > summary {
  cursor: pointer; list-style: none;
  display: flex; align-items: center; gap: 10px;
  padding: 7px 11px; font-size: .8rem;
}
details.tool > summary::-webkit-details-marker { display: none; }
details.tool:hover { border-color: var(--cp-border-strong); }
.tname {
  font-family: Consolas, "Courier New", Courier, monospace;
  font-size: .76rem; color: var(--cp-accent);
  background: var(--cp-accent-soft); border: 1px solid var(--cp-accent-soft);
  border-radius: 5px; padding: 1px 7px; white-space: nowrap;
}
.targ {
  color: var(--cp-text-muted); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; flex: 1; min-width: 0;
  font-family: Consolas, "Courier New", Courier, monospace; font-size: .76rem;
}
.tstat { font-size: .95rem; line-height: 1; }
.tstat.ok { color: var(--cp-success); }
.tstat.bad { color: var(--cp-danger); }
details.tool pre {
  margin: 0; border-top: 1px solid var(--cp-border);
  padding: 11px 13px; font-size: .76rem; overflow-x: auto;
  color: var(--cp-text-muted); white-space: pre-wrap; word-break: break-word;
}

.toolstack { display: flex; flex-direction: column; gap: 5px; }

/* ---------- ask / answer ---------- */
.bubble.ask {
  background: var(--cp-surface);
  border: 1px solid var(--cp-accent);
  border-bottom-left-radius: 6px;
  max-width: 100%; width: 100%;
  box-shadow: 0 0 0 4px var(--cp-accent-soft);
}
.qcard + .qcard { margin-top: 14px; border-top: 1px solid var(--cp-border); padding-top: 14px; }
.qhdr {
  display: inline-block; font-size: .66rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--cp-accent);
  background: var(--cp-accent-soft); border-radius: 999px;
  padding: 2px 10px; margin-bottom: 8px;
}
.qtext { font-size: 1.02rem; font-weight: 600; margin-bottom: 6px; line-height: 1.4; }
.qmsg { font-size: .88rem; color: var(--cp-text-muted); }
.qmsg > *:last-child { margin-bottom: 0; }

.opts { list-style: none; margin: 12px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.opt {
  display: flex; align-items: flex-start; gap: 9px;
  border: 1px solid var(--cp-border); border-radius: 0.625rem;
  padding: 7px 11px; font-size: .88rem; color: var(--cp-text-muted);
  background: var(--cp-bg-elevated);
}
.opt .mark {
  flex: none; width: 15px; height: 15px; margin-top: 2px;
  border: 1.5px solid var(--cp-border-strong); border-radius: 50%;
}
.opt.multi .mark { border-radius: 4px; }
.opt.sel {
  border-color: var(--cp-accent); background: var(--cp-accent-soft);
  color: var(--cp-text); font-weight: 600;
}
.opt.sel .mark {
  border-color: var(--cp-accent); background: var(--cp-accent);
  box-shadow: inset 0 0 0 2.5px var(--cp-surface);
}
.opt .rec {
  font-size: .64rem; text-transform: uppercase; letter-spacing: .08em;
  color: var(--cp-text-soft); border: 1px solid var(--cp-border);
  border-radius: 999px; padding: 0 7px; margin-left: auto; flex: none;
  align-self: center; font-weight: 400;
}
.opt .od { display: block; font-weight: 400; font-size: .8rem; color: var(--cp-text-soft); }
.more-opts {
  border: 0; background: none; color: var(--cp-text-soft);
  font: inherit; font-size: .78rem; cursor: pointer; padding: 4px 2px; text-align: left;
}
.more-opts:hover { color: var(--cp-accent); }
.noans {
  margin-top: 10px; font-size: .74rem; color: var(--cp-text-soft);
  border: 1px dashed var(--cp-border-strong); border-radius: 0.625rem;
  padding: 5px 10px; display: inline-block;
}

.bubble.answer {
  background: var(--cp-user-bg); color: var(--cp-user-fg);
  border: 1px solid var(--cp-user-border);
  border-bottom-right-radius: 6px;
}
.bubble.answer .who { color: var(--cp-user-fg); opacity: .78; justify-content: flex-end; }
.bubble.answer .dot { background: var(--cp-user-dot); }
.areply + .areply { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--cp-on-accent-rule); }
.aq { font-size: .72rem; opacity: .75; margin-bottom: 4px; }
.pick {
  display: inline-block; margin: 0 6px 5px 0; padding: 3px 11px;
  border: 1px solid var(--cp-on-accent-border); border-radius: 999px;
  background: var(--cp-on-accent-soft); font-size: .88rem; font-weight: 600;
}
.free { margin-top: 4px; }
.free .md p { margin-bottom: 6px; }

.time { font-size: .68rem; color: var(--cp-text-soft); }
.bubble.user .time { color: var(--cp-user-fg); opacity: .7; }

.endcap {
  text-align: center; color: var(--cp-text-soft); font-size: .78rem;
  padding: 18px 0 8px; border-top: 1px solid var(--cp-border); margin-top: 16px;
}
.daysep {
  display: flex; align-items: center; gap: 12px;
  margin: 6px 0 22px; color: var(--cp-text-soft);
  font-size: .7rem; text-transform: uppercase; letter-spacing: .09em;
}
.daysep::before, .daysep::after {
  content: ""; flex: 1; height: 1px; background: var(--cp-border);
}

/* progress */
.progress { height: 2px; background: var(--cp-accent); width: 0; position: sticky; top: 0; z-index: 6; }

@media (max-width: 880px) {
  .app { grid-template-columns: 1fr; }
  .sidebar { display: none; }
  .thread { padding: 18px 14px 50vh; }
}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="side-head">
      <div class="side-title">Session</div>
      <div style="font-size:.86rem;margin-top:4px" id="m-date"></div>
      <div class="mono" style="font-size:.68rem;color:var(--cp-text-soft);word-break:break-all" id="m-id"></div>
      <div class="stats">
        <div class="stat"><div class="n" id="m-human"></div><div class="l">Your inputs</div></div>
        <div class="stat"><div class="n" id="m-ask"></div><div class="l">Questions</div></div>
        <div class="stat"><div class="n" id="m-tools"></div><div class="l">Tool calls</div></div>
        <div class="stat"><div class="n" id="m-dur"></div><div class="l">Elapsed</div></div>
      </div>
    </div>
    <div class="side-scroll" id="nav"></div>
  </aside>

  <div class="main">
    <div class="topbar">
      <div class="title">
        <h1>__TITLE__</h1>
        <div class="sub" id="m-sub"></div>
      </div>
      <div class="spacer"></div>
      <button class="chip-btn" id="t-reason" aria-pressed="false">Thinking</button>
      <button class="chip-btn" id="t-tools" aria-pressed="true">Tools</button>
      <button class="chip-btn" id="t-system" aria-pressed="false">System noise</button>
      <button class="chip-btn" id="t-theme">Theme</button>
    </div>
    <div class="progress" id="progress"></div>
    <div class="scroll" id="scroll">
      <div class="thread" id="thread"></div>
    </div>
  </div>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);

/* ---------------- markdown ---------------- */
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, (m, c) => "<code>" + c + "</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function md(src) {
  const blocks = [];
  let text = src.replace(/```([\w+-]*)\n?([\s\S]*?)```/g, (m, lang, code) => {
    blocks.push('<pre><code>' + esc(code.replace(/\n$/, "")) + "</code></pre>");
    return "\u0000B" + (blocks.length - 1) + "\u0000";
  });
  text = esc(text);
  const lines = text.split("\n");
  const out = [];
  let list = null, para = [], table = null;

  const flushPara = () => {
    if (para.length) { out.push("<p>" + inline(para.join("\n")).replace(/\n/g, "<br />") + "</p>"); para = []; }
  };
  const flushList = () => {
    if (list) { out.push("<" + list.tag + ">" + list.items.map(i => "<li>" + inline(i) + "</li>").join("") + "</" + list.tag + ">"); list = null; }
  };
  const flushTable = () => {
    if (!table) return;
    const cells = r => r.replace(/^\||\|$/g, "").split("|").map(c => c.trim());
    let html = "<table><thead><tr>" + cells(table[0]).map(c => "<th>" + inline(c) + "</th>").join("") + "</tr></thead><tbody>";
    for (let i = 2; i < table.length; i++) {
      html += "<tr>" + cells(table[i]).map(c => "<td>" + inline(c) + "</td>").join("") + "</tr>";
    }
    out.push(html + "</tbody></table>");
    table = null;
  };
  const flushAll = () => { flushPara(); flushList(); flushTable(); };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const raw = line.trim();
    if (/^\u0000B\d+\u0000$/.test(raw)) { flushAll(); out.push(raw); continue; }
    if (!raw) { flushAll(); continue; }
    if (table) {
      if (/^\|/.test(raw)) { table.push(raw); continue; }
      flushTable();
    }
    if (/^\|.*\|$/.test(raw) && i + 1 < lines.length && /^\|[\s:|-]+\|$/.test(lines[i + 1].trim())) {
      flushPara(); flushList(); table = [raw, lines[i + 1].trim()]; i++; continue;
    }
    let m;
    if ((m = raw.match(/^(#{1,6})\s+(.*)$/))) {
      flushAll();
      const lvl = Math.min(m[1].length, 3);
      out.push("<h" + lvl + ">" + inline(m[2]) + "</h" + lvl + ">");
      continue;
    }
    if (/^([-*_])\1{2,}$/.test(raw)) { flushAll(); out.push("<hr />"); continue; }
    if ((m = raw.match(/^&gt;\s?(.*)$/))) {
      flushPara(); flushList();
      out.push("<blockquote>" + inline(m[1]) + "</blockquote>");
      continue;
    }
    if ((m = raw.match(/^[-*+]\s+(.*)$/))) {
      flushPara(); flushTable();
      if (!list || list.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; }
      list.items.push(m[1]); continue;
    }
    if ((m = raw.match(/^\d+[.)]\s+(.*)$/))) {
      flushPara(); flushTable();
      if (!list || list.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; }
      list.items.push(m[1]); continue;
    }
    if (list) { list.items[list.items.length - 1] += " " + raw; continue; }
    para.push(raw);
  }
  flushAll();
  return out.join("\n").replace(/\u0000B(\d+)\u0000/g, (m, i) => blocks[+i]);
}

/* ---------------- render ---------------- */
const hhmm = ts => (ts || "").slice(11, 16);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};

const thread = document.getElementById("thread");
const nav = document.getElementById("nav");
const anchors = [];

function firstLine(t, n) {
  const s = t.trim().split("\n").find(x => x.trim()) || t.trim();
  return s.length > n ? s.slice(0, n) + "…" : s;
}

let lastDay = null;
function dayLabel(d) {
  const dt = new Date(d + "T00:00:00Z");
  return dt.toLocaleDateString(undefined, {
    weekday: "long", year: "numeric", month: "long", day: "numeric", timeZone: "UTC"
  });
}

DATA.items.forEach((item, idx) => {
  const day = (item.ts || "").slice(0, 10);
  if (day && day !== lastDay) {
    lastDay = day;
    thread.appendChild(el("div", "daysep", esc(dayLabel(day))));
  }
  const row = el("div", "row " + (item.kind === "user" ? "user" : "assistant"));
  row.dataset.index = idx;
  if (item.kind === "user") row.dataset.role = item.role;

  if (item.kind === "ask") {
    const b = el("div", "bubble ask");
    b.appendChild(el("div", "who",
      '<span class="dot"></span>Discovery asks <span class="time">' + hhmm(item.ts) + "</span>"));
    item.questions.forEach(q => {
      const card = el("div", "qcard");
      if (q.header) card.appendChild(el("div", "qhdr", esc(q.header)));
      card.appendChild(el("div", "qtext", esc(q.question)));
      if (q.message) card.appendChild(el("div", "qmsg md", md(q.message)));
      if (!q.answered) card.appendChild(el("div", "noans", "No response recorded"));
      if (q.options.length) {
        const ul = el("ul", "opts");
        const HIDE_AFTER = 6;
        let hidden = 0;
        q.options.forEach(o => {
          const li = el("li", "opt" + (o.selected ? " sel" : "") + (q.multi ? " multi" : ""));
          li.innerHTML = '<span class="mark"></span><span>' + esc(o.label) +
            (o.description ? '<span class="od">' + esc(o.description) + "</span>" : "") +
            "</span>" + (o.recommended ? '<span class="rec">suggested</span>' : "");
          ul.appendChild(li);
          if (!o.selected && ul.children.length > HIDE_AFTER) {
            li.dataset.extra = "1";
            li.style.display = "none";
            hidden++;
          }
        });
        card.appendChild(ul);
        if (hidden) {
          const btn = el("button", "more-opts", "Show " + hidden + " more option" + (hidden > 1 ? "s" : ""));
          let open = false;
          btn.onclick = () => {
            open = !open;
            ul.querySelectorAll('[data-extra="1"]').forEach(n => { n.style.display = open ? "" : "none"; });
            btn.textContent = open ? "Hide extra options"
              : "Show " + hidden + " more option" + (hidden > 1 ? "s" : "");
          };
          card.appendChild(btn);
        }
      }
      b.appendChild(card);
    });
    row.appendChild(b);
    row.id = "anchor-" + anchors.length;
    anchors.push({ id: row.id, label: item.questions[0].question, ts: item.ts, kind: "ask" });
    thread.appendChild(row);
    return;
  }

  if (item.kind === "answer") {
    row.className = "row user";
    const b = el("div", "bubble answer");
    b.appendChild(el("div", "who",
      '<span class="dot"></span>You answered <span class="time">' + hhmm(item.ts) + "</span>"));
    item.replies.forEach(r => {
      const w = el("div", "areply");
      if (item.replies.length > 1 && r.header) w.appendChild(el("div", "aq", esc(r.header)));
      r.selected.forEach(s => w.appendChild(el("span", "pick", esc(s))));
      if (r.freeform.trim()) w.appendChild(el("div", "free md", md(r.freeform)));
      b.appendChild(w);
    });
    row.appendChild(b);
    thread.appendChild(row);
    return;
  }

  if (item.kind === "user") {
    const isSys = item.role === "system";
    const b = el("div", "bubble " + (isSys ? "system" : "user"));
    b.appendChild(el("div", "who", '<span class="dot"></span>' +
      (isSys ? "System notice" : "You") + ' <span class="time">' + hhmm(item.ts) + "</span>"));
    if (isSys) {
      const d = el("details", "tool");
      d.appendChild(el("summary", null, '<span class="targ">' + esc(firstLine(item.text, 90)) + "</span>"));
      d.appendChild(el("pre", null, esc(item.text.slice(0, 4000))));
      b.appendChild(d);
    } else {
      b.appendChild(el("div", "md", md(item.text)));
    }
    row.appendChild(b);
    if (!isSys) {
      row.id = "anchor-" + anchors.length;
      anchors.push({ id: row.id, label: item.text, ts: item.ts, kind: "user" });
    }
  } else {
    const b = el("div", "bubble assistant");
    b.appendChild(el("div", "who", '<span class="dot"></span>Copilot <span class="time">' + hhmm(item.ts) + "</span>"));
    let stack = null;
    item.parts.forEach(p => {
      if (p.t === "tool") {
        if (!stack) { stack = el("div", "part toolstack"); stack.dataset.kind = "tools"; b.appendChild(stack); }
        const d = el("details", "tool");
        const stat = p.ok === false ? '<span class="tstat bad">✕</span>'
          : p.ok === true ? '<span class="tstat ok">●</span>' : "";
        d.appendChild(el("summary", null,
          '<span class="tname">' + esc(p.name) + "</span>" +
          '<span class="targ">' + esc(p.summary || "") + "</span>" + stat));
        d.appendChild(el("pre", null, esc(p.args || "")));
        stack.appendChild(d);
        return;
      }
      stack = null;
      if (p.t === "reasoning") {
        const d = el("details", "reason");
        d.appendChild(el("summary", null, "Thinking"));
        d.appendChild(el("div", "body", esc(p.text)));
        const w = el("div", "part");
        w.dataset.kind = "reason";
        w.appendChild(d);
        b.appendChild(w);
      } else {
        const w = el("div", "part md", md(p.text));
        b.appendChild(w);
      }
    });
    row.appendChild(b);
  }
  thread.appendChild(row);
});

thread.appendChild(el("div", "endcap", "End of session &middot; " + DATA.meta.items + ""));

/* nav */
anchors.forEach((a, i) => {
  const btn = el("button", "nav-item" + (a.kind === "ask" ? " ask" : ""),
    '<span class="t">' + hhmm(a.ts) + (a.kind === "ask" ? " &middot; question" : "") +
    "</span>" + esc(firstLine(a.label, 64)));
  btn.onclick = () => document.getElementById(a.id).scrollIntoView({ block: "start" });
  btn.dataset.for = a.id;
  nav.appendChild(btn);
});

/* meta */
const M = DATA.meta;
document.getElementById("m-date").textContent =
  M.date === M.endDate
    ? M.date + "  ·  " + M.start + "–" + M.end + " UTC"
    : M.date + " " + M.start + "  →  " + M.endDate + " " + M.end + " UTC";
document.getElementById("m-id").textContent = M.sessionId;
document.getElementById("m-human").textContent = M.humanMessages;
document.getElementById("m-ask").textContent = M.questions;
document.getElementById("m-tools").textContent = M.tools;
document.getElementById("m-dur").textContent = M.duration;
document.getElementById("m-sub").textContent =
  M.typedMessages + " typed · " + M.answers + " answered · " +
  M.tools + " tool calls (" + M.toolFailures + " failed)";

/* toggles */
function refreshEmpty() {
  document.querySelectorAll(".row.assistant").forEach(row => {
    const parts = [...row.querySelectorAll(".bubble.assistant > .part")];
    if (!parts.length) return;          // question cards have no parts — always keep
    row.style.display = parts.some(p => p.style.display !== "none") ? "" : "none";
  });
}
function toggle(btnId, apply, initial) {
  const btn = document.getElementById(btnId);
  let on = initial;
  const set = v => {
    on = v;
    btn.setAttribute("aria-pressed", String(v));
    apply(v);
    refreshEmpty();
  };
  btn.onclick = () => set(!on);
  set(initial);
}
toggle("t-reason", v => {
  document.querySelectorAll('.part[data-kind="reason"]').forEach(n => { n.style.display = v ? "" : "none"; });
}, false);
toggle("t-tools", v => {
  document.querySelectorAll('.part[data-kind="tools"]').forEach(n => { n.style.display = v ? "" : "none"; });
}, false);
toggle("t-system", v => {
  document.querySelectorAll('.row[data-role="system"]').forEach(n => { n.style.display = v ? "" : "none"; });
}, false);

const THEMES = ["discovery", "light", "dark"];
document.getElementById("t-theme").onclick = () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
  document.documentElement.setAttribute("data-theme", next);
};

/* progress + active nav */
const scroll = document.getElementById("scroll");
const bar = document.getElementById("progress");
const navBtns = [...nav.querySelectorAll(".nav-item")];
scroll.addEventListener("scroll", () => {
  const max = scroll.scrollHeight - scroll.clientHeight;
  bar.style.width = (max > 0 ? (scroll.scrollTop / max) * 100 : 0) + "%";
  let active = null;
  for (const b of navBtns) {
    const t = document.getElementById(b.dataset.for);
    if (t && t.getBoundingClientRect().top < 160) active = b;
  }
  navBtns.forEach(b => b.classList.toggle("active", b === active));
}, { passive: true });

/* keyboard: n/p jump between your messages */
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.metaKey || e.ctrlKey) return;
  if (e.key !== "n" && e.key !== "p") return;
  const tops = anchors.map(a => document.getElementById(a.id));
  const cur = tops.findIndex(t => t.getBoundingClientRect().top > 150);
  let next = e.key === "n" ? cur : (cur <= 0 ? 0 : cur - 2);
  if (next < 0) next = 0;
  if (next >= tops.length) next = tops.length - 1;
  tops[next].scrollIntoView({ block: "start" });
});
</script>
</body>
</html>
"""


def render_html(events_path, answers_path=None):
    """Return (html, payload) for one session transcript."""
    events = load(events_path)
    answers = load_answers(answers_path)
    payload = build(events, answers, source=str(events_path))
    payload["meta"]["items"] = "%d messages · %d tool calls" % (
        len(payload["items"]), payload["meta"]["tools"])
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    title = "Discovery session · %s" % payload["meta"]["date"]
    html = TEMPLATE.replace("__DATA__", data).replace("__TITLE__", title)
    return html, payload


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, out = sys.argv[1], sys.argv[2]
    answers_src = sys.argv[3] if len(sys.argv) > 3 else None
    html, payload = render_html(src, answers_src)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("wrote", out, os.path.getsize(out), "bytes")
    print("items:", len(payload["items"]), "meta:", payload["meta"])


if __name__ == "__main__":
    main()
