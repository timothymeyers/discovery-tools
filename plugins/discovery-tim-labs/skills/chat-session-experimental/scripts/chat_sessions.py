#!/usr/bin/env python3
"""chat-session-experimental: list, find, render, and archive Copilot chat
sessions that belong to one project workspace.

Sources searched (read-only):
  * VS Code-family workspace storage (VS Code, Insiders, Discovery App, ...):
      <userData>/User/workspaceStorage/<hash>/workspace.json  -> project folder
      .../GitHub.copilot-chat/transcripts/<id>.jsonl          -> event transcript
      .../chatSessions/<id>.jsonl|.json                       -> UI model (answers)
      .../GitHub.copilot-chat/debug-logs/<id>/                -> telemetry
      .../GitHub.copilot-chat/chat-session-resources/<id>/
      .../chatEditingSessions/<id>/
  * Copilot CLI / SDK / engine sessions:
      $COPILOT_HOME (default ~/.copilot)/session-state/<id>/  (cwd in workspace.yaml)
  * This skill's own archive: <workspace>/.discovery/chat-sessions/<id>/

Every command prints one JSON object on stdout.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chat_view  # noqa: E402

VERSION = "0.1.0-experimental"
ARCHIVE_REL = Path(".discovery") / "chat-sessions"
SKIP_SUFFIXES = (".lock",)

VSCODE_ARTIFACTS = [
    # kind, path relative to the storage dir (with {id}), archive destination
    ("transcript", "GitHub.copilot-chat/transcripts/{id}.jsonl", "vscode/transcript.jsonl"),
    ("chat-session", "chatSessions/{id}.jsonl", "vscode/chat-session.jsonl"),
    ("chat-session", "chatSessions/{id}.json", "vscode/chat-session.json"),
    ("debug-logs", "GitHub.copilot-chat/debug-logs/{id}", "vscode/debug-logs"),
    ("chat-session-resources", "GitHub.copilot-chat/chat-session-resources/{id}",
     "vscode/chat-session-resources"),
    ("chat-editing-session", "chatEditingSessions/{id}", "vscode/chat-editing-session"),
]
CLI_DEST = "copilot-cli/session-state"

STOPWORDS = set("""
a an the i we you me my our your us about with did do does had have has chat chats
conversation conversations session sessions talk talked talking go find look that
this those these for of on in to and or was were is it its where when what which
some related regarding re can could please there one earlier previous before
""".split())


# --------------------------------------------------------------------------- utils

def out(obj, code=0):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    sys.exit(code)


def fail(msg, **extra):
    out(dict({"ok": False, "error": msg}, **extra), 1)


def is_within(child, root):
    return child == root or root in child.parents


def copilot_home():
    return Path(os.environ.get("COPILOT_HOME") or (Path.home() / ".copilot"))


def storage_roots(extra):
    home = Path.home()
    bases = []
    if sys.platform == "darwin":
        bases.append(home / "Library" / "Application Support")
    elif os.name == "nt":
        if os.environ.get("APPDATA"):
            bases.append(Path(os.environ["APPDATA"]))
    else:
        bases.append(Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config")))
    roots = []
    for base in bases:
        if not base.is_dir():
            continue
        for pattern in ("*/User/workspaceStorage", "*/user-data/User/workspaceStorage"):
            roots.extend(p for p in base.glob(pattern) if p.is_dir())
    roots.extend(Path(x).expanduser() for x in (extra or []))
    seen, uniq = set(), []
    for r in roots:
        key = str(r.resolve())
        if key not in seen and r.is_dir():
            seen.add(key)
            uniq.append(r)
    return uniq


def app_name(storage_dir):
    parts = storage_dir.parts
    i = parts.index("workspaceStorage") if "workspaceStorage" in parts else len(parts)
    j = i - 1
    while j > 0 and parts[j] in ("User", "user-data"):
        j -= 1
    return parts[j] if j >= 0 else "unknown"


def file_uri_to_path(uri):
    p = urlparse(uri)
    if p.scheme != "file":
        return None
    return Path(unquote(p.path))


def storage_matches(storage_dir, root):
    try:
        data = json.loads((storage_dir / "workspace.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if data.get("folder"):
        p = file_uri_to_path(data["folder"])
        return bool(p) and p.resolve() == root
    if data.get("workspace"):
        ws = file_uri_to_path(data["workspace"])
        if not ws or not ws.is_file():
            return False
        try:
            text = re.sub(r"^\s*//.*$", "", ws.read_text(encoding="utf-8"), flags=re.M)
            folders = json.loads(text).get("folders", [])
        except (OSError, ValueError):
            return False
        for f in folders:
            fp = f.get("path") or (file_uri_to_path(f.get("uri", "")) or "")
            if fp and (ws.parent / fp).resolve() == root:
                return True
    return False


def yaml_scalar(text, key):
    m = re.search(r"^%s:\s*(.*)$" % re.escape(key), text, flags=re.M)
    if not m:
        return ""
    v = m.group(1).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    return v


# ----------------------------------------------------------------------- discovery

TYPE_RE = re.compile(r'"type"\s*:\s*"([^"]+)"')
TS_RE = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')


def summarize(events_path, want_text=False):
    """Cheap pass over a transcript: timestamps, counts, human messages."""
    s = {"start": "", "end": "", "userMessages": 0, "toolCalls": 0, "turns": 0,
         "human": [], "assistant": []}
    try:
        fh = open(events_path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return s
    with fh:
        for line in fh:
            m = TYPE_RE.search(line)
            if not m:
                continue
            typ = m.group(1)
            t = TS_RE.search(line)
            if t:
                s["start"] = s["start"] or t.group(1)
                s["end"] = t.group(1)
            if typ == "tool.execution_start":
                s["toolCalls"] += 1
            elif typ == "assistant.turn_start":
                s["turns"] += 1
            elif typ == "user.message" or (want_text and typ == "assistant.message"):
                try:
                    data = json.loads(line).get("data", {})
                except ValueError:
                    continue
                if typ == "user.message":
                    text = data.get("content") or ""
                    if text.strip() and not chat_view.is_system_user_msg(text):
                        s["human"].append(text)
                else:
                    text = (data.get("content") or "").strip()
                    if text:
                        s["assistant"].append(text)
    s["userMessages"] = len(s["human"])
    return s


def title_of(human):
    for msg in human:
        line = next((x.strip() for x in msg.splitlines()
                     if x.strip() and not re.fullmatch(r"</?[\w-]+>", x.strip())), "")
        if line:
            return line[:120] + ("…" if len(line) > 120 else "")
    return ""


def discover(root, extra_storage=None):
    sessions = {}

    def get(sid):
        return sessions.setdefault(sid, {
            "id": sid, "origin": "", "app": "", "client": "", "live": False,
            "archived": False, "events": None, "answers": None, "artifacts": [],
        })

    # 1. VS Code-family chat panels
    for sroot in storage_roots(extra_storage):
        for sdir in sroot.iterdir():
            if not sdir.is_dir() or not storage_matches(sdir, root):
                continue
            ids = set()
            for pat in ("GitHub.copilot-chat/transcripts/*.jsonl", "chatSessions/*.json*"):
                ids.update(p.name.split(".")[0] for p in sdir.glob(pat))
            ids.update(p.name for p in sdir.glob("GitHub.copilot-chat/debug-logs/*") if p.is_dir())
            for sid in ids:
                s = get(sid)
                s.update(origin="vscode-chat", app=app_name(sdir), client="vscode", live=True,
                         storageDir=str(sdir))
                for kind, rel, dest in VSCODE_ARTIFACTS:
                    p = sdir / rel.format(id=sid)
                    if p.exists():
                        s["artifacts"].append({"kind": kind, "src": str(p), "dest": dest})
                        if kind == "transcript":
                            s["events"] = str(p)
                        elif kind == "chat-session":
                            s["answers"] = str(p)

    # 2. Copilot CLI / SDK / engine sessions
    state = copilot_home() / "session-state"
    if state.is_dir():
        for sdir in state.iterdir():
            y = sdir / "workspace.yaml"
            try:
                text = y.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cwd = yaml_scalar(text, "cwd")
            if not cwd or not is_within(Path(cwd).resolve(), root):
                continue
            s = get(sdir.name)
            s.update(origin=s["origin"] or "copilot-cli", live=True,
                     client=s["client"] if s["origin"] == "vscode-chat"
                     else (yaml_scalar(text, "client_name") or "cli"),
                     app=s["app"] or "copilot")
            s["artifacts"].append({"kind": "session-state", "src": str(sdir), "dest": CLI_DEST})
            if not s["events"] and (sdir / "events.jsonl").exists():
                s["events"] = str(sdir / "events.jsonl")

    # 3. This skill's archive (survives editor cleanup)
    adir = root / ARCHIVE_REL
    if adir.is_dir():
        for mdir in adir.iterdir():
            mf = mdir / "manifest.json"
            if not mf.is_file():
                continue
            try:
                man = json.loads(mf.read_text(encoding="utf-8"))
            except ValueError:
                continue
            s = get(man.get("sessionId", mdir.name))
            s["archived"] = True
            s["archiveDir"] = str(mdir)
            for k in ("origin", "app", "client"):
                s[k] = s[k] or man.get(k, "")
            if not s["events"]:
                for cand in ("vscode/transcript.jsonl", CLI_DEST + "/events.jsonl"):
                    if (mdir / cand).is_file():
                        s["events"] = str(mdir / cand)
                        break
            if not s["answers"]:
                for cand in ("vscode/chat-session.jsonl", "vscode/chat-session.json"):
                    if (mdir / cand).is_file():
                        s["answers"] = str(mdir / cand)
                        break
    return sessions


def describe(s, summary):
    return {
        "id": s["id"],
        "title": title_of(summary["human"]),
        "origin": s["origin"],
        "app": s["app"],
        "client": s["client"],
        "start": summary["start"],
        "end": summary["end"],
        "userMessages": summary["userMessages"],
        "toolCalls": summary["toolCalls"],
        "interactive": is_interactive(s),
        "live": s["live"],
        "archived": s["archived"],
        "renderable": bool(s["events"]),
        "events": s["events"],
    }


def is_interactive(s):
    """A human chatting directly (chat panel or Copilot CLI), not an engine/SDK run."""
    return s["origin"] == "vscode-chat" or s["client"] in ("cli", "vscode", "")


def filtered(sessions, origin, interactive_only=False):
    for s in sessions.values():
        if origin != "all" and s["origin"] != origin:
            continue
        if interactive_only and not is_interactive(s):
            continue
        yield s


def resolve_one(sessions, ref):
    ref = ref.strip().lower()
    hits = [s for sid, s in sessions.items() if sid.lower().startswith(ref)]
    if not hits:
        fail("No session matches %r for this workspace." % ref)
    if len(hits) > 1:
        fail("Session prefix %r is ambiguous." % ref, candidates=[h["id"] for h in hits])
    return hits[0]


# ------------------------------------------------------------------------ commands

def cmd_list(args, root):
    sessions = discover(root, args.storage_root)
    rows = [describe(s, summarize(s["events"]) if s["events"] else summarize(""))
            for s in filtered(sessions, args.origin, args.interactive_only)]
    rows.sort(key=lambda r: r["start"] or "", reverse=True)
    out({"ok": True, "workspace": str(root), "count": len(rows), "sessions": rows})


def query_terms(q):
    toks = [t for t in re.findall(r"[a-z0-9][a-z0-9_.\-]*", q.lower()) if t not in STOPWORDS]
    terms = list(dict.fromkeys(toks))
    for a, b in zip(toks, toks[1:]):
        for joined in (a + b, a + "-" + b, a + " " + b):
            if joined not in terms:
                terms.append(joined)
    return toks, terms


def snippet(text, term, width=90):
    i = text.lower().find(term)
    if i < 0:
        return ""
    a, b = max(0, i - width), min(len(text), i + len(term) + width)
    s = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("…" if a else "") + s + ("…" if b < len(text) else "")


def cmd_find(args, root):
    base_toks, terms = query_terms(args.query)
    if not terms:
        fail("Query has no searchable terms after removing filler words.")
    sessions = discover(root, args.storage_root)
    results = []
    for s in filtered(sessions, args.origin, args.interactive_only):
        if not s["events"]:
            continue
        summ = summarize(s["events"], want_text=True)
        human = "\n".join(summ["human"]).lower()
        assistant = "\n".join(summ["assistant"]).lower()
        title = title_of(summ["human"]).lower()
        score, matched = 0.0, []
        for t in terms:
            hc, ac, tc = human.count(t), assistant.count(t), title.count(t)
            if hc or ac or tc:
                matched.append(t)
                # presence matters more than volume, so long engine logs don't dominate
                score += (5 if tc else 0) \
                    + (3 + 0.5 * min(hc - 1, 2) if hc else 0) \
                    + (1 + min(math.log1p(ac), 1) if ac else 0)
        if not matched:
            continue
        phrase = " ".join(base_toks)
        if len(base_toks) > 1 and (phrase in human or phrase in assistant):
            score += 6
        coverage = sum(1 for t in base_toks if t in matched or any(t in m for m in matched))
        snips = []
        for t in sorted(matched, key=len, reverse=True):
            for msg in summ["human"]:
                sn = snippet(msg, t)
                if sn and sn not in snips:
                    snips.append(sn)
                    break
            if len(snips) >= 2:
                break
        if not snips:
            for t in matched:
                sn = snippet("\n".join(summ["assistant"]), t)
                if sn:
                    snips.append("[assistant] " + sn)
                    break
        row = describe(s, summ)
        row.update(score=round(score, 2), coverage="%d/%d" % (coverage, len(base_toks) or 1),
                   matchedTerms=matched, snippets=snips)
        results.append((coverage, score, row))
    results.sort(key=lambda r: (r[0], r[1]), reverse=True)
    out({"ok": True, "workspace": str(root), "query": args.query, "terms": terms,
         "count": len(results), "results": [r[2] for r in results[: args.limit]]})


def cmd_render(args, root):
    sessions = discover(root, args.storage_root)
    s = resolve_one(sessions, args.session)
    if not s["events"]:
        fail("Session %s has no transcript to render." % s["id"])
    html, payload = chat_view.render_html(s["events"], s["answers"])
    dest = Path(args.out).expanduser().resolve() if args.out else \
        root / ARCHIVE_REL / s["id"] / "chat.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    url = dest.as_uri()
    if args.theme:
        url += "?clawpilotTheme=" + args.theme
    out({"ok": True, "sessionId": s["id"], "path": str(dest), "url": url,
         "bytes": dest.stat().st_size, "items": len(payload["items"]), "meta": payload["meta"]})


def iter_files(src):
    src = Path(src)
    if src.is_file():
        yield src, Path()
        return
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.is_symlink() or fn.endswith(SKIP_SUFFIXES):
                continue
            yield p, p.relative_to(src)


def git_status(root, path):
    try:
        r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(path)],
                           capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return {0: "ignored", 1: "not-ignored"}.get(r.returncode, "not-a-repo")


def cmd_archive(args, root):
    sessions = discover(root, args.storage_root)
    targets = [resolve_one(sessions, args.session)] if args.session else \
        [s for s in filtered(sessions, args.origin, args.interactive_only) if s["live"]]
    adir = root / ARCHIVE_REL
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = []
    for s in targets:
        if not s["artifacts"]:
            report.append({"id": s["id"], "skipped": "no live source (already archived only)"})
            continue
        sdir = adir / s["id"]
        stats = {"added": 0, "updated": 0, "unchanged": 0, "bytes": 0}
        for art in s["artifacts"]:
            src = Path(art["src"])
            for f, rel in iter_files(src):
                d = sdir / art["dest"] / rel if rel.parts else sdir / art["dest"]
                try:
                    st = f.stat()
                except OSError:
                    continue
                if d.exists():
                    dst = d.stat()
                    if dst.st_size == st.st_size and int(dst.st_mtime) == int(st.st_mtime):
                        stats["unchanged"] += 1
                        continue
                    stats["updated"] += 1
                else:
                    stats["added"] += 1
                stats["bytes"] += st.st_size
                if not args.dry_run:
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, d)
        summ = summarize(s["events"]) if s["events"] else summarize("")
        if not args.dry_run:
            mf = sdir / "manifest.json"
            prev = {}
            if mf.is_file():
                try:
                    prev = json.loads(mf.read_text(encoding="utf-8"))
                except ValueError:
                    prev = {}
            man = {
                "schema": 1,
                "tool": "chat-session-experimental",
                "toolVersion": VERSION,
                "sessionId": s["id"],
                "title": title_of(summ["human"]),
                "origin": s["origin"],
                "app": s["app"],
                "client": s["client"],
                "start": summ["start"],
                "end": summ["end"],
                "userMessages": summ["userMessages"],
                "toolCalls": summ["toolCalls"],
                "sources": [{"kind": a["kind"], "src": a["src"], "dest": a["dest"]}
                            for a in s["artifacts"]],
                "firstArchivedAt": prev.get("firstArchivedAt", now),
                "lastArchivedAt": now,
            }
            sdir.mkdir(parents=True, exist_ok=True)
            mf.write_text(json.dumps(man, indent=2, ensure_ascii=False), encoding="utf-8")
        report.append(dict({"id": s["id"], "title": title_of(summ["human"]),
                            "origin": s["origin"], "start": summ["start"]}, **stats))

    if not args.dry_run and report:
        index = []
        for mf in sorted(adir.glob("*/manifest.json")):
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except ValueError:
                continue
            index.append({k: m.get(k) for k in ("sessionId", "title", "origin", "app", "client",
                                               "start", "end", "userMessages", "toolCalls",
                                               "lastArchivedAt")})
        index.sort(key=lambda r: r.get("start") or "", reverse=True)
        (adir / "index.json").write_text(json.dumps(
            {"schema": 1, "updatedAt": now, "sessions": index}, indent=2, ensure_ascii=False),
            encoding="utf-8")

    git = git_status(root, adir / "index.json")
    result = {"ok": True, "dryRun": args.dry_run, "archiveDir": str(adir),
              "sessions": report, "git": git}
    if git == "not-ignored":
        result["warning"] = ("Archive contains full prompts, tool arguments, outputs and "
                             "telemetry, and .discovery/chat-sessions/ is NOT git-ignored. "
                             "It may be committed and pushed.")
    out(result)


# ---------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--workspace", required=True, help="absolute project root")
        p.add_argument("--origin", choices=["all", "vscode-chat", "copilot-cli"], default="all")
        p.add_argument("--interactive-only", action="store_true",
                       help="skip automated engine/SDK sessions")
        p.add_argument("--storage-root", action="append",
                       help="extra VS Code workspaceStorage directory (repeatable)")
        return p

    common(sub.add_parser("list", help="list all sessions for the workspace"))
    p = common(sub.add_parser("find", help="rank sessions by keyword relevance"))
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=5)
    p = common(sub.add_parser("render", help="build the single-page chat viewer"))
    p.add_argument("--session", required=True, help="session id or unique prefix")
    p.add_argument("--out", help="output html path (default: archive dir/<id>/chat.html)")
    p.add_argument("--theme", choices=["discovery", "light", "dark"])
    p = common(sub.add_parser("archive", help="copy logs + telemetry into .discovery/chat-sessions"))
    p.add_argument("--session", help="archive only this session id or prefix")
    p.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    root = Path(args.workspace).expanduser()
    if not root.is_absolute():
        fail("--workspace must be an absolute path.")
    root = root.resolve()
    if not root.is_dir():
        fail("Workspace does not exist: %s" % root)
    {"list": cmd_list, "find": cmd_find, "render": cmd_render, "archive": cmd_archive}[args.cmd](args, root)


if __name__ == "__main__":
    main()
