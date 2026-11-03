import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen


SERVER_PATH = Path(__file__).resolve().parents[1] / "assets" / "engine-chat" / "server.py"
SERVER_SPEC = importlib.util.spec_from_file_location("engine_chat_runtime_server", SERVER_PATH)
if SERVER_SPEC is None or SERVER_SPEC.loader is None:
    raise RuntimeError("Unable to load Engine Chat runtime server")
server = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(server)


def event(kind, content, sequence, timestamp):
    return {
        "kind": kind,
        "content": content,
        "sequence": sequence,
        "timestamp": timestamp,
    }


def proposed(title, raw_input, sequence, timestamp, kind="read"):
    content = "**{}**\n```json\n{}\n```".format(
        title,
        json.dumps({"rawInput": raw_input, "kind": kind}),
    )
    return event("ActionProposed", content, sequence, timestamp)


def applied(title, payload, sequence, timestamp):
    content = "**{}**\n```json\n{{}}\n```\n\n→\n\n```json\n{}\n```".format(
        title,
        json.dumps(payload),
    )
    return event("ActionApplied", content, sequence, timestamp)


class EngineRunParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.run_dir = self.workspace / ".discovery" / "engine-runs" / "mission-control" / "abc123"
        self.run_dir.mkdir(parents=True)
        self.meta = {
            "instanceId": "abc123",
            "definitionId": "mission-control",
            "state": "Running",
            "startedAt": "2026-10-24T10:00:00+00:00",
        }
        (self.run_dir / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_events(self, events):
        with (self.run_dir / "output.jsonl").open("w", encoding="utf-8") as stream:
            for item in events:
                stream.write(json.dumps(item) + "\n")

    def parse(self):
        return server.EngineRunParser(self.workspace).parse(self.run_dir, self.meta, (1, 2, 3, 4))

    def test_preserves_timeline_order_while_coalescing_actions_and_sleep(self):
        task_title = "discovery-app-sdk-discovery-tasks-get"
        sleep_title = "discovery-app-sdk-discovery-engine-sleep"
        events = [
            event("Thinking", "**Planning**\nI need", 1, "2026-10-24T10:00:01+00:00"),
            event("Thinking", " context.", 2, "2026-10-24T10:00:01.1+00:00"),
            proposed(task_title, {"taskId": "DX-21"}, 3, "2026-10-24T10:00:02+00:00"),
            applied(task_title, {"rawOutput": {"content": "{}"}, "status": "completed"}, 4, "2026-10-24T10:00:03+00:00"),
            proposed(sleep_title, {"minutes": 2, "reason": "Waiting for DX-21."}, 5, "2026-10-24T10:00:04+00:00", kind="other"),
            applied(
                sleep_title,
                {
                    "rawOutput": {
                        "content": json.dumps(
                            {
                                "acknowledged": True,
                                "sleepUntil": "2026-10-24T10:02:05+00:00",
                                "_sleepMinutes": 2,
                            }
                        )
                    },
                    "status": "completed",
                },
                6,
                "2026-10-24T10:00:04.1+00:00",
            ),
            event("Observation", "The task", 7, "2026-10-24T10:00:05+00:00"),
            event("Observation", " is ready.", 8, "2026-10-24T10:00:05.1+00:00"),
            event("Done", "Stop reason: end_turn", 9, "2026-10-24T10:00:06+00:00"),
            proposed(
                "Viewing ...blog/knowledge/report.md",
                {"path": str(self.workspace / "knowledge" / "report.md")},
                10,
                "2026-10-24T10:02:06+00:00",
            ),
        ]
        self.write_events(events)

        payload = self.parse()

        self.assertEqual(2, len(payload["turns"]))
        first = payload["turns"][0]
        self.assertEqual(
            ["thinking", "action", "observation", "sleep"],
            [entry["type"] for entry in first["timeline"]],
        )
        self.assertEqual("Planning", first["timeline"][0]["summary"])
        self.assertEqual("I need context.", first["timeline"][0]["detail"])
        action = first["timeline"][1]
        self.assertEqual("Getting additional detail on DX-21", action["summary"])
        self.assertEqual("completed", action["status"])
        self.assertEqual("The task is ready.", first["timeline"][2]["summary"])
        self.assertEqual(120, first["sleep"]["durationSeconds"])
        self.assertEqual("2026-10-24T10:02:05+00:00", first["sleep"]["until"])
        second_action = payload["turns"][1]["timeline"][0]
        self.assertEqual("Reading knowledge/report.md", second_action["summary"])
        self.assertEqual("pending", second_action["status"])

        serialized = json.dumps(payload)
        self.assertNotIn("rawInput", serialized)
        self.assertNotIn(str(self.workspace), serialized)
        self.assertNotIn("discovery-app-sdk", serialized)

    def test_error_marks_proposed_action_without_exposing_error_payload(self):
        title = "Viewing ...blog/knowledge/missing.md"
        events = [
            proposed(title, {"path": str(self.workspace / "knowledge" / "missing.md")}, 1, "2026-10-24T10:00:01+00:00"),
            event(
                "Error",
                'Tool call failed: {} - {{"message":"Path does not exist","code":"failure"}}'.format(title),
                2,
                "2026-10-24T10:00:02+00:00",
            ),
        ]
        self.write_events(events)

        payload = self.parse()
        action = payload["turns"][0]["timeline"][0]

        self.assertEqual("failed", action["status"])
        self.assertEqual("Reading knowledge/missing.md", action["summary"])
        self.assertNotIn("Path does not exist", json.dumps(payload))

    def test_agent_launch_keeps_prompt_in_place_as_sanitized_detail(self):
        title = "discovery-app-sdk-discovery-agents-run-start"
        prompt = "Read {} and execute DX-74 only.\nCommit the finished package.".format(
            self.workspace / "knowledge" / "handoff.md"
        )
        events = [
            event("Thinking", "**Choosing a worker**\nThis needs a focused production pass.", 1, "2026-10-24T10:00:01+00:00"),
            proposed(
                title,
                {
                    "agentId": "catalog/batteries-included",
                    "taskId": "DX-74",
                    "conversationName": "DX-74 production",
                    "prompt": prompt,
                },
                2,
                "2026-10-24T10:00:02+00:00",
                kind="other",
            ),
            applied(
                title,
                {"rawOutput": {"content": '{"run":{"runId":"secret-run-id"}}'}, "status": "completed"},
                3,
                "2026-10-24T10:00:03+00:00",
            ),
            event("Observation", "The delegated production run is underway.", 4, "2026-10-24T10:00:04+00:00"),
        ]
        self.write_events(events)

        payload = self.parse()
        timeline = payload["turns"][0]["timeline"]

        self.assertEqual(["thinking", "action", "observation"], [entry["type"] for entry in timeline])
        self.assertEqual("Launching catalog/batteries-included to work on DX-74", timeline[1]["summary"])
        self.assertEqual("Delegated prompt", timeline[1]["detailLabel"])
        self.assertEqual("agent", timeline[1]["visual"])
        self.assertIn("Read knowledge/handoff.md", timeline[1]["detail"])
        self.assertEqual("completed", timeline[1]["status"])
        self.assertEqual("Launched DX-74 production", payload["turns"][0]["summary"])
        serialized = json.dumps(payload)
        self.assertNotIn(str(self.workspace), serialized)
        self.assertNotIn("secret-run-id", serialized)

    def test_truncated_agent_launch_recovers_identifiers_and_prompt_excerpt(self):
        title = "discovery-app-sdk-discovery-agents-run-start"
        truncated_content = (
            "**{}**\n```json\n{{\"rawInput\":{{"
            "\"agentId\":\"catalog/batteries-included\","
            "\"taskId\":\"DX-74\","
            "\"prompt\":\"Execute DX-74 and preserve the reviewed baseline"
            "\n…\n```"
        ).format(title)
        events = [
            event("ActionProposed", truncated_content, 1, "2026-10-24T10:00:01+00:00"),
            applied(title, {"rawOutput": {"content": "{}"}, "status": "completed"}, 2, "2026-10-24T10:00:02+00:00"),
        ]
        self.write_events(events)

        payload = self.parse()
        action = payload["turns"][0]["timeline"][0]

        self.assertEqual("Launching catalog/batteries-included to work on DX-74", action["summary"])
        self.assertEqual("Delegated prompt (log excerpt)", action["detailLabel"])
        self.assertEqual("Execute DX-74 and preserve the reviewed baseline", action["detail"])
        self.assertEqual("completed", action["status"])

    def test_truncated_transition_recovers_target_status(self):
        title = "discovery-app-sdk-discovery-tasks-transition"
        truncated_content = (
            "**{}**\n```json\n{{\"rawInput\":{{"
            "\"taskId\":\"DX-74\","
            "\"status\":\"executing\","
            "\"comment\":\"Production has started"
            "\n…\n```"
        ).format(title)
        self.write_events(
            [event("ActionProposed", truncated_content, 1, "2026-10-24T10:00:01+00:00")]
        )

        action = self.parse()["turns"][0]["timeline"][0]

        self.assertEqual("Updating DX-74 to In progress", action["summary"])
        self.assertEqual("Progress note", action["detailLabel"])
        self.assertEqual("Production has started", action["detail"])

    def test_rolls_up_context_and_merges_chunked_revisits(self):
        task_title = "discovery-app-sdk-discovery-tasks-get"
        review_title = "Viewing ...blog/knowledge/dx-75/review.md"
        manifest_title = "Viewing ...blog/knowledge/dx-75/manifest.json"
        verify_title = "Verify the DX-75 review packet"
        review_path = str(self.workspace / "knowledge" / "dx-75" / "review.md")
        manifest_path = str(self.workspace / "knowledge" / "dx-75" / "manifest.json")
        events = [
            proposed(task_title, {"taskId": "DX-75"}, 1, "2026-10-24T10:00:01+00:00"),
            applied(task_title, {"rawOutput": {"content": "{}"}, "status": "completed"}, 2, "2026-10-24T10:00:02+00:00"),
            proposed(task_title, {"taskId": "DX-75"}, 3, "2026-10-24T10:00:03+00:00"),
            applied(task_title, {"rawOutput": {"content": "{}"}, "status": "completed"}, 4, "2026-10-24T10:00:04+00:00"),
            proposed(review_title, {"path": review_path}, 5, "2026-10-24T10:00:05+00:00"),
            applied(
                review_title,
                {"rawOutput": {"content": "File too large to read at once. Consider using the grep tool."}, "status": "completed"},
                6,
                "2026-10-24T10:00:06+00:00",
            ),
            proposed(manifest_title, {"path": manifest_path}, 7, "2026-10-24T10:00:07+00:00"),
            applied(manifest_title, {"rawOutput": {"content": "{}"}, "status": "completed"}, 8, "2026-10-24T10:00:08+00:00"),
            proposed(review_title, {"path": review_path, "view_range": [1, 150]}, 9, "2026-10-24T10:00:09+00:00"),
            event("Error", "Tool call failed: {} - out of bounds".format(review_title), 10, "2026-10-24T10:00:10+00:00"),
            proposed(review_title, {"path": review_path, "view_range": [151, 300]}, 11, "2026-10-24T10:00:11+00:00"),
            applied(review_title, {"rawOutput": {"content": "section"}, "status": "completed"}, 12, "2026-10-24T10:00:12+00:00"),
            proposed(
                verify_title,
                {"taskId": "DX-75", "description": "Verify the packet"},
                13,
                "2026-10-24T10:00:13+00:00",
                kind="execute",
            ),
            applied(verify_title, {"rawOutput": {"content": "PASS"}, "status": "completed"}, 14, "2026-10-24T10:00:14+00:00"),
        ]
        self.write_events(events)

        payload = self.parse()
        timeline = payload["turns"][0]["timeline"]

        self.assertEqual(1, len(timeline))
        group = timeline[0]
        self.assertEqual("group", group["type"])
        self.assertEqual("context", group["groupKind"])
        self.assertEqual("Gathering context on DX-75", group["summary"])
        self.assertEqual("completed", group["status"])
        self.assertEqual(7, group["actionCount"])
        self.assertEqual(5, len(group["children"]))
        self.assertEqual(2, group["children"][0]["mergedCount"])
        revisit = group["children"][3]
        self.assertEqual("Checking review.md again", revisit["summary"])
        self.assertEqual(2, revisit["mergedCount"])
        self.assertIn("Lines 1–150 — didn’t complete", revisit["detail"])
        self.assertIn("Lines 151–300 — completed", revisit["detail"])
        self.assertEqual(7, payload["stats"]["actions"])
        self.assertEqual("Reviewed DX-75", payload["turns"][0]["summary"])

    def test_identical_action_detail_is_not_expandable(self):
        title = "Verify original closure packet and clear index before label correction"
        events = [
            proposed(
                title,
                {"description": "Verify original closure packet and clear index before label correction"},
                1,
                "2026-10-24T10:00:01+00:00",
                kind="execute",
            ),
            applied(title, {"rawOutput": {"content": "PASS"}, "status": "completed"}, 2, "2026-10-24T10:00:02+00:00"),
        ]
        self.write_events(events)

        action = self.parse()["turns"][0]["timeline"][0]

        self.assertEqual(title, action["summary"])
        self.assertIsNone(action["detail"])
        self.assertIsNone(action["detailLabel"])

    def test_starting_prompt_excludes_engine_memory_and_precedes_turns(self):
        self.meta["prompt"] = (
            "<engine-memory>\nPrivate retrieval context that should not render.\n</engine-memory>\n\n"
            "Complete DX-68 and keep task statuses current."
        )
        self.write_events(
            [event("Observation", "Starting now.", 1, "2026-10-24T10:00:01+00:00")]
        )

        payload = self.parse()

        self.assertEqual("Complete DX-68 and keep task statuses current.", payload["startingPrompt"])
        self.assertNotIn("Private retrieval context", json.dumps(payload))

    def test_steering_messages_are_queued_between_turns(self):
        task_title = "discovery-app-sdk-discovery-tasks-get"
        events = [
            proposed(task_title, {"taskId": "DX-73"}, 1, "2026-10-24T10:00:01+00:00"),
            applied(task_title, {"rawOutput": {"content": "{}"}, "status": "completed"}, 2, "2026-10-24T10:00:02+00:00"),
            event("Done", "Stop reason: end_turn", 3, "2026-10-24T10:00:03+00:00"),
            event(
                "Progress",
                "Communication queued on steering:text: Preserve the v9.2 narrative voice.",
                4,
                "2026-10-24T10:00:04+00:00",
            ),
            proposed(task_title, {"taskId": "DX-74"}, 5, "2026-10-24T10:00:05+00:00"),
        ]
        self.write_events(events)

        payload = self.parse()

        self.assertEqual(2, len(payload["turns"]))
        self.assertEqual([], payload["turns"][0]["queuedMessagesBefore"])
        self.assertEqual(
            "Preserve the v9.2 narrative voice.",
            payload["turns"][1]["queuedMessagesBefore"][0]["text"],
        )
        self.assertEqual([], payload["queuedMessages"])
        self.assertFalse(
            any(
                "Communication queued" in str(action.get("summary"))
                for turn in payload["turns"]
                for action in server.iter_actions(turn["timeline"])
            )
        )

    def test_steering_during_live_turn_remains_queued_for_next_turn(self):
        self.write_events(
            [
                event("Observation", "Current work continues.", 1, "2026-10-24T10:00:01+00:00"),
                event(
                    "Progress",
                    "Communication queued on steering:text: Recheck the approved definitions.",
                    2,
                    "2026-10-24T10:00:02+00:00",
                ),
            ]
        )

        payload = self.parse()

        self.assertEqual([], payload["turns"][0]["queuedMessagesBefore"])
        self.assertEqual("Recheck the approved definitions.", payload["queuedMessages"][0]["text"])
        self.assertEqual(1, payload["stats"]["queuedMessages"])

    def test_thinking_stream_survives_interleaved_tool_events(self):
        title = "Viewing ...blog/knowledge/report.md"
        events = [
            event("Thinking", "**Comparing artifacts**\n\nI need", 1, "2026-10-24T10:00:01.000000+00:00"),
            proposed(title, {"path": str(self.workspace / "knowledge" / "report.md")}, 2, "2026-10-24T10:00:01.100000+00:00"),
            event("Thinking", " to inspect", 3, "2026-10-24T10:00:01.200000+00:00"),
            applied(title, {"rawOutput": {"content": "report"}, "status": "completed"}, 4, "2026-10-24T10:00:01.300000+00:00"),
            event("Thinking", " exact artifacts for comparison.", 5, "2026-10-24T10:00:01.400000+00:00"),
        ]
        self.write_events(events)

        timeline = self.parse()["turns"][0]["timeline"]
        thinking = [entry for entry in timeline if entry["type"] == "thinking"]

        self.assertEqual(1, len(thinking))
        self.assertEqual("Comparing artifacts", thinking[0]["summary"])
        self.assertEqual("I need to inspect exact artifacts for comparison.", thinking[0]["detail"])

    def test_alternating_thinking_and_grader_json_become_two_coherent_rows(self):
        events = [
            event("Thinking", "**Checking scores**\n\nI need", 1, "2026-10-24T10:00:01.000000+00:00"),
            event("Observation", '{"questionScores":[{"questionId":"q1"', 2, "2026-10-24T10:00:01.050000+00:00"),
            event("Thinking", " to verify", 3, "2026-10-24T10:00:01.100000+00:00"),
            event("Observation", ',"normalizedScore":1,"reasoning":"pass"', 4, "2026-10-24T10:00:01.150000+00:00"),
            event("Thinking", " the weighted result.", 5, "2026-10-24T10:00:01.200000+00:00"),
            event("Observation", ',"evidence":["artifact"]}]}', 6, "2026-10-24T10:00:01.250000+00:00"),
        ]
        self.write_events(events)

        timeline = self.parse()["turns"][0]["timeline"]

        self.assertEqual(["thinking", "observation"], [entry["type"] for entry in timeline])
        self.assertEqual("I need to verify the weighted result.", timeline[0]["detail"])
        self.assertEqual("Received grading results", timeline[1]["summary"])
        self.assertIsNone(timeline[1]["detail"])

    def test_repeated_agent_session_cancellations_merge_into_one_row(self):
        title = "discovery-app-sdk-discovery-agents-session-cancel"
        events = []
        for index in range(5):
            events.append(
                proposed(
                    title,
                    {"sessionId": "session-{}".format(index)},
                    index + 1,
                    "2026-10-24T10:00:01.{}00000+00:00".format(index),
                    kind="other",
                )
            )
        for index in range(5):
            events.append(
                applied(
                    title,
                    {"rawOutput": {"content": "cancelled"}, "status": "completed"},
                    index + 6,
                    "2026-10-24T10:00:02.{}00000+00:00".format(index),
                )
            )
        self.write_events(events)

        timeline = self.parse()["turns"][0]["timeline"]

        self.assertEqual(1, len(timeline))
        self.assertEqual("Stopping 5 agent sessions", timeline[0]["summary"])
        self.assertEqual(5, timeline[0]["mergedCount"])
        self.assertEqual("completed", timeline[0]["status"])

    def test_repository_recommends_latest_live_run(self):
        self.write_events([event("Done", "Stop reason: end_turn", 1, "2026-10-24T10:00:01+00:00")])
        old_dir = self.workspace / ".discovery" / "engine-runs" / "mission-control" / "old456"
        old_dir.mkdir(parents=True)
        (old_dir / "meta.json").write_text(
            json.dumps(
                {
                    "instanceId": "old456",
                    "definitionId": "mission-control",
                    "state": "Completed",
                    "startedAt": "2026-10-23T10:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        (old_dir / "output.jsonl").write_text(
            json.dumps(event("Done", "Stop reason: end_turn", 1, "2026-10-23T10:00:01+00:00")) + "\n",
            encoding="utf-8",
        )

        payload = server.EngineRunRepository(self.workspace).list_runs()

        self.assertEqual("abc123", payload["recommended"]["instanceId"])
        self.assertTrue(next(run for run in payload["runs"] if run["instanceId"] == "abc123")["isLive"])

    def test_timestamp_normalization_supports_mac_python_fraction_rules(self):
        short = server.normalize_iso("2026-10-24T10:00:00.12345+00:00")
        long = server.normalize_iso("2026-10-24T10:00:00.1234567Z")

        self.assertEqual("2026-10-24T10:00:00.123450+00:00", short)
        self.assertEqual("2026-10-24T10:00:00.123456+00:00", long)
        self.assertIsNotNone(server.parse_time(short))
        self.assertIsNotNone(server.parse_time(long))

    def test_stale_idle_run_does_not_override_newest_completed_run(self):
        self.meta["state"] = "Completed"
        (self.run_dir / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
        self.write_events([event("Done", "Stop reason: end_turn", 1, "2026-10-24T10:00:01+00:00")])
        stale_dir = self.workspace / ".discovery" / "engine-runs" / "mission-control" / "stale789"
        stale_dir.mkdir(parents=True)
        (stale_dir / "meta.json").write_text(
            json.dumps(
                {
                    "instanceId": "stale789",
                    "definitionId": "mission-control",
                    "state": "Idle",
                    "startedAt": "2026-10-20T10:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        stale_output = stale_dir / "output.jsonl"
        stale_output.write_text(
            json.dumps(event("Done", "Stop reason: end_turn", 1, "2026-10-20T10:00:01+00:00")) + "\n",
            encoding="utf-8",
        )
        os.utime(stale_output, (1, 1))

        payload = server.EngineRunRepository(self.workspace).list_runs()

        self.assertEqual("abc123", payload["recommended"]["instanceId"])
        self.assertFalse(next(run for run in payload["runs"] if run["instanceId"] == "stale789")["isLive"])

    def test_active_agents_use_session_truth_and_friendly_work_labels(self):
        sessions = self.workspace / ".discovery" / "sessions"
        agent_runs = self.workspace / ".discovery" / "agent-runs"
        tasks_dir = self.workspace / ".discovery" / "tasks"
        sessions.mkdir(parents=True)
        agent_runs.mkdir(parents=True)
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "index.json").write_text(
            json.dumps(
                {
                    "tasks": [
                        {"taskId": "uuid-73", "dxId": "DX-73", "title": "Write the Canonical v10.0"},
                        {"taskId": "uuid-74", "dxId": "DX-74", "title": "Build the v10.0 Editions"},
                        {"taskId": "uuid-75", "dxId": "DX-75", "title": "Independently Validate the v10.0 Package"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        records = [
            ("run-battery", "catalog/batteries-included", "DX-74", "running", "DX-74 production"),
            ("run-writer", "catalog/postdoctoral-science-writer", "uuid-73", "running", "DX-73 targeted rewrite"),
            ("run-grader", "catalog/agentic-grader", "DX-75", "queued", "DX-75 registered grading"),
            ("run-stale", "catalog/postdoctoral-science-writer", "DX-73", "cancelled", "stale projection"),
        ]
        for index, (run_id, agent_id, task_id, status, conversation) in enumerate(records):
            (sessions / "{}.json".format(run_id)).write_text(
                json.dumps(
                    {
                        "header": {
                            "sessionId": run_id,
                            "agentId": agent_id,
                            "taskId": task_id,
                            "status": status,
                            "startedAt": "2026-10-24T10:0{}:00+00:00".format(index),
                        },
                        "events": [],
                    }
                ),
                encoding="utf-8",
            )
            (agent_runs / "{}.json".format(run_id)).write_text(
                json.dumps(
                    {
                        "runId": run_id,
                        "agentId": agent_id,
                        "taskId": task_id,
                        "conversationName": conversation,
                        "status": "Running",
                    }
                ),
                encoding="utf-8",
            )

        payload = server.EngineRunRepository(self.workspace).list_active_agents()
        by_name = {agent["displayName"]: agent for agent in payload["agents"]}

        self.assertEqual(3, payload["count"])
        self.assertEqual("DX-74", by_name["Batteries Included"]["workingOn"])
        self.assertEqual("DX-73", by_name["Postdoctoral Writer"]["workingOn"])
        self.assertEqual("Grading v10.0", by_name["Agentic Grader"]["workingOn"])
        self.assertEqual("Queued", by_name["Agentic Grader"]["statusLabel"])
        self.assertNotIn("run-stale", [agent["runId"] for agent in payload["agents"]])


class EngineChatServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / ".discovery").mkdir()
        self.httpd = server.create_server(self.workspace, 0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def get(self, path):
        return urlopen("http://127.0.0.1:{}{}".format(self.port, path), timeout=2)

    def test_health_identifies_service_and_sends_security_headers(self):
        with self.get("/api/health") as response:
            payload = json.loads(response.read().decode("utf-8"))
            headers = response.headers

        self.assertTrue(payload["ok"])
        self.assertEqual("engine-chat", payload["serviceId"])
        self.assertEqual("0.1.0-experimental", payload["engineChatVersion"])
        self.assertEqual("1", payload["protocolVersion"])
        self.assertEqual("0.15.19", payload["testedDiscoveryVersion"])
        self.assertEqual("macOS", payload["testedOperatingSystem"])
        self.assertEqual(server.workspace_fingerprint(self.workspace), payload["workspaceFingerprint"])
        self.assertEqual(os.getpid(), payload["pid"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertEqual("DENY", headers["X-Frame-Options"])
        self.assertEqual("no-referrer", headers["Referrer-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn("script-src 'self' 'unsafe-inline'", headers["Content-Security-Policy"])
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])

    def test_valid_project_without_engine_runs_returns_empty_state(self):
        with self.get("/api/runs") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual([], payload["runs"])
        self.assertIsNone(payload["recommended"])
        self.assertEqual("127.0.0.1", self.httpd.server_address[0])

    def test_traversal_ids_and_resolved_symlink_escape_are_rejected(self):
        repository = server.EngineRunRepository(self.workspace)
        self.assertIsNone(repository.get_run(".", "run"))
        self.assertIsNone(repository.get_run("..", "run"))
        self.assertIsNone(repository.get_run("engine", ".."))

        outside = Path(self.temp.name).parent / (Path(self.temp.name).name + "-outside")
        outside_run = outside / "run"
        outside_run.mkdir(parents=True)
        (outside_run / "meta.json").write_text("{}", encoding="utf-8")
        runs_root = self.workspace / ".discovery" / "engine-runs"
        runs_root.mkdir()
        try:
            (runs_root / "escape").symlink_to(outside, target_is_directory=True)
            self.assertIsNone(repository.get_run("escape", "run"))
        finally:
            if (runs_root / "escape").exists() or (runs_root / "escape").is_symlink():
                (runs_root / "escape").unlink()
            for child in outside_run.iterdir():
                child.unlink()
            outside_run.rmdir()
            outside.rmdir()


if __name__ == "__main__":
    unittest.main()
