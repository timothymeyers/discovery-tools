#!/usr/bin/env python3
"""Freeze an approved outline so the build cannot drift from what the user approved.

Run ONLY after the user has explicitly approved the outline (chat reply or
question-form answer). Pass the user's approval words verbatim with --approval.

Writes outline.lock.json next to outline.json with:
  - sha256 of outline.json at approval time
  - the approved scene/beat skeleton (ids, titles, modes, seconds, primaryIds)
  - the approval text and timestamp

build_story.py refuses to build when story.json disagrees with the lock or
outline.json has changed since locking. Re-run this after any re-approval.

Usage: lock_outline.py DIR/outline.json --approval "user's words" [--force]
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outline")
    ap.add_argument("--approval", required=True, help="the user's approval, verbatim")
    ap.add_argument("--force", action="store_true", help="overwrite an existing lock")
    a = ap.parse_args()
    path = Path(a.outline)
    raw = path.read_bytes()
    o = json.loads(raw)
    lock_path = path.with_name("outline.lock.json")
    if lock_path.exists() and not a.force:
        sys.exit(f"{lock_path} exists; pass --force only after the user re-approved a changed outline")
    problems = []
    for s in o["scenes"]:
        title = s.get("title") or s.get("draftTitle")
        if not s.get("title"):
            problems.append(f"{s['id']}: set a plain-language 'title' (draftTitle is the task name)")
        if not 1 <= len(s["beats"]) <= 12:
            problems.append(f"{s['id']}: {len(s['beats'])} beats (allowed 1–12)")
        if s["mode"] not in ("tree", "flow", "metrics"):
            problems.append(f"{s['id']}: unknown mode {s['mode']}")
        s["_title"] = title
    if problems:
        sys.exit("outline not lockable:\n  " + "\n  ".join(problems))
    lock = {
        "schema": "discovery-project-story/lock@1",
        "outlineSha256": hashlib.sha256(raw).hexdigest(),
        "approvedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "approval": a.approval,
        "attestation": "Locked by the agent after the user's approval above; this file does not itself prove approval.",
        "minutes": o.get("minutes"),
        "scenes": [{"id": s["id"], "title": s["_title"], "mode": s["mode"], "seconds": s["seconds"],
                    "appendix": bool(s.get("appendix")),
                    "beats": [{"id": b["id"], "kind": b["kind"], "primaryId": b.get("primaryId")} for b in s["beats"]]}
                   for s in o["scenes"]],
    }
    lock_path.write_text(json.dumps(lock, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"lock": str(lock_path), "scenes": len(lock["scenes"]),
                      "beats": sum(len(s["beats"]) for s in lock["scenes"])}, indent=1))


if __name__ == "__main__":
    main()
