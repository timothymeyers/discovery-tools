#!/usr/bin/env python3
"""Generate the single-file dashboard runtime.

Authoring is multi-file (collect.py + serve.py + index.html) because that stays
maintainable and testable. Shipping also includes a single-file build, so the
dashboard can be dropped into an arbitrary project and committed there without
dragging a directory along.

    python3 scripts/build.py           # writes dist/discovery_dashboard.py
    python3 scripts/build.py --check   # verify dist/ is current, exit 1 if stale

CI runs --check, so re-run the build after editing anything in scripts/.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
DIST = SRC.parent / "dist" / "discovery_dashboard.py"

HEADER = '''#!/usr/bin/env python3
"""Discovery project dashboard - generated single-file runtime.

DO NOT EDIT. Generated from scripts/{{collect,serve}}.py and scripts/index.html
by scripts/build.py. Edit those and re-run the build.

Read-only, loopback-only, standard library only.

    python3 discovery_dashboard.py [--port 8787] [--workspace .]
    python3 discovery_dashboard.py --once | --json | --html out.html

Source checksum: %s
"""
'''


def _strip_module_docstring(text):
    """Drop a leading module docstring so the generated file has exactly one."""
    stripped = text.lstrip()
    if not stripped.startswith(('"""', "'''")):
        return text
    quote = stripped[:3]
    end = stripped.find(quote, 3)
    if end == -1:
        return text
    return stripped[end + 3:].lstrip("\n")


def _strip_shebang(text):
    return text.split("\n", 1)[1] if text.startswith("#!") else text


def build():
    collect_src = (SRC / "collect.py").read_text(encoding="utf-8")
    serve_src = (SRC / "serve.py").read_text(encoding="utf-8")
    index_html = (SRC / "index.html").read_text(encoding="utf-8")

    checksum = hashlib.sha256(
        (collect_src + serve_src + index_html).encode("utf-8")
    ).hexdigest()[:16]

    collect_body = _strip_module_docstring(_strip_shebang(collect_src))
    # The collector's __main__ block would otherwise fire inside the bundle.
    collect_body = collect_body.split('if __name__ == "__main__":')[0].rstrip()
    # The __future__ import must lead the generated file, ahead of every other
    # statement, so it is hoisted rather than left inline.
    collect_body = collect_body.replace("from __future__ import annotations\n", "", 1)

    serve_body = _strip_module_docstring(_strip_shebang(serve_src))
    # Rewrite the parts of serve.py that assume a sibling module and a file on
    # disk. Everything else is reused verbatim.
    serve_body = serve_body.replace(
        'sys.path.insert(0, str(Path(__file__).resolve().parent))\n'
        'import collect as collector  # noqa: E402\n',
        '',
    )
    # Collector helpers are already in this module's namespace in the bundle.
    serve_body = serve_body.replace(
        'from collect import _process_start_epoch  # noqa: E402\n', '')
    serve_body = serve_body.replace(
        'HERE = Path(__file__).resolve().parent\nINDEX_PATH = HERE / "index.html"\n',
        '',
    )
    serve_body = serve_body.replace(
        'html = INDEX_PATH.read_text(encoding="utf-8")', 'html = INDEX_HTML'
    )
    serve_body = serve_body.replace(
        '''            try:
                self._send(200, INDEX_PATH.read_text(encoding="utf-8"), "text/html; charset=utf-8")
            except OSError as exc:
                self._send(500, "index.html unreadable: %s" % exc, "text/plain; charset=utf-8")''',
        '            self._send(200, INDEX_HTML, "text/html; charset=utf-8")',
    )
    # collector.X resolves to this module's own namespace in the bundle.
    serve_body = serve_body.replace("collector.collect(", "collect(")
    serve_body = serve_body.replace("collector.summarize(", "summarize(")
    # Only one `from __future__` is legal, and collect's copy already leads.
    serve_body = serve_body.replace("from __future__ import annotations\n", "")

    parts = [
        HEADER % checksum,
        "from __future__ import annotations\n",
        collect_body,
        "\n\n# " + "-" * 74,
        "# Embedded frontend (scripts/index.html)",
        "# " + "-" * 74 + "\n",
        "INDEX_HTML = r'''%s'''\n" % index_html.replace("'''", "\\'\\'\\'"),
        "\n# " + "-" * 74,
        "# Server (scripts/serve.py)",
        "# " + "-" * 74 + "\n",
        serve_body,
    ]
    return "\n".join(parts), checksum


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="fail if dist/ is stale rather than writing it")
    args = parser.parse_args(argv)

    content, checksum = build()

    if args.check:
        if not DIST.exists():
            print("dist/ missing; run: python3 scripts/build.py", file=sys.stderr)
            return 1
        if DIST.read_text(encoding="utf-8") != content:
            print("dist/ is stale; run: python3 scripts/build.py", file=sys.stderr)
            return 1
        print("dist/discovery_dashboard.py is current (%s)" % checksum)
        return 0

    DIST.parent.mkdir(parents=True, exist_ok=True)
    DIST.write_text(content, encoding="utf-8")
    DIST.chmod(0o755)
    print("wrote %s (%d KB, checksum %s)"
          % (DIST.relative_to(SRC.parent), len(content) // 1024, checksum))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
