#!/usr/bin/env python3
"""Propose an evidence-derived scene/beat outline for a Discovery project story.

Reads evidence.json (from mine_exhaust.py) and writes:
  outline.json              machine-readable proposal (edit this after review)
  outline-proposal.md       human-readable proposal to show the user

Nothing here is final: the agent must rename draft titles in plain language,
show the proposal to the user, apply requested changes to outline.json, and
only then run lock_outline.py.

Scene count is derived, not fixed:
  1. Every event (human turn, commit, task, dispatch, run, grade) is mapped to
     a work unit = the top-level milestone its DX task lives under.
  2. Units are ordered by their activity centre and scored for story signal.
  3. Adjacent low-signal units are merged (same-root pairs first) until the
     time budget (~minutes*60/135 scenes, clamped 3–12) is met. If the
     evidence holds fewer strong units than the budget, fewer scenes are
     proposed — the outline is never padded.
  4. Beats are the highest-signal moments inside each scene (2–9, sized by
     the scene's share of speaking time), kept in chronological order.
  5. Mode per scene: "flow" when the record shows real concurrent dispatch or
     a multi-role loop, otherwise "tree".

Usage: propose_outline.py evidence.json --out DIR [--minutes 15] [--audience TEXT]
       [--no-token-scene] [--token-appendix] [--scenes N]
"""
import argparse
import datetime as dt
import json
import math
import re
from pathlib import Path

REDIRECT = re.compile(r"\b(instead|rather|actually|don'?t|do not|stop|change|rethink|why|wrong|missing|not what|let'?s|"
                      r"I want|I'd like|should|must|shouldn'?t|need(s)? to|realiz|concern|worried|scope|pivot|"
                      r"reconsider|what if|add|remove|drop|include)\b", re.I)
LOW = re.compile(r"^\s*(ok(ay)?|yes|y|go|continue|proceed|thanks?( you)?|sounds good|great|do it|commit( this| it)?|"
                 r"looks good|lgtm|next|sure|please continue|keep going|approved?)[\s.!]*$", re.I)
GATE = re.compile(r"major revision|reject|fail(ed|ure)?|reopen|regress|blocker|not ready|gate (fail|not)|revise and resubmit", re.I)
LOOP = re.compile(r"\b(cycle|round|iteration|pass)\s*[-#]?\s*(\d+)\b", re.I)
META = re.compile(r"presentation|project[- ]story|demo\b|slide|deck|storyboard", re.I)


def ts(s):
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def fmt(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if t else "?"


class Tree:
    def __init__(self, tasks):
        self.t = {x["dx"]: x for x in tasks}

    def depth(self, dx):
        d, cur, seen = 0, self.t.get(dx), set()
        while cur and cur.get("parent") in self.t and cur["dx"] not in seen:
            seen.add(cur["dx"])
            cur, d = self.t[cur["parent"]], d + 1
        return d

    def is_umbrella(self, dx):
        t = self.t.get(dx)
        return bool(t) and t.get("parent") not in self.t and bool(t.get("children"))

    def unit(self, dx):
        cur, seen = self.t.get(dx), set()
        if not cur or self.is_umbrella(dx):
            return None  # a root with children spans the whole project; place its events by time instead
        while cur.get("parent") in self.t and self.t[cur["parent"]].get("parent") in self.t and cur["dx"] not in seen:
            seen.add(cur["dx"])
            cur = self.t[cur["parent"]]
        return cur["dx"]

    def root(self, dx):
        cur, seen = self.t.get(dx), set()
        while cur and cur.get("parent") in self.t and cur["dx"] not in seen:
            seen.add(cur["dx"])
            cur = self.t[cur["parent"]]
        return cur["dx"] if cur else None

    def best_unit(self, ids):
        known = [d for d in ids or [] if d in self.t and not self.is_umbrella(d)]
        if not known:
            return None
        deepest = max(known, key=lambda d: (self.depth(d), int(d.split("-")[1])))
        return self.unit(deepest)


def human_signal(h):
    text = h["text"].strip()
    if h["kind"] == "control":
        return 0.1
    if h["kind"] == "answer":
        return 2.0 if len(text) > 60 else 0.6
    if LOW.match(text):
        return 0.3
    s = 1.0 + min(len(text), 1200) / 300.0
    s += 1.5 * min(len(REDIRECT.findall(text)), 4) / 2
    if h.get("steering"):
        s += 1.5
    return round(s, 2)


def build_events(ev, tree):
    events = []
    first_task = min((ts(t["createdAt"]) for t in ev["tasks"] if t.get("createdAt")), default=None)
    for h in ev.get("human", []):
        events.append({"t": ts(h.get("timestamp")), "type": "human", "ref": h["id"], "kind": h["kind"],
                       "unit": tree.best_unit(re.findall(r"\bDX-\d+\b", h["text"])),
                       "signal": human_signal(h), "text": h["text"], "title": h.get("questionTitle"),
                       "session": h.get("sessionTitle")})
    for c in ev.get("commits", []):
        gate = bool(GATE.search(c["subject"] + " " + c["body"][:400]))
        events.append({"t": ts(c["date"]), "type": "commit", "ref": c["short"], "unit": tree.best_unit(c["dxIds"]),
                       "signal": (0.4 + (0.8 if c["category"] == "manuscript_and_packaging" else 0) + (1.5 if gate else 0)),
                       "gate": gate, "text": c["subject"], "category": c["category"]})
    for t in ev.get("tasks", []):
        if t.get("createdAt"):
            events.append({"t": ts(t["createdAt"]), "type": "task", "ref": t["dx"], "unit": tree.unit(t["dx"]),
                           "signal": 0.15 + (2.5 * t.get("reopened", 0)), "text": t["title"],
                           "reopened": t.get("reopened", 0)})
    for r in ev.get("engineRuns", []):
        events.append({"t": ts(r["startedAt"]), "type": "engine", "ref": r["id"], "engine": r["engine"],
                       "unit": tree.best_unit(r["dxIds"]), "signal": 0.8, "text": f"{r['engine']} run"})
        for d in r["dispatches"]:
            events.append({"t": ts(d["proposedAt"] or d["t"]), "end": ts(d["t"]) if d.get("synchronous") else None,
                           "type": "dispatch", "ref": d["dispatchId"], "agent": d.get("agent") or d["tool"],
                           "unit": tree.best_unit(d["dxIds"]), "signal": 0.5,
                           "text": d.get("label") or f"{d.get('agent') or d['tool']} dispatch", "engineRun": r["id"]})
    for s in ev.get("scienceRuns", []):
        events.append({"t": ts(s["startedAt"]), "end": ts(s["updatedAt"]), "type": "science", "ref": s["id"],
                       "agent": "clio", "unit": tree.best_unit(s["dxIds"]), "signal": 0.4, "text": s["goalHead"][:120]})
    for a in ev.get("agentRuns", []):
        events.append({"t": ts(a["startedAt"]), "end": ts(a["completedAt"]), "type": "agent", "ref": a["id"],
                       "agent": a["agentId"], "unit": tree.unit(a["taskId"]) if a.get("taskId") else None,
                       "signal": 0.4 if a.get("status") != "Failed" else 1.5, "text": f"{a['agentId']} run ({a.get('status')})"})
    for g in ev.get("grades", []):
        events.append({"t": ts(g["gradedAt"]), "type": "grade", "ref": g["id"], "unit": tree.unit(g["taskId"]) if g.get("taskId") else None,
                       "signal": 2.0 if str(g.get("passed")) in ("True", "true") else 3.0,
                       "text": f"grade {'passed' if str(g.get('passed')) in ('True', 'true') else 'not passed'} ({g.get('composite')})"})
    p = ev.get("purpose") or {}
    if p.get("createdAt"):
        events.append({"t": ts(p["createdAt"]), "type": "purpose", "ref": "purpose", "unit": "SETUP", "signal": 2.0,
                       "text": p.get("title") or "purpose set"})
    for o in ev.get("outcomes", []):
        early = first_task is None or (ts(o["createdAt"]) or 0) < first_task
        events.append({"t": ts(o["createdAt"]), "type": "outcome", "ref": o["id"], "unit": "SETUP" if early else None,
                       "signal": 0.8 if early else 3.0, "text": o.get("title")})
    events = [e for e in events if e["t"]]
    events.sort(key=lambda e: (e["t"], e["type"], str(e["ref"])))
    for e in events:
        if e["unit"] is None and first_task and e["t"] < first_task:
            e["unit"] = "SETUP"
    return events


def assign_unmapped(events):
    """Give each unmapped event the unit of the nearest mapped event, preferring the next one (work follows asks)."""
    mapped = [e for e in events if e["unit"]]
    for i, e in enumerate(events):
        if e["unit"]:
            continue
        nxt = next((x for x in events[i + 1:] if x["unit"] and x["t"] - e["t"] < 6 * 3600), None)
        prv = next((x for x in reversed(events[:i]) if x["unit"]), None)
        e["unit"] = (nxt or prv or (mapped[0] if mapped else {"unit": "PROJECT"}))["unit"]
        e["inferredUnit"] = True


def max_concurrency(evts):
    spans = sorted((e["t"], e["end"] or e["t"] + 60) for e in evts if e["type"] in ("dispatch", "science", "agent"))
    best, active = 0, []
    for s, en in spans:
        active = [x for x in active if x > s] + [en]
        best = max(best, len(active))
    return best


def score(evts):
    humans = [e for e in evts if e["type"] == "human"]
    s = sum(e["signal"] for e in evts)
    conc = max_concurrency(evts)
    s += 2.0 * max(0, min(conc, 8) - 1)
    s += 1.5 * len({e.get("agent") for e in evts if e.get("agent")})
    return round(s + 2.0 * len([h for h in humans if h["signal"] >= 3]), 2)


def cluster(units, target, tree):
    groups = [dict(u) for u in units]
    while len(groups) > target:
        best, bi = None, None
        for i in range(len(groups) - 1):
            a, b = groups[i], groups[i + 1]
            same_root = bool(set(a["roots"]) & set(b["roots"]))
            gap = max(0.0, b["start"] - a["end"]) / 3600.0
            cost = (a["score"] + b["score"]) * (0.6 if same_root else 1.0) * (1 + min(gap, 48) / 24)
            if best is None or cost < best:
                best, bi = cost, i
        a, b = groups[bi], groups.pop(bi + 1)
        a["units"] += b["units"]
        a["roots"] = sorted(set(a["roots"]) | set(b["roots"]))
        a["events"] = sorted(a["events"] + b["events"], key=lambda e: e["t"])
        a["start"], a["end"] = min(a["start"], b["start"]), max(a["end"], b["end"])
        a["score"] = score(a["events"])
    return groups


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))]


def make_units(events, tree, ev):
    by = {}
    for e in events:
        by.setdefault(e["unit"], []).append(e)
    titles = {t["dx"]: t["title"] for t in ev["tasks"]}
    units, planned = [], []
    for u, evts in by.items():
        work = [e for e in evts if e["type"] != "task" or e.get("reopened")]
        title = "Project setup" if u == "SETUP" else titles.get(u, u)
        if not work and u != "SETUP":
            planned.append(title)
            continue
        times = [e["t"] for e in (work or evts)]
        units.append({"units": [u], "roots": [tree.root(u) or u], "events": evts, "score": score(evts),
                      "start": pct(times, 0.1), "end": pct(times, 0.9), "centre": pct(times, 0.5),
                      "title": title})
    units.sort(key=lambda x: x["centre"])
    return units, planned


def pick_beats(scene, seconds, ev):
    k = max(2, min(9, round(seconds / 32)))
    evts = scene["events"]
    cands = []
    for e in evts:
        if e["type"] == "human" and e["signal"] >= 1.2:
            cands.append(("human", e["signal"] + 2, e["t"], [e]))
        elif e["type"] == "grade" or (e["type"] == "commit" and e.get("gate")) or (e["type"] == "task" and e.get("reopened")):
            cands.append(("gate", e["signal"] + 1, e["t"], [e]))
        elif e["type"] == "purpose" or (e["type"] == "outcome" and e["signal"] >= 3):
            cands.append(("scope", 4, e["t"], [e]))
    # dispatch waves: dispatches/runs starting within 20 min of each other
    wave = []
    waves = []
    for e in [x for x in evts if x["type"] in ("dispatch", "science", "agent")]:
        if wave and e["t"] - wave[-1]["t"] > 1200:
            waves.append(wave)
            wave = []
        wave.append(e)
    if wave:
        waves.append(wave)
    for w in waves:
        if len(w) >= 2:
            cands.append(("dispatch", 1.5 + 0.6 * min(len(w), 8), w[0]["t"], w))
    deliver = [e for e in evts if e["type"] == "commit" and e["category"] == "manuscript_and_packaging"]
    if deliver:
        cands.append(("artifact", 2.5, deliver[-1]["t"], [deliver[-1]]))
    cands.sort(key=lambda c: -c[1])
    chosen, used = [], set()
    for kind, s, t, items in cands:
        key = (kind, items[0]["ref"])
        if key in used:
            continue
        # keep human beats from crowding each other: skip a human ask within 3 min of a chosen one
        if kind == "human" and any(c[0] == "human" and abs(c[2] - t) < 180 for c in chosen):
            continue
        chosen.append((kind, s, t, items))
        used.add(key)
        if len(chosen) >= k:
            break
    chosen.sort(key=lambda c: c[2])
    if not chosen:
        chosen = [("summary", 1, scene["start"], evts[:1])]
    beats = []
    for i, (kind, s, t, items) in enumerate(chosen, 1):
        first = items[0]
        refs = {"human": [x["ref"] for x in items if x["type"] == "human"],
                "commits": [x["ref"] for x in items if x["type"] == "commit"],
                "dispatches": [x["ref"] for x in items if x["type"] in ("dispatch", "science", "agent")],
                "tasks": [x["ref"] for x in items if x["type"] == "task"],
                "grades": [x["ref"] for x in items if x["type"] == "grade"]}
        # nearest human ask before a non-human beat (a candidate "why", not an assumed cause)
        if kind != "human":
            prior = [x for x in evts if x["type"] == "human" and x["t"] <= t and x["signal"] >= 1.2]
            if prior:
                refs["candidateCause"] = prior[-1]["ref"]
        label = (first.get("title") or first["text"] or "").strip().replace("\n", " ")
        if kind == "dispatch":
            agents = sorted({x.get("agent") or x["type"] for x in items})
            label = f"{len(items)} runs dispatched ({', '.join(agents[:4])}{'…' if len(agents) > 4 else ''})"
        beats.append({"id": f"B{i}", "kind": kind, "time": fmt(t), "signal": round(s, 2),
                      "primaryId": refs["human"][0] if kind == "human" else None,
                      "draftLabel": label[:110], "refs": refs,
                      "parallel": kind == "dispatch" and max_concurrency(items) >= 2})
    return beats


def loop_info(scene):
    counts = {}
    for e in scene["events"]:
        if e["type"] in ("task", "commit"):
            for m in LOOP.finditer(e["text"] or ""):
                counts.setdefault(m.group(1).lower(), set()).add(int(m.group(2)))
    best = max(counts.items(), key=lambda kv: len(kv[1]), default=(None, set()))
    return {"word": best[0], "cycles": len(best[1])} if len(best[1]) >= 3 else None


def actors(evts):
    engines, agents = {}, {}
    for e in evts:
        if e["type"] == "engine":
            engines[e["engine"]] = engines.get(e["engine"], 0) + 1
        elif e["type"] in ("dispatch", "science", "agent") and e.get("agent"):
            name = re.sub(r"^catalog[/-]", "", str(e["agent"]))
            agents[name] = agents.get(name, 0) + 1
    return {"engines": engines, "agents": dict(sorted(agents.items(), key=lambda kv: -kv[1])),
            "humanTurns": sum(1 for e in evts if e["type"] == "human" and e["kind"] == "request"),
            "commits": sum(1 for e in evts if e["type"] == "commit")}


CHRON_KIND = {"human": "human", "commit": "commit", "task": "task", "engine": "engine", "dispatch": "run",
              "science": "clio run", "agent": "agent run", "grade": "grade", "purpose": "purpose", "outcome": "outcome"}


def chronology(evts, cap=80):
    keep = evts
    if len(evts) > cap:
        ranked = sorted(evts, key=lambda e: -e["signal"])[:cap]
        ids = {id(e) for e in ranked}
        keep = [e for e in evts if id(e) in ids]
    out = []
    for e in keep:
        label = (e.get("title") + ": " if e.get("title") else "") + " ".join(str(e.get("text") or "").split())
        out.append({"t": fmt(e["t"]), "kind": CHRON_KIND.get(e["type"], e["type"]), "label": label[:220], "ref": str(e["ref"])})
    return out


def choose_mode(scene):
    conc = max_concurrency(scene["events"])
    agents = {e.get("agent") for e in scene["events"] if e.get("agent")}
    if conc >= 2 or len(agents) >= 3:
        return "flow", f"record shows up to {conc} concurrent runs across {len(agents)} actor types"
    return "tree", "work is mostly sequential decisions and artifacts; no recorded concurrency worth animating"


def propose(ev, minutes, n_scenes, token_scene, token_appendix):
    tree = Tree(ev.get("tasks", []))
    events = build_events(ev, tree)
    assign_unmapped(events)
    units, planned = make_units(events, tree, ev)
    speak = minutes * 60
    token_seconds = 60 if token_scene and not token_appendix else 0
    budget = n_scenes or max(3, min(12, round((speak - token_seconds) / 135)))
    strong = [u for u in units if u["score"] >= 6]
    target = min(budget, max(3, len(strong))) if not n_scenes else n_scenes
    target = min(target, len(units)) if units else 0

    def realise(count):
        groups = cluster(units, count, tree) if units else []
        total = sum(math.sqrt(max(g["score"], 1)) for g in groups) or 1
        scenes = []
        for i, g in enumerate(groups, 1):
            secs = int(round(max(60, min(240, (speak - token_seconds) * math.sqrt(max(g["score"], 1)) / total)) / 15) * 15)
            mode, why = choose_mode(g)
            beats = pick_beats(g, secs, ev)
            humans = [e for e in g["events"] if e["type"] == "human"]
            title = " + ".join(dict.fromkeys(
                u if u == "SETUP" else next((t["title"] for t in ev["tasks"] if t["dx"] == u), u) for u in g["units"]))
            scenes.append({
                "id": f"S{i}", "draftTitle": title[:160],
                "metaCandidate": bool(META.search(title)),
                "units": g["units"], "from": fmt(g["start"]), "to": fmt(g["end"]), "seconds": secs,
                "mode": mode, "modeReason": why, "score": g["score"], "loop": loop_info(g),
                "counts": {"humanTurns": sum(1 for h in humans if h["kind"] == "request"),
                           "formAnswers": sum(1 for h in humans if h["kind"] == "answer"),
                           "commits": sum(1 for e in g["events"] if e["type"] == "commit"),
                           "dispatches": sum(1 for e in g["events"] if e["type"] in ("dispatch", "science", "agent")),
                           "maxConcurrency": max_concurrency(g["events"]),
                           "gates": sum(1 for e in g["events"] if e.get("gate") or e.get("reopened") or e["type"] == "grade")},
                "beats": beats,
                "actors": actors(g["events"]),
                "chronology": chronology(g["events"]),
            })
        return scenes

    scenes = realise(target)
    if token_scene:
        scenes.append({"id": f"S{len(scenes) + 1}", "draftTitle": "What the work cost in tokens", "units": [],
                       "seconds": 0 if token_appendix else token_seconds, "mode": "metrics",
                       "modeReason": "token usage is quantitative; shown as bars, not a process",
                       "appendix": bool(token_appendix), "score": None, "loop": None, "counts": {},
                       "beats": [{"id": "B1", "kind": "metrics", "primaryId": None, "draftLabel": "Usage by engine", "refs": {}},
                                 {"id": "B2", "kind": "metrics", "primaryId": None, "draftLabel": "Usage by agent / model", "refs": {}}]})
    alt = {"compact": [{"draftTitle": s["draftTitle"], "from": s["from"], "to": s["to"]} for s in realise(max(3, target - 2))],
           "detailed": [{"draftTitle": s["draftTitle"], "from": s["from"], "to": s["to"]} for s in realise(min(12, len(units), target + 3))]}
    unmapped = sum(1 for e in events if e.get("inferredUnit"))
    gaps = list(ev.get("notes", []))
    if unmapped:
        gaps.append(f"{unmapped} events had no task reference and were placed by time proximity (verify attribution).")
    tok = ev.get("tokens") or {}
    gaps += tok.get("caveats", [])
    if not ev.get("human"):
        gaps.append("No human chat turns were mined; the story cannot show human steering without them.")
    if planned:
        gaps.append(f"{len(planned)} planned work item(s) have no recorded work yet and are left out: " + "; ".join(planned[:8]))
    weak = [u["title"] for u in units if u["score"] < 6]
    return {
        "schema": "discovery-project-story/outline@1", "status": "proposed",
        "workspace": ev["workspace"]["name"], "head": ev["workspace"].get("head"),
        "minutes": minutes, "budgetScenes": budget, "proposedScenes": len(scenes),
        "scenes": scenes, "alternatives": alt, "lowSignalUnitsMerged": weak, "gaps": gaps,
        "unitsConsidered": [{"unit": u["units"][0], "title": u["title"], "score": u["score"], "from": fmt(u["start"]),
                             "to": fmt(u["end"])} for u in units],
    }


def humans_index(ev):
    return {h["id"]: h for h in ev.get("human", [])}


def markdown(o, ev, audience):
    H = humans_index(ev)
    lines = [f"# Story outline proposal — {o['workspace']}", "",
             f"Audience: {audience or '(not specified)'} · Spoken length: {o['minutes']} min · "
             f"Budget ≈ {o['budgetScenes']} scenes · Proposed: **{o['proposedScenes']} scenes**", "",
             "> Draft titles come from task names; rewrite them in plain language before showing the audience. "
             "Beat labels are placeholders — exact human words stay in the evidence drawer.", "",
             "| # | Draft title | Window (UTC) | Mode | Time | Beats | Signal |", "|---|---|---|---|---|---|---|"]
    for s in o["scenes"]:
        lines.append(f"| {s['id']} | {s['draftTitle']} | {s.get('from', '—')} → {s.get('to', '—')} | {s['mode']} | "
                     f"{s['seconds']}s{' (appendix)' if s.get('appendix') else ''} | {len(s['beats'])} | {s['score'] if s['score'] is not None else '—'} |")
    for s in o["scenes"]:
        lines += ["", f"## {s['id']} — {s['draftTitle']}", "",
                  f"*Mode:* **{s['mode']}** — {s['modeReason']}."]
        if s.get("counts"):
            c = s["counts"]
            lines.append(f"*Evidence:* {c['humanTurns']} human requests, {c['formAnswers']} form answers, {c['commits']} commits, "
                         f"{c['dispatches']} dispatched runs (max {c['maxConcurrency']} concurrent), {c['gates']} gate/grade events.")
        if s.get("loop"):
            lines.append(f"*Loop:* {s['loop']['cycles']} {s['loop']['word']}s detected — recommend one collapsed beat with the full chronology in a drawer.")
        if s.get("metaCandidate"):
            lines.append("*Meta:* this looks like work on the presentation itself — confirm whether it belongs in the story or should be cut.")
        lines.append("")
        for b in s["beats"]:
            q = H.get(b.get("primaryId") or "")
            said = q["text"].strip().replace("\n", " ") if q else ""
            text = f"“{said[:160]}{'…' if len(said) > 160 else ''}” ({q['id']})" if q else b["draftLabel"]
            cause = f" · nearest prior ask: {b['refs']['candidateCause']}" if b.get("refs", {}).get("candidateCause") else ""
            lines.append(f"- **{b['id']}** [{b['kind']}{', parallel' if b.get('parallel') else ''}] {b.get('time', '')} — {text}{cause}")
    lines += ["", "## Alternatives", "", f"**Compact ({len(o['alternatives']['compact'])} scenes):** " +
              " → ".join(x["draftTitle"][:48] for x in o["alternatives"]["compact"]), "",
              f"**Detailed ({len(o['alternatives']['detailed'])} scenes):** " +
              " → ".join(x["draftTitle"][:48] for x in o["alternatives"]["detailed"]), "",
              "## Merged or cut (low signal)", ""]
    lines += [f"- {t}" for t in o["lowSignalUnitsMerged"]] or ["- none"]
    lines += ["", "## Gaps and caveats", ""] + ([f"- {g}" for g in o["gaps"]] or ["- none"])
    lines += ["", "## Questions for approval", "",
              "1. Is the scene count right, or should we use the compact / detailed alternative?",
              "2. Any scene to merge, split, cut, or reorder?",
              "3. Any human moment missing that must be on screen?",
              "4. Token usage as a scene, an appendix, or omitted?", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("evidence")
    ap.add_argument("--out", required=True)
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--scenes", type=int, help="force a scene count (only after the user asks for one)")
    ap.add_argument("--audience")
    ap.add_argument("--no-token-scene", action="store_true")
    ap.add_argument("--token-appendix", action="store_true")
    a = ap.parse_args()
    ev = json.loads(Path(a.evidence).read_text(encoding="utf-8"))
    o = propose(ev, a.minutes, a.scenes, not a.no_token_scene, a.token_appendix)
    o["audience"] = a.audience
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "outline.json").write_text(json.dumps(o, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "outline-proposal.md").write_text(markdown(o, ev, a.audience), encoding="utf-8")
    print(json.dumps({"outline": str(out / "outline.json"), "proposal": str(out / "outline-proposal.md"),
                      "budget": o["budgetScenes"], "scenes": [(s["id"], s["mode"], len(s["beats"]), s["draftTitle"][:60]) for s in o["scenes"]]},
                     indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
