#!/usr/bin/env python3
"""Read-only HTTP server for the portable Discovery project dashboard.

Binds to loopback only. Serves a fixed route table: no filesystem path is ever
derived from a request, and no raw workspace file, prompt, or log is exposed.

    python3 dashboard/serve.py [--port 8787] [--workspace .]
    python3 dashboard/serve.py --once        # text summary, exit
    python3 dashboard/serve.py --json        # full snapshot as JSON, exit
    python3 dashboard/serve.py --html out.html   # static snapshot, exit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect as collector  # noqa: E402

HERE = Path(__file__).resolve().parent
INDEX_PATH = HERE / "index.html"

# Collecting walks the log tails, so a short cache keeps polling cheap without
# letting the view go meaningfully stale.
CACHE_TTL_S = 2.5

_lock = threading.Lock()
_cache = {"at": 0.0, "snapshot": None}


def get_snapshot(workspace, force=False):
    with _lock:
        now = time.time()
        if not force and _cache["snapshot"] and (now - _cache["at"]) < CACHE_TTL_S:
            return _cache["snapshot"]
        snapshot = collector.collect(workspace)
        _cache["at"] = now
        _cache["snapshot"] = snapshot
        return snapshot


def render_static(workspace):
    """Self-contained HTML with the snapshot inlined, for sharing or archiving."""
    snapshot = get_snapshot(workspace, force=True)
    html = INDEX_PATH.read_text(encoding="utf-8")
    payload = json.dumps(snapshot).replace("</", "<\\/")
    return html.replace(
        "/*__STATIC_SNAPSHOT__*/",
        "window.__STATIC_SNAPSHOT__ = %s;" % payload,
    )


class Handler(BaseHTTPRequestHandler):
    workspace = "."
    server_version = "DiscoveryDashboard/1.0"

    def log_message(self, *args):
        pass  # keep the console clean for the operator

    def _send(self, code, body, content_type):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        # Nothing here is meant to be embedded or loaded cross-origin.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        # Fixed route table. No path from the request ever reaches the filesystem.
        if route == "/":
            try:
                self._send(200, INDEX_PATH.read_text(encoding="utf-8"), "text/html; charset=utf-8")
            except OSError as exc:
                self._send(500, "index.html unreadable: %s" % exc, "text/plain; charset=utf-8")
        elif route == "/api/snapshot":
            try:
                snapshot = get_snapshot(self.workspace)
                self._send(200, json.dumps(snapshot), "application/json; charset=utf-8")
            except Exception as exc:  # never take the server down on one bad read
                self._send(500, json.dumps({"error": str(exc)}),
                           "application/json; charset=utf-8")
        elif route == "/api/health":
            self._send(200, json.dumps({"ok": True, "workspace": str(self.workspace)}),
                       "application/json; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")


def serve(workspace, port, open_browser=True):
    Handler.workspace = workspace
    server = None
    chosen = port
    # An occupied port is common (an older instance is often still listening),
    # so walk forward rather than failing.
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            chosen = candidate
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit("no free port in range %d-%d" % (port, port + 19))

    url = "http://127.0.0.1:%d/" % chosen
    print("Discovery dashboard: %s   (Ctrl+C to stop)" % url)
    print("Workspace: %s" % workspace)
    if chosen != port:
        print("Note: port %d was busy; using %d." % (port, chosen))
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)),
                         daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Discovery project dashboard")
    parser.add_argument("--workspace", default=".", help="project root (default: cwd)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    parser.add_argument("--once", action="store_true", help="print a text summary and exit")
    parser.add_argument("--json", action="store_true", help="print the snapshot as JSON and exit")
    parser.add_argument("--html", metavar="PATH", help="write a static snapshot and exit")
    args = parser.parse_args(argv)

    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(os.path.join(workspace, ".discovery")):
        print("warning: %s has no .discovery/ directory; panels will report as missing."
              % workspace, file=sys.stderr)

    if args.once:
        print(collector.summarize(get_snapshot(workspace, force=True)))
        return 0
    if args.json:
        print(json.dumps(get_snapshot(workspace, force=True), indent=2))
        return 0
    if args.html:
        Path(args.html).write_text(render_static(workspace), encoding="utf-8")
        print("wrote %s" % args.html)
        return 0

    serve(workspace, args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
