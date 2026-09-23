#!/usr/bin/env python3
"""Mine a Discovery workspace's exhaust into one read-only evidence.json.

Sources (all optional; each degrades to an empty list with a note):
  git history, .discovery/{purpose,outcomes,grades,tasks,engine-runs,
  science-runs,agent-runs}, VS Code / Discovery App chat sessions (human
  turns + question-form answers only, never assistant text), and token usage
  (via the discovery-token-usage skill when installed, plus chat metadata).

Nothing in the workspace is modified. Standard library only.

Usage:
  mine_exhaust.py [WORKSPACE] --out DIR [--since REV] [--until REV]
                  [--chat | --no-chat] [--exclude-session ID ...]
                  [--chat-root DIR ...] [--no-tokens]
"""
import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

SCHEMA = "discovery-project-story/evidence@1"
DX = re.compile(r"\bDX-\d+\b")
ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1"}
NOTES = []

CATEGORIES = [
    ("setup_and_governance", r"purpose|outcome|rubric|task (tree|plan)|setup|set up|bootstrap|agent|bookshelf|config|instructions|scaffold"),
    ("review_and_correction", r"review|revis|fix|correct|audit|gate|referee|respond|reconcil|verify|recheck|errat|retract"),
    ("evidence_and_search", r"search|corpus|extract|ingest|literature|pubmed|pmc|source|dataset|data\b|harvest|screen(ed|ing) record"),
    ("methods_and_testing", r"method|test|validat|analys|model|framework|spec|simulat|statistic|benchmark|protocol|pipeline"),
    ("manuscript_and_packaging", r"manuscript|paper|pdf|latex|release|presentation|slide|deck|report|package|figure|freeze|publish|story"),
]


def note(msg):
    NOTES.append(msg)
    print("note:", msg, file=sys.stderr)


def iso(value):
    """Normalise ms-epoch / ISO / 'YYYY-MM-DD HH:MM:SSZ' to ISO-8601 UTC."""
    if value in (None, "", "None"):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            v = float(value)
            if v > 1e11:
                v /= 1000.0
            return dt.datetime.fromtimestamp(v, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        s = str(value).strip().replace(" ", "T", 1)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        t = dt.datetime.fromisoformat(s)
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OSError, OverflowError):
        return None


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        note(f"unreadable JSON {path}: {exc.__class__.__name__}")
        return None


def dx_ids(*texts):
    found = set()
    for t in texts:
        if t:
            found.update(DX.findall(str(t)))
    return sorted(found, key=lambda s: int(s.split("-")[1]))


def within(ts, lo, hi):
    return ts is None or ((lo is None or ts >= lo) and (hi is None or ts <= hi))


# ── git ──────────────────────────────────────────────────────────────────────

def git(ws, *args):
    return subprocess.run(["git", "-C", str(ws), *args], env=ENV, capture_output=True,
                          text=True, check=True).stdout


SECRET = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|"
                    r"xox[abpr]-[A-Za-z0-9-]{10,})\b|(?i:\b(?:api[_-]?key|secret|password|passwd|token)\s*[:=]\s*)\S+")


def redact(message):
    out = []
    for line in message.splitlines():
        if re.match(r"^[A-Za-z-]+:\s", line):
            line = re.sub(r"<[^<>\s]+@[^<>\s]+>", "<email omitted>", line)
        out.append(SECRET.sub("[redacted]", line))
    return "\n".join(out).strip()


def categorise(subject, body, files):
    text = (subject + "\n" + body + "\n" + " ".join(files[:40])).lower()
    for name, pattern in CATEGORIES:
        if re.search(pattern, subject.lower()):
            return name
    for name, pattern in CATEGORIES:
        if re.search(pattern, text):
            return name
    return "other"


def mine_git(ws, since, until):
    try:
        head = git(ws, "rev-parse", "HEAD").strip()
        branch = git(ws, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        note("not a git repository; commits omitted")
        return {"head": None, "branch": None, "range": None}, []
    rng = f"{since}..{until or 'HEAD'}" if since else (until or "HEAD")
    fmt = "%x1e%H%x1f%aI%x1f%cI%x1f%an%x1f%P%x1f%B%x1d"
    raw = git(ws, "log", "--reverse", "--numstat", f"--format={fmt}", rng)
    commits = []
    for chunk in raw.split("\x1e")[1:]:
        meta, _, stat = chunk.partition("\x1d")
        h, adate, cdate, author, parents, body = meta.split("\x1f", 5)
        files, ins, dels = [], 0, 0
        for line in stat.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                files.append(parts[2])
                ins += int(parts[0]) if parts[0].isdigit() else 0
                dels += int(parts[1]) if parts[1].isdigit() else 0
        message = redact(body)
        subject, _, rest = message.partition("\n")
        commits.append({
            "sha": h, "short": h[:7], "authorDate": iso(adate), "date": iso(cdate),
            "author": author, "merge": len(parents.split()) > 1,
            "subject": subject.strip(), "body": rest.strip(),
            "category": categorise(subject, rest, files),
            "dxIds": dx_ids(message, " ".join(files)),
            "filesCount": len(files), "files": files[:60], "insertions": ins, "deletions": dels,
        })
    return {"head": head, "branch": branch, "range": rng}, commits


# ── .discovery state ─────────────────────────────────────────────────────────

def mine_purpose(d):
    p = load_json(d / "purpose.json") if (d / "purpose.json").exists() else None
    purpose = None
    if p:
        purpose = {k: p.get(k) for k in ("purposeId", "title", "statement", "version", "status")}
        purpose.update(createdAt=iso(p.get("createdAt")), updatedAt=iso(p.get("updatedAt")))
    outcomes, grades = [], []
    for f in sorted(glob.glob(str(d / "outcomes" / "*.json"))):
        o = load_json(f) or {}
        outcomes.append({"id": o.get("outcomeId"), "title": o.get("title"), "description": o.get("description"),
                         "type": o.get("type"), "status": o.get("status"), "taskIds": o.get("taskIds") or [],
                         "createdAt": iso(o.get("createdAt")), "updatedAt": iso(o.get("updatedAt"))})
    for f in sorted(glob.glob(str(d / "grades" / "*" / "*.json"))):
        g = load_json(f) or {}
        grades.append({"id": g.get("gradeId"), "outcomeId": g.get("outcomeId"), "taskId": g.get("taskId"),
                       "composite": g.get("compositeScore"), "passed": g.get("passed"),
                       "gradedAt": iso(g.get("gradedAt")), "gradedBy": g.get("gradedBy"),
                       "graderId": g.get("graderId")})
    grades.sort(key=lambda g: g["gradedAt"] or "")
    return purpose, outcomes, grades


def mine_tasks(d):
    entries = {}
    for f in glob.glob(str(d / "tasks" / "taskentries" / "*.json")):
        t = load_json(f)
        if t and t.get("dxId"):
            entries[t.get("name")] = t
    by_uuid = {k: v["dxId"] for k, v in entries.items()}
    tasks = []
    for uid, t in entries.items():
        hist = t.get("executionHistory") or []
        transitions = [{"t": iso(h.get("createdAt")), "action": h.get("action"), "by": h.get("createdByType")}
                       for h in hist if isinstance(h, dict) and str(h.get("action", "")).startswith("status:")]
        tasks.append({
            "dx": t["dxId"], "uuid": uid, "title": t.get("title"), "status": t.get("status"),
            "parent": by_uuid.get(t.get("parentId"), t.get("parentId")),
            "order": t.get("siblingOrder"),
            "dependsOn": [by_uuid.get(x, x) for x in (t.get("dependsOn") or [])],
            "labels": t.get("labels") or [], "createdAt": iso(t.get("createdAt")),
            "lastModifiedAt": iso(t.get("lastModifiedAt")), "transitions": transitions,
            "reopened": sum(1 for x in transitions if re.search(r"(Completed|Done|Verified)→(New|Executing|Ready|Reopen)", x["action"] or "")),
            "comments": len(t.get("comments") or []),
            "descriptionHead": (t.get("description") or "")[:400],
        })
    tasks.sort(key=lambda t: int(t["dx"].split("-")[1]))
    kids = {}
    for t in tasks:
        kids.setdefault(t["parent"], []).append(t["dx"])
    for t in tasks:
        t["children"] = kids.get(t["dx"], [])
    return tasks


DISPATCH = re.compile(r"clio-start|agents-run-start|cognition-startInstance|engine-start|startInstance|create_agent|launch_agent")
RUNID = re.compile(r"(?:run_id:\s*|\\?\"runId\\?\"\s*:\s*\\?\"|agent_id:\s*|\\u0022runId\\u0022:\\u0022)([0-9a-fA-F-]{8,40})")


def mine_engines(d, ws):
    runs = []
    for meta_path in sorted(glob.glob(str(d / "engine-runs" / "*" / "*" / "meta.json"))):
        m = load_json(meta_path) or {}
        out = Path(meta_path).with_name("output.jsonl")
        applied = proposed = 0
        dispatches, transitions, kinds = [], [], {}
        pending = {}
        if out.exists():
            with open(out, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    head = line[:40]
                    k = re.search(r'"kind":"(\w+)"', head)
                    if not k:
                        continue
                    kind = k.group(1)
                    kinds[kind] = kinds.get(kind, 0) + 1
                    if kind not in ("ActionProposed", "ActionApplied"):
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    label = rec.get("content", "").split("\n")[0].strip("*")
                    tool = label.split(" ")[0]
                    if kind == "ActionProposed":
                        proposed += 1
                        content = rec.get("content", "")
                        if not DISPATCH.search(tool) and '"agent_type"' in content[:3000]:
                            tool = "subagent:" + label[:80]
                        if DISPATCH.search(tool) or tool.startswith("subagent:") or tool.endswith("tasks-transition"):
                            pending[label if tool.startswith("subagent:") else tool] = \
                                pending.get(label if tool.startswith("subagent:") else tool, []) + [(n, rec, content, tool)]
                        continue
                    applied += 1
                    queue = pending.get(tool) or pending.get(label) or []
                    if not queue:
                        continue
                    pn, prec, pcontent, tool = queue.pop(0)
                    loc = {"file": os.path.relpath(out, ws), "line": n, "proposedLine": pn}
                    if tool.endswith("tasks-transition"):
                        st = re.search(r'"status":\s*"([\w-]+)"', pcontent)
                        transitions.append({"t": iso(rec.get("timestamp")), "dx": dx_ids(pcontent[:400])[:1],
                                            "status": st.group(1) if st else None, "locator": loc})
                        continue
                    agent = re.search(r'"(?:agentId|agent_type|definitionId)":\s*"([^"]+)"', pcontent)
                    rid = RUNID.search(rec.get("content", ""))
                    dispatches.append({
                        "t": iso(rec.get("timestamp")), "proposedAt": iso(prec.get("timestamp")),
                        "tool": "subagent" if tool.startswith("subagent:") else tool,
                        "label": tool[9:] if tool.startswith("subagent:") else None,
                        "agent": agent.group(1) if agent else ("clio" if "clio" in tool else None),
                        "runId": rid.group(1) if rid else None, "dxIds": dx_ids(pcontent[:1200]), "locator": loc,
                        "dispatchId": (rid.group(1) if rid else f"{loc['file']}#L{pn}"),
                        "synchronous": tool.startswith("subagent:") and not rid,
                    })
        prompt = m.get("prompt") or ""
        runs.append({
            "id": m.get("instanceId"), "engine": m.get("definitionId"), "adapter": m.get("adapterKind"),
            "state": m.get("state"), "startedAt": iso(m.get("startedAt")), "completedAt": iso(m.get("completedAt")),
            "dxIds": dx_ids(prompt[:3000]), "promptHead": prompt[:600], "promptSha256": sha(prompt),
            "eventCounts": kinds, "actionsProposed": proposed, "actionsApplied": applied,
            "dispatches": dispatches, "transitions": transitions,
            "locator": {"file": os.path.relpath(meta_path, ws)},
        })
    runs.sort(key=lambda r: r["startedAt"] or "")
    science = []
    for f in sorted(glob.glob(str(d / "science-runs" / "*.json"))):
        s = load_json(f) or {}
        goal = s.get("goal") or ""
        science.append({
            "id": s.get("run_id"), "parent": None if s.get("parent_run_id") in (None, "None") else s.get("parent_run_id"),
            "depth": s.get("depth"), "state": s.get("state"), "startedAt": iso(s.get("started")),
            "updatedAt": iso(s.get("updated")), "toolCalls": s.get("tool_calls"), "subagents": s.get("subagents"),
            "steers": s.get("steers"), "dxIds": dx_ids(goal), "goalHead": goal[:400],
            "locator": {"file": os.path.relpath(f, ws)},
        })
    science.sort(key=lambda r: r["startedAt"] or "")
    agents = []
    for f in sorted(glob.glob(str(d / "agent-runs" / "*.json"))):
        a = load_json(f) or {}
        agents.append({"id": a.get("runId"), "agentId": a.get("agentId"), "taskId": a.get("taskId"),
                       "status": a.get("status"), "startedAt": iso(a.get("startedAt")),
                       "completedAt": iso(a.get("completedAt")), "locator": {"file": os.path.relpath(f, ws)}})
    agents.sort(key=lambda r: r["startedAt"] or "")
    return runs, science, agents


# ── chat sessions (human side only) ──────────────────────────────────────────

def storage_roots(extra):
    home = Path.home()
    pats = [home / "Library/Application Support/*/user-data/User/workspaceStorage",
            home / "Library/Application Support/*/User/workspaceStorage",
            home / ".config/*/User/workspaceStorage", home / ".config/*/user-data/User/workspaceStorage"]
    if os.environ.get("APPDATA"):
        pats.append(Path(os.environ["APPDATA"]) / "*/User/workspaceStorage")
    roots = [Path(p) for p in extra]
    for p in pats:
        roots += [Path(x) for x in glob.glob(str(p))]
    return roots


def chat_dirs(ws, extra):
    target = os.path.realpath(ws)
    found = []
    for root in storage_roots(extra):
        if (root / "chatSessions").is_dir():
            found.append(root / "chatSessions")
            continue
        for wj in glob.glob(str(root / "*" / "workspace.json")):
            info = load_json(wj) or {}
            uri = info.get("folder") or info.get("workspace") or ""
            if not uri.startswith("file://"):
                continue
            path = urllib.parse.unquote(urllib.parse.urlparse(uri).path)
            if os.path.realpath(path) == target and (Path(wj).parent / "chatSessions").is_dir():
                found.append(Path(wj).parent / "chatSessions")
    return sorted(set(found))


KEY = re.compile(r'^\{"kind":(\d),"k":\[([^\]]*)\]')
# Messages the app injects into the user's turn slot (not human words) and UI control turns.
SYSTEM_TURN = re.compile(r"^\[(Terminal [0-9a-fA-F-]+ notification|System|Automated)[^\]]*\]", re.S)
CONTROL_TURN = re.compile(r"^\s*(/compact\b|/clear\b|@agent (Try Again|Continue\b))", re.I)


def answer_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = []
        if value.get("selectedValue") is not None:
            parts.append(str(value["selectedValue"]))
        if value.get("selectedValues"):
            parts.append("; ".join(map(str, value["selectedValues"])))
        if value.get("freeformValue"):
            parts.append(str(value["freeformValue"]))
        return " | ".join(parts)
    return ""


def carousels(items, base_pointer):
    for j, it in enumerate(items if isinstance(items, list) else []):
        if isinstance(it, dict) and it.get("kind") == "questionCarousel" and it.get("data"):
            titles = {q.get("id"): q for q in it.get("questions") or []}
            for qid, val in it["data"].items():
                text = answer_text(val)
                if text.strip():
                    q = titles.get(qid, {})
                    yield qid, it.get("resolveId"), q.get("title"), q.get("message"), text, f"{base_pointer}/{j}/data/{qid}", val


def mine_chat_file(path, ws):
    rel = str(path)
    session = {"file": rel, "id": Path(path).stem, "title": None, "created": None, "requests": 0}
    reqs, human, answers, steering = [], [], {}, set()

    def add_request(r, line_no, pointer):
        msg = (r.get("message") or {}).get("text") if isinstance(r.get("message"), dict) else None
        rec = {"requestId": r.get("requestId"), "timestamp": iso(r.get("timestamp")), "model": r.get("modelId"),
               "promptTokens": None, "outputTokens": None, "completionTokens": r.get("completionTokens")}
        reqs.append(rec)
        if msg and msg.strip() and SYSTEM_TURN.match(msg):
            rec["systemInjected"] = True
        elif msg and msg.strip():
            human.append({"kind": "control" if CONTROL_TURN.match(msg) else "request", "text": msg,
                          "requestIndex": len(reqs) - 1,
                          "locator": {"file": rel, "line": line_no, "pointer": pointer + "/message/text",
                                      "sha256": sha(msg)}, **rec})
        for qa in carousels(r.get("response"), pointer + "/response"):
            record_answer(qa, line_no, len(reqs) - 1)

    def record_answer(qa, line_no, idx):
        qid, resolve, title, question, text, pointer, raw = qa
        answers[qid] = {"kind": "answer", "text": text, "questionTitle": title, "question": (question or "")[:400],
                        "requestIndex": idx, "resolveId": resolve,
                        "locator": {"file": rel, "line": line_no, "pointer": pointer, "sha256": sha(text),
                                    "rawSha256": sha(json.dumps(raw, ensure_ascii=False, sort_keys=True))}}

    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            m = KEY.match(line[:200])
            if n == 1 and line.startswith('{"kind":0'):
                try:
                    v = json.loads(line)["v"]
                except ValueError:
                    continue
                session["title"] = v.get("customTitle")
                session["created"] = iso(v.get("creationDate"))
                for j, r in enumerate(v.get("requests") or []):
                    add_request(r, n, f"/v/requests/{j}")
                continue
            if not m:
                continue
            kind, keys = m.group(1), m.group(2)
            if keys == '"customTitle"':
                try:
                    session["title"] = json.loads(line)["v"]
                except ValueError:
                    pass
            elif keys == '"requests"' and kind == "2":
                d = json.loads(line)
                if "i" in d and isinstance(d["i"], int):
                    del reqs[d["i"]:]
                for j, r in enumerate(d.get("v") or []):
                    add_request(r, n, f"/v/{j}")
            elif keys == '"pendingRequests"' and '"steering"' in line:
                for r in json.loads(line).get("v") or []:
                    if isinstance(r, dict) and r.get("kind") == "steering" and r.get("id"):
                        steering.add(r["id"])
            elif keys.startswith('"requests",') and keys.endswith(',"result"'):
                idx = int(keys.split(",")[1])
                try:
                    md = (json.loads(line).get("v") or {}).get("metadata") or {}
                except ValueError:
                    continue
                if idx < len(reqs):
                    reqs[idx].update(promptTokens=md.get("promptTokens"), outputTokens=md.get("outputTokens"),
                                     model=md.get("resolvedModel") or reqs[idx]["model"])
            elif keys.startswith('"requests",') and keys.endswith(',"response"') and '"questionCarousel"' in line and '"data"' in line:
                idx = int(keys.split(",")[1])
                d = json.loads(line)
                for qa in carousels(d.get("v"), "/v"):
                    record_answer(qa, n, idx)
    session["requests"] = len(reqs)
    session["systemInjected"] = sum(1 for r in reqs if r.get("systemInjected"))
    for h in human:
        idx = h["requestIndex"]
        if idx < len(reqs):
            h.update({k: reqs[idx][k] for k in ("promptTokens", "outputTokens", "model")})
        h["steering"] = h.get("requestId") in steering
    for a in answers.values():
        idx = a["requestIndex"]
        a["timestamp"] = reqs[idx]["timestamp"] if idx < len(reqs) else None
        a["timeBasis"] = "parent-request"
        a["requestId"] = reqs[idx]["requestId"] if idx < len(reqs) else None
    return session, human + list(answers.values()), reqs


def mine_chat(ws, extra, exclude, lo, hi):
    dirs = chat_dirs(ws, extra)
    if not dirs:
        note("no chat sessions found for this workspace (workspaceStorage not matched)")
        return [], [], {}
    sessions, human, usage = [], [], {}
    for d in dirs:
        for f in sorted(glob.glob(str(d / "*.jsonl"))) + sorted(glob.glob(str(d / "*.json"))):
            sid = Path(f).stem
            if sid in exclude:
                continue
            if f.endswith(".json"):
                note(f"legacy chat session format skipped: {f}")
                continue
            s, h, reqs = mine_chat_file(f, ws)
            s["storage"] = str(d.parent)
            sessions.append(s)
            for item in h:
                if within(item.get("timestamp"), lo, hi):
                    item.update(sessionId=sid, sessionTitle=s["title"])
                    human.append(item)
            for r in reqs:
                if within(r["timestamp"], lo, hi) and r.get("promptTokens") is not None:
                    u = usage.setdefault(r.get("model") or "unknown", {"requests": 0, "input": 0, "output": 0})
                    u["requests"] += 1
                    u["input"] += int(r["promptTokens"] or 0)
                    u["output"] += int(r["outputTokens"] or 0)
    human.sort(key=lambda h: (h.get("timestamp") or "", h["locator"]["line"], h["locator"]["pointer"]))
    for i, h in enumerate(human, 1):
        h["id"] = f"H{i:03d}"
        h["chars"] = len(h["text"])
    sessions.sort(key=lambda s: s["created"] or "")
    return sessions, human, usage


# ── tokens ───────────────────────────────────────────────────────────────────

def mine_tokens(ws, out, chat_usage):
    result = {"source": None, "engines": {}, "interactive": {"byModel": chat_usage, "basis": "chat request metadata"},
              "caveats": []}
    script = Path.home() / ".copilot/skills/discovery-token-usage/scripts/mine_tokens.py"
    if not script.exists():
        result["caveats"].append("discovery-token-usage skill not installed; engine token usage not mined")
        return result
    raw_path = Path(out) / "token-usage.raw.json"
    try:
        proc = subprocess.run([sys.executable, str(script), str(ws), "--json", str(raw_path)], env=ENV,
                              capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        result["caveats"].append("token miner timed out")
        return result
    if proc.returncode != 0 or not raw_path.exists():
        result["caveats"].append("token miner failed: " + proc.stderr.strip()[-300:])
        return result
    raw = load_json(raw_path) or {}
    result["source"] = "discovery-token-usage"
    result["readout"] = [l for l in proc.stdout.splitlines() if l.strip()][:40]

    def fold(rows, group_key):
        agg = {}
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            g = str(r.get(group_key) or r.get("model") or r.get("engine") or "unknown")
            a = agg.setdefault(g, {"calls": 0, "input": 0, "output": 0, "cachedRead": 0})
            a["calls"] += 1
            a["input"] += int(r.get("inputTokens") or r.get("input_tokens") or 0)
            a["output"] += int(r.get("outputTokens") or r.get("output_tokens") or 0)
            a["cachedRead"] += int(r.get("cachedReadTokens") or r.get("cache_read_tokens") or 0)
        return agg

    result["engines"]["copilot-cli"] = {"byAgent": fold(raw.get("acp_usage"), "agent"),
                                        "models": (raw.get("adapter_meta") or {}).get("copilot-cli", {}).get("models", [])}
    result["engines"]["clio"] = {"byModel": fold(raw.get("clio_shutdown") or raw.get("clio_stderr_usage"), "model"),
                                 "models": (raw.get("adapter_meta") or {}).get("clio", {}).get("models", [])}
    result["engines"]["interactive-journal"] = {"byModel": fold(raw.get("journal"), "model")}
    result["caveats"].append("Token counts are as logged by each surface; cached reads are included in input where the source reports them that way.")
    if not raw.get("clio_shutdown") and not raw.get("clio_stderr_usage"):
        result["caveats"].append("No Clio / science-engine token records were found by the miner; do not present Clio usage as zero work.")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workspace", nargs="?", default=os.getcwd())
    ap.add_argument("--out", required=True)
    ap.add_argument("--since", help="git revision to start after (exclusive)")
    ap.add_argument("--until", help="git revision to end at (inclusive; default HEAD)")
    ap.add_argument("--chat", dest="chat", action="store_true", default=True)
    ap.add_argument("--no-chat", dest="chat", action="store_false")
    ap.add_argument("--exclude-session", action="append", default=[])
    ap.add_argument("--chat-root", action="append", default=[], help="extra workspaceStorage/<hash> dir")
    ap.add_argument("--no-tokens", action="store_true")
    a = ap.parse_args()
    ws = Path(a.workspace).resolve()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    disc = ws / ".discovery"
    if not disc.is_dir():
        note(".discovery/ not found — Discovery state omitted")
    gitinfo, commits = mine_git(ws, a.since, a.until)
    lo = commits[0]["date"] if (a.since and commits) else None
    hi = commits[-1]["date"] if (a.until and commits) else None
    purpose, outcomes, grades = mine_purpose(disc) if disc.is_dir() else (None, [], [])
    tasks = mine_tasks(disc) if disc.is_dir() else []
    engines, science, agents = mine_engines(disc, ws) if disc.is_dir() else ([], [], [])
    sessions, human, usage = mine_chat(ws, a.chat_root, set(a.exclude_session), lo, hi) if a.chat else ([], [], {})
    if not a.chat:
        note("chat mining disabled by --no-chat; human turns come only from commits/tasks")
    tokens = None if a.no_tokens else mine_tokens(ws, out, usage)
    ev = {
        "schema": SCHEMA, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "workspace": {"path": str(ws), "name": ws.name, **gitinfo},
        "window": {"from": lo, "to": hi},
        "purpose": purpose, "outcomes": outcomes, "grades": grades, "tasks": tasks,
        "commits": commits, "engineRuns": engines, "scienceRuns": science, "agentRuns": agents,
        "chatSessions": sessions, "human": human, "tokens": tokens, "notes": NOTES,
        "privacy": "Human chat turns and form answers only; assistant responses are never exported. "
                   "Identity-trailer emails are masked. Treat this file as private project provenance.",
    }
    path = out / "evidence.json"
    path.write_text(json.dumps(ev, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {"commits": len(commits), "tasks": len(tasks), "outcomes": len(outcomes), "grades": len(grades),
               "engineRuns": len(engines), "dispatches": sum(len(r["dispatches"]) for r in engines),
               "scienceRuns": len(science), "agentRuns": len(agents), "chatSessions": len(sessions),
               "humanTurns": sum(1 for h in human if h["kind"] == "request"),
               "formAnswers": sum(1 for h in human if h["kind"] == "answer"),
               "controlTurns": sum(1 for h in human if h["kind"] == "control"),
               "systemInjectedTurnsExcluded": sum(s.get("systemInjected", 0) for s in sessions), "notes": len(NOTES)}
    print(json.dumps({"evidence": str(path), **summary}, indent=1))


if __name__ == "__main__":
    main()
