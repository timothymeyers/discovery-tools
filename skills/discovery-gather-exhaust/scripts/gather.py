#!/usr/bin/env python3
"""Gather Discovery project exhaust (agent sessions, chats, logs, telemetry)
from outside the project into .discovery/exhaust/ and optionally commit it.

Default is a dry run. Use --apply to copy, which also commits unless --no-commit.
Python 3.12+, standard library only.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offload  # noqa: E402

OUT_REL = Path(".discovery/exhaust")
PRIVACY = ("raw", "scrub", "exclude-sensitive")
PRIVACY_RANK = {"raw": 0, "scrub": 1, "exclude-sensitive": 2}
CATEGORIES = ("agent-sessions", "chat", "copilot-process", "app-terminal", "discovery-express", "token-usage")
APP_SUPPORT_NAMES = ("DiscoveryAppPreview", "DiscoveryApp", "Discovery App")
UTC = timezone.utc

# --------------------------------------------------------------------------- scrubbing
_TOKCH = r"[A-Za-z0-9._~+/=-]"
SECRET_PATTERNS = [
    (re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?(?:bearer|basic|token)?\s*)" + _TOKCH + "{8,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+" + _TOKCH + "{16,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED-GH-TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED-GH-TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "[REDACTED-KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED-JWT]"),
    (re.compile(r"(?i)([?&]sig=)[^&\s\"'\\]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(AccountKey=)[^;\s\"'\\]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\"(?:api[_-]?key|client[_-]?secret|password|access[_-]?token|refresh[_-]?token)\"\s*:\s*\")[^\"\\]{6,}"), r"\1[REDACTED]"),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w.])(?:\+?1[ .-])?(?:\(\d{3}\) ?\d{3}-\d{4}|\d{3}-\d{3}-\d{4}|\d{3}\.\d{3}\.\d{4})(?![\w.])")
SENSITIVE_MARKERS = re.compile(r"(?i)ask work-iq to answer|workiq|work[ -]iq")
# System prompts list every skill (incl. work-iq's description); they hold no M365 data.
SYSTEM_PROMPT = re.compile(r"<version_information>|<skills>")
EXCLUDED_LINE = '"[EXCLUDED: Work IQ content]"'


class Scrubber:
    def __init__(self, privacy: str, names: list[str], salt: bytes):
        self.privacy = privacy
        self.salt = salt
        names = sorted({n.strip() for n in names if n.strip()}, key=len, reverse=True)
        self.names = re.compile(r"(?i)\b(" + "|".join(map(re.escape, names)) + r")\b") if names else None
        self.counts: dict[str, int] = {}

    def _ph(self, kind: str, value: str) -> str:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return f"<{kind}-{hmac.new(self.salt, value.lower().encode(), hashlib.sha256).hexdigest()[:8]}>"

    def text(self, s: str) -> str:
        for pat, rep in SECRET_PATTERNS:
            s, n = pat.subn(rep, s)
            if n:
                self.counts["secret"] = self.counts.get("secret", 0) + n
        if self.privacy == "raw":
            return s
        s = EMAIL.sub(lambda m: self._ph("email", m.group(0)), s)
        s = PHONE.sub(lambda m: self._ph("phone", m.group(0)), s)
        if self.names:
            s = self.names.sub(lambda m: self._ph("person", m.group(0)), s)
        return s

    def data(self, raw: bytes) -> tuple[bytes, bool]:
        """Returns (processed bytes, is_text)."""
        try:
            txt = raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw, False
        return self.text(txt).encode("utf-8"), True


def load_salt() -> bytes:
    p = Path(os.environ.get("GATHER_EXHAUST_SALT_FILE", Path.home() / ".config/discovery-gather-exhaust/salt"))
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_hex(32))
        p.chmod(0o600)
    return p.read_text().strip().encode()


# --------------------------------------------------------------------------- helpers
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_ts(s) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_text(p: Path) -> str:
    try:
        return p.read_text(errors="ignore")
    except OSError:
        return ""


def iter_files(root: Path):
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            p = Path(dirpath) / f
            if p.is_file() and not p.is_symlink():
                yield p


# --------------------------------------------------------------------------- discovery
@dataclass
class Run:
    run_id: str
    agent: str
    task: str | None
    start: datetime | None
    end: datetime | None
    links: dict = field(default_factory=lambda: {"session_state": [], "chat": [], "logs": []})


@dataclass
class Item:
    category: str
    dest: str                    # relative to .discovery/exhaust
    source: str
    match: str                   # how it was tied to this project
    sensitive: bool = False
    data: bytes | None = None    # processed output (filled lazily)
    raw_size: int = 0
    mode: str = "file"           # file | lines
    new_lines: list[str] = field(default_factory=list)


@dataclass
class Env:
    repo: Path
    copilot_home: Path
    app_support: Path | None
    since: datetime | None


def find_app_support(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    base = Path.home() / "Library/Application Support"
    for n in APP_SUPPORT_NAMES:
        if (base / n).exists():
            return base / n
    return None


def find_workspace_storage(env: Env) -> list[Path]:
    if not env.app_support:
        return []
    root = env.app_support / "user-data/User/workspaceStorage"
    hits = []
    for wj in root.glob("*/workspace.json"):
        try:
            folder = json.loads(wj.read_text()).get("folder", "")
        except (OSError, json.JSONDecodeError):
            continue
        if folder.startswith("file://") and Path(unquote(urlparse(folder).path)).resolve() == env.repo:
            hits.append(wj.parent)
    return hits


def load_runs(repo: Path) -> list[Run]:
    runs: dict[str, Run] = {}
    for f in sorted((repo / ".discovery/agent-runs").glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        rid = d.get("runId") or f.stem
        runs[rid] = Run(rid, d.get("agentId", ""), d.get("taskId"), parse_ts(d.get("startedAt")), parse_ts(d.get("completedAt")))
    for f in sorted((repo / ".discovery/sessions").glob("*.json")):
        try:
            h = json.loads(f.read_text()).get("header", {})
        except (OSError, json.JSONDecodeError):
            continue
        rid = h.get("legacyRunId") or h.get("sessionId") or f.stem
        r = runs.setdefault(rid, Run(rid, h.get("agentId", ""), h.get("taskId"), parse_ts(h.get("startedAt")), None))
        r.end = r.end or parse_ts(h.get("completedAt")) or parse_ts(h.get("lastActivityAt"))
    return list(runs.values())


def session_summary(d: Path) -> dict:
    first = last = None
    prompt = ""
    ev = d / "events.jsonl"
    if ev.exists():
        with ev.open(errors="ignore") as fh:
            for line in fh:
                try:
                    j = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = parse_ts(j.get("timestamp")) or parse_ts((j.get("data") or {}).get("startTime"))
                if ts:
                    first = first or ts
                    last = ts
                if not prompt and j.get("type") == "user.message":
                    prompt = str((j.get("data") or {}).get("content", ""))
    p = prompt.lstrip()
    if p.startswith("Ask work-iq"):
        kind = "workiq"
    elif p.startswith("<engine-memory>"):
        kind = "engine-memory"
    elif p.startswith("# Task Instructions"):
        kind = "agent-run"
    elif p.startswith("Your objective:"):
        kind = "subagent-review"
    else:
        kind = "other"
    task = re.search(r"\*\*#([A-Z]+-\d+)\*\*", prompt)
    return {"first": first, "last": last, "kind": kind, "task": task.group(1) if task else None}


def project_session_dirs(env: Env) -> list[Path]:
    root = env.copilot_home / "session-state"
    out = []
    for wy in sorted(root.glob("*/workspace.yaml")):
        y = read_text(wy)
        for key in ("cwd", "git_root"):
            m = re.search(rf"^{key}:\s*(.+)$", y, re.M)
            p = Path(m.group(1).strip().strip("'\"")).expanduser().resolve() if m else None
            if p and (p == env.repo or env.repo in p.parents):
                out.append(wy.parent)
                break
    return out


# --------------------------------------------------------------------------- collection
def collect(env: Env, runs: list[Run], include: set[str], privacy: str) -> tuple[list[Item], dict]:
    items: list[Item] = []
    ids = [r.run_id for r in runs if r.run_id]
    id_re = re.compile("|".join(map(re.escape, ids))) if ids else None
    repo_s = str(env.repo)
    repo_alts = {repo_s, repo_s.removeprefix("/private")}

    def mentions_repo(text: str) -> bool:
        return any(r in text for r in repo_alts)
    links: dict = {"generated_at": now_iso(), "runs": {}, "session_state": {}}
    by_id = {r.run_id: r for r in runs}
    windows: list[tuple[datetime, datetime]] = []
    for r in runs:
        if r.start:
            windows.append((r.start, r.end or r.start + timedelta(hours=2)))

    def recent(p: Path) -> bool:
        if not env.since:
            return True
        try:
            return datetime.fromtimestamp(p.stat().st_mtime, UTC) >= env.since
        except OSError:
            return False

    def id_hits(text: str) -> set[str]:
        return set(id_re.findall(text)) if id_re else set()

    # agent sessions --------------------------------------------------------
    if "agent-sessions" in include:
        for d in project_session_dirs(env):
            s = session_summary(d)
            ev_text = read_text(d / "events.jsonl")
            hits = id_hits(ev_text)
            if s["first"]:
                windows.append((s["first"], s["last"] or s["first"]))
            sid = d.name
            links["session_state"][sid] = {"kind": s["kind"], "task": s["task"],
                                           "started": s["first"].isoformat() if s["first"] else None,
                                           "mentions_runs": sorted(hits)}
            for rid in hits:
                by_id[rid].links["session_state"].append({"id": sid, "match": "run-id"})
            sensitive = s["kind"] == "workiq"
            for f in iter_files(d):
                if not recent(f):
                    continue
                items.append(Item("agent-sessions", f"agent-sessions/{sid}/{f.relative_to(d)}", str(f),
                                  f"cwd={repo_s} kind={s['kind']}", sensitive=sensitive, raw_size=f.stat().st_size))
        # time + task fallback for runs whose own session never names the run id
        for r in runs:
            if any(l["match"] == "run-id" and links["session_state"][l["id"]]["task"] == r.task
                   for l in r.links["session_state"]) or not r.start:
                continue
            for sid, info in links["session_state"].items():
                st = parse_ts(info["started"])
                if info["task"] and info["task"] == r.task and st and timedelta(0) <= st - r.start <= timedelta(seconds=30):
                    r.links["session_state"].append({"id": sid, "match": "time+task"})

    # chat (editor) -----------------------------------------------------------
    if "chat" in include:
        for ws in find_workspace_storage(env):
            groups = [("chat/sessions", ws / "chatSessions"),
                      ("chat/editing-sessions", ws / "chatEditingSessions"),
                      ("chat/transcripts", ws / "GitHub.copilot-chat/transcripts"),
                      ("chat/debug-logs", ws / "GitHub.copilot-chat/debug-logs"),
                      ("chat/tool-outputs", ws / "GitHub.copilot-chat/chat-session-resources"),
                      ("chat/memory", ws / "GitHub.copilot-chat/memory-tool")]
            for dest_root, src_root in groups:
                if not src_root.exists():
                    continue
                for f in iter_files(src_root):
                    if not recent(f):
                        continue
                    txt = read_text(f) if f.stat().st_size < 200 * 2**20 else ""
                    rel = f"{dest_root}/{f.relative_to(src_root)}"
                    for rid in id_hits(txt):
                        by_id[rid].links["chat"].append({"file": rel, "match": "run-id"})
                    items.append(Item("chat", rel, str(f), f"workspaceStorage/{ws.name}",
                                      sensitive="ask work-iq to answer" in txt.lower(), raw_size=f.stat().st_size))

    # copilot process logs ------------------------------------------------------
    if "copilot-process" in include:
        for f in sorted((env.copilot_home / "logs").glob("*.log")):
            if not recent(f):
                continue
            txt = read_text(f)
            hits = id_hits(txt)
            if not hits and not mentions_repo(txt):
                continue
            rel = f"logs/copilot-process/{f.name}"
            for rid in hits:
                by_id[rid].links["logs"].append({"file": rel, "match": "run-id"})
            items.append(Item("copilot-process", rel, str(f), "run-id" if hits else "project-path",
                              sensitive=bool(SENSITIVE_MARKERS.search(txt)), raw_size=f.stat().st_size))

    def filtered(category: str, srcs: list[Path], dest: str, keep) -> None:
        lines: list[str] = []
        seen: set[str] = set()
        for f in srcs:
            for line in read_text(f).splitlines():
                k = keep(line)
                if k and line not in seen:
                    seen.add(line)
                    lines.append(k if isinstance(k, str) else line)
        if lines:
            items.append(Item(category, dest, ", ".join(str(s) for s in srcs), "filtered-lines",
                              mode="lines", new_lines=lines, raw_size=sum(map(len, lines))))

    if env.app_support:
        tele = env.app_support / "telemetry"
        if "discovery-express" in include:
            srcs = sorted(tele.glob("logs/discovery-express.jsonl*"), key=lambda p: p.name, reverse=True)
            filtered("discovery-express", srcs, "telemetry/discovery-express.jsonl",
                     lambda l: bool(id_re and id_re.search(l)) or mentions_repo(l))
        if "token-usage" in include:
            win = [(a - timedelta(seconds=60), b + timedelta(seconds=60)) for a, b in windows]

            def in_window(line: str):
                try:
                    j = json.loads(line)
                except json.JSONDecodeError:
                    return False
                ts = parse_ts(j.get("timestamp")) if isinstance(j, dict) else None
                if not ts or j.get("_sentinel") or not any(a <= ts <= b for a, b in win):
                    return False
                j["_gather_match"] = "time-window"
                return json.dumps(j, separators=(",", ":"))
            filtered("token-usage", [tele / "token-usage.jsonl"], "telemetry/token-usage.jsonl", in_window)
        if "app-terminal" in include:
            for f in sorted((env.app_support / "user-data/logs").glob("*/terminal.log")):
                if recent(f):
                    filtered("app-terminal", [f], f"logs/app-terminal/{f.parent.name}.log",
                             lambda l: mentions_repo(l) or bool(id_re and id_re.search(l)))

    for r in runs:
        links["runs"][r.run_id] = {"agent": r.agent, "task": r.task,
                                   "started": r.start.isoformat() if r.start else None,
                                   "completed": r.end.isoformat() if r.end else None, **r.links}
    return items, links


# --------------------------------------------------------------------------- planning / applying
def load_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def match_policy(rel: str, policies: dict, default: str | None) -> str | None:
    for pat, choice in policies.items():
        if fnmatch.fnmatch(rel, pat):
            return choice
    return default


def process(items: list[Item], out: Path, manifest: dict, scrub: Scrubber, privacy: str,
            allow_downgrade: bool, policies: dict, default_policy: str | None) -> list[dict]:
    plan = []
    for it in items:
        rec = {"dest": it.dest, "category": it.category, "source": it.source, "match": it.match,
               "sensitive": it.sensitive}
        prev = manifest.get("files", {}).get(it.dest)
        if privacy == "exclude-sensitive" and it.sensitive and it.category != "copilot-process":
            plan.append({**rec, "status": "excluded-sensitive"})
            continue
        if prev and PRIVACY_RANK[prev.get("privacy", "raw")] > PRIVACY_RANK[privacy] and not allow_downgrade:
            plan.append({**rec, "status": "kept-stricter-privacy", "privacy": prev["privacy"]})
            continue
        if it.mode == "lines":
            dest = out / it.dest
            existing = dest.read_text().splitlines() if dest.exists() else []
            have = set(existing)
            add = [l for l in (scrub.text(x) for x in it.new_lines) if l not in have]
            it.data = ("\n".join(existing + add) + "\n").encode() if (existing or add) else b""
            rec["added_lines"] = len(add)
        elif it.category == "copilot-process" and privacy == "exclude-sensitive":
            lines = read_text(Path(it.source)).splitlines()
            kept, dropped = [], 0
            for l in lines:
                if SENSITIVE_MARKERS.search(l) and not SYSTEM_PROMPT.search(l):
                    dropped += 1
                    kept.append(re.match(r"\s*", l).group(0) + EXCLUDED_LINE)
                else:
                    kept.append(scrub.text(l))
            it.data = ("\n".join(kept) + "\n").encode()
            rec["redacted_lines"] = dropped
        else:
            raw = Path(it.source).read_bytes()
            it.data, is_text = scrub.data(raw)
            if not is_text:
                rec["binary"] = True
        digest = sha256(it.data)
        rec.update(size=len(it.data), sha256=digest, privacy=privacy)
        if prev and prev.get("sha256") == digest:
            rec["status"] = "unchanged"
        else:
            rec["status"] = "updated" if prev else "new"
            if len(it.data) > offload.WARN_BYTES:
                choice = match_policy(it.dest, policies, default_policy)
                rec["large"] = True
                rec["large_choice"] = choice
                if choice is None:
                    rec["status"] = "large-needs-decision"
                elif choice == "git" and len(it.data) > offload.HARD_BYTES:
                    rec["status"] = "large-needs-decision"
                    rec["note"] = "over 100 MB; plain git refused"
        plan.append(rec)
    return plan


def write_readme(out: Path, manifest: dict) -> None:
    counts: dict[str, int] = {}
    for f in manifest["files"].values():
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    rows = "\n".join(f"| {c} | {n} |" for c, n in sorted(counts.items()))
    (out / "README.md").write_text(f"""# Discovery exhaust

Copies of agent, chat, log and telemetry files that the Discovery App and Copilot
keep outside this project. Gathered by the `discovery-gather-exhaust` skill so
they stay with the project (and in git) after the app or editor cleans them up.
Last gathered: {manifest['last_gathered']}.

| Folder | What it is | Original location |
|---|---|---|
| `agent-sessions/<uuid>/` | Full Copilot CLI/SDK session for every agent run, Work IQ query, engine-memory and subagent session whose working folder is this project (`events.jsonl`, `files/`, `checkpoints/`) | `~/.copilot/session-state/` |
| `chat/sessions/`, `chat/editing-sessions/` | Editor chat sessions and edit sessions | `<app>/user-data/User/workspaceStorage/<hash>/` |
| `chat/transcripts/`, `chat/debug-logs/`, `chat/tool-outputs/`, `chat/memory/` | Copilot Chat transcripts, debug logs, large tool outputs, memory | `.../<hash>/GitHub.copilot-chat/` |
| `logs/copilot-process/` | Copilot process logs that mention this project or its runs | `~/.copilot/logs/` |
| `logs/app-terminal/<date>.log` | Only the lines of the app terminal log that mention this project | `<app>/user-data/logs/<date>/terminal.log` |
| `telemetry/discovery-express.jsonl` | Only this project's lines from the app service log (all rotations, de-duplicated) | `<app>/telemetry/logs/` |
| `telemetry/token-usage.jsonl` | Token-usage records inside this project's run/session time windows. Matched by time only (`_gather_match`), so may include other activity in the same window | `<app>/telemetry/token-usage.jsonl` |

`<app>` is `~/Library/Application Support/DiscoveryAppPreview`.

- `manifest.json`: every gathered file with source path, sha256, size, match method, privacy setting and gather time.
- `links.json`: each agent run (`.discovery/agent-runs`) linked to its session folders, chats and logs, with the match method (`run-id` or `time+task`).
- `config.json`: saved choices (privacy names list, large-file policy, blob target).

Gathering only adds or updates; it never deletes. Secrets (tokens, keys, auth
headers, SAS signatures) are always redacted. With privacy `scrub`, emails,
phone numbers and configured names become stable placeholders like `<person-1a2b3c4d>`.

| Category | Files |
|---|---|
{rows}
""")


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def commit(repo: Path, paths: list[str], message: str) -> tuple[bool, str]:
    paths = [p for p in paths if (repo / p).exists()]
    r = git(repo, "add", "--", *paths)
    if r.returncode:
        return False, r.stderr.strip()
    if git(repo, "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return False, "nothing changed; no commit made"
    r = git(repo, "commit", "-m", message, "--", *paths)
    if r.returncode:
        return False, f"commit failed (files left staged): {(r.stderr or r.stdout).strip()[:600]}"
    return True, git(repo, "log", "-1", "--format=%h %s").stdout.strip()


# --------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", default=".", help="project root (default: cwd)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True)
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--privacy", choices=PRIVACY, help="raw | scrub | exclude-sensitive (default: config or scrub)")
    ap.add_argument("--allow-downgrade", action="store_true", help="allow replacing files gathered under a stricter privacy setting")
    ap.add_argument("--since", help="only files modified on/after this date (YYYY-MM-DD)")
    ap.add_argument("--include", help=f"comma list of categories: {','.join(CATEGORIES)}")
    ap.add_argument("--exclude", help="comma list of categories to skip")
    ap.add_argument("--large-file-policy", choices=offload.CHOICES, help="default choice for files over 50 MB")
    ap.add_argument("--large", action="append", default=[], metavar="GLOB=CHOICE",
                    help="per-path large-file choice, saved to config.json (repeatable)")
    ap.add_argument("--blob-account"), ap.add_argument("--blob-container")
    ap.add_argument("--name", action="append", default=[], help="person name to scrub (repeatable, saved)")
    ap.add_argument("--no-commit", action="store_true", help="gather and stage, but do not commit")
    ap.add_argument("--copilot-home", default=os.environ.get("GATHER_EXHAUST_COPILOT_HOME", str(Path.home() / ".copilot")))
    ap.add_argument("--app-support", default=os.environ.get("GATHER_EXHAUST_APP_SUPPORT"))
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    a = ap.parse_args(argv)

    repo = Path(a.workspace).expanduser().resolve()
    if not (repo / ".discovery").is_dir():
        print(f"error: {repo} has no .discovery folder", file=sys.stderr)
        return 2
    out = repo / OUT_REL
    cfg_path = out / "config.json"
    cfg = load_json(cfg_path, {})
    privacy = a.privacy or cfg.get("privacy", "scrub")
    names = sorted(set(cfg.get("scrub_names", [])) | set(a.name))
    policies = dict(cfg.get("large_files", {}))
    for spec in a.large:
        pat, _, choice = spec.partition("=")
        if choice not in offload.CHOICES:
            ap.error(f"--large {spec}: choice must be one of {offload.CHOICES}")
        policies[pat] = choice
    blob = dict(cfg.get("blob", {}))
    if a.blob_account:
        blob["account"] = a.blob_account
    if a.blob_container:
        blob["container"] = a.blob_container
    default_policy = a.large_file_policy or cfg.get("large_file_default")

    include = set(a.include.split(",")) if a.include else set(CATEGORIES)
    include -= set(a.exclude.split(",")) if a.exclude else set()
    bad = include - set(CATEGORIES)
    if bad:
        ap.error(f"unknown categories: {sorted(bad)}")
    since = parse_ts(a.since + "T00:00:00+00:00") if a.since else None
    env = Env(repo, Path(a.copilot_home).expanduser(), find_app_support(a.app_support), since)

    runs = load_runs(repo)
    items, links = collect(env, runs, include, privacy)
    manifest = load_json(out / "manifest.json", {"files": {}})
    manifest.setdefault("files", {})
    scrub = Scrubber(privacy, names, load_salt())
    plan = process(items, out, manifest, scrub, privacy, a.allow_downgrade, policies, default_policy)

    summary: dict[str, dict[str, int]] = {}
    for p in plan:
        summary.setdefault(p["category"], {}).setdefault(p["status"], 0)
        summary[p["category"]][p["status"]] += 1
    pending = [p for p in plan if p["status"] == "large-needs-decision"]
    sensitive = sorted({p["dest"].split("/")[1] if p["category"] == "agent-sessions" else p["dest"]
                        for p in plan if p["sensitive"]})
    changed = [p for p in plan if p["status"] in ("new", "updated")]

    if a.json:
        print(json.dumps({"privacy": privacy, "summary": summary, "pending_large": pending,
                          "sensitive": sensitive, "scrub_counts": scrub.counts, "plan": plan}, indent=1, default=str))
    else:
        mode = "APPLY" if a.apply else "DRY RUN"
        print(f"[{mode}] privacy={privacy}  workspace={repo}")
        print(f"app support: {env.app_support or 'not found'}  copilot home: {env.copilot_home}")
        print(f"runs: {len(runs)}  session folders: {len(links['session_state'])}")
        for c in CATEGORIES:
            if c in summary:
                print(f"  {c:18} " + "  ".join(f"{k}={v}" for k, v in sorted(summary[c].items())))
        mb = sum(p.get("size", 0) for p in changed) / 2**20
        print(f"to write: {len(changed)} files, {mb:.1f} MB")
        print(f"redactions/placeholders: {scrub.counts or 'none'}")
        if sensitive:
            print(f"sensitive ({len(sensitive)}): " + ", ".join(sensitive[:15]) + (" ..." if len(sensitive) > 15 else ""))
        for p in pending:
            print(f"LARGE {p['size'] / 2**20:.0f} MB needs a choice ({'/'.join(offload.CHOICES)}): {p['dest']} {p.get('note', '')}")

    if not a.apply:
        return 3 if pending else 0

    # ---- apply
    out.mkdir(parents=True, exist_ok=True)
    by_dest = {it.dest: it for it in items}
    touched_extra: set[str] = set()
    errors = []
    for p in changed:
        if p["status"] == "large-needs-decision":
            continue
        it = by_dest[p["dest"]]
        dest = out / it.dest
        choice = p.get("large_choice") if p.get("large") else "git"
        rel_repo = str(OUT_REL / it.dest)
        try:
            status, written = offload.apply_choice(choice, repo=repo, dest=dest, rel_repo=rel_repo,
                                                   data=it.data, blob_cfg=blob)
        except (offload.OffloadError, subprocess.CalledProcessError) as e:
            errors.append(f"{it.dest}: {e}")
            continue
        if choice == "lfs":
            touched_extra.add(".gitattributes")
        manifest["files"][it.dest] = {k: p.get(k) for k in ("category", "source", "match", "sensitive", "size", "sha256", "privacy")} | {
            "gathered_at": now_iso(), "stored_as": status,
            "path": str(written.relative_to(out)) if written else None}
    old_links = load_json(out / "links.json", {})
    stamp = links.pop("generated_at")
    links_changed = {k: v for k, v in old_links.items() if k != "generated_at"} != links
    if changed or links_changed or not (out / "manifest.json").exists():
        manifest["last_gathered"] = now_iso()
        manifest["privacy_last_run"] = privacy
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        (out / "links.json").write_text(json.dumps({**links, "generated_at": stamp}, indent=1, sort_keys=True) + "\n")
    cfg.update(privacy=privacy, scrub_names=names, large_files=policies)
    if default_policy:
        cfg["large_file_default"] = default_policy
    if blob:
        cfg["blob"] = blob
    cfg_path.write_text(json.dumps(cfg, indent=1, sort_keys=True) + "\n")
    if "last_gathered" in manifest:
        write_readme(out, manifest)

    print(f"wrote {len(changed) - len(pending) - len(errors)} files into {OUT_REL}")
    for e in errors:
        print("ERROR", e)
    if pending:
        print(f"{len(pending)} large file(s) left out until a choice is made (--large GLOB=CHOICE)")

    if a.no_commit:
        git(repo, "add", "--", str(OUT_REL), *touched_extra)
        print("staged (not committed, --no-commit)")
        return 1 if errors else 0
    counts = ", ".join(f"{c} +{sum(v for k, v in s.items() if k in ('new', 'updated'))}"
                       for c, s in summary.items() if any(k in ('new', 'updated') for k in s))
    offloaded = sum(1 for p in changed if p.get("large") and p["status"] != "large-needs-decision")
    linked = sum(1 for r in links["runs"].values() if r["session_state"])
    msg = (f"exhaust: gather {datetime.now(UTC).date()} (privacy={privacy}, +{len(changed) - len(pending)} files"
           + (f", {offloaded} large" if offloaded else "") + ")\n\n"
           + f"Categories: {counts or 'none'}\nRuns linked to sessions: {linked}/{len(links['runs'])}\n"
           + (f"Large-file choices: {policies}\n" if offloaded else "")
           + "Gathered by the discovery-gather-exhaust skill (scripts/gather.py)")
    ok, info = commit(repo, [str(OUT_REL), *sorted(touched_extra)], msg)
    print(("committed: " if ok else "") + info)
    return 0 if (ok or info.startswith("nothing")) and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
