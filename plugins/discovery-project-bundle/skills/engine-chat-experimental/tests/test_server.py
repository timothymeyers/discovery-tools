import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
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

    def test_applied_steering_ends_turn_and_puts_message_before_new_context(self):
        title = "discovery-app-sdk-discovery-tasks-get"
        message = "Did you actually assign and start agents to run the leaf tasks?\nPlease delegate the work."
        self.write_events([
            event("Thinking", "Reviewing meeting notes.", 1, "2026-10-24T10:00:01+00:00"),
            event("Progress", "Communication applied on steering:text: " + message, 2, "2026-10-24T10:00:02+00:00"),
            event("Progress", "Turn interrupted by steering message — restarting.", 3, "2026-10-24T10:00:02.1+00:00"),
            proposed(title, {"taskId": "DX-74"}, 4, "2026-10-24T10:00:03+00:00"),
            applied(title, {"status": "completed"}, 5, "2026-10-24T10:00:03.1+00:00"),
            proposed(title, {"taskId": "DX-75"}, 6, "2026-10-24T10:00:04+00:00"),
            applied(title, {"status": "completed"}, 7, "2026-10-24T10:00:04.1+00:00"),
            event("Done", "Stop reason: end_turn", 8, "2026-10-24T10:00:05+00:00"),
        ])

        payload = self.parse()

        self.assertEqual(2, len(payload["turns"]))
        first, second = payload["turns"]
        self.assertEqual("steering", first["endReason"])
        self.assertEqual("2026-10-24T10:00:02+00:00", first["endedAt"])
        self.assertEqual(["thinking", "steering"], [entry["type"] for entry in first["timeline"]])
        self.assertEqual("Received steering message", first["timeline"][-1]["summary"])
        self.assertEqual("steering", second["startReason"])
        self.assertEqual("2026-10-24T10:00:03+00:00", second["startedAt"])
        self.assertEqual("Gathering context after your steering message.", second["intro"])
        self.assertEqual("group", second["timeline"][0]["type"])
        self.assertEqual("Gathering context on DX-74", second["timeline"][0]["summary"])
        self.assertEqual(1, len(second["queuedMessagesBefore"]))
        self.assertEqual(message, second["queuedMessagesBefore"][0]["text"])
        self.assertEqual("applied", second["queuedMessagesBefore"][0]["delivery"])
        self.assertEqual(2, payload["stats"]["actions"])
        self.assertEqual(0, payload["stats"]["queuedMessages"])
        self.assertEqual(1, payload["stats"]["steeringMessages"])
        self.assertNotIn("Communication applied", json.dumps(payload))
        self.assertNotIn("Turn interrupted by steering", json.dumps(payload))

    def test_steering_does_not_stitch_text_across_interruption(self):
        for kind in ("Thinking", "Observation"):
            with self.subTest(kind=kind):
                self.write_events([
                    event(kind, "Before steering.", 1, "2026-10-24T10:00:01+00:00"),
                    event("Progress", "Communication applied on steering:text: Change focus.", 2, "2026-10-24T10:00:01.1+00:00"),
                    event("Progress", "Turn interrupted by steering message — restarting.", 3, "2026-10-24T10:00:01.2+00:00"),
                    event(kind, "After steering.", 4, "2026-10-24T10:00:01.3+00:00"),
                ])

                payload = self.parse()

                self.assertEqual(2, len(payload["turns"]))
                self.assertEqual("Before steering.", payload["turns"][0]["timeline"][0]["summary"])
                self.assertEqual("After steering.", payload["turns"][1]["timeline"][0]["summary"])

    def test_applied_steering_without_next_activity_stays_after_closed_turn(self):
        events = [
            event("Thinking", "Working.", 1, "2026-10-24T10:00:01+00:00"),
            event("Progress", "Communication applied on steering:text: Delegate now.", 2, "2026-10-24T10:00:02+00:00"),
        ]
        self.write_events(events)
        initial = self.parse()
        events.append(event("Progress", "Turn interrupted by steering message — restarting.", 3, "2026-10-24T10:00:02.1+00:00"))
        self.write_events(events)
        updated = self.parse()

        self.assertEqual(1, len(updated["turns"]))
        self.assertEqual("steering", updated["turns"][0]["endReason"])
        self.assertEqual(initial["turns"], updated["turns"])
        self.assertEqual(initial["queuedMessages"], updated["queuedMessages"])
        self.assertEqual("Delegate now.", updated["queuedMessages"][0]["text"])
        self.assertEqual("applied", updated["queuedMessages"][0]["delivery"])
        self.assertEqual(0, updated["stats"]["queuedMessages"])

    def test_queued_then_applied_steering_has_only_one_message(self):
        self.write_events([
            event("Thinking", "Current work.", 1, "2026-10-24T10:00:01+00:00"),
            event("Progress", "Communication queued on steering:text: Recheck the definitions.", 2, "2026-10-24T10:00:02+00:00"),
            event("Progress", "Communication applied on steering:text: Recheck the definitions.", 3, "2026-10-24T10:00:03+00:00"),
            event("Thinking", "Checking now.", 4, "2026-10-24T10:00:04+00:00"),
        ])

        payload = self.parse()
        messages = payload["turns"][1]["queuedMessagesBefore"]

        self.assertEqual(1, len(messages))
        self.assertEqual("steering-2", messages[0]["id"])
        self.assertEqual("2026-10-24T10:00:02+00:00", messages[0]["queuedAt"])
        self.assertEqual("2026-10-24T10:00:03+00:00", messages[0]["receivedAt"])
        self.assertEqual("applied", messages[0]["delivery"])
        self.assertEqual(1, payload["stats"]["steeringMessages"])
        self.assertEqual(0, payload["stats"]["queuedMessages"])

    def test_restart_done_and_late_tool_result_do_not_create_empty_turn(self):
        title = "discovery-app-sdk-discovery-tasks-get"
        self.write_events([
            proposed(title, {"taskId": "DX-74"}, 1, "2026-10-24T10:00:01+00:00"),
            event("Progress", "Communication applied on steering:text: Change focus.", 2, "2026-10-24T10:00:02+00:00"),
            applied(title, {"status": "completed"}, 3, "2026-10-24T10:00:02.1+00:00"),
            event("Progress", "Turn interrupted by steering message — restarting.", 4, "2026-10-24T10:00:02.2+00:00"),
            event("Done", "Stop reason: cancelled", 5, "2026-10-24T10:00:02.3+00:00"),
            proposed(title, {"taskId": "DX-75"}, 6, "2026-10-24T10:00:03+00:00"),
        ])

        payload = self.parse()

        self.assertEqual(2, len(payload["turns"]))
        first, second = payload["turns"]
        self.assertEqual("interrupted", first["timeline"][0]["status"])
        self.assertEqual(0, first["pendingActionCount"])
        self.assertEqual(0, first["completedActionCount"])
        self.assertEqual("2026-10-24T10:00:03+00:00", second["startedAt"])
        self.assertEqual("pending", second["timeline"][0]["status"])
        self.assertEqual("Change focus.", second["queuedMessagesBefore"][0]["text"])

    def test_interruption_notice_can_precede_applied_message_or_stand_alone(self):
        for include_applied in (False, True):
            with self.subTest(include_applied=include_applied):
                events = [
                    event("Thinking", "Before steering.", 1, "2026-10-24T10:00:01+00:00"),
                    event("Progress", "Turn interrupted by steering message — restarting.", 2, "2026-10-24T10:00:02+00:00"),
                ]
                if include_applied:
                    events.append(event("Progress", "Communication applied on steering:text: Check agents.", 3, "2026-10-24T10:00:02.1+00:00"))
                events.append(event("Thinking", "After steering.", 4, "2026-10-24T10:00:03+00:00"))
                self.write_events(events)

                payload = self.parse()

                self.assertEqual(2, len(payload["turns"]))
                self.assertEqual("steering", payload["turns"][0]["endReason"])
                self.assertEqual("steering", payload["turns"][1]["startReason"])
                self.assertEqual(int(include_applied), len(payload["turns"][1]["queuedMessagesBefore"]))
                self.assertEqual(1, sum(entry["type"] == "steering" for entry in payload["turns"][0]["timeline"]))

    def test_distinct_steering_events_with_identical_text_are_not_dropped(self):
        self.write_events([
            event("Thinking", "Working.", 1, "2026-10-24T10:00:01+00:00"),
            event("Progress", "Communication applied on steering:text: Check agents.", 2, "2026-10-24T10:00:02+00:00"),
            event("Progress", "Communication applied on steering:text: Check agents.", 3, "2026-10-24T10:00:02.1+00:00"),
            event("Thinking", "Checking now.", 4, "2026-10-24T10:00:03+00:00"),
        ])

        payload = self.parse()

        self.assertEqual(2, len(payload["turns"]))
        self.assertEqual(["steering-2", "steering-3"], [message["id"] for message in payload["turns"][1]["queuedMessagesBefore"]])

    def test_non_steering_configuration_progress_does_not_interrupt(self):
        self.write_events([
            event("Thinking", "Working.", 1, "2026-10-24T10:00:01+00:00"),
            event("Progress", "Communication applied on config:model: model-name", 2, "2026-10-24T10:00:02+00:00"),
            event("Observation", "Still working.", 3, "2026-10-24T10:00:03+00:00"),
        ])

        payload = self.parse()

        self.assertEqual(1, len(payload["turns"]))
        self.assertIsNone(payload["turns"][0]["endedAt"])
        self.assertEqual("Updated engine settings", payload["turns"][0]["timeline"][1]["summary"])
        self.assertEqual([], payload["queuedMessages"])

    def test_interrupted_context_group_does_not_keep_spinning(self):
        title = "discovery-app-sdk-discovery-tasks-get"
        self.write_events([
            proposed(title, {"taskId": "DX-74"}, 1, "2026-10-24T10:00:01+00:00"),
            applied(title, {"status": "completed"}, 2, "2026-10-24T10:00:01.1+00:00"),
            proposed(title, {"taskId": "DX-75"}, 3, "2026-10-24T10:00:02+00:00"),
            event("Progress", "Communication applied on steering:text: Change focus.", 4, "2026-10-24T10:00:03+00:00"),
        ])

        payload = self.parse()
        group = payload["turns"][0]["timeline"][0]

        self.assertEqual("group", group["type"])
        self.assertEqual("interrupted", group["status"])
        self.assertEqual(["completed", "interrupted"], [action["status"] for action in group["children"]])
        self.assertEqual(0, payload["turns"][0]["pendingActionCount"])

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


class EngineHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.run_dir = self.workspace / ".discovery" / "engine-runs" / "mission-control" / "handoff123"
        self.run_dir.mkdir(parents=True)
        self.tasks_path = self.workspace / ".discovery" / "tasks" / "index.json"
        self.tasks_path.parent.mkdir(parents=True)
        self.meta = {
            "instanceId": "handoff123", "definitionId": "mission-control", "state": "Idle",
            "startedAt": "2026-10-24T10:00:00+00:00", "completedAt": "2026-10-24T10:00:04+00:00",
        }
        self.events = [
            proposed("discovery-app-sdk-discovery-tasks-comment", {"taskId": "DX-2", "text": "Finished the work."}, 1, "2026-10-24T10:00:01+00:00"),
            applied("discovery-app-sdk-discovery-tasks-comment", {"status": "completed"}, 2, "2026-10-24T10:00:02+00:00"),
            event("Observation", "All five DX-2 subtasks are at executionDone and waiting for your approval.", 3, "2026-10-24T10:00:03+00:00"),
            event("Done", "Stop reason: end_turn", 4, "2026-10-24T10:00:04+00:00"),
        ]
        self.tasks = [
            {"taskId": "root-task", "dxId": "DX-2", "status": "new", "title": "Milestone"},
            *[{"taskId": "task-{}".format(number), "dxId": "DX-{}".format(number),
               "status": status, "title": "Deliverable {}".format(number), "parentId": "root-task"}
              for number, status in [(7, "complete"), (8, "complete"), (9, "incomplete"), (10, "executionDone"), (11, "incomplete")]],
            {"taskId": "unrelated-task", "dxId": "DX-99", "status": "executionDone", "title": "Unrelated review"},
        ]
        self.write_run()
        self.write_tasks()
        self.repository = server.EngineRunRepository(self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def write_run(self):
        (self.run_dir / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
        (self.run_dir / "output.jsonl").write_text("".join(json.dumps(item) + "\n" for item in self.events), encoding="utf-8")

    def write_tasks(self):
        self.tasks_path.write_text(json.dumps({"tasks": self.tasks}), encoding="utf-8")

    def get_run(self):
        return self.repository.get_run("mission-control", "handoff123")

    def test_idle_finished_turn_surfaces_only_current_scoped_approval(self):
        payload = self.get_run()
        run = payload["run"]
        attention = run["attention"]
        self.assertEqual("Idle", run["state"])
        self.assertEqual("Idle", run["stateLabel"])
        self.assertEqual(self.meta["completedAt"], run["completedAt"])
        self.assertEqual("Needs your action", attention["label"])
        self.assertEqual("1 task needs your review", attention["title"])
        self.assertEqual(["DX-10"], [task["id"] for task in attention["tasks"]])
        self.assertEqual("Current task board", attention["source"])
        self.assertEqual("turn-1", attention["turnId"])
        self.assertEqual("observation-3", attention["entryId"])
        self.assertNotIn("All five", attention["excerpt"])
        self.assertIn("All five", payload["turns"][-1]["timeline"][-1]["summary"])

    def test_only_reviewable_task_statuses_trigger_approval(self):
        for status in ("executionDone", "flaggedHuman", "complete", "incomplete", "onHold", "stale", "failed", "flaggedAi", "new", "executing", "removed"):
            with self.subTest(status=status):
                self.tasks[4]["status"] = status
                self.write_tasks()
                self.assertEqual(status in {"executionDone", "flaggedHuman"}, self.get_run()["run"]["attention"] is not None)

    def test_approval_refreshes_without_reparsing_logs_and_returns_snapshots(self):
        first = self.get_run()
        first_version = first["run"]["version"]
        with mock.patch.object(self.repository.parser, "parse", side_effect=AssertionError("Logs did not change")):
            self.tasks[-1]["title"] = "Unrelated edit must not churn the feed"
            self.write_tasks()
            self.assertEqual(first_version, self.get_run()["run"]["version"])
            self.tasks[4]["status"] = "complete"
            self.write_tasks()
            resolved = self.get_run()
            self.assertIsNone(resolved["run"]["attention"])
            self.assertNotEqual(first_version, resolved["run"]["version"])
            self.assertEqual("DX-10", first["run"]["attention"]["tasks"][0]["id"])

    def test_missing_or_malformed_task_data_never_keeps_old_approval(self):
        self.assertIsNotNone(self.get_run()["run"]["attention"])
        for content in (None, b"{", b"\xff", b'{"tasks":{}}', b'{"tasks":[]}', b'{"tasks":[null,"invalid"]}'):
            with self.subTest(content=content):
                if content is None:
                    self.tasks_path.unlink()
                else:
                    self.tasks_path.write_bytes(content)
                self.assertIsNone(self.get_run()["run"]["attention"])

    def test_running_sleeping_and_terminal_states_keep_lifecycle_status(self):
        for state in ("Running", "Sleeping", "Completed", "Stopped", "Failed", "Cancelled", "Unknown"):
            with self.subTest(state=state):
                self.meta["state"] = state
                self.write_run()
                run = self.get_run()["run"]
                self.assertIsNone(run["attention"])
                self.assertEqual(state, run["state"])
                self.assertEqual(server.state_label(state), run["stateLabel"])

    def test_paused_handoff_is_distinct_from_completion(self):
        self.meta["state"] = "Paused"
        self.write_run()
        run = self.get_run()["run"]
        self.assertEqual("Paused", run["stateLabel"])
        self.assertEqual("Needs your action", run["attention"]["label"])

    def test_completed_at_does_not_finish_an_open_turn(self):
        self.events.pop()
        self.write_run()
        self.assertIsNone(self.get_run()["run"]["attention"])

    def test_pending_or_failed_actions_prevent_a_finished_handoff(self):
        original = list(self.events)
        for failed in (False, True):
            with self.subTest(failed=failed):
                extra = [proposed("Review artifacts", {}, 5, "2026-10-24T10:00:02.1+00:00")]
                if failed:
                    extra.append(event("Error", "Tool call failed: Review artifacts - failed", 6, "2026-10-24T10:00:02.2+00:00"))
                self.events = original[:2] + extra + original[2:]
                self.write_run()
                self.assertIsNone(self.get_run()["run"]["attention"])

    def test_new_turn_or_pending_steering_clears_handoff(self):
        original = list(self.events)
        for kind, text in (
            ("Thinking", "Starting the next pass."),
            ("Progress", "Communication queued on steering:text: Continue."),
            ("Progress", "Communication applied on steering:text: Continue."),
        ):
            with self.subTest(text=text):
                self.events = original + [event(kind, text, 5, "2026-10-24T10:01:00+00:00")]
                self.write_run()
                self.assertIsNone(self.get_run()["run"]["attention"])

    def test_old_request_is_not_reused_after_more_activity_or_a_new_final_update(self):
        original = list(self.events)
        for has_update in (False, True):
            with self.subTest(has_update=has_update):
                self.events = original[:-1] + [
                    proposed("Inspect the new artifacts", {}, 5, "2026-10-24T10:00:10+00:00"),
                    applied("Inspect the new artifacts", {"status": "completed"}, 6, "2026-10-24T10:00:11+00:00"),
                ]
                if has_update:
                    self.events.append(event("Observation", "All approvals are resolved. No action needed.", 7, "2026-10-24T10:00:12+00:00"))
                self.events.append(event("Done", "Stop reason: end_turn", 8, "2026-10-24T10:00:13+00:00"))
                self.write_run()
                self.assertIsNone(self.get_run()["run"]["attention"])

    def test_scheduled_sleep_blocks_handoff_until_its_deadline_on_cache_hits(self):
        title = "discovery-app-sdk-discovery-engine-sleep"
        self.events = self.events[:2] + [
            proposed(title, {"minutes": 2}, 5, "2026-10-24T10:00:02.1+00:00"),
            applied(title, {"sleepUntil": "2026-10-24T10:02:00+00:00", "_sleepMinutes": 2, "status": "completed"}, 6, "2026-10-24T10:00:02.2+00:00"),
        ] + self.events[2:]
        self.write_run()
        with mock.patch.object(server, "datetime", wraps=server.datetime) as clock:
            clock.now.return_value = server.parse_time("2026-10-24T10:01:00+00:00")
            sleeping = self.get_run()
            self.assertIsNone(sleeping["run"]["attention"])
            clock.now.return_value = server.parse_time("2026-10-24T10:03:00+00:00")
            with mock.patch.object(self.repository.parser, "parse", side_effect=AssertionError("Logs did not change")):
                awake = self.get_run()
            self.assertIsNotNone(awake["run"]["attention"])
            self.assertNotEqual(sleeping["run"]["version"], awake["run"]["version"])

    def test_sleep_without_acknowledgement_is_not_a_handoff(self):
        self.events.insert(2, proposed("discovery-app-sdk-discovery-engine-sleep", {"minutes": 2}, 5, "2026-10-24T10:00:02.1+00:00"))
        self.write_run()
        self.assertIsNone(self.get_run()["run"]["attention"])

    def test_general_input_request_remains_explicitly_observation_based(self):
        self.events = [event("Observation", "I need your input on the design.", 3, "2026-10-24T10:00:03+00:00"), self.events[-1]]
        self.write_run()
        self.tasks_path.unlink()
        attention = self.get_run()["run"]["attention"]
        self.assertEqual("input", attention["kind"])
        self.assertEqual("Latest observation", attention["source"])
        self.assertNotIn("tasks", attention)

    def test_guid_task_references_do_not_expand_scope_from_progress_text(self):
        title = "discovery-app-sdk-discovery-tasks-comment"
        self.events[0] = proposed(title, {"taskId": "task-10", "text": "DX-99 is unrelated."}, 1, "2026-10-24T10:00:01+00:00")
        self.events[2]["content"] = "The result is ready for your review."
        self.write_run()
        attention = self.get_run()["run"]["attention"]
        self.assertEqual(["DX-10"], [task["id"] for task in attention["tasks"]])

    def test_explicit_final_request_does_not_pull_in_other_touched_tasks(self):
        self.tasks[1]["status"] = "executionDone"
        self.write_tasks()
        self.events[2]["content"] = "DX-10 is waiting for your approval."
        self.write_run()
        self.assertEqual(["DX-10"], [task["id"] for task in self.get_run()["run"]["attention"]["tasks"]])

    def test_request_language_ignores_quotes_negation_history_and_resolution(self):
        for text in (
            "Waiting for delegated agents.", "The regulator needs approval.",
            "We are not waiting for your approval.", "Your approval was already received.",
            "Earlier we were waiting for your input.", "If it is ready for your review, send a note.",
            'Example: "waiting for your approval".', "`waiting for your approval`",
            "> Waiting for your approval.\nNo new request.",
            "```text\nWaiting for your approval\n```", "We aren't waiting for your decision.",
            "Waiting for your approval. Approval received.",
            "Waiting for your input. No further action is required.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(server.explicit_user_request(text, self.workspace))
        for text, kind in (
            ("Waiting for your approval.", "approval"), ("Ready for your review.", "review"),
            ("I need your input.", "input"), ("Awaiting your decision.", "decision"),
            ("Pending your confirmation.", "confirmation"), ("Requires human review.", "review"),
        ):
            with self.subTest(text=text):
                self.assertEqual(kind, server.explicit_user_request(text, self.workspace)["kind"])

    def test_handoff_rendering_does_not_mutate_run_or_task_files(self):
        paths = [self.run_dir / "meta.json", self.run_dir / "output.jsonl", self.tasks_path]
        before = [path.read_bytes() for path in paths]
        self.get_run()
        self.get_run()
        self.assertEqual(before, [path.read_bytes() for path in paths])


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
        self.assertEqual("0.2.0-experimental", payload["engineChatVersion"])
        self.assertEqual("1", payload["protocolVersion"])
        self.assertEqual("0.15.21", payload["testedDiscoveryVersion"])
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


class AgentSessionTests(unittest.TestCase):
    RUN_ID = "run-abc12345"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.workspace = root / "ws"
        self.state = root / "state"
        discovery = self.workspace / ".discovery"
        (discovery / "sessions").mkdir(parents=True)
        (discovery / "agent-runs").mkdir(parents=True)
        self.state.mkdir()
        (discovery / "sessions" / "{}.json".format(self.RUN_ID)).write_text(json.dumps({
            "header": {"sessionId": self.RUN_ID, "agentId": "catalog/batteries-included",
                       "taskId": "DX-16", "status": "running",
                       "startedAt": "2026-10-24T10:00:00+00:00"},
            "events": [],
        }), encoding="utf-8")
        env = mock.patch.dict(os.environ, {"ENGINE_CHAT_COPILOT_STATE": str(self.state)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.temp.cleanup)

    def write_copilot_session(self, name, created_at="2026-10-24T10:00:01Z", client="discovery-express", events=None):
        folder = self.state / name
        folder.mkdir()
        (folder / "workspace.yaml").write_text(
            "cwd: {}\nclient_name: {}\nname: Work on #DX-16\ncreated_at: {}\n".format(self.workspace, client, created_at),
            encoding="utf-8",
        )
        lines = events if events is not None else [
            {"type": "user.message", "data": {"content": "Claim {} and finish DX-16.".format(self.RUN_ID)}},
            {"type": "assistant.message", "timestamp": "2026-10-24T10:00:05Z",
             "data": {"reasoningText": "Plan the work.", "content": "Starting now."}},
            {"type": "tool.execution_start", "timestamp": "2026-10-24T10:00:06Z",
             "data": {"toolCallId": "c1", "toolName": "bash",
                      "arguments": {"command": "ls", "description": "List files"}}},
            {"type": "tool.execution_complete", "timestamp": "2026-10-24T10:00:07Z",
             "data": {"toolCallId": "c1", "success": True}},
        ]
        (folder / "events.jsonl").write_text("\n".join(json.dumps(e) for e in lines) + "\nnot json\n", encoding="utf-8")
        return folder

    def test_live_copilot_session_is_flattened_without_turns(self):
        self.write_copilot_session("s1")
        payload = server.EngineRunRepository(self.workspace).get_agent_session(self.RUN_ID)

        self.assertEqual("copilot-session", payload["source"])
        self.assertIn("finish DX-16", payload["startingPrompt"])
        self.assertNotIn("turns", payload["stats"])
        self.assertEqual(1, payload["stats"]["malformedLinesIgnored"])
        self.assertTrue(payload["agent"]["isLive"])
        text = json.dumps(payload["timeline"])
        self.assertIn("Plan the work.", text)
        self.assertIn("List files", text)

    def test_sessions_from_other_clients_or_times_are_ignored(self):
        self.write_copilot_session("other-client", client="vscode")
        self.write_copilot_session("too-late", created_at="2026-10-24T11:00:00Z")
        payload = server.EngineRunRepository(self.workspace).get_agent_session(self.RUN_ID)
        self.assertEqual("discovery-session", payload["source"])

    def test_session_naming_the_run_wins_over_closer_candidate(self):
        self.write_copilot_session("closer", created_at="2026-10-24T10:00:00Z", events=[
            {"type": "user.message", "data": {"content": "Different run."}},
        ])
        self.write_copilot_session("named")
        repository = server.EngineRunRepository(self.workspace)
        self.assertEqual(self.state / "named", repository._find_copilot_session(
            self.RUN_ID, "DX-16", "2026-10-24T10:00:00+00:00"))

    def test_invalid_or_unknown_ids_return_none(self):
        repository = server.EngineRunRepository(self.workspace)
        for run_id in ("../etc", "a/b/c/d/e", "short", "x" * 65, "run-missing1"):
            self.assertIsNone(repository.get_agent_session(run_id))

    def test_tool_titles(self):
        self.assertEqual("Using skill: docx", server.copilot_tool_title({"toolName": "skill"}, {"skill": "docx"}))
        self.assertEqual("view", server.copilot_tool_title({"toolName": "view"}, {"path": "/x"}))
        self.assertEqual("List files", server.copilot_tool_title({"toolName": "bash"}, {"description": "List files"}))

    def test_route_returns_agent_session_and_404(self):
        self.write_copilot_session("s1")
        httpd = server.create_server(self.workspace, 0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:{}".format(httpd.server_address[1])
            with urlopen(base + "/api/agents/" + self.RUN_ID, timeout=2) as response:
                self.assertEqual(self.RUN_ID, json.loads(response.read())["agent"]["runId"])
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + "/api/agents/run-missing1", timeout=2)
            self.assertEqual(404, caught.exception.code)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
