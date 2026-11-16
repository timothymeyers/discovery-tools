#!/usr/bin/env python3
"""Re-verify exact human quotes against their original chat-session source.

For every human item (all of evidence.json, or only those a story.json cites),
reopen the recorded file, read the recorded line, follow the JSON pointer and
check the text is byte-identical and its sha256 matches.

Usage: verify_quotes.py evidence.json [--story story.json] [--out report.json]
Exit status 1 if any cited quote fails.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mine_exhaust import answer_text  # noqa: E402


def follow(doc, pointer):
    cur = doc
    for part in pointer.strip("/").split("/"):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def read_line(path, number, cache):
    if path not in cache:
        cache[path] = {}
    if number not in cache[path]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                if n == number:
                    cache[path][number] = line
                    break
    return cache[path].get(number)


def cited_ids(story):
    ids = set()
    for scene in story.get("scenes", []):
        for beat in scene.get("beats", []):
            h = beat.get("human") or {}
            if h.get("quoteId"):
                ids.add(h["quoteId"])
            ids.update(beat.get("exchangeIds") or [])
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("evidence")
    ap.add_argument("--story")
    ap.add_argument("--out")
    a = ap.parse_args()
    ev = json.loads(Path(a.evidence).read_text(encoding="utf-8"))
    items = {h["id"]: h for h in ev.get("human", [])}
    wanted = cited_ids(json.loads(Path(a.story).read_text(encoding="utf-8"))) if a.story else set(items)
    cache, results = {}, []
    for qid in sorted(wanted):
        h = items.get(qid)
        if not h:
            results.append({"id": qid, "status": "FAIL", "reason": "not in evidence.json"})
            continue
        loc = h["locator"]
        try:
            line = read_line(loc["file"], loc["line"], cache)
            doc = json.loads(line)
            value = follow(doc, loc["pointer"])
            text = answer_text(value) if h["kind"] == "answer" else value
            ok = text == h["text"] and hashlib.sha256(text.encode("utf-8")).hexdigest() == loc["sha256"]
            results.append({"id": qid, "status": "PASS" if ok else "FAIL",
                            "reason": None if ok else "text or hash differs from source"})
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            results.append({"id": qid, "status": "FAIL", "reason": f"{exc.__class__.__name__}: {exc}"[:200]})
    fails = [r for r in results if r["status"] != "PASS"]
    report = {"checked": len(results), "passed": len(results) - len(fails), "failed": fails}
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: (v if k != "failed" else v[:10]) for k, v in report.items()}, indent=1))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
