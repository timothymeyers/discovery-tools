#!/usr/bin/env python3
"""Scaffold, validate and build a Discovery project story.

  build_story.py scaffold --dir WORK
      From WORK/outline.lock.json + outline.json + evidence.json, write
      WORK/story.json: every approved scene/beat with exact-quote ids,
      evidence refs, chronology, auto flow graphs and token metrics filled in,
      and "TODO" in every field an author must write.

  build_story.py build --dir WORK [--out DIR] [--only S3] [--skip-source-check]
      Validate story.json against the lock, evidence and narrative rules, then
      render one self-contained HTML (DIR/discovery-work.html) plus
      DIR/build-report.json. Deterministic: same inputs → identical bytes.
      --only S3[,S5] writes WORK/preview-S3.html for iteration (no lock check
      for other scenes, never a release).

Exit status 1 on any validation error (listed in the report and stderr).
"""
import argparse
import copy
import datetime as dt
import hashlib
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from verify_quotes import follow, read_line  # noqa: E402
from mine_exhaust import answer_text, SECRET  # noqa: E402

TEMPLATE = HERE.parent / "templates" / "discovery-work.template.html"
SKILL_VERSION = "1.0.0"
PHASE_MS, GAP_MS, METRIC_MS = 1400, 1000, 1200
BANNED = re.compile(r"\bDX-\d+\b|\bH\d{3}\b|\b[0-9a-f]{12,40}\b|/Users/|~/|\.discovery/|\bresearch/|"
                    r"\b[\w-]+\.(?:md|json|jsonl|tex|py|csv|html|txt)\b", re.I)
LINT = [
    (re.compile(r"(?<!AI )(?<!simulated )(?<!Simulated )\bpeer[- ]review", re.I), "say 'simulated AI review' — AI review is not human peer review"),
    (re.compile(r"clinical(ly)? validat|validated (in|for) (clinic|patients)", re.I), "software checks are not clinical validation"),
    (re.compile(r"\b(the user|the human) approved\b", re.I), "only claim approval that is recorded verbatim in the evidence"),
    (re.compile(r"\b(guarantee[sd]?|proves?|proven)\b", re.I), "avoid certainty claims the record does not support"),
]
NEGATION = re.compile(r"\b(not|no|nobody|never|isn't|wasn't|without|nor)\b[^.]*$", re.I)
HEADINGS_NO_QUOTE = {"Project scope", "Recorded outcome"}
HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# ── scaffold ─────────────────────────────────────────────────────────────────

def short_agent(a):
    return re.sub(r"^catalog[/-]", "", str(a))


def auto_graph(actors):
    nodes = [{"id": "human", "label": "Researcher", "kind": "human", "lane": 0},
             {"id": "chat", "label": "Discovery chat", "kind": "discovery", "lane": 1}]
    edges = [{"id": "ask", "from": "human", "to": "chat", "label": ""}]
    for i, (eng, n) in enumerate(list(actors.get("engines", {}).items())[:3]):
        nid = f"engine{i}"
        nodes.append({"id": nid, "label": eng.replace("-", " "), "kind": "discovery", "kindLabel": "engine", "lane": 1, "count": n})
        edges.append({"id": f"start-{nid}", "from": "chat", "to": nid, "label": ""})
    lead = next((n["id"] for n in nodes if n["id"].startswith("engine")), "chat")
    agents = list(actors.get("agents", {}).items())
    if len(agents) > 6:
        agents = agents[:5] + [("other agents", sum(n for _, n in agents[5:]))]
    for i, (name, n) in enumerate(agents):
        nid = f"agent{i}"
        nodes.append({"id": nid, "label": name.replace("-", " "), "kind": "agent", "lane": 2, "count": n})
        edges.append({"id": f"run-{nid}", "from": lead, "to": nid, "label": ""})
        edges.append({"id": f"out-{nid}", "from": nid, "to": "repo", "label": ""})
    nodes += [{"id": "tasks", "label": "Task board", "kind": "record", "lane": 1},
              {"id": "repo", "label": "Project files", "kind": "record", "lane": 3}]
    edges += [{"id": "track", "from": lead, "to": "tasks", "label": ""},
              {"id": "back", "from": "chat", "to": "human", "label": ""}]
    if not agents:
        edges.append({"id": "write", "from": lead, "to": "repo", "label": ""})
    return {"nodes": nodes, "edges": edges}


def flow_phases(kind, graph, refs, ev_dispatch):
    ids = {e["id"] for e in graph["edges"]}
    lead = next((n["id"] for n in graph["nodes"] if n["id"].startswith("engine")), "chat")
    if kind == "human":
        ph = [[{"edge": "ask"}]]
        if f"start-{lead}" in ids:
            ph.append([{"edge": f"start-{lead}"}])
        return ph, ["human", "chat"]
    if kind == "dispatch":
        label_to_node = {n["label"]: n["id"] for n in graph["nodes"] if n["kind"] == "agent"}
        counts = {}
        for d in refs.get("dispatches", []):
            a = ev_dispatch.get(d)
            nid = label_to_node.get(short_agent(a).replace("-", " ")) if a else None
            nid = nid or label_to_node.get("other agents")
            if nid:
                counts[nid] = counts.get(nid, 0) + 1
        out = [[{"edge": f"run-{n}", "count": c} for n, c in counts.items() if f"run-{n}" in ids]]
        out.append([{"edge": f"out-{n}", "count": c} for n, c in counts.items() if f"out-{n}" in ids])
        return [p for p in out if p], [lead]
    if "track" in ids:
        return [[{"edge": "track"}]], [lead, "tasks"]
    return [], [lead]


def scaffold(work):
    lock, outline, ev = load(work / "outline.lock.json"), load(work / "outline.json"), load(work / "evidence.json")
    if sha((work / "outline.json").read_bytes()) != lock["outlineSha256"]:
        sys.exit("outline.json changed after locking — re-approve and re-lock first")
    if (work / "story.json").exists():
        sys.exit("story.json exists; scaffold never overwrites authored work (move it aside deliberately)")
    O = {s["id"]: s for s in outline["scenes"]}
    human = {h["id"]: h for h in ev.get("human", [])}
    by_req = {}
    for h in ev.get("human", []):
        if h.get("requestId"):
            by_req.setdefault(h["requestId"], []).append(h["id"])
    commits = {c["short"]: c for c in ev.get("commits", [])}
    ev_dispatch = {d["dispatchId"]: d.get("agent") or d["tool"] for r in ev.get("engineRuns", []) for d in r["dispatches"]}
    ev_dispatch.update({s["id"]: "clio" for s in ev.get("scienceRuns", [])})
    ev_dispatch.update({a["id"]: a["agentId"] for a in ev.get("agentRuns", [])})
    tok = ev.get("tokens") or {}
    scenes = []
    for ls in lock["scenes"]:
        os_ = O[ls["id"]]
        sc = {"id": ls["id"], "title": ls["title"], "milestone": "", "mode": ls["mode"], "seconds": ls["seconds"],
              "appendix": ls.get("appendix", False), "notes": "TODO presenter notes: what to say, what not to claim.",
              "chronology": os_.get("chronology", []), "beats": []}
        if ls["mode"] == "tree":
            n = len(ls["beats"])
            per = max(1, -(-n // 6))
            nb = -(-n // per)
            sc["tree"] = {"root": "TODO root", "branches": [{"title": "TODO branch", "sub": "TODO short line"} for _ in range(nb)]}
        elif ls["mode"] == "flow":
            sc["graph"] = auto_graph(os_.get("actors", {}))
        else:
            sc["metrics"] = token_metrics(tok)
        for bi, lb in enumerate(ls["beats"]):
            ob = next(b for b in os_["beats"] if b["id"] == lb["id"])
            refs = ob.get("refs", {})
            pid = lb.get("primaryId")
            beat = {"id": lb["id"], "label": "TODO short beat title",
                    "human": ({"quoteId": pid, "summary": "TODO plain-language summary (not a quote)", "relation": ""} if pid else
                              {"quoteId": None, "heading": "Project scope" if ob["kind"] == "scope" else "Recorded outcome",
                               "summary": "TODO what the record shows"}),
                    "exchangeIds": ([pid] + [x for x in by_req.get(human[pid].get("requestId"), []) if x != pid])[:6] if pid in human else [],
                    "discovery": {"action": "TODO what Discovery did", "result": "TODO what came of it"},
                    "evidenceRefs": refs, "notes": ""}
            if refs.get("candidateCause") and not pid:
                beat["notes"] = f"Nearest prior human ask is {refs['candidateCause']} — only link them if the record shows the connection."
            if pid in human and BANNED.search(human[pid]["text"][:320]):
                beat["human"]["excerpt"] = clean_excerpt(human[pid]["text"])
            nxt = ls["beats"][bi + 1] if bi + 1 < len(ls["beats"]) else None
            nxt_t = next((b.get("time") for b in os_["beats"] if nxt and b["id"] == nxt["id"]), None)
            cands = [commits[x] for x in refs.get("commits", []) if x in commits] + \
                commits_between(ev.get("commits", []), ob.get("time"), nxt_t or os_.get("to"))
            cands = list({c["short"]: c for c in cands}.values())[:5]
            beat["evidenceRefs"] = dict(refs, commitCandidates=[f"{c['short']} {c['subject']}" for c in cands])
            c = cands[0] if cands else None
            if c:
                beat["artifact"] = {"label": "TODO artifact label", "preview": "TODO rewrite for the audience: " + c["subject"], "detail": c["body"][:4000],
                                    "source": f"commit {c['short']} · {c['date'][:10]}"}
            if ls["mode"] == "tree":
                cards = ([{"id": "h", "role": "human", "label": "TODO", "col": 0, "row": 0}] if pid else []) + \
                        [{"id": "d", "role": "discovery", "label": "TODO", "col": 1 if pid else 0, "row": 0},
                         {"id": "r", "role": "record", "label": "TODO", "col": 1, "row": 1}]
                routes = ([{"from": "h", "to": "d", "label": ""}] if pid else []) + [{"from": "d", "to": "r", "label": ""}]
                beat["tree"] = {"branch": min(bi // max(1, -(-len(ls["beats"]) // 6)), len(sc["tree"]["branches"]) - 1),
                                "title": "TODO detail heading", "cards": cards, "routes": routes,
                                "phases": [[i] for i in range(len(routes))], "note": ""}
            elif ls["mode"] == "flow":
                phases, active = flow_phases(ob["kind"], sc["graph"], refs, ev_dispatch)
                beat["flow"] = {"title": "TODO detail heading", "active": active, "phases": phases, "note": ""}
            else:
                beat["metrics"] = {"group": ["engines", "agents", "interactive"][min(bi, 2)], "highlight": [], "note": ""}
            sc["beats"].append(beat)
        scenes.append(sc)
    story = {
        "schema": "discovery-project-story/story@1", "version": "0.1.0",
        "title": "TODO story title", "subtitle": "TODO one-line subtitle", "audience": outline.get("audience") or "",
        "minutes": lock.get("minutes"), "gapMs": GAP_MS, "phaseMs": PHASE_MS,
        "theme": {"accentLight": "#3451b2", "accentDark": "#93a8ff"},
        "about": {"method": "Built from the project's own record: git history, the Discovery task board, engine and agent run "
                            "logs, and the human side of the chat sessions (assistant replies are never quoted). Human words "
                            "shown as quotes are exact and re-checked against their source at build time; everything else "
                            "is a labelled summary.",
                  "caveats": list(dict.fromkeys((outline.get("gaps") or []) + (tok.get("caveats") or [])))},
        "scenes": scenes,
    }
    (work / "story.json").write_text(json.dumps(story, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    todo = json.dumps(story).count("TODO")
    print(json.dumps({"story": str(work / "story.json"), "scenes": len(scenes),
                      "beats": sum(len(s["beats"]) for s in scenes), "todoFields": todo}, indent=1))


def clean_excerpt(text, cap=320):
    """Longest exact substring free of internal ids, trimmed to whole words (still exact)."""
    parts, last = [], 0
    for m in BANNED.finditer(text):
        parts.append((last, m.start()))
        last = m.end()
    parts.append((last, len(text)))
    a, b = max(parts, key=lambda p: p[1] - p[0])
    seg = text[a:b]
    lead = len(seg) - len(seg.lstrip(" ,.;:()-\n\t"))
    seg = seg[lead:].rstrip(" ,;:(-\n\t")
    if len(seg) > cap:
        cut = seg.rfind(" ", 0, cap)
        seg = seg[:cut if cut > 40 else cap].rstrip()
    return seg if len(seg) >= 20 else "TODO choose an exact excerpt without internal ids"


def utc(s):
    if not s or s == "?":
        return None
    s = s.replace("Z", "+00:00")
    d = dt.datetime.fromisoformat(s if ("+" in s[10:] or len(s) > 16) else s.replace(" ", "T") + "+00:00")
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()


def commits_between(commits, lo, hi, cap_hours=6):
    a, b = utc(lo), utc(hi)
    if a is None:
        return []
    b = min(b or a + cap_hours * 3600, a + cap_hours * 3600)
    return sorted((c for c in commits if a <= (utc(c["date"]) or 0) <= b), key=lambda c: utc(c["date"]))


def token_metrics(tok):
    eng = tok.get("engines") or {}
    cli = (eng.get("copilot-cli") or {}).get("byAgent") or {}
    clio = (eng.get("clio") or {}).get("byModel") or {}
    inter = (tok.get("interactive") or {}).get("byModel") or {}

    def total(d, calls="calls"):
        return {"input": sum(v.get("input", 0) for v in d.values()), "output": sum(v.get("output", 0) for v in d.values()),
                "cachedRead": sum(v.get("cachedRead", 0) for v in d.values()),
                "calls": sum(v.get(calls, 0) for v in d.values())}
    engines = []
    if cli:
        engines.append({"label": "Discovery engines & agents", **total(cli)})
    if clio:
        engines.append({"label": "Science engine (Clio)", **total(clio)})
    if inter:
        engines.append({"label": "Interactive chat", **total(inter, "requests")})
    agents = sorted(({"label": short_agent(k), "input": v["input"], "output": v["output"], "cachedRead": v.get("cachedRead", 0),
                      "calls": v.get("calls", 0)} for k, v in cli.items()), key=lambda r: -r["input"])
    models = sorted(({"label": k, "input": v["input"], "output": v["output"], "cachedRead": 0, "calls": v.get("requests", 0)}
                     for k, v in inter.items()), key=lambda r: -r["input"])
    return {"groups": [{"id": "engines", "label": "Token usage by surface", "rows": engines},
                       {"id": "agents", "label": "Engine and agent runs", "rows": agents},
                       {"id": "interactive", "label": "Interactive chat by model", "rows": models}],
            "caveats": tok.get("caveats") or ["No token usage was mined."]}


# ── build ────────────────────────────────────────────────────────────────────

class Problems:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}")


def main_surface(story):
    """Yield (where, text) for every string an audience sees without opening a drawer."""
    yield "story.title", story.get("title")
    yield "story.subtitle", story.get("subtitle")
    for s in story["scenes"]:
        w = s["id"]
        yield f"{w}.title", s.get("title")
        yield f"{w}.milestone", s.get("milestone")
        if s.get("tree"):
            yield f"{w}.tree.root", s["tree"].get("root")
            for i, b in enumerate(s["tree"].get("branches", [])):
                yield f"{w}.branch{i}", b.get("title")
                yield f"{w}.branch{i}.sub", b.get("sub")
        for n in (s.get("graph") or {}).get("nodes", []):
            yield f"{w}.node.{n.get('id')}", n.get("label")
        for e in (s.get("graph") or {}).get("edges", []):
            yield f"{w}.edge.{e.get('id')}", e.get("label")
        for b in s["beats"]:
            x = f"{w}.{b['id']}"
            yield f"{x}.label", b.get("label")
            h = b.get("human") or {}
            yield f"{x}.human.summary", h.get("summary")
            yield f"{x}.human.relation", h.get("relation")
            yield f"{x}.human.heading", h.get("heading")
            for k in ("action", "result"):
                yield f"{x}.discovery.{k}", (b.get("discovery") or {}).get(k)
            a = b.get("artifact") or {}
            yield f"{x}.artifact.label", a.get("label")
            yield f"{x}.artifact.preview", a.get("preview")
            t = b.get("tree") or {}
            yield f"{x}.tree.title", t.get("title")
            yield f"{x}.tree.note", t.get("note")
            for c in t.get("cards", []):
                yield f"{x}.card.{c.get('id')}", c.get("label")
            for r in t.get("routes", []):
                yield f"{x}.route", r.get("label")
            f = b.get("flow") or {}
            yield f"{x}.flow.title", f.get("title")
            yield f"{x}.flow.note", f.get("note")
            m = b.get("metrics") or {}
            yield f"{x}.metrics.title", m.get("title")
            yield f"{x}.metrics.note", m.get("note")


def validate(story, lock, ev, P, only, skip_source):
    if "TODO" in json.dumps(story):
        for where, text in walk(story):
            if isinstance(text, str) and "TODO" in text:
                P.err(where, "unfinished TODO")
    if not only:
        ls = [(s["id"], s["mode"], [b["id"] for b in s["beats"]]) for s in lock["scenes"]]
        ss = [(s["id"], s["mode"], [b["id"] for b in s["beats"]]) for s in story["scenes"]]
        if ls != ss:
            P.err("story", "scene/beat ids or modes differ from the approved lock — re-propose and re-approve instead of drifting")
        for L, S in zip(lock["scenes"], story["scenes"]):
            if L["title"] != S["title"]:
                P.warn(S["id"], f"title changed after approval: {L['title']!r} → {S['title']!r} (confirm with the user)")
            for lb, sb in zip(L["beats"], S["beats"]):
                if lb.get("primaryId") != (sb.get("human") or {}).get("quoteId"):
                    P.err(f"{S['id']}.{sb['id']}", "primary quote differs from the approved outline")
    for where, text in main_surface(story):
        if not text:
            continue
        if BANNED.search(text):
            P.err(where, f"internal identifier/path on the main surface: {BANNED.search(text).group(0)!r}")
        for rx, why in LINT:
            m = rx.search(text)
            if m and not NEGATION.search(text[max(0, m.start() - 40):m.start()]):
                P.warn(where, why)
    humans = {h["id"]: h for h in ev.get("human", [])}
    quotes, cache = {}, {}
    for s in story["scenes"]:
        if s["mode"] not in ("tree", "flow", "metrics"):
            P.err(s["id"], f"unknown mode {s['mode']}")
            continue
        check_mode(s, P)
        for b in s["beats"]:
            w = f"{s['id']}.{b['id']}"
            h = b.get("human") or {}
            if not h.get("summary"):
                P.err(w, "human.summary is required")
            qid = h.get("quoteId")
            if qid is None:
                if h.get("heading") not in HEADINGS_NO_QUOTE:
                    P.err(w, "beats without a quote must use heading 'Project scope' or 'Recorded outcome' (never inherit a quote)")
            ids = ([qid] if qid else []) + [x for x in b.get("exchangeIds", []) if x != qid]
            for x in ids:
                if x not in humans:
                    P.err(w, f"quote {x} not in evidence.json")
                    continue
                quotes[x] = humans[x]
                if SECRET.search(humans[x]["text"]):
                    P.err(w, f"quote {x} looks like it contains a credential; choose another quote or drop it from exchangeIds")
            if qid in humans:
                full, ex = humans[qid]["text"], h.get("excerpt")
                if ex is not None and ex not in full:
                    P.err(w, "human.excerpt must be an exact substring of the quote (no edits, no paraphrase)")
                shown = ex if ex is not None else full[:320]
                if BANNED.search(shown):
                    P.err(w, f"the displayed quote contains {BANNED.search(shown).group(0)!r}; set human.excerpt to an exact "
                             "substring that leaves it out (the full text stays in the exchange drawer)")
    checked = 0
    if not skip_source:
        for qid, h in quotes.items():
            loc = h["locator"]
            try:
                v = follow(json.loads(read_line(loc["file"], loc["line"], cache)), loc["pointer"])
                text = answer_text(v) if h["kind"] == "answer" else v
                if text != h["text"] or sha(text.encode()) != loc["sha256"]:
                    P.err(qid, "quote no longer matches its chat-session source")
                checked += 1
            except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                P.err(qid, f"cannot re-read source ({exc.__class__.__name__})")
    return quotes, checked


def walk(o, path="story"):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("chronology", "evidenceRefs"):
                continue
            yield from walk(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, o


def check_mode(s, P):
    w = s["id"]
    if s["mode"] == "tree":
        br = (s.get("tree") or {}).get("branches") or []
        if not 1 <= len(br) <= 6:
            P.err(w, f"tree needs 1–6 branches (has {len(br)})")
        for b in s["beats"]:
            t, x = b.get("tree") or {}, f"{w}.{b['id']}"
            if not isinstance(t.get("branch"), int) or not 0 <= t["branch"] < len(br):
                P.err(x, "tree.branch out of range")
            cards, seen, pos = t.get("cards") or [], set(), set()
            if not 1 <= len(cards) <= 6:
                P.err(x, "1–6 cards per beat")
            for c in cards:
                if c.get("id") in seen:
                    P.err(x, f"duplicate card {c.get('id')}")
                seen.add(c.get("id"))
                if c.get("role") not in ("human", "discovery", "record"):
                    P.err(x, f"card {c.get('id')} role must be human/discovery/record")
                if c.get("col", 0) not in (0, 0.5, 1) or c.get("row", 0) not in (0, 1, 2):
                    P.err(x, f"card {c.get('id')} col must be 0/0.5/1 and row 0/1/2")
                key = (c.get("col", 0), c.get("row", 0))
                if key in pos or (c.get("col", 0) == 0.5 and any(p[1] == key[1] for p in pos)) or \
                        any(p[0] == 0.5 and p[1] == key[1] for p in pos):
                    P.err(x, f"card {c.get('id')} overlaps another card")
                pos.add(key)
                if c.get("role") == "human" and not (b.get("human") or {}).get("quoteId"):
                    P.warn(x, "HUMAN card on a beat with no human quote — make sure it describes a recorded human action")
            routes = t.get("routes") or []
            for r in routes:
                if r.get("from") not in seen or r.get("to") not in seen:
                    P.err(x, f"route {r.get('from')}→{r.get('to')} references a missing card")
            flat = [i for ph in t.get("phases") or [] for i in ph]
            if sorted(flat) != list(range(len(routes))):
                P.err(x, "tree.phases must list every route index exactly once")
    elif s["mode"] == "flow":
        g = s.get("graph") or {}
        ids = [n.get("id") for n in g.get("nodes", [])]
        if len(ids) != len(set(ids)):
            P.err(w, "duplicate node ids")
        lanes = {}
        for n in g.get("nodes", []):
            if n.get("lane") not in (0, 1, 2, 3):
                P.err(w, f"node {n.get('id')} lane must be 0–3")
            lanes[n.get("lane")] = lanes.get(n.get("lane"), 0) + 1
            if n.get("kind") not in ("human", "discovery", "agent", "record"):
                P.err(w, f"node {n.get('id')} kind must be human/discovery/agent/record")
        for lane, k in lanes.items():
            if k > 7:
                P.err(w, f"lane {lane} has {k} nodes (max 7) — group repeated runs with count")
        eids = {e.get("id") for e in g.get("edges", [])}
        for e in g.get("edges", []):
            if e.get("from") not in ids or e.get("to") not in ids:
                P.err(w, f"edge {e.get('id')} references a missing node")
        for b in s["beats"]:
            f = b.get("flow") or {}
            for ph in f.get("phases") or []:
                for pk in ph:
                    if pk.get("edge") not in eids:
                        P.err(f"{w}.{b['id']}", f"phase references unknown edge {pk.get('edge')}")
            for a in f.get("active") or []:
                if a not in ids:
                    P.err(f"{w}.{b['id']}", f"active node {a} unknown")
    else:
        groups = {g["id"] for g in (s.get("metrics") or {}).get("groups", [])}
        for b in s["beats"]:
            if (b.get("metrics") or {}).get("group") not in groups:
                P.err(f"{w}.{b['id']}", "metrics.group must name a metrics group")


def when(ts):
    return (ts or "")[:16].replace("T", " ") + (" UTC" if ts else "")


def compile_story(story, quotes, lock, ev, template_bytes, story_bytes, lock_bytes):
    D = copy.deepcopy(story)
    phase_ms, gap = int(D.get("phaseMs") or PHASE_MS), int(D.get("gapMs") or GAP_MS)
    D["gapMs"] = gap
    for s in D["scenes"]:
        visited_nodes, visited_edges = set(), set()
        for b in s["beats"]:
            b.pop("evidenceRefs", None)
            if s["mode"] == "tree":
                phases = b["tree"]["phases"]
                b["routePhase"] = [next(pi for pi, ph in enumerate(phases) if i in ph) for i in range(len(b["tree"]["routes"]))]
            elif s["mode"] == "flow":
                phases = b["flow"].get("phases") or []
                b["visitedNodes"], b["visitedEdges"] = sorted(visited_nodes), sorted(visited_edges)
                edges = {e["id"]: e for e in s["graph"]["edges"]}
                for ph in phases:
                    for pk in ph:
                        visited_edges.add(pk["edge"])
                        visited_nodes.update([edges[pk["edge"]]["from"], edges[pk["edge"]]["to"]])
                visited_nodes.update(b["flow"].get("active") or [])
            else:
                phases = []
            n = len(phases)
            b["phaseWindows"] = [[i * phase_ms, (i + 1) * phase_ms] for i in range(n)]
            b["animationMs"] = n * phase_ms if n else METRIC_MS
    D["beatCount"] = sum(len(s["beats"]) for s in D["scenes"])
    D["quotes"] = {qid: {"kind": h["kind"], "text": h["text"], "title": h.get("questionTitle"),
                         "question": h.get("question") or None, "when": when(h.get("timestamp")),
                         "session": h.get("sessionTitle"),
                         "source": f"chat session {h.get('sessionId', '')[:8]} · line {h['locator']['line']} · sha256 {h['locator']['sha256'][:12]}"}
                   for qid, h in sorted(quotes.items())}
    D["build"] = {"storyVersion": D.get("version"), "skill": f"discovery-project-story {SKILL_VERSION}",
                  "storySha256": sha(story_bytes), "lockSha256": sha(lock_bytes) if lock_bytes else None,
                  "templateSha256": sha(template_bytes), "sourceHead": (ev.get("workspace") or {}).get("head"),
                  "approval": (lock or {}).get("approval")}
    theme = D.pop("theme", {}) or {}
    light, dark = theme.get("accentLight", "#3451b2"), theme.get("accentDark", "#93a8ff")
    if not (HEX.match(light) and HEX.match(dark)):
        raise SystemExit("theme accents must be #rgb or #rrggbb")
    data = json.dumps(D, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace("<", "\\u003c")
    page = template_bytes.decode("utf-8")
    for k, v in (("__TITLE__", html.escape(D.get("title") or "Discovery project story")),
                 ("__VERSION__", html.escape(str(D.get("version")))), ("__ACCENT_LIGHT__", light), ("__ACCENT_DARK__", dark)):
        page = page.replace(k, v)
    page = page.replace("__STORY_DATA__", data)
    return page.encode("utf-8"), D


def build(work, out, only, skip_source):
    story_path, lock_path = work / "story.json", work / "outline.lock.json"
    story_bytes = story_path.read_bytes()
    story, ev = json.loads(story_bytes), load(work / "evidence.json")
    lock_bytes = lock_path.read_bytes() if lock_path.exists() else None
    P = Problems()
    lock = json.loads(lock_bytes) if lock_bytes else None
    if not only:
        if not lock:
            sys.exit("no outline.lock.json — the user must approve the outline and it must be locked before a release build")
        if sha((work / "outline.json").read_bytes()) != lock["outlineSha256"]:
            P.err("lock", "outline.json changed after it was locked")
    if only:
        keep = set(only.split(","))
        story = dict(story, scenes=[s for s in story["scenes"] if s["id"] in keep])
        if not story["scenes"]:
            sys.exit(f"--only {only}: no such scene")
    quotes, checked = validate(story, lock, ev, P, bool(only), skip_source)
    template = TEMPLATE.read_bytes()
    report = {"errors": P.errors, "warnings": P.warnings, "quotesEmbedded": len(quotes),
              "quotesSourceChecked": checked, "sourceCheckSkipped": skip_source,
              "scenes": len(story["scenes"]), "beats": sum(len(s["beats"]) for s in story["scenes"])}
    if P.errors:
        print("\n".join(["BUILD FAILED"] + P.errors[:60] + ([f"... {len(P.errors) - 60} more"] if len(P.errors) > 60 else [])), file=sys.stderr)
        (work / "build-report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        sys.exit(1)
    page, D = compile_story(story, quotes, lock, ev, template, story_bytes, lock_bytes)
    again, _ = compile_story(story, quotes, lock, ev, template, story_bytes, lock_bytes)
    assert page == again, "non-deterministic build"
    if only:
        target = work / f"preview-{only.replace(',', '-')}.html"
    else:
        out.mkdir(parents=True, exist_ok=True)
        target = out / "discovery-work.html"
    target.write_bytes(page)
    report.update(output=str(target), sha256=sha(page), bytes=len(page),
                  plannedSeconds=sum(s["seconds"] for s in story["scenes"] if not s.get("appendix")),
                  modes={m: sum(1 for s in story["scenes"] if s["mode"] == m) for m in ("tree", "flow", "metrics")})
    rp = (work if only else out) / "build-report.json"
    rp.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for w in P.warnings[:40]:
        print("warning:", w, file=sys.stderr)
    print(json.dumps({k: report[k] for k in ("output", "sha256", "bytes", "scenes", "beats", "quotesEmbedded",
                                             "quotesSourceChecked", "plannedSeconds", "modes")}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("scaffold")
    a1.add_argument("--dir", required=True)
    a2 = sub.add_parser("build")
    a2.add_argument("--dir", required=True)
    a2.add_argument("--out")
    a2.add_argument("--only")
    a2.add_argument("--skip-source-check", action="store_true")
    a = ap.parse_args()
    work = Path(a.dir).resolve()
    if a.cmd == "scaffold":
        scaffold(work)
    else:
        build(work, Path(a.out).resolve() if a.out else work / "release", a.only, a.skip_source_check)


if __name__ == "__main__":
    main()
