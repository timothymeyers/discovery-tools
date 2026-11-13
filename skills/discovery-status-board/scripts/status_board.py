#!/usr/bin/env python3
"""Read-only Status board for a Discovery workspace.

Reads .discovery/{outcomes,rubrics,grades,tasks/taskentries} and renders a
single self-contained HTML page: outcome grade bar + kanban with collapsible
swimlanes following the task hierarchy. Never writes to the workspace.

Usage:
  status_board.py serve  --workspace DIR [--port 8766]   # live, re-reads per request
  status_board.py render --workspace DIR --out FILE      # one-off snapshot
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"

COLUMNS = ["todo", "progress", "waiting", "review", "done"]
COLUMN_LABELS = {
    "todo": "To Do",
    "progress": "In Progress",
    "waiting": "Waiting",
    "review": "Review",
    "done": "Done",
}
STATUS_COLUMN = {
    "new": "todo",
    "executing": "progress",
    "executiondone": "review",
    "complete": "done",
}
# Human-readable reasons for statuses that land in Waiting.
WAITING_REASONS = {
    "flaggedhuman": "Needs human input",
    "blocked": "Blocked",
    "failed": "Failed",
    "rejected": "Rejected in review",
    "paused": "Paused",
    "cancelled": "Cancelled",
}
YELLOW_BAND = 0.25  # below minimumScore but within this band -> yellow


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_dir(path: Path) -> list[dict]:
    if not path.is_dir():
        return []
    return [d for p in sorted(path.glob("*.json")) if isinstance(d := _load_json(p), dict)]


def load_outcomes(dx: Path) -> list[dict]:
    rubrics = {r.get("rubricId"): r for r in _load_dir(dx / "rubrics")}
    outcomes = sorted(_load_dir(dx / "outcomes"), key=lambda o: o.get("createdAt", ""))
    result = []
    for idx, o in enumerate(outcomes, 1):
        oid = o.get("outcomeId")
        rubric = rubrics.get(o.get("rubricId")) or {}
        grades = _load_dir(dx / "grades" / str(oid))
        grades.sort(key=lambda g: g.get("createdAt") or g.get("gradedAt") or "")
        latest = grades[-1] if grades else None
        score, color = None, "none"
        if latest:
            weights = {q.get("questionId"): q.get("weight") for q in rubric.get("questions", [])}
            qs = latest.get("questionScores") or []
            num = den = 0.0
            for q in qs:
                s = q.get("normalizedScore")
                if s is None:
                    continue
                w = weights.get(q.get("questionId")) or 1.0
                num += w * float(s)
                den += w
            if den:
                score = num / den
                minimum = float((rubric.get("passingCriteria") or {}).get("minimumScore", 0.8))
                if score >= minimum:
                    color = "green"
                elif score >= minimum - YELLOW_BAND:
                    color = "yellow"
                else:
                    color = "red"
        result.append({
            "id": oid,
            "short": f"O{idx}",
            "title": o.get("title", ""),
            "description": o.get("description", ""),
            "status": o.get("status", ""),
            "taskIds": o.get("taskIds") or [],
            "score": score,
            "color": color,
            "minimum": (rubric.get("passingCriteria") or {}).get("minimumScore"),
            "gradedAt": latest and (latest.get("createdAt") or latest.get("gradedAt")),
            "grader": latest and latest.get("graderId"),
        })
    return result


def load_tasks(dx: Path) -> dict[str, dict]:
    tasks = {}
    for t in _load_dir(dx / "tasks" / "taskentries"):
        if t.get("name"):
            tasks[t["name"]] = t
    return tasks


def build_model(workspace: Path) -> dict:
    dx = workspace / ".discovery"
    tasks = load_tasks(dx)
    outcomes = load_outcomes(dx)
    children: dict[str | None, list[dict]] = {}
    for t in tasks.values():
        pid = t.get("parentId") if t.get("parentId") in tasks else None
        children.setdefault(pid, []).append(t)
    for lst in children.values():
        lst.sort(key=lambda t: (t.get("siblingOrder", 0), t.get("dxId", "")))

    # Map every task to the outcomes covering it or any ancestor.
    task_outcomes: dict[str, set[str]] = {}

    def mark(tid: str, oid: str):
        task_outcomes.setdefault(tid, set()).add(oid)
        for c in children.get(tid, []):
            mark(c["name"], oid)

    for o in outcomes:
        for tid in o["taskIds"]:
            if tid in tasks:
                mark(tid, o["id"])

    def card(t: dict) -> dict:
        status = str(t.get("status", "")).strip()
        key = status.lower()
        col = STATUS_COLUMN.get(key, "waiting")
        unmet = [
            tasks[d].get("dxId", d[:8]) for d in t.get("dependsOn") or []
            if d in tasks and str(tasks[d].get("status", "")).lower() != "complete"
        ]
        reason = None
        if col == "waiting":
            reason = WAITING_REASONS.get(key, f"Status: {status or 'unknown'}")
        return {
            "id": t["name"],
            "dx": t.get("dxId", ""),
            "title": t.get("title", ""),
            "status": status,
            "column": col,
            "notReady": col == "todo" and bool(unmet),
            "unmet": unmet,
            "reason": reason,
            "labels": t.get("labels") or [],
            "outcomes": sorted(task_outcomes.get(t["name"], [])),
        }

    def lane(t: dict | None, depth: int) -> dict:
        kids = children.get(t["name"] if t else None, [])
        leaves = [card(k) for k in kids if not children.get(k["name"])]
        sublanes = [lane(k, depth + 1) for k in kids if children.get(k["name"])]
        counts = {c: 0 for c in COLUMNS}
        for c in leaves:
            counts[c["column"]] += 1
        for s in sublanes:
            for c in COLUMNS:
                counts[c] += s["counts"][c]
        return {
            "id": t["name"] if t else "__root__",
            "dx": t.get("dxId", "") if t else "",
            "title": t.get("title", "") if t else "Unparented tasks",
            "status": t.get("status", "") if t else "",
            "depth": depth,
            "cards": leaves,
            "lanes": sublanes,
            "counts": counts,
            "outcomes": sorted(task_outcomes.get(t["name"], [])) if t else [],
        }

    root = lane(None, 0)
    # A single top-level task (the project) becomes the page heading; its children are the lanes.
    project = None
    if not root["cards"] and len(root["lanes"]) == 1:
        project = root["lanes"][0]
        root = dict(project, title="", dx="")
        _shift(root, -1)
    return {
        "project": project and {"dx": project["dx"], "title": project["title"], "status": project["status"]},
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workspace": workspace.name,
        "columns": [{"key": c, "label": COLUMN_LABELS[c]} for c in COLUMNS],
        "outcomes": outcomes,
        "root": root,
        "taskCount": len(tasks),
    }


def _shift(lane: dict, delta: int):
    lane["depth"] += delta
    for s in lane["lanes"]:
        _shift(s, delta)


def render(workspace: Path) -> str:
    template = (ASSETS / "status-board.html").read_text(encoding="utf-8")
    payload = json.dumps(build_model(workspace)).replace("</", "<\\/")
    return (template
            .replace("__TITLE__", html.escape(workspace.name))
            .replace("__PAYLOAD__", payload))


def serve(workspace: Path, port: int):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                body, ctype = render(workspace).encode("utf-8"), "text/html; charset=utf-8"
            elif path == "/data.json":
                body, ctype = json.dumps(build_model(workspace)).encode("utf-8"), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Status board: http://127.0.0.1:{port}/", flush=True)
    server.serve_forever()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--workspace", required=True)
    s.add_argument("--port", type=int, default=8766)
    r = sub.add_parser("render")
    r.add_argument("--workspace", required=True)
    r.add_argument("--out", required=True)
    j = sub.add_parser("json")
    j.add_argument("--workspace", required=True)
    args = ap.parse_args(argv)

    ws = Path(args.workspace).expanduser().resolve()
    if not (ws / ".discovery").is_dir():
        print(f"error: no .discovery/ in {ws}", file=sys.stderr)
        return 2
    if args.cmd == "serve":
        serve(ws, args.port)
    elif args.cmd == "render":
        out = Path(args.out).expanduser().resolve()
        if out == ws or ws in out.parents:
            print("error: --out must be outside the workspace (read-only skill)", file=sys.stderr)
            return 2
        out.write_text(render(ws), encoding="utf-8")
        print(out)
    else:
        print(json.dumps(build_model(ws), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
