#!/usr/bin/env python3
"""Tests for the Discovery dashboard collector.

Fixture-driven: every test builds a synthetic .discovery/ tree in a temp dir, so
the suite runs anywhere and does not depend on any real workspace.

The golden-fixture tests at the end additionally run the collector against any
real workspaces found under ~/discovery, and are skipped when none exist. Those
catch schema drift across Discovery Express versions, which synthetic fixtures
cannot.

    python3 -m unittest discover -s dashboard -p 'test_dashboard.py' -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect as collector  # noqa: E402


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload) if not isinstance(payload, str) else payload,
        encoding="utf-8",
    )


class Workspace:
    """Builder for a synthetic .discovery/ tree."""

    def __init__(self, root):
        self.root = Path(root)
        self.discovery = self.root / ".discovery"
        self.discovery.mkdir(parents=True, exist_ok=True)

    def tasks(self, entries, edges=None, index_rows=None):
        tasks_dir = self.discovery / "tasks"
        for entry in entries:
            write(tasks_dir / "taskentries" / ("%s.json" % entry["name"]), entry)
        if index_rows is not None:
            write(tasks_dir / "index.json", {"tasks": index_rows})
        if edges is not None:
            write(tasks_dir / "graph.json", {"edges": edges})
        return self

    def engine_run(self, definition, instance, meta, events=None):
        run_dir = self.discovery / "engine-runs" / definition / instance
        write(run_dir / "meta.json", meta)
        if events:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "output.jsonl").write_text(
                "\n".join(json.dumps(e) for e in events), encoding="utf-8"
            )
        return self

    def agent_run(self, run_id, payload):
        write(self.discovery / "agent-runs" / ("%s.json" % run_id), payload)
        return self

    def catalog_run(self, adapter, catalog_dir_name, instance, run_json, stdout=None):
        base = self.discovery / "engine" / adapter / "logs" / catalog_dir_name / instance
        write(base / "run.json", run_json)
        if stdout is not None:
            base.mkdir(parents=True, exist_ok=True)
            (base / "copilot-stdout.log").write_text(stdout, encoding="utf-8")
        return self

    def collect(self):
        return collector.collect(str(self.root))


def task(name, dx, status, parent=None, depends=None):
    return {
        "name": name, "dxId": dx, "title": "task %s" % dx,
        "status": status, "parentId": parent, "dependsOn": depends or [],
    }


class TempWorkspaceCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# --------------------------------------------------------------------------
# Status normalisation and the complete / executionDone split
# --------------------------------------------------------------------------

class StatusSemanticsTests(TempWorkspaceCase):
    def test_approved_and_awaiting_review_are_never_summed(self):
        self.ws.tasks(
            [task("a", "DX-1", "complete"), task("b", "DX-2", "executionDone")],
            edges=[],
        )
        totals = self.ws.collect()["tasks"]["totals"]
        self.assertEqual(totals["leafApproved"], 1)
        self.assertEqual(totals["leafAwaitingReview"], 1)
        self.assertNotIn("leafDone", totals)

    def test_status_casing_is_normalised(self):
        """index.json is camelCase; status-summary.json is PascalCase."""
        self.ws.tasks([task("a", "DX-1", "ExecutionDone")], edges=[])
        totals = self.ws.collect()["tasks"]["totals"]
        self.assertEqual(totals["leafAwaitingReview"], 1)
        self.assertEqual(totals["leafApproved"], 0)

    def test_awaiting_review_is_reported_as_an_alert_not_as_done(self):
        self.ws.tasks([task("a", "DX-1", "executionDone")], edges=[])
        alerts = self.ws.collect()["alerts"]
        self.assertEqual(alerts["counts"]["awaitingReview"], 1)
        self.assertEqual(alerts["counts"]["actionNeeded"], 0)


# --------------------------------------------------------------------------
# Leaf derivation and the progress denominator
# --------------------------------------------------------------------------

class LeafDerivationTests(TempWorkspaceCase):
    def test_parents_are_excluded_from_the_denominator(self):
        self.ws.tasks(
            [task("root", "DX-1", "complete"),
             task("kid1", "DX-2", "complete", parent="root"),
             task("kid2", "DX-3", "complete", parent="root")],
            edges=[{"from": "root", "to": "kid1", "type": "decomposition"},
                   {"from": "root", "to": "kid2", "type": "decomposition"}],
        )
        totals = self.ws.collect()["tasks"]["totals"]
        self.assertEqual(totals["leafTotal"], 2)  # root excluded
        self.assertEqual(totals["allTotal"], 3)

    def test_dependency_edges_do_not_make_a_task_a_parent(self):
        self.ws.tasks(
            [task("a", "DX-1", "complete"), task("b", "DX-2", "complete")],
            edges=[{"from": "a", "to": "b", "type": "dependency"}],
        )
        self.assertEqual(self.ws.collect()["tasks"]["totals"]["leafTotal"], 2)

    def test_falls_back_to_labels_when_graph_is_unreadable(self):
        self.ws.tasks([task("a", "DX-1", "complete"), task("b", "DX-2", "complete")])
        write(self.ws.discovery / "tasks" / "graph.json", "{ this is not json")
        write(self.ws.discovery / "tasks" / "index.json",
              {"tasks": [{"taskId": "a", "status": "complete", "labels": ["leaf"]},
                         {"taskId": "b", "status": "complete", "labels": []}]})
        snap = self.ws.collect()
        self.assertEqual(snap["tasks"]["totals"]["leafTotal"], 1)
        states = {c["source"]: c["state"] for c in snap["coverage"]["entries"]}
        self.assertEqual(states["tasks.graph"], "partial")


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------

class DependencyTests(TempWorkspaceCase):
    def test_execution_done_satisfies_a_dependency(self):
        self.ws.tasks(
            [task("a", "DX-1", "executionDone"),
             task("b", "DX-2", "pending", depends=["a"])],
            edges=[],
        )
        front = self.ws.collect()["tasks"]["workFront"]
        self.assertEqual([t["dxId"] for t in front["ready"]], ["DX-2"])
        self.assertEqual(front["blocked"], [])

    def test_unresolvable_dependency_remains_a_blocker(self):
        self.ws.tasks([task("b", "DX-2", "pending", depends=["ghost"])], edges=[])
        snap = self.ws.collect()
        blocked = snap["tasks"]["workFront"]["blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["blockedBy"][0]["status"], "unresolved")
        self.assertEqual(len(snap["tasks"]["unresolvedDependencies"]), 1)

    def test_dependencies_field_is_ignored_only_dependson_is_read(self):
        entry = task("b", "DX-2", "pending")
        entry["dependencies"] = ["ghost"]  # not a valid projection; must be ignored
        self.ws.tasks([entry], edges=[])
        front = self.ws.collect()["tasks"]["workFront"]
        self.assertEqual([t["dxId"] for t in front["ready"]], ["DX-2"])


# --------------------------------------------------------------------------
# Engine liveness
# --------------------------------------------------------------------------

class EngineLivenessTests(TempWorkspaceCase):
    def test_completed_at_outranks_process_state(self):
        self.ws.engine_run("mission-control", "i1", {
            "instanceId": "i1", "definitionId": "mission-control",
            "state": "Completed", "startedAt": "2026-09-20T03:22:29+00:00",
            "completedAt": "2026-09-20T16:49:02+00:00",
            "ownerProcessId": 999999, "ownerProcessStartedAt": "2026-09-20T01:12:53+00:00",
        })
        engine = self.ws.collect()["engines"]["engines"][0]
        self.assertEqual(engine["liveness"], "finished")

    def test_dead_pid_without_completion_is_stopped_and_raises_an_alert(self):
        self.ws.engine_run("mission-control", "i1", {
            "instanceId": "i1", "definitionId": "mission-control",
            "state": "Running", "startedAt": "2026-09-20T03:22:29+00:00",
            "ownerProcessId": 999999, "ownerProcessStartedAt": "2026-09-20T01:12:53+00:00",
        })
        snap = self.ws.collect()
        self.assertEqual(snap["engines"]["engines"][0]["liveness"], "stopped")
        self.assertTrue(any(a["kind"] == "engine" for a in snap["alerts"]["actionNeeded"]))

    def test_live_pid_with_wrong_start_time_is_unknown_not_running(self):
        """PID reuse must never be reported as a healthy worker."""
        self.ws.engine_run("mission-control", "i1", {
            "instanceId": "i1", "definitionId": "mission-control", "state": "Running",
            "ownerProcessId": os.getpid(),
            "ownerProcessStartedAt": "1999-01-01T00:00:00+00:00",
        })
        engine = self.ws.collect()["engines"]["engines"][0]
        self.assertEqual(engine["processState"], "pid_reused")
        self.assertEqual(engine["liveness"], "unknown")

    def test_missing_pid_is_unknown_not_stopped(self):
        self.ws.engine_run("mission-control", "i1", {
            "instanceId": "i1", "definitionId": "mission-control", "state": "Running",
        })
        engine = self.ws.collect()["engines"]["engines"][0]
        self.assertEqual(engine["liveness"], "unknown")
        self.assertEqual(engine["processState"], "unverifiable")

    def test_stale_run_is_never_labelled_stalled(self):
        """Age alone must not diagnose a stall."""
        self.ws.engine_run("mission-control", "i1", {
            "instanceId": "i1", "definitionId": "mission-control", "state": "Running",
            "startedAt": "2001-01-01T00:00:00+00:00",
        })
        snap = self.ws.collect()
        self.assertNotIn(
            "stalled", json.dumps(snap["engines"]).lower(),
            "age must never produce a 'stalled' verdict",
        )

    def test_operator_prompt_is_never_emitted(self):
        secret = "SENTINEL-DO-NOT-LEAK-THIS-PROMPT"
        self.ws.engine_run("mission-control", "i1", {
            "instanceId": "i1", "definitionId": "mission-control",
            "state": "Completed", "completedAt": "2026-09-20T16:49:02+00:00",
            "prompt": secret,
        })
        self.assertNotIn(secret, json.dumps(self.ws.collect()))


class VerifyProcessTests(unittest.TestCase):
    def test_current_process_verifies_live_with_a_matching_start(self):
        import datetime
        start = collector._process_start_epoch(os.getpid())
        self.assertIsNotNone(start, "ps lstart should be readable on this platform")
        iso = datetime.datetime.fromtimestamp(start, datetime.timezone.utc).isoformat()
        state, _ = collector.verify_process(os.getpid(), iso)
        self.assertEqual(state, "live")

    def test_no_pid_is_unverifiable(self):
        self.assertEqual(collector.verify_process(None, None)[0], "unverifiable")


# --------------------------------------------------------------------------
# Agent alias graph
# --------------------------------------------------------------------------

class AliasGraphTests(unittest.TestCase):
    def test_transitive_ids_collapse_to_one_run(self):
        aliases = collector.Aliases()
        aliases.union(["run-1", "sess-A"])
        aliases.union(["sess-A", "legacy-9"])
        self.assertEqual(aliases.canonical("run-1"), aliases.canonical("legacy-9"))

    def test_unrelated_ids_stay_separate(self):
        aliases = collector.Aliases()
        aliases.union(["run-1", "sess-A"])
        aliases.union(["run-2", "sess-B"])
        self.assertNotEqual(aliases.canonical("run-1"), aliases.canonical("run-2"))

    def test_empty_ids_are_ignored(self):
        self.assertIsNone(collector.Aliases().union([None, ""]))


class AgentCollectionTests(TempWorkspaceCase):
    def test_same_run_from_both_layouts_is_not_double_counted(self):
        """agent-runs/ and catalog stdout describe the same run in .14."""
        self.ws.agent_run("abc123", {
            "runId": "abc123", "agentId": "catalog/stat-agent", "taskId": "DX-9",
            "status": "Completed", "startedAt": "2026-09-03T14:23:01+00:00",
        })
        self.ws.catalog_run("copilot-cli", "catalog-stat-agent", "abc123", {
            "definitionId": "catalog/stat-agent", "instanceId": "abc123",
        })
        agents = self.ws.collect()["agents"]["agents"]
        self.assertEqual(len(agents), 1)
        self.assertEqual(sorted(agents[0]["sources"]), ["agent-runs", "catalog-logs"])

    def test_both_catalog_layouts_are_walked(self):
        """catalog-<name>/<instance> and catalog/<name>/<instance> both occur."""
        self.ws.catalog_run("copilot-cli", "catalog-stat-agent", "i1",
                            {"definitionId": "catalog/stat-agent", "instanceId": "i1"})
        base = self.ws.discovery / "engine" / "copilot-cli" / "logs" / "catalog" / "other"
        write(base / "i2" / "run.json",
              {"definitionId": "catalog/other", "instanceId": "i2"})
        agents = self.ws.collect()["agents"]["agents"]
        self.assertEqual(len({a["agentId"] for a in agents}), 2)

    def test_acp_session_from_stdout_joins_runs(self):
        session = "da66d91d-7ac4-4508-957a-b62b3a81ce8f"
        self.ws.agent_run("r1", {"runId": "r1", "sessionId": session,
                                 "agentId": "catalog/stat-agent", "status": "Completed"})
        self.ws.catalog_run(
            "copilot-cli", "catalog-stat-agent", "inst-9",
            {"definitionId": "catalog/stat-agent", "instanceId": "inst-9"},
            stdout=json.dumps({"params": {"sessionId": session}}),
        )
        self.assertEqual(len(self.ws.collect()["agents"]["agents"]), 1)

    def test_generic_execution_failure_is_its_own_class(self):
        self.ws.agent_run("r1", {
            "runId": "r1", "agentId": "catalog/batteries-included", "status": "Failed",
            "output": {"text": "Agent execution failed. See diagnostics for exception type."},
        })
        snap = self.ws.collect()
        self.assertTrue(snap["agents"]["agents"][0]["registrationFailure"])
        kinds = [a["kind"] for a in snap["alerts"]["actionNeeded"]]
        self.assertIn("agent-registration", kinds)

    def test_missing_agent_runs_directory_is_not_an_error(self):
        """agent-runs/ does not exist in 0.15.15."""
        self.ws.catalog_run("copilot-cli", "catalog-stat-agent", "i1",
                            {"definitionId": "catalog/stat-agent", "instanceId": "i1"})
        snap = self.ws.collect()
        states = {c["source"]: c["state"] for c in snap["coverage"]["entries"]}
        self.assertEqual(states["agents.agent-runs"], "missing")
        self.assertEqual(snap["coverage"]["degraded"], [])


# --------------------------------------------------------------------------
# CLIO
# --------------------------------------------------------------------------

class ClioTests(TempWorkspaceCase):
    def _run_with(self, contents):
        self.ws.engine_run(
            "mission-control", "i1",
            {"instanceId": "i1", "definitionId": "mission-control",
             "completedAt": "2026-09-20T16:49:02+00:00"},
            events=[{"kind": "ActionProposed", "content": c,
                     "timestamp": "2026-09-20T04:00:00+00:00"} for c in contents],
        )
        return self.ws.collect()["clio"]

    def test_counts_clio_verbs(self):
        clio = self._run_with(["**clio-start**\n```json\n{}\n```",
                               "**clio-wait**\n```json\n{}\n```"])
        self.assertEqual(clio["observedTotal"], 2)

    def test_ignores_path_fragments_that_merely_contain_clio(self):
        """clio-plugin and clio-stderr are paths, not tool calls."""
        clio = self._run_with([
            "**Viewing ...dist/clio-plugin/plugins/clio/x.js**",
            "**Running: cat clio-stderr.log**",
        ])
        self.assertEqual(clio["observedTotal"], 0)

    def test_applied_events_do_not_double_count_proposals(self):
        self.ws.engine_run(
            "mission-control", "i1",
            {"instanceId": "i1", "definitionId": "mission-control"},
            events=[
                {"kind": "ActionProposed", "content": "**clio-start**"},
                {"kind": "ActionApplied", "content": "**clio-start**"},
            ],
        )
        self.assertEqual(self.ws.collect()["clio"]["observedTotal"], 1)

    def test_investigation_lifecycle_is_tracked(self):
        clio = self._run_with(["**clio-start**", "**clio-start**", "**clio-stop**"])
        self.assertEqual(clio["investigationsOpened"], 2)
        self.assertEqual(clio["investigationsClosed"], 1)
        self.assertEqual(clio["investigationsOutstanding"], 1)

    def test_outstanding_never_goes_negative(self):
        clio = self._run_with(["**clio-archive**", "**clio-stop**"])
        self.assertEqual(clio["investigationsOutstanding"], 0)

    def test_caveat_is_always_present(self):
        clio = self._run_with(["**clio-start**"])
        self.assertIn("does not mean CLIO was never used", clio["caveat"])


# --------------------------------------------------------------------------
# Coverage model
# --------------------------------------------------------------------------

class CoverageTests(TempWorkspaceCase):
    def test_missing_is_distinct_from_empty(self):
        self.ws.tasks([task("a", "DX-1", "complete")], edges=[])
        states = {c["source"]: c["state"] for c in self.ws.collect()["coverage"]["entries"]}
        self.assertEqual(states["bookshelf"], "missing")
        self.assertEqual(states["outcomes"], "missing")

    def test_torn_json_is_partial_not_missing(self):
        write(self.ws.discovery / "purpose.json", "{ half-written")
        entries = {c["source"]: c["state"] for c in self.ws.collect()["coverage"]["entries"]}
        self.assertEqual(entries["purpose"], "partial")

    def test_empty_workspace_yields_no_tasks_and_does_not_raise(self):
        snap = self.ws.collect()
        self.assertFalse(snap["tasks"]["available"])
        self.assertEqual(snap["alerts"]["counts"]["actionNeeded"], 0)

    def test_snapshot_is_json_serialisable(self):
        self.ws.tasks([task("a", "DX-1", "complete")], edges=[])
        json.dumps(self.ws.collect())


# --------------------------------------------------------------------------
# Bounded reads and git
# --------------------------------------------------------------------------

class BoundedReadTests(TempWorkspaceCase):
    def test_tail_is_bounded_and_reports_truncation(self):
        path = Path(self._tmp.name) / "big.jsonl"
        path.write_text("x" * (collector.TAIL_BYTES * 2), encoding="utf-8")
        _, truncated = collector._tail_lines(path)
        self.assertTrue(truncated)

    def test_small_file_is_not_truncated(self):
        path = Path(self._tmp.name) / "small.jsonl"
        path.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
        lines, truncated = collector._tail_lines(path)
        self.assertFalse(truncated)
        self.assertEqual(len(lines), 2)

    def test_missing_file_returns_empty_without_raising(self):
        lines, truncated = collector._tail_lines(Path(self._tmp.name) / "nope.jsonl")
        self.assertEqual(lines, [])
        self.assertFalse(truncated)


class GitTests(TempWorkspaceCase):
    def test_absent_git_repo_is_reported_missing_not_error(self):
        snap = self.ws.collect()
        self.assertFalse(snap["git"]["available"])
        states = {c["source"]: c["state"] for c in snap["coverage"]["entries"]}
        self.assertEqual(states["git"], "missing")

    def test_repo_with_no_commits_is_empty_not_an_error(self):
        """Present-and-empty is a different thing from could-not-read."""
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self._tmp.name, check=True)
        snap = self.ws.collect()
        states = {c["source"]: c["state"] for c in snap["coverage"]["entries"]}
        self.assertEqual(states["git"], "ok")
        self.assertEqual(snap["git"]["commits"], [])
        self.assertEqual(snap["coverage"]["degraded"], [])


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

class EventTests(TempWorkspaceCase):
    def test_thinking_events_are_dropped_and_kinds_are_mapped(self):
        self.ws.engine_run(
            "mission-control", "i1", {"instanceId": "i1", "definitionId": "mission-control"},
            events=[
                {"kind": "Thinking", "content": "hmm", "timestamp": "2026-09-20T04:00:00+00:00"},
                {"kind": "ActionApplied", "content": "did", "timestamp": "2026-09-20T04:00:01+00:00"},
                {"kind": "Error", "content": "boom", "timestamp": "2026-09-20T04:00:02+00:00"},
            ],
        )
        events = self.ws.collect()["events"]["events"]
        types = [e["type"] for e in events]
        self.assertNotIn("THINK", types)
        self.assertIn("ACTION", types)
        self.assertIn("ERROR", types)

    def test_untimestamped_events_keep_a_null_timestamp(self):
        """An event must never borrow the log file's mtime."""
        self.ws.engine_run(
            "mission-control", "i1", {"instanceId": "i1", "definitionId": "mission-control"},
            events=[{"kind": "Observation", "content": "no time here"}],
        )
        self.assertIsNone(self.ws.collect()["events"]["events"][0]["timestamp"])

    def test_streaming_fragments_are_reassembled(self):
        """Observation streams token-by-token; fragments must be rejoined."""
        self.ws.engine_run(
            "mission-control", "i1", {"instanceId": "i1", "definitionId": "mission-control"},
            events=[
                {"kind": "Observation", "content": "The ", "timestamp": "2026-09-20T04:00:00+00:00"},
                {"kind": "Observation", "content": "sweep is ", "timestamp": "2026-09-20T04:00:01+00:00"},
                {"kind": "Observation", "content": "action", "timestamp": "2026-09-20T04:00:02+00:00"},
                {"kind": "Observation", "content": "able.", "timestamp": "2026-09-20T04:00:03+00:00"},
            ],
        )
        events = self.ws.collect()["events"]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"], "The sweep is actionable.")
        # Timestamp is the start of the message, not the last fragment.
        self.assertEqual(events[0]["timestamp"], "2026-09-20T04:00:00+00:00")

    def test_discrete_events_are_not_merged_together(self):
        """Two tool calls in a row must stay two entries."""
        self.ws.engine_run(
            "mission-control", "i1", {"instanceId": "i1", "definitionId": "mission-control"},
            events=[
                {"kind": "ActionProposed", "content": "**clio-start**",
                 "timestamp": "2026-09-20T04:00:00+00:00"},
                {"kind": "ActionProposed", "content": "**clio-status**",
                 "timestamp": "2026-09-20T04:00:01+00:00"},
            ],
        )
        self.assertEqual(len(self.ws.collect()["events"]["events"]), 2)

    def test_a_discrete_event_breaks_a_streaming_run(self):
        self.ws.engine_run(
            "mission-control", "i1", {"instanceId": "i1", "definitionId": "mission-control"},
            events=[
                {"kind": "Observation", "content": "before", "timestamp": "2026-09-20T04:00:00+00:00"},
                {"kind": "ActionApplied", "content": "**did**", "timestamp": "2026-09-20T04:00:01+00:00"},
                {"kind": "Observation", "content": "after", "timestamp": "2026-09-20T04:00:02+00:00"},
            ],
        )
        texts = [e["text"] for e in self.ws.collect()["events"]["events"]]
        self.assertIn("before", texts)
        self.assertIn("after", texts)
        self.assertNotIn("beforeafter", texts)


class BookshelfTests(TempWorkspaceCase):
    def test_source_name_comes_from_uri(self):
        write(self.ws.discovery / "bookshelf" / "abc" / "ingest-state.json", {
            "status": "completed",
            "sources": [{"uri": "/long/path/to/pmid-24513544-abstract.txt",
                         "status": "resolved", "outcome": "indexed"}],
        })
        shelf = self.ws.collect()["bookshelf"]
        self.assertEqual(shelf["sources"][0]["name"], "pmid-24513544-abstract.txt")
        self.assertEqual(shelf["failed"], 0)

    def test_failed_sources_are_counted(self):
        write(self.ws.discovery / "bookshelf" / "abc" / "ingest-state.json", {
            "sources": [
                {"uri": "/a.txt", "outcome": "indexed"},
                {"uri": "/b.txt", "outcome": "failed", "error": "cracking failed"},
            ],
        })
        self.assertEqual(self.ws.collect()["bookshelf"]["failed"], 1)


# --------------------------------------------------------------------------
# Golden fixtures: real workspaces, multiple Discovery Express versions
# --------------------------------------------------------------------------

def _real_workspaces(limit=6):
    base = Path.home() / "discovery"
    if not base.is_dir():
        return []
    found = []
    for child in sorted(base.iterdir()):
        if (child / ".discovery").is_dir():
            found.append(child)
        if len(found) >= limit:
            break
    return found


class GoldenWorkspaceTests(unittest.TestCase):
    """Runs against real workspaces when present. Skipped otherwise.

    Synthetic fixtures cannot catch schema drift between Discovery Express
    versions; these can.
    """

    @classmethod
    def setUpClass(cls):
        cls.workspaces = _real_workspaces()
        if not cls.workspaces:
            raise unittest.SkipTest("no ~/discovery workspaces available")

    def test_every_workspace_collects_without_raising(self):
        for workspace in self.workspaces:
            with self.subTest(workspace=workspace.name):
                snapshot = collector.collect(str(workspace))
                self.assertEqual(snapshot["schemaVersion"], collector.SCHEMA_VERSION)
                json.dumps(snapshot)

    def test_no_workspace_reports_a_hard_coverage_error(self):
        for workspace in self.workspaces:
            with self.subTest(workspace=workspace.name):
                snapshot = collector.collect(str(workspace))
                errors = [c for c in snapshot["coverage"]["entries"] if c["state"] == "error"]
                self.assertEqual(errors, [], "unexpected read errors: %s" % errors)

    def test_approved_never_exceeds_leaf_total(self):
        for workspace in self.workspaces:
            with self.subTest(workspace=workspace.name):
                totals = collector.collect(str(workspace))["tasks"].get("totals", {})
                if totals.get("leafTotal"):
                    self.assertLessEqual(
                        totals["leafApproved"] + totals["leafAwaitingReview"],
                        totals["leafTotal"],
                    )

    def test_no_operator_prompts_leak_from_real_workspaces(self):
        """meta.json carries the full operator prompt; it must never be emitted."""
        for workspace in self.workspaces:
            with self.subTest(workspace=workspace.name):
                blob = json.dumps(collector.collect(str(workspace)))
                self.assertNotIn("ABSOLUTE RULE", blob)
                self.assertNotIn("== THE RESEARCH QUESTION ==", blob)

    def test_summary_renders_for_every_workspace(self):
        for workspace in self.workspaces:
            with self.subTest(workspace=workspace.name):
                text = collector.summarize(collector.collect(str(workspace)))
                self.assertIn("Discovery dashboard", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
