import importlib.util
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path


MANAGER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "engine_chat.py"
MANAGER_SPEC = importlib.util.spec_from_file_location("engine_chat_manager", MANAGER_PATH)
if MANAGER_SPEC is None or MANAGER_SPEC.loader is None:
    raise RuntimeError("Unable to load Engine Chat manager")
manager = importlib.util.module_from_spec(MANAGER_SPEC)
MANAGER_SPEC.loader.exec_module(manager)


class EngineChatManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name).resolve()
        (self.workspace / ".discovery").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def install(self):
        return manager.install_runtime(self.workspace)

    def metadata(self):
        return manager.read_json(manager.install_metadata_path(self.workspace))

    def write_metadata(self, payload):
        manager.atomic_write_json(manager.install_metadata_path(self.workspace), payload)

    def test_missing_installation_is_reported(self):
        state = manager.inspect_installation(self.workspace)

        self.assertEqual("missing", state["state"])
        self.assertFalse(state["requiresRepair"])

    def test_install_is_current_and_idempotent(self):
        first = self.install()
        metadata_before = self.metadata()
        second = self.install()
        metadata_after = self.metadata()

        self.assertTrue(first["changed"])
        self.assertEqual("missing", first["stateBefore"])
        self.assertEqual("current", first["stateAfter"])
        self.assertFalse(second["changed"])
        self.assertEqual("current", second["stateAfter"])
        self.assertEqual(metadata_before, metadata_after)
        self.assertEqual(manager.PACKAGE_VERSION, metadata_after["packageVersion"])
        self.assertEqual("0.15.21", metadata_after["testedCompatibility"]["discoveryVersion"])
        self.assertEqual("macOS", metadata_after["testedCompatibility"]["operatingSystem"])
        self.assertEqual(
            manager.load_manifest()["payloadHashes"],
            metadata_after["payloadHashes"],
        )

    def test_modified_install_requires_explicit_repair(self):
        self.install()
        index_path = manager.runtime_path(self.workspace) / "index.html"
        index_path.write_text(index_path.read_text(encoding="utf-8") + "\nmodified\n", encoding="utf-8")

        state = manager.inspect_installation(self.workspace)
        self.assertEqual("modified", state["state"])
        with self.assertRaises(manager.ManagerError) as context:
            manager.install_runtime(self.workspace)
        self.assertEqual("repair_required", context.exception.code)

    def test_incomplete_install_requires_explicit_repair(self):
        self.install()
        (manager.runtime_path(self.workspace) / "server.py").unlink()

        state = manager.inspect_installation(self.workspace)

        self.assertEqual("incomplete", state["state"])
        self.assertIn("server.py", state["missing"])
        with self.assertRaises(manager.ManagerError) as context:
            manager.install_runtime(self.workspace)
        self.assertEqual("repair_required", context.exception.code)

    def test_intact_outdated_install_updates_without_force(self):
        self.install()
        metadata = self.metadata()
        metadata["packageVersion"] = "0.0.9-experimental"
        self.write_metadata(metadata)

        before = manager.inspect_installation(self.workspace)
        result = manager.install_runtime(self.workspace)

        self.assertEqual("outdated", before["state"])
        self.assertTrue(before["safeToUpdate"])
        self.assertEqual("outdated", result["stateBefore"])
        self.assertEqual("current", result["stateAfter"])
        self.assertNotIn("backupPath", result)

    def test_repair_preserves_modified_runtime_and_metadata_backup(self):
        self.install()
        runtime = manager.runtime_path(self.workspace)
        index_path = runtime / "index.html"
        marker = "\n<!-- locally modified -->\n"
        index_path.write_text(index_path.read_text(encoding="utf-8") + marker, encoding="utf-8")
        old_metadata = self.metadata()

        result = manager.install_runtime(self.workspace, repair=True)
        backup = Path(result["backupPath"])

        self.assertEqual("modified", result["stateBefore"])
        self.assertEqual("current", result["stateAfter"])
        self.assertTrue((backup / "runtime" / "index.html").is_file())
        self.assertIn(marker.strip(), (backup / "runtime" / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(old_metadata, manager.read_json(backup / "install.json"))
        self.assertEqual("current", manager.inspect_installation(self.workspace)["state"])

    def test_stale_owned_run_state_is_cleaned_and_treated_as_stopped(self):
        state_path = manager.run_state_path(self.workspace)
        manager.atomic_write_json(
            state_path,
            {
                "serviceId": manager.SERVICE_ID,
                "protocolVersion": manager.PROTOCOL_VERSION,
                "engineChatVersion": manager.PACKAGE_VERSION,
                "workspaceFingerprint": manager.workspace_fingerprint(self.workspace),
                "pid": 99999999,
                "host": "127.0.0.1",
                "port": manager.PREFERRED_PORT,
            },
        )

        result = manager.inspect_running_state(
            self.workspace,
            probe=lambda port, timeout: None,
            pid_checker=lambda pid: False,
            ports=[],
        )

        self.assertEqual("stopped", result["state"])
        self.assertFalse(result["running"])
        self.assertTrue(result["staleStateCleaned"])
        self.assertFalse(state_path.exists())

    def test_workspace_fingerprint_is_stable_and_path_specific(self):
        same = Path(str(self.workspace) + "/.")
        other = self.workspace.parent / (self.workspace.name + "-other")

        self.assertEqual(
            manager.workspace_fingerprint(self.workspace),
            manager.workspace_fingerprint(same),
        )
        self.assertNotEqual(
            manager.workspace_fingerprint(self.workspace),
            manager.workspace_fingerprint(other),
        )
        self.assertEqual(64, len(manager.workspace_fingerprint(self.workspace)))

    def test_occupied_preferred_port_walks_forward_and_reports_collision(self):
        occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        occupied_port = occupied.getsockname()[1]
        free_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        free_probe.bind(("127.0.0.1", 0))
        free_port = free_probe.getsockname()[1]
        free_probe.close()
        try:
            selected, collisions = manager.choose_available_port([occupied_port, free_port])
        finally:
            occupied.close()

        self.assertEqual(free_port, selected)
        self.assertEqual(occupied_port, collisions[0]["port"])
        self.assertEqual("occupied", collisions[0]["reason"])

    def test_matching_service_is_reused_without_starting_another_process(self):
        self.install()
        fingerprint = manager.workspace_fingerprint(self.workspace)
        running = {
            "state": "running",
            "running": True,
            "url": "http://127.0.0.1:8765/",
            "host": "127.0.0.1",
            "port": 8765,
            "pid": os.getpid(),
            "workspaceFingerprint": fingerprint,
            "_health": {
                "ok": True,
                "serviceId": manager.SERVICE_ID,
                "protocolVersion": manager.PROTOCOL_VERSION,
                "engineChatVersion": manager.PACKAGE_VERSION,
                "workspaceFingerprint": fingerprint,
                "pid": os.getpid(),
            },
        }
        ready = []

        def fail_if_started(*args, **kwargs):
            self.fail("A matching service must be reused, not duplicated")

        result = manager.launch_engine_chat(
            self.workspace,
            running_inspector=lambda workspace: running,
            popen_factory=fail_if_started,
            on_ready=ready.append,
        )

        self.assertTrue(result["reused"])
        self.assertEqual("reused", result["action"])
        self.assertEqual("http://127.0.0.1:8765/", result["url"])
        self.assertEqual([result], ready)

    def test_matching_health_found_by_bounded_scan_reconstructs_run_state(self):
        fingerprint = manager.workspace_fingerprint(self.workspace)
        health = {
            "ok": True,
            "serviceId": manager.SERVICE_ID,
            "protocolVersion": manager.PROTOCOL_VERSION,
            "engineChatVersion": manager.PACKAGE_VERSION,
            "workspaceFingerprint": fingerprint,
            "pid": 4242,
        }

        result = manager.inspect_running_state(
            self.workspace,
            probe=lambda port, timeout: health if port == 8767 else None,
            pid_checker=lambda pid: True,
            ports=[8765, 8766, 8767],
        )
        recorded = manager.read_json(manager.run_state_path(self.workspace))

        self.assertTrue(result["running"])
        self.assertEqual("port-scan", result["source"])
        self.assertEqual(8767, result["port"])
        self.assertEqual(4242, recorded["pid"])
        self.assertEqual(fingerprint, recorded["workspaceFingerprint"])


if __name__ == "__main__":
    unittest.main()
