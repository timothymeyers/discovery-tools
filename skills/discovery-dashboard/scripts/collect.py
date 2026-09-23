#!/usr/bin/env python3
"""Telemetry collector for the portable Discovery project dashboard.

Read-only. Standard library only. Never writes to the workspace.

THE TELEMETRY CONTRACT
======================
Every top-line number has exactly one definition here. Panels never compute
their own. The rules below were learned the hard way across four earlier
dashboards and are deliberately conservative: where evidence is ambiguous this
module reports UNKNOWN rather than guessing.

Task status
  - Raw status casing varies by source. tasks/index.json and taskentries/*.json
    use camelCase ("executionDone"); tasks/status-summary.json uses PascalCase
    ("ExecutionDone"). All statuses are normalised to lowercase before use.
  - APPROVED COMPLETE counts only `complete`.
  - AWAITING REVIEW counts only `executionDone`. It is NOT approval and is never
    summed into "complete" for a headline number.
  - Progress denominator is LEAF tasks only, so parents and children are not
    both counted. Leaves are derived from graph.json decomposition edges; the
    "leaf" label is only a fallback when the graph is unreadable.

Dependencies
  - The field is `dependsOn`. `dependencies` is not a valid projection and is
    never read.
  - A dependency is satisfied by either `complete` or `executionDone`, matching
    Discovery's own readiness semantics.
  - A dependsOn reference to a task we cannot find remains a BLOCKER and is
    recorded as an unresolved reference. Missing is not satisfied.

Task identity
  - `name` (or `taskId`) is a UUID. `dxId` is the human reference. Both are
    carried; joins are always on the UUID.

Engine liveness
  - `completedAt` present  -> finished. This outranks everything else.
  - No completedAt, PID verified live -> running.
  - No completedAt, PID verifiably dead -> stopped.
  - Anything else -> unknown. Explicitly unknown, never "stalled".
  - Age is NEVER used to diagnose a stall. `run.json`/`meta.json` writtenAtUtc
    and startedAt are startup metadata, not heartbeats. Log mtime is evidence of
    a log write, not of an event.
  - PID verification requires BOTH the recorded pid to exist AND its observed
    process start time to match the recorded ownerProcessStartedAt. A matching
    pid with a different start time is PID reuse and yields unknown.
  - A live shared host process does not prove any individual worker is healthy.

Agent run identity
  - Runs are deduplicated through an alias graph over every identifier seen:
    runId, instanceId, sessionId, legacyRunId, acpSessionId, plus the ACP
    sessionId observed inside copilot-stdout.log. A single-key dedup both
    double-counts and collapses unrelated runs, so union-find is used.

CLIO
  - Detected two ways: a pluginDirectory pointing at a clio plugin, and observed
    structured `mcp_science_autop_*` tool calls.
  - Observed is a FLOOR, never a ceiling. Direct editor invocations and
    subagents can be invisible. Zero observed does not mean CLIO was unused.
    Distinct ACP sessions and no observed tool calls do not prove isolation.

Coverage
  - Every source is probed and lands in exactly one state: ok, missing, error,
    or partial. "Could not read" is reported separately from "genuinely empty",
    because conflating them is how a dashboard lies quietly.

Security
  - Engine `meta.json` carries the full operator prompt. It is never emitted.
    Only a fixed allowlist of scalar fields leaves this module.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"

# Bounded cold-tail read. Never rescan a full history.
TAIL_BYTES = 512 * 1024

# Seconds of tolerance when matching an observed process start time against the
# recorded one. Wider than clock jitter, far narrower than a plausible reuse.
PID_START_TOLERANCE_S = 90

DONE_STATUS = "complete"
AWAITING_STATUS = "executiondone"
SATISFYING_STATUSES = {DONE_STATUS, AWAITING_STATUS}
ATTENTION_STATUSES = {"failed", "incomplete", "flaggedhuman", "flaggedai", "error"}

CLIO_TOOL_RE = re.compile(r"mcp_science_autop[a-z0-9_]*", re.IGNORECASE)
# In engine exhaust CLIO surfaces as clio-<verb>, not under its MCP tool id.
# Restricted to a known verb set: a bare `clio-\w+` also matches path fragments
# like clio-plugin and clio-stderr, which are not tool calls.
CLIO_VERBS = {
    "start", "stop", "run", "wait", "status", "steer",
    "mode", "archive", "disposition", "investigate",
}
CLIO_VERB_RE = re.compile(r"\bclio-([a-z]+)\b", re.IGNORECASE)
# Tool calls render as **<tool-name>** at the head of the event content.
TOOL_NAME_RE = re.compile(r"^\*\*(.+?)\*\*")
# Verbs that open an investigation, and those that close one.
CLIO_OPEN_VERBS = {"start", "run"}
CLIO_CLOSE_VERBS = {"stop", "archive", "disposition"}
DX_RE = re.compile(r"\bDX-\d+\b")

# Engine event kinds -> the dashboard's typed log vocabulary.
EVENT_KIND_MAP = {
    "thinking": "THINK",
    "observation": "OBSERVE",
    "actionproposed": "PROPOSE",
    "actionapplied": "ACTION",
    "error": "ERROR",
    "done": "DONE",
}

# These kinds stream as per-token fragments and must be reassembled before
# display. Action/Error/Done arrive whole.
STREAMING_KINDS = {"thinking", "observation"}

# Reasoning traces are too voluminous to surface and are not project state.
DROPPED_KINDS = {"thinking"}

# The generic failure that means "agent never started" rather than "task
# failed". It carries no diagnostic, so it gets its own class.
REGISTRATION_FAILURE_MARKER = "agent execution failed"


def _utcnow():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat() if dt else None


def _parse_iso(value):
    """Parse Discovery's ISO timestamps. Returns an aware datetime or None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _norm_status(value):
    """Normalise status casing. index.json is camelCase, summary is Pascal."""
    if not isinstance(value, str):
        return "unknown"
    return value.strip().lower()


class Coverage:
    """Tracks what could and could not be read.

    A source that is absent is different from a source that errored, which is
    different from a source that is present and genuinely empty. Collapsing
    those three is how a dashboard reports confident nonsense.
    """

    def __init__(self):
        self.entries = []

    def record(self, source, state, path=None, detail=None):
        self.entries.append(
            {
                "source": source,
                "state": state,  # ok | missing | error | partial
                "path": str(path) if path else None,
                "detail": detail,
            }
        )

    def ok(self, source, path=None, detail=None):
        self.record(source, "ok", path, detail)

    def missing(self, source, path=None, detail=None):
        self.record(source, "missing", path, detail)

    def error(self, source, path=None, detail=None):
        self.record(source, "error", path, detail)

    def partial(self, source, path=None, detail=None):
        self.record(source, "partial", path, detail)

    def state_of(self, source):
        for entry in self.entries:
            if entry["source"] == source:
                return entry["state"]
        return None

    @property
    def degraded(self):
        return [e for e in self.entries if e["state"] in ("error", "partial")]

    @property
    def absent(self):
        return [e for e in self.entries if e["state"] == "missing"]


class Aliases:
    """Union-find over every identifier a single run can be known by.

    run.json instanceId matches sessionId/legacyRunId, and the ACP sessionId
    that actually appears in tool traffic is only found inside
    copilot-stdout.log. Deduplicating on any one of those keys alone is wrong in
    both directions, so all known ids for a run are unioned.
    """

    def __init__(self):
        self._parent = {}

    def _find(self, key):
        self._parent.setdefault(key, key)
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[key] != root:  # path compression
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, keys):
        keys = [k for k in keys if k]
        if not keys:
            return None
        root = self._find(keys[0])
        for key in keys[1:]:
            other = self._find(key)
            if other != root:
                self._parent[other] = root
        return root

    def canonical(self, key):
        return self._find(key) if key else None


def _read_json(path, coverage=None, source=None):
    """Read one JSON file, tolerating partial writes.

    Discovery rewrites these files live, so a torn read is expected rather than
    exceptional and must not take the whole snapshot down.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return json.load(handle)
    except FileNotFoundError:
        if coverage and source:
            coverage.missing(source, path)
        return None
    except json.JSONDecodeError as exc:
        if coverage and source:
            coverage.partial(source, path, "torn or malformed JSON: %s" % exc.msg)
        return None
    except OSError as exc:
        if coverage and source:
            coverage.error(source, path, str(exc))
        return None


def _tail_lines(path, limit_bytes=TAIL_BYTES):
    """Read the last limit_bytes of a file and return (lines, truncated).

    Bounded by design: full-history rescans are what made earlier dashboards
    slow and what made them re-read gigabytes on every five-second poll.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > limit_bytes:
                handle.seek(size - limit_bytes)
                handle.readline()  # discard the partial first line
                truncated = True
            else:
                truncated = False
            data = handle.read()
    except OSError:
        return [], False
    text = data.decode("utf-8", errors="replace")
    return [ln for ln in text.splitlines() if ln.strip()], truncated


def _process_start_epoch(pid):
    """Observed start time of pid, or None if it is not running.

    Uses ps lstart, which is local time, and converts through mktime so it can
    be compared against the recorded UTC value.
    """
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip()
    if out.returncode != 0 or not line:
        return None
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %d %b %H:%M:%S %Y"):
        try:
            return time.mktime(time.strptime(line, fmt))
        except ValueError:
            continue
    return None


def verify_process(pid, recorded_start_iso):
    """Verify a recorded pid still refers to the process that was recorded.

    Returns (state, detail) where state is one of:
      live          pid exists and its start time matches the record
      dead          pid does not exist
      pid_reused    pid exists but started at a different time
      unverifiable  no pid recorded, or no start time to check against

    A bare pid-exists check is not enough: pids are recycled, and a recycled pid
    would otherwise be reported as a healthy worker.
    """
    if not pid:
        return "unverifiable", "no pid recorded"
    observed = _process_start_epoch(pid)
    if observed is None:
        return "dead", "pid %s not running" % pid
    recorded = _parse_iso(recorded_start_iso)
    if recorded is None:
        return "unverifiable", "pid %s alive but no recorded start time to match" % pid
    delta = abs(observed - recorded.timestamp())
    if delta <= PID_START_TOLERANCE_S:
        return "live", "pid %s verified (%.0fs drift)" % (pid, delta)
    return "pid_reused", "pid %s alive but started %.0fs from record" % (pid, delta)


class Collector:
    """Builds one immutable snapshot of workspace state."""

    def __init__(self, workspace="."):
        self.root = Path(workspace).resolve()
        self.discovery = self.root / ".discovery"
        self._log_cache = {}

    # -- tasks ----------------------------------------------------------

    def collect_tasks(self, coverage):
        tasks_dir = self.discovery / "tasks"
        if not tasks_dir.is_dir():
            coverage.missing("tasks", tasks_dir)
            return {"available": False, "tasks": [], "totals": {}}

        index = _read_json(tasks_dir / "index.json", coverage, "tasks.index")
        entries_dir = tasks_dir / "taskentries"

        by_id = {}
        if isinstance(index, dict) and isinstance(index.get("tasks"), list):
            coverage.ok("tasks.index", tasks_dir / "index.json")
            for row in index["tasks"]:
                if not isinstance(row, dict):
                    continue
                task_id = row.get("taskId") or row.get("name")
                if not task_id:
                    continue
                by_id[task_id] = {
                    "taskId": task_id,
                    "dxId": row.get("dxId"),
                    "title": row.get("title"),
                    "status": _norm_status(row.get("status")),
                    "parentId": row.get("parentId"),
                    "labels": row.get("labels") or [],
                    "updatedAt": row.get("updatedAt"),
                    "dependsOn": [],
                }

        # taskentries carry dependsOn, which the index projection omits.
        entries_read = 0
        if entries_dir.is_dir():
            for entry_path in sorted(entries_dir.glob("*.json")):
                entry = _read_json(entry_path)
                if not isinstance(entry, dict):
                    continue
                entries_read += 1
                task_id = entry.get("name") or entry.get("taskId")
                if not task_id:
                    continue
                record = by_id.setdefault(
                    task_id, {"taskId": task_id, "dependsOn": [], "labels": []}
                )
                record.setdefault("labels", [])
                record["dxId"] = entry.get("dxId") or record.get("dxId")
                record["title"] = entry.get("title") or record.get("title")
                record["status"] = _norm_status(entry.get("status") or record.get("status"))
                record["parentId"] = entry.get("parentId") or record.get("parentId")
                # The contract: dependsOn only. `dependencies` is not read.
                deps = entry.get("dependsOn")
                record["dependsOn"] = [d for d in deps if d] if isinstance(deps, list) else []
            coverage.ok("tasks.entries", entries_dir, "%d entries" % entries_read)
        else:
            coverage.missing("tasks.entries", entries_dir)

        if not by_id:
            coverage.partial("tasks", tasks_dir, "no task records could be read")
            return {"available": False, "tasks": [], "totals": {}}

        leaves = self._leaf_ids(tasks_dir, by_id, coverage)
        for task_id, record in by_id.items():
            record["isLeaf"] = task_id in leaves

        return self._task_rollup(by_id)

    def _leaf_ids(self, tasks_dir, by_id, coverage):
        """Leaves via graph decomposition edges; labels only as a fallback."""
        graph = _read_json(tasks_dir / "graph.json", coverage, "tasks.graph")
        if isinstance(graph, dict) and isinstance(graph.get("edges"), list):
            coverage.ok("tasks.graph", tasks_dir / "graph.json")
            parents = {
                edge.get("from")
                for edge in graph["edges"]
                if isinstance(edge, dict) and edge.get("type") == "decomposition"
            }
            return {tid for tid in by_id if tid not in parents}
        coverage.partial(
            "tasks.graph",
            tasks_dir / "graph.json",
            "graph unreadable; falling back to the 'leaf' label, which may be stale",
        )
        labelled = {tid for tid, r in by_id.items() if "leaf" in (r.get("labels") or [])}
        if labelled:
            return labelled
        parents = {r.get("parentId") for r in by_id.values() if r.get("parentId")}
        return {tid for tid in by_id if tid not in parents}

    def _task_rollup(self, by_id):
        tasks = list(by_id.values())
        leaves = [t for t in tasks if t.get("isLeaf")]

        def satisfied(task_id):
            record = by_id.get(task_id)
            if record is None:
                return False  # unresolved reference is not satisfaction
            return record.get("status") in SATISFYING_STATUSES

        unresolved = []
        blocked, ready, executing, attention, registration_failures = [], [], [], [], []

        for task in tasks:
            status = task.get("status")
            missing_deps = [d for d in task.get("dependsOn", []) if d not in by_id]
            if missing_deps:
                unresolved.append({"taskId": task["taskId"], "dxId": task.get("dxId"),
                                   "missing": missing_deps})
            if status in SATISFYING_STATUSES:
                continue
            if status == "executing":
                executing.append(task)
                continue
            if status in ATTENTION_STATUSES:
                attention.append(task)
                continue
            unmet = [d for d in task.get("dependsOn", []) if not satisfied(d)]
            if unmet:
                task = dict(task, blockedBy=unmet)
                blocked.append(task)
            else:
                ready.append(task)

        approved = [t for t in leaves if t.get("status") == DONE_STATUS]
        awaiting = [t for t in leaves if t.get("status") == AWAITING_STATUS]

        return {
            "available": True,
            "tasks": tasks,
            "totals": {
                # Deliberately three separate numbers. Never sum approved and
                # awaiting into a single "done" figure.
                "leafTotal": len(leaves),
                "leafApproved": len(approved),
                "leafAwaitingReview": len(awaiting),
                "allTotal": len(tasks),
                "executing": len(executing),
                "ready": len(ready),
                "blocked": len(blocked),
                "attention": len(attention),
            },
            "workFront": {
                "executing": _slim_tasks(executing),
                "ready": _slim_tasks(ready),
                "blocked": _slim_tasks(blocked, include_blockers=True, by_id=by_id),
            },
            "attention": _slim_tasks(attention),
            "awaitingReview": _slim_tasks(awaiting),
            "registrationFailures": registration_failures,
            "unresolvedDependencies": unresolved,
        }

    # -- engines --------------------------------------------------------

    def collect_engines(self, coverage):
        runs_dir = self.discovery / "engine-runs"
        if not runs_dir.is_dir():
            coverage.missing("engines", runs_dir)
            return {"available": False, "engines": []}

        engines = []
        for definition_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
            for instance_dir in sorted(p for p in definition_dir.iterdir() if p.is_dir()):
                meta = _read_json(instance_dir / "meta.json")
                if not isinstance(meta, dict):
                    continue
                engines.append(self._engine_record(meta, definition_dir, instance_dir))
        coverage.ok("engines", runs_dir, "%d run(s)" % len(engines))
        engines.sort(key=lambda e: e.get("startedAt") or "", reverse=True)
        return {"available": True, "engines": engines}

    def _engine_record(self, meta, definition_dir, instance_dir):
        completed_at = meta.get("completedAt")
        pid = meta.get("ownerProcessId")
        proc_state, proc_detail = verify_process(pid, meta.get("ownerProcessStartedAt"))

        # completedAt outranks every other signal.
        if completed_at:
            state, why = "finished", "completedAt is set"
        elif proc_state == "live":
            state, why = "running", proc_detail
        elif proc_state == "dead":
            state, why = "stopped", proc_detail
        else:
            # Explicitly unknown. Age is never used to infer a stall.
            state, why = "unknown", proc_detail

        output_path = instance_dir / "output.jsonl"
        log_written_at = None
        if output_path.exists():
            try:
                log_written_at = _iso(
                    datetime.fromtimestamp(output_path.stat().st_mtime, timezone.utc)
                )
            except OSError:
                log_written_at = None

        # Allowlist only. meta.json contains the full operator prompt, which
        # must never leave this process.
        return {
            "instanceId": meta.get("instanceId"),
            "definitionId": meta.get("definitionId") or definition_dir.name,
            "adapterKind": meta.get("adapterKind"),
            "reportedState": meta.get("state"),
            "startedAt": meta.get("startedAt"),
            "completedAt": completed_at,
            "ownerProcessId": pid,
            "processState": proc_state,
            "liveness": state,
            "livenessBasis": why,
            # Named to make its epistemic status unmistakable at the call site.
            "logLastWrittenAt": log_written_at,
            "isCopilotCli": (meta.get("adapterKind") or "").lower() == "copilot-cli",
            "path": str(instance_dir),
        }

    # -- agents ---------------------------------------------------------

    def collect_agents(self, coverage):
        """Agent runs from both known layouts, deduplicated via alias graph.

        agent-runs/ exists in 0.15.13/0.15.14 but not 0.15.15. Catalog stdout
        lives under engine/<adapter>/logs/ as either catalog-<name>/<instance>
        or catalog/<name>/<instance>. Both are walked; absence of either is a
        normal condition, not an error.

        Two passes are required. Unioning while grouping is wrong: a later union
        can re-root an existing group, so a key captured earlier stops resolving
        to the same root and one run splits into two. So every record's ids are
        unioned first, and only then are records grouped by canonical root.
        """
        aliases = Aliases()
        raw = []
        found_any = False

        agent_runs_dir = self.discovery / "agent-runs"
        if agent_runs_dir.is_dir():
            found_any = True
            count = 0
            for path in sorted(agent_runs_dir.glob("*.json")):
                record = _read_json(path)
                if not isinstance(record, dict):
                    continue
                count += 1
                raw.append((record, path, "agent-runs"))
            coverage.ok("agents.agent-runs", agent_runs_dir, "%d run(s)" % count)
        else:
            coverage.missing(
                "agents.agent-runs",
                agent_runs_dir,
                "absent in 0.15.15+; catalog logs are the fallback",
            )

        catalog_runs, catalog_dirs = self._collect_catalog_runs(coverage)
        if catalog_dirs:
            found_any = True
        for record, path in catalog_runs:
            raw.append((record, path, "catalog-logs"))

        if not found_any:
            return {"available": False, "agents": []}

        # Pass 1: collect every identifier and union them.
        prepared = []
        for record, path, source in raw:
            ids = self._run_ids(record, path)
            aliases.union(ids)
            prepared.append((record, path, source, ids))

        # Pass 2: group by the now-stable canonical root.
        runs = {}
        for record, path, source, ids in prepared:
            key = next((aliases.canonical(i) for i in ids if i), None)
            if key is None:
                continue
            self._merge_agent_run(runs, key, record, path, source, ids)

        agents = sorted(
            runs.values(), key=lambda r: r.get("startedAt") or "", reverse=True
        )
        return {"available": True, "agents": agents}

    def _run_ids(self, record, path):
        ids = [
            record.get("runId"),
            record.get("instanceId"),
            record.get("sessionId"),
            record.get("legacyRunId"),
            record.get("acpSessionId"),
        ]
        if path.is_dir():
            acp = self._acp_session_id(path / "copilot-stdout.log")
            if acp:
                ids.append(acp)
        return [i for i in ids if i]

    def _collect_catalog_runs(self, coverage):
        """Walk both catalog layouts under every engine adapter's logs dir."""
        engine_dir = self.discovery / "engine"
        results, searched = [], []
        if not engine_dir.is_dir():
            coverage.missing("agents.catalog-logs", engine_dir)
            return results, searched

        for adapter_dir in sorted(p for p in engine_dir.iterdir() if p.is_dir()):
            logs_dir = adapter_dir / "logs"
            if not logs_dir.is_dir():
                continue
            for child in sorted(p for p in logs_dir.iterdir() if p.is_dir()):
                # Layout A: catalog-<name>/<instance>
                if child.name.startswith("catalog-"):
                    searched.append(child)
                    results.extend(self._instances_under(child))
                # Layout B: catalog/<name>/<instance>
                elif child.name == "catalog":
                    for named in sorted(p for p in child.iterdir() if p.is_dir()):
                        searched.append(named)
                        results.extend(self._instances_under(named))

        if searched:
            coverage.ok(
                "agents.catalog-logs", engine_dir, "%d catalog dir(s)" % len(searched)
            )
        else:
            coverage.missing("agents.catalog-logs", engine_dir, "no catalog log dirs")
        return results, searched

    def _instances_under(self, catalog_dir):
        out = []
        for instance_dir in sorted(p for p in catalog_dir.iterdir() if p.is_dir()):
            record = _read_json(instance_dir / "run.json")
            if isinstance(record, dict):
                out.append((record, instance_dir))
        return out

    def _merge_agent_run(self, runs, key, record, path, source, ids):
        status = _norm_status(record.get("status"))
        output = record.get("output") if isinstance(record.get("output"), dict) else {}
        text = json.dumps(output).lower()
        # Distinct class: the agent never started. Not an ordinary task failure.
        is_registration_failure = REGISTRATION_FAILURE_MARKER in text

        acp_session = None
        if path.is_dir():
            acp_session = self._acp_session_id(path / "copilot-stdout.log")

        existing = runs.get(key, {})
        runs[key] = {
            "key": key,
            "agentId": record.get("agentId") or record.get("definitionId")
            or existing.get("agentId"),
            "taskId": record.get("taskId") or existing.get("taskId"),
            "status": status if status != "unknown" else existing.get("status", "unknown"),
            "resultStatus": _norm_status(output.get("resultStatus"))
            if output.get("resultStatus") else existing.get("resultStatus"),
            "startedAt": record.get("startedAt") or existing.get("startedAt"),
            "completedAt": record.get("completedAt") or existing.get("completedAt"),
            "acpSessionId": acp_session or existing.get("acpSessionId"),
            "usesClioPlugin": _is_clio_plugin(record.get("pluginDirectory"))
            or existing.get("usesClioPlugin", False),
            "registrationFailure": is_registration_failure
            or existing.get("registrationFailure", False),
            "sources": sorted(set(existing.get("sources", []) + [source])),
            "aliasCount": len(set(existing.get("aliases", [])) | set(ids)),
            "aliases": sorted(set(existing.get("aliases", [])) | set(ids)),
        }

    def _acp_session_id(self, stdout_path):
        """The real ACP sessionId only appears inside copilot-stdout.log."""
        if not stdout_path.exists():
            return None
        cached = self._log_cache.get(stdout_path)
        if cached is not None:
            return cached
        lines, _ = _tail_lines(stdout_path, 64 * 1024)
        session_id = None
        for line in lines:
            match = re.search(r'"sessionId"\s*:\s*"([0-9a-fA-F-]{8,})"', line)
            if match:
                session_id = match.group(1)
                break
        self._log_cache[stdout_path] = session_id
        return session_id

    # -- clio -----------------------------------------------------------

    def collect_clio(self, engines, agents, coverage):
        """Observed CLIO activity. A floor, never a ceiling.

        Tool names are read from the `**<tool>**` head of ActionProposed
        events rather than by scanning raw lines, so file paths that merely
        contain "clio" are not miscounted as invocations.
        """
        tool_calls = {}
        opened = closed = 0
        scanned = 0
        truncated_any = False

        runs_dir = self.discovery / "engine-runs"
        if runs_dir.is_dir():
            for definition_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
                for instance_dir in sorted(p for p in definition_dir.iterdir() if p.is_dir()):
                    output = instance_dir / "output.jsonl"
                    if not output.exists():
                        continue
                    scanned += 1
                    lines, truncated = _tail_lines(output)
                    truncated_any = truncated_any or truncated
                    for line in lines:
                        if "clio" not in line.lower() and "autop" not in line.lower():
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        # Count proposals only; ActionApplied echoes the same
                        # name and would double every call.
                        if _norm_status(record.get("kind")) != "actionproposed":
                            continue
                        name = _tool_name(record.get("content"))
                        if not name:
                            continue
                        verb = _clio_verb(name)
                        if verb:
                            key = "clio-%s" % verb
                            tool_calls[key] = tool_calls.get(key, 0) + 1
                            if verb in CLIO_OPEN_VERBS:
                                opened += 1
                            elif verb in CLIO_CLOSE_VERBS:
                                closed += 1
                        elif CLIO_TOOL_RE.search(name):
                            key = name.lower()
                            tool_calls[key] = tool_calls.get(key, 0) + 1

        plugin_agents = [a for a in agents if a.get("usesClioPlugin")]
        if truncated_any:
            coverage.partial(
                "clio",
                runs_dir,
                "log tails bounded to %d KiB; earlier calls are outside coverage"
                % (TAIL_BYTES // 1024),
            )
        elif scanned:
            coverage.ok("clio", runs_dir, "%d output log(s) scanned" % scanned)
        else:
            coverage.missing("clio", runs_dir)

        # Open-minus-closed is an estimate from a bounded window. If the window
        # clipped the opening call, this can read low or negative; it is
        # clamped and labelled rather than presented as a count.
        outstanding = max(0, opened - closed)

        return {
            "available": scanned > 0 or bool(plugin_agents),
            "observedToolCalls": sorted(
                ({"tool": k, "count": v} for k, v in tool_calls.items()),
                key=lambda r: -r["count"],
            ),
            "observedTotal": sum(tool_calls.values()),
            "investigationsOpened": opened,
            "investigationsClosed": closed,
            "investigationsOutstanding": outstanding,
            "investigationsEstimated": truncated_any,
            "pluginAgents": len(plugin_agents),
            "logsTruncated": truncated_any,
            "caveat": (
                "Observed structured tool calls only, within bounded log tails. "
                "Direct editor invocations and subagents can be invisible. "
                "Zero observed does not mean CLIO was never used. Outstanding "
                "investigations are opened-minus-closed within the scanned "
                "window, not a verified live count."
            ),
        }

    # -- live log -------------------------------------------------------

    def collect_events(self, limit=80):
        """Most recent typed engine events across all runs, newest first.

        Thinking and Observation are emitted as per-token fragments, so a naive
        one-event-per-line read produces unreadable shrapnel ("ionable.", "is
        act"). Consecutive fragments of the same streaming kind within a run are
        coalesced back into one message. Action/Error/Done events arrive whole
        and are kept discrete, so distinct tool calls are never merged.
        """
        events = []
        runs_dir = self.discovery / "engine-runs"
        if not runs_dir.is_dir():
            return {"available": False, "events": []}

        for definition_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
            for instance_dir in sorted(p for p in definition_dir.iterdir() if p.is_dir()):
                output = instance_dir / "output.jsonl"
                if not output.exists():
                    continue
                lines, _ = _tail_lines(output, 128 * 1024)
                events.extend(
                    self._coalesce(lines, definition_dir.name, instance_dir.name)
                )

        # Events with no timestamp sort last rather than borrowing one.
        events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
        return {"available": True, "events": events[:limit]}

    def _coalesce(self, lines, engine, instance):
        out = []
        buffer = None

        def flush():
            if buffer and buffer["kind"] not in DROPPED_KINDS:
                text = buffer["text"].strip()
                if text:
                    out.append(
                        {
                            "type": EVENT_KIND_MAP.get(buffer["kind"],
                                                       buffer["kind"].upper()[:8] or "EVENT"),
                            "engine": engine,
                            "instanceId": instance,
                            # Start of the message, not the log file's mtime.
                            "timestamp": buffer["timestamp"],
                            "text": text[:400],
                        }
                    )

        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = _norm_status(record.get("kind") or record.get("type"))
            content = record.get("content") or record.get("text") or ""
            if not isinstance(content, str):
                content = str(content)

            if kind in STREAMING_KINDS:
                if buffer and buffer["kind"] == kind:
                    buffer["text"] += content
                    continue
                flush()
                buffer = {"kind": kind, "text": content,
                          "timestamp": record.get("timestamp")}
                continue

            flush()
            buffer = None
            if kind in DROPPED_KINDS:
                continue
            text = content.strip()
            if text:
                out.append(
                    {
                        "type": EVENT_KIND_MAP.get(kind, kind.upper()[:8] or "EVENT"),
                        "engine": engine,
                        "instanceId": instance,
                        "timestamp": record.get("timestamp"),
                        "text": text[:400],
                    }
                )
        flush()
        return out

    # -- git ------------------------------------------------------------

    def collect_git(self, coverage, limit=25):
        """Recent commits, with .discovery churn separated out.

        .discovery/logs and .discovery/tasks/*.json churn constantly while
        Discovery runs (rebuiltAt timestamps, live log appends), so the working
        tree is never clean. Counting that as project activity is exactly the
        "stale and irrelevant information" failure. It is reported as a
        suppressed count instead.
        """
        if not (self.root / ".git").exists():
            coverage.missing("git", self.root / ".git")
            return {"available": False, "commits": []}

        try:
            proc = subprocess.run(
                ["git", "log", "-n", str(limit), "--date=iso-strict",
                 "--format=%H%x1f%an%x1f%ad%x1f%s", "--numstat"],
                cwd=str(self.root), capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            coverage.error("git", self.root, str(exc))
            return {"available": False, "commits": []}

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            # An empty repository is present-and-empty, not a read failure. The
            # whole point of the coverage model is to keep those apart.
            if "does not have any commits" in stderr:
                coverage.ok("git", self.root, "repository has no commits yet")
                return {"available": True, "commits": [], "note": "No commits yet."}
            coverage.error("git", self.root, stderr[:200])
            return {"available": False, "commits": []}

        commits, current = [], None
        for line in proc.stdout.splitlines():
            if "\x1f" in line:
                if current:
                    commits.append(current)
                sha, author, date, subject = line.split("\x1f", 3)
                current = {
                    "sha": sha[:8], "author": author, "date": date, "subject": subject,
                    "dxRefs": sorted(set(DX_RE.findall(subject))),
                    "filesChanged": 0, "insertions": 0, "deletions": 0,
                    "suppressedDiscoveryFiles": 0,
                }
            elif current and line.strip():
                parts = line.split("\t")
                if len(parts) != 3:
                    continue
                added, removed, path = parts
                if path.startswith(".discovery/"):
                    current["suppressedDiscoveryFiles"] += 1
                    continue
                current["filesChanged"] += 1
                current["insertions"] += int(added) if added.isdigit() else 0
                current["deletions"] += int(removed) if removed.isdigit() else 0
        if current:
            commits.append(current)

        coverage.ok("git", self.root, "%d commit(s)" % len(commits))
        return {
            "available": True,
            "commits": commits,
            "note": ".discovery churn is excluded from per-commit counts and "
                    "reported separately as suppressedDiscoveryFiles.",
        }

    # -- purpose, outcomes, bookshelf ------------------------------------

    def collect_purpose(self, coverage):
        purpose = _read_json(self.discovery / "purpose.json", coverage, "purpose")
        outcomes_dir = self.discovery / "outcomes"
        outcomes = []
        if outcomes_dir.is_dir():
            for path in sorted(outcomes_dir.glob("*.json")):
                record = _read_json(path)
                if not isinstance(record, dict):
                    continue
                outcomes.append(
                    {
                        "outcomeId": record.get("outcomeId"),
                        "title": record.get("title"),
                        "status": _norm_status(record.get("status")),
                        "type": record.get("type"),
                        "verificationMode": record.get("verificationMode"),
                        "taskCount": len(record.get("taskIds") or []),
                    }
                )
            coverage.ok("outcomes", outcomes_dir, "%d outcome(s)" % len(outcomes))
        else:
            coverage.missing("outcomes", outcomes_dir)

        if isinstance(purpose, dict):
            coverage.ok("purpose", self.discovery / "purpose.json")
            head = {"title": purpose.get("title"), "statement": purpose.get("statement")}
        else:
            head = {"title": None, "statement": None}

        return {"available": bool(head["title"]) or bool(outcomes),
                "purpose": head, "outcomes": outcomes}

    def collect_bookshelf(self, coverage):
        """Per-source ingest state, if a bookshelf exists at all."""
        shelf_dir = self.discovery / "bookshelf"
        if not shelf_dir.is_dir():
            coverage.missing("bookshelf", shelf_dir)
            return {"available": False, "sources": []}

        sources = []
        failures = 0
        for state_path in shelf_dir.rglob("ingest-state.json"):
            record = _read_json(state_path)
            if not isinstance(record, dict):
                continue
            entries = record.get("sources") or record.get("items") or []
            if isinstance(entries, dict):
                entries = list(entries.values())
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                # The identifying field is `uri`; there is no `name`.
                uri = entry.get("uri") or entry.get("path") or entry.get("sourceId") or ""
                outcome = _norm_status(entry.get("outcome"))
                if entry.get("error") or outcome in ("failed", "error"):
                    failures += 1
                sources.append(
                    {
                        "name": os.path.basename(str(uri)) or str(uri),
                        "uri": str(uri),
                        "outcome": outcome,
                        "status": _norm_status(entry.get("status")),
                        "stage": _norm_status(entry.get("stage")),
                        "error": entry.get("error"),
                    }
                )
        coverage.ok("bookshelf", shelf_dir,
                    "%d source(s), %d failed" % (len(sources), failures))
        return {"available": True, "sources": sources, "failed": failures}

    # -- snapshot --------------------------------------------------------

    def snapshot(self):
        started = time.time()
        coverage = Coverage()

        tasks = self.collect_tasks(coverage)
        engines = self.collect_engines(coverage)
        agents = self.collect_agents(coverage)
        clio = self.collect_clio(engines.get("engines", []), agents.get("agents", []), coverage)
        events = self.collect_events()
        git = self.collect_git(coverage)
        purpose = self.collect_purpose(coverage)
        bookshelf = self.collect_bookshelf(coverage)

        alerts = self._alerts(tasks, engines, agents)

        return {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": _iso(_utcnow()),
            "workspace": str(self.root),
            "workspaceName": self.root.name,
            "collectionMs": int((time.time() - started) * 1000),
            "tasks": tasks,
            "engines": engines,
            "agents": agents,
            "clio": clio,
            "events": events,
            "git": git,
            "purpose": purpose,
            "bookshelf": bookshelf,
            "alerts": alerts,
            "coverage": {
                "entries": coverage.entries,
                "degraded": coverage.degraded,
                "absent": coverage.absent,
            },
        }

    def _alerts(self, tasks, engines, agents):
        """Three distinct buckets. A single 'needs attention' count is useless.

        actionNeeded is the only one that means "a human must do something now".
        """
        totals = tasks.get("totals", {}) if tasks.get("available") else {}
        action, blocked_items, awaiting = [], [], []

        for task in tasks.get("attention", []) if tasks.get("available") else []:
            action.append({"kind": "task", "label": _label(task),
                           "detail": "status %s" % task.get("status")})

        for engine in engines.get("engines", []):
            if engine.get("liveness") == "stopped" and not engine.get("completedAt"):
                action.append({
                    "kind": "engine",
                    "label": engine.get("definitionId") or engine.get("instanceId"),
                    "detail": "process gone with no completedAt: " + str(engine.get("livenessBasis")),
                })

        for agent in agents.get("agents", []):
            if agent.get("registrationFailure"):
                action.append({
                    "kind": "agent-registration",
                    "label": agent.get("agentId") or agent.get("key"),
                    "detail": "agent never started (generic execution failure, no diagnostic)",
                })

        if tasks.get("available"):
            for task in tasks.get("workFront", {}).get("blocked", []):
                blocked_items.append({"kind": "task", "label": _label(task),
                                      "detail": "%d unmet dependency(ies)"
                                                % len(task.get("blockedBy") or [])})
            for task in tasks.get("awaitingReview", []):
                awaiting.append({"kind": "task", "label": _label(task),
                                 "detail": "executionDone, not approved"})

        return {
            "actionNeeded": action,
            "blocked": blocked_items,
            "awaitingReview": awaiting,
            "counts": {
                "actionNeeded": len(action),
                "blocked": len(blocked_items),
                "awaitingReview": len(awaiting),
                "unresolvedDependencies": len(tasks.get("unresolvedDependencies", []))
                if tasks.get("available") else 0,
                "leafTotal": totals.get("leafTotal", 0),
            },
        }


def _label(task):
    dx = task.get("dxId")
    title = task.get("title") or task.get("taskId")
    return "%s %s" % (dx, title) if dx else str(title)


def _slim_tasks(tasks, include_blockers=False, by_id=None, limit=60):
    out = []
    for task in tasks[:limit]:
        row = {
            "taskId": task.get("taskId"),
            "dxId": task.get("dxId"),
            "title": task.get("title"),
            "status": task.get("status"),
            "isLeaf": task.get("isLeaf", False),
            "updatedAt": task.get("updatedAt"),
        }
        if include_blockers:
            blockers = []
            for dep in task.get("blockedBy") or []:
                target = (by_id or {}).get(dep)
                blockers.append(
                    {
                        "taskId": dep,
                        "dxId": (target or {}).get("dxId"),
                        "status": (target or {}).get("status", "unresolved"),
                    }
                )
            row["blockedBy"] = blockers
        out.append(row)
    return out


def _is_clio_plugin(plugin_dir):
    return bool(plugin_dir) and "clio" in str(plugin_dir).lower()


def _tool_name(content):
    """Tool name from the `**<tool>**` head of an event, if present."""
    if not isinstance(content, str):
        return None
    match = TOOL_NAME_RE.match(content.strip())
    return match.group(1).strip() if match else None


def _clio_verb(tool_name):
    """The CLIO verb for a tool name, or None if it is not a CLIO call."""
    match = CLIO_VERB_RE.match(tool_name.strip())
    if not match:
        return None
    verb = match.group(1).lower()
    return verb if verb in CLIO_VERBS else None


def collect(workspace="."):
    return Collector(workspace).snapshot()


def summarize(snapshot):
    """Plain-text summary for --once."""
    lines = []
    totals = snapshot["tasks"].get("totals", {})
    counts = snapshot["alerts"]["counts"]
    lines.append("Discovery dashboard - %s" % snapshot["workspaceName"])
    lines.append("  generated %s (%d ms)" % (snapshot["generatedAt"], snapshot["collectionMs"]))
    if snapshot["tasks"].get("available"):
        lines.append(
            "  leaf tasks: %d approved / %d awaiting review / %d total"
            % (totals.get("leafApproved", 0), totals.get("leafAwaitingReview", 0),
               totals.get("leafTotal", 0))
        )
        lines.append(
            "  work front: %d executing, %d ready, %d blocked"
            % (totals.get("executing", 0), totals.get("ready", 0), totals.get("blocked", 0))
        )
    else:
        lines.append("  tasks: unavailable")
    lines.append(
        "  alerts: %d action needed, %d blocked, %d awaiting review"
        % (counts["actionNeeded"], counts["blocked"], counts["awaitingReview"])
    )
    for engine in snapshot["engines"].get("engines", [])[:6]:
        lines.append(
            "  engine %-22s %-9s %s"
            % (str(engine.get("definitionId"))[:22], engine.get("liveness"),
               engine.get("livenessBasis"))
        )
    clio = snapshot["clio"]
    if clio.get("available"):
        lines.append(
            "  clio: %d observed call(s), %d investigation(s) outstanding%s, %d plugin agent(s)"
            % (clio.get("observedTotal", 0), clio.get("investigationsOutstanding", 0),
               " (estimated)" if clio.get("investigationsEstimated") else "",
               clio.get("pluginAgents", 0))
        )
    degraded = snapshot["coverage"]["degraded"]
    if degraded:
        lines.append("  COVERAGE GAPS (%d):" % len(degraded))
        for entry in degraded[:6]:
            lines.append("    %s [%s] %s" % (entry["source"], entry["state"],
                                             entry.get("detail") or ""))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(summarize(collect(sys.argv[1] if len(sys.argv) > 1 else ".")))
