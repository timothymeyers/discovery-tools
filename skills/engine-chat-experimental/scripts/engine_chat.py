#!/usr/bin/env python3
"""Install, inspect, and launch the project-local Engine Chat runtime."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


SERVICE_ID = "engine-chat"
PACKAGE_VERSION = "0.1.0-experimental"
PROTOCOL_VERSION = "1"
TESTED_DISCOVERY_VERSION = "0.15.21"
TESTED_OS = "macOS"
PREFERRED_PORT = 8765
LAST_PORT = 8785
SKILL_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = SKILL_ROOT / "assets" / "manifest.json"
BUNDLED_RUNTIME = SKILL_ROOT / "assets" / "engine-chat"


class ManagerError(RuntimeError):
    """An expected manager failure with a stable machine-readable code."""

    def __init__(self, message: str, code: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


_LOOPBACK_OPENER = build_opener(_NoRedirect)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compatibility_payload() -> Dict[str, str]:
    return {
        "testedDiscoveryVersion": TESTED_DISCOVERY_VERSION,
        "testedOperatingSystem": TESTED_OS,
        "supportStatement": (
            "Tested with Discovery {} on {}; other versions and platforms are untested."
        ).format(TESTED_DISCOVERY_VERSION, TESTED_OS),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_fingerprint(workspace: Path) -> str:
    canonical = str(workspace.expanduser().resolve())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_workspace(value: str) -> Path:
    workspace = Path(value).expanduser().resolve()
    if not workspace.is_dir():
        raise ManagerError(
            "Workspace does not exist or is not a directory: {}".format(workspace),
            "invalid_workspace",
        )
    discovery = workspace / ".discovery"
    if not discovery.is_dir():
        raise ManagerError(
            "Workspace must already contain a .discovery directory: {}".format(workspace),
            "missing_discovery_directory",
        )
    return workspace


def manager_root(workspace: Path) -> Path:
    return workspace / ".discovery" / "engine-chat-experimental"


def runtime_path(workspace: Path) -> Path:
    return manager_root(workspace) / "runtime"


def install_metadata_path(workspace: Path) -> Path:
    return manager_root(workspace) / "install.json"


def run_state_path(workspace: Path) -> Path:
    return manager_root(workspace) / "run.json"


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{}.{}.tmp".format(path.name, uuid.uuid4().hex))
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def safe_payload_name(value: str) -> str:
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ManagerError("Unsafe payload path in manifest: {}".format(value), "invalid_manifest")
    return candidate.as_posix()


def load_manifest() -> Dict[str, Any]:
    manifest = read_json(MANIFEST_PATH)
    if manifest is None:
        raise ManagerError("Bundled manifest is missing or invalid.", "invalid_manifest")

    expected_fields = {
        "serviceId": SERVICE_ID,
        "packageVersion": PACKAGE_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
    }
    for field, expected in expected_fields.items():
        if manifest.get(field) != expected:
            raise ManagerError(
                "Bundled manifest has an unexpected {}.".format(field),
                "invalid_manifest",
                field=field,
            )

    compatibility = manifest.get("testedCompatibility")
    if not isinstance(compatibility, dict):
        raise ManagerError("Bundled compatibility metadata is missing.", "invalid_manifest")
    if (
        compatibility.get("discoveryVersion") != TESTED_DISCOVERY_VERSION
        or compatibility.get("operatingSystem") != TESTED_OS
    ):
        raise ManagerError("Bundled compatibility metadata is inconsistent.", "invalid_manifest")

    payload_hashes = manifest.get("payloadHashes")
    if not isinstance(payload_hashes, dict) or not payload_hashes:
        raise ManagerError("Bundled payload hashes are missing.", "invalid_manifest")

    normalized: Dict[str, str] = {}
    for raw_name, raw_hash in payload_hashes.items():
        name = safe_payload_name(str(raw_name))
        digest = str(raw_hash)
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ManagerError("Bundled payload hash is invalid for {}.".format(name), "invalid_manifest")
        source = (BUNDLED_RUNTIME / name).resolve()
        try:
            source.relative_to(BUNDLED_RUNTIME.resolve())
        except ValueError as error:
            raise ManagerError("Bundled payload escapes its asset root.", "invalid_manifest") from error
        if not source.is_file() or source.is_symlink():
            raise ManagerError("Bundled payload is missing: {}".format(name), "bundle_incomplete")
        actual = sha256_file(source)
        if actual != digest:
            raise ManagerError(
                "Bundled payload integrity check failed for {}.".format(name),
                "bundle_modified",
                payload=name,
                expected=digest,
                actual=actual,
            )
        normalized[name] = digest

    result = dict(manifest)
    result["payloadHashes"] = dict(sorted(normalized.items()))
    return result


def runtime_inventory(root: Path) -> Tuple[Dict[str, str], List[str]]:
    hashes: Dict[str, str] = {}
    issues: List[str] = []
    if not root.is_dir():
        return hashes, issues
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_symlink():
            issues.append("symbolic link: {}".format(relative))
        elif candidate.is_file():
            hashes[relative] = sha256_file(candidate)
        elif not candidate.is_dir():
            issues.append("unsupported entry: {}".format(relative))
    return hashes, issues


def _installation_result(
    workspace: Path,
    state: str,
    reason: str,
    metadata: Optional[Dict[str, Any]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "state": state,
        "reason": reason,
        "installRoot": str(manager_root(workspace)),
        "runtimePath": str(runtime_path(workspace)),
        "installedVersion": metadata.get("packageVersion") if metadata else None,
        "expectedVersion": PACKAGE_VERSION,
        "requiresRepair": state in {"modified", "incomplete"},
    }
    result.update(extra)
    return result


def inspect_installation(workspace: Path, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    manifest = manifest or load_manifest()
    runtime = runtime_path(workspace)
    metadata_path = install_metadata_path(workspace)
    runtime_exists = runtime.exists()
    metadata_exists = metadata_path.exists()

    if not runtime_exists and not metadata_exists:
        return _installation_result(workspace, "missing", "No project-local installation exists.")
    if not runtime.is_dir() or not metadata_path.is_file():
        missing = []
        if not runtime.is_dir():
            missing.append("runtime directory")
        if not metadata_path.is_file():
            missing.append("install metadata")
        return _installation_result(
            workspace,
            "incomplete",
            "Installation is missing {}.".format(" and ".join(missing)),
            missing=missing,
        )

    metadata = read_json(metadata_path)
    if metadata is None:
        return _installation_result(
            workspace,
            "incomplete",
            "Install metadata is malformed.",
            missing=["valid install metadata"],
        )
    if metadata.get("serviceId") != SERVICE_ID:
        return _installation_result(
            workspace,
            "modified",
            "Install metadata does not identify Engine Chat.",
            metadata,
        )

    recorded = metadata.get("payloadHashes")
    if not isinstance(recorded, dict) or not recorded:
        return _installation_result(
            workspace,
            "incomplete",
            "Install metadata has no payload hash inventory.",
            metadata,
            missing=["payload hash inventory"],
        )
    recorded_hashes = {str(name): str(digest) for name, digest in recorded.items()}
    actual_hashes, inventory_issues = runtime_inventory(runtime)
    if inventory_issues:
        return _installation_result(
            workspace,
            "modified",
            "Installed runtime contains unsupported entries.",
            metadata,
            inventoryIssues=inventory_issues,
        )

    missing_recorded = sorted(set(recorded_hashes) - set(actual_hashes))
    if missing_recorded:
        return _installation_result(
            workspace,
            "incomplete",
            "Installed runtime is missing recorded payload files.",
            metadata,
            missing=missing_recorded,
        )

    unexpected = sorted(set(actual_hashes) - set(recorded_hashes))
    changed = sorted(
        name
        for name in set(actual_hashes) & set(recorded_hashes)
        if actual_hashes[name] != recorded_hashes[name]
    )
    if unexpected or changed:
        return _installation_result(
            workspace,
            "modified",
            "Installed runtime differs from its recorded payload.",
            metadata,
            unexpected=unexpected,
            changed=changed,
        )

    expected_hashes = manifest["payloadHashes"]
    installed_version = str(metadata.get("packageVersion") or "")
    if installed_version == PACKAGE_VERSION:
        missing_expected = sorted(set(expected_hashes) - set(recorded_hashes))
        if missing_expected:
            return _installation_result(
                workspace,
                "incomplete",
                "Current-version metadata omits required payload files.",
                metadata,
                missing=missing_expected,
            )

    compatibility = metadata.get("testedCompatibility")
    current_compatibility = manifest.get("testedCompatibility")
    if (
        installed_version != PACKAGE_VERSION
        or recorded_hashes != expected_hashes
        or compatibility != current_compatibility
        or str(metadata.get("protocolVersion") or "") != PROTOCOL_VERSION
    ):
        return _installation_result(
            workspace,
            "outdated",
            "Installed payload is intact but does not match this package release.",
            metadata,
            safeToUpdate=True,
        )

    return _installation_result(
        workspace,
        "current",
        "Installed payload and metadata match this package.",
        metadata,
        verifiedPayloadHashes=dict(sorted(actual_hashes.items())),
    )


def _copy_bundled_runtime(stage: Path, manifest: Dict[str, Any]) -> None:
    stage.mkdir(parents=True, exist_ok=False)
    for name in manifest["payloadHashes"]:
        source = BUNDLED_RUNTIME / name
        destination = stage / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source), str(destination))
    actual, issues = runtime_inventory(stage)
    if issues or actual != manifest["payloadHashes"]:
        raise ManagerError(
            "Staged runtime failed payload verification.",
            "stage_verification_failed",
            issues=issues,
        )


def _backup_name() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "{}-{}".format(stamp, uuid.uuid4().hex[:8])


def install_runtime(workspace: Path, repair: bool = False) -> Dict[str, Any]:
    manifest = load_manifest()
    before = inspect_installation(workspace, manifest)
    if before["state"] == "current":
        return {
            "operation": "install",
            "ok": True,
            "changed": False,
            "stateBefore": "current",
            "stateAfter": "current",
            "installation": before,
            "compatibility": compatibility_payload(),
        }
    if before["state"] in {"modified", "incomplete"} and not repair:
        raise ManagerError(
            "Installation is {} and requires explicit repair consent.".format(before["state"]),
            "repair_required",
            installation=before,
            requiresRepair=True,
        )

    root = manager_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    runtime = runtime_path(workspace)
    metadata_path = install_metadata_path(workspace)
    stage = root / ".runtime-staging-{}".format(uuid.uuid4().hex)
    displaced: Optional[Path] = None
    backup: Optional[Path] = None
    old_runtime_was_present = runtime.is_dir()
    _copy_bundled_runtime(stage, manifest)

    try:
        if repair and (runtime.exists() or metadata_path.exists()):
            backup = root / "backups" / _backup_name()
            backup.mkdir(parents=True, exist_ok=False)
            if metadata_path.is_file():
                shutil.copyfile(str(metadata_path), str(backup / "install.json"))
            if runtime.exists():
                os.replace(str(runtime), str(backup / "runtime"))
            atomic_write_json(
                backup / "backup.json",
                {
                    "createdAt": utc_now(),
                    "reason": "Explicit repair of a {} installation.".format(before["state"]),
                    "stateBefore": before["state"],
                    "serviceId": SERVICE_ID,
                },
            )
        elif runtime.exists():
            displaced = root / ".runtime-previous-{}".format(uuid.uuid4().hex)
            os.replace(str(runtime), str(displaced))

        os.replace(str(stage), str(runtime))
        metadata = {
            "schemaVersion": 1,
            "serviceId": SERVICE_ID,
            "packageVersion": PACKAGE_VERSION,
            "protocolVersion": PROTOCOL_VERSION,
            "installedAt": utc_now(),
            "testedCompatibility": dict(manifest["testedCompatibility"]),
            "payloadHashes": dict(manifest["payloadHashes"]),
            "sourceManifestSha256": sha256_file(MANIFEST_PATH),
        }
        atomic_write_json(metadata_path, metadata)
    except Exception:
        if runtime.is_dir():
            shutil.rmtree(runtime, ignore_errors=True)
        if backup and (backup / "runtime").is_dir() and old_runtime_was_present:
            shutil.copytree(str(backup / "runtime"), str(runtime))
        elif displaced and displaced.is_dir():
            os.replace(str(displaced), str(runtime))
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)

    if displaced and displaced.exists():
        shutil.rmtree(displaced, ignore_errors=True)

    after = inspect_installation(workspace, manifest)
    if after["state"] != "current":
        raise ManagerError(
            "Installed runtime did not validate as current.",
            "post_install_verification_failed",
            installation=after,
        )

    result = {
        "operation": "install",
        "ok": True,
        "changed": True,
        "stateBefore": before["state"],
        "stateAfter": after["state"],
        "installation": after,
        "compatibility": compatibility_payload(),
    }
    if backup is not None:
        result["backupPath"] = str(backup)
    append_manager_log(workspace, "install", result)
    return result


def append_manager_log(workspace: Path, event: str, payload: Dict[str, Any]) -> None:
    log_path = manager_root(workspace) / "logs" / "manager.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": utc_now(),
        "event": event,
        "serviceId": SERVICE_ID,
        "packageVersion": PACKAGE_VERSION,
        "details": payload,
    }
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def probe_health(port: int, timeout: float = 0.15) -> Optional[Dict[str, Any]]:
    if not isinstance(port, int) or port < 1 or port > 65535:
        return None
    request = Request(
        "http://127.0.0.1:{}/api/health".format(port),
        headers={"Accept": "application/json", "Connection": "close"},
    )
    try:
        with _LOOPBACK_OPENER.open(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            content = response.read(64 * 1024)
        payload = json.loads(content.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return None


def health_matches(health: Optional[Dict[str, Any]], fingerprint: str) -> bool:
    if not isinstance(health, dict):
        return False
    pid = health.get("pid")
    return bool(
        health.get("ok") is True
        and health.get("serviceId") == SERVICE_ID
        and health.get("protocolVersion") == PROTOCOL_VERSION
        and health.get("engineChatVersion") == PACKAGE_VERSION
        and health.get("workspaceFingerprint") == fingerprint
        and isinstance(pid, int)
        and pid > 0
    )


def pid_is_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    except OSError as error:
        return error.errno == errno.EPERM


def runtime_record(workspace: Path, port: int, health: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schemaVersion": 1,
        "serviceId": SERVICE_ID,
        "protocolVersion": PROTOCOL_VERSION,
        "engineChatVersion": PACKAGE_VERSION,
        "workspaceFingerprint": workspace_fingerprint(workspace),
        "pid": health["pid"],
        "host": "127.0.0.1",
        "port": port,
        "url": "http://127.0.0.1:{}/".format(port),
        "recordedAt": utc_now(),
    }


def remove_run_state_if_owned(workspace: Path, pid: int) -> bool:
    state_path = run_state_path(workspace)
    state = read_json(state_path)
    if not state:
        return False
    if (
        state.get("serviceId") == SERVICE_ID
        and state.get("workspaceFingerprint") == workspace_fingerprint(workspace)
        and state.get("pid") == pid
    ):
        try:
            state_path.unlink()
            return True
        except FileNotFoundError:
            return False
    return False


def _running_result(workspace: Path, port: int, health: Dict[str, Any], source: str) -> Dict[str, Any]:
    return {
        "state": "running",
        "running": True,
        "source": source,
        "url": "http://127.0.0.1:{}/".format(port),
        "host": "127.0.0.1",
        "port": port,
        "pid": health["pid"],
        "workspaceFingerprint": workspace_fingerprint(workspace),
        "staleStateCleaned": False,
        "warnings": [],
        "_health": health,
    }


def inspect_running_state(
    workspace: Path,
    probe: Optional[Callable[[int, float], Optional[Dict[str, Any]]]] = None,
    pid_checker: Optional[Callable[[Any], bool]] = None,
    ports: Optional[Iterable[int]] = None,
) -> Dict[str, Any]:
    probe = probe or probe_health
    pid_checker = pid_checker or pid_is_alive
    port_list = list(ports if ports is not None else range(PREFERRED_PORT, LAST_PORT + 1))
    fingerprint = workspace_fingerprint(workspace)
    state_path = run_state_path(workspace)
    state_exists = state_path.exists()
    state = read_json(state_path) if state_exists else None
    stale_cleaned = False
    warnings: List[str] = []
    checked_ports: set[int] = set()

    if state_exists and state is None:
        try:
            state_path.unlink()
            stale_cleaned = True
        except FileNotFoundError:
            pass
    elif state is not None:
        raw_port = state.get("port")
        port = raw_port if isinstance(raw_port, int) and 1 <= raw_port <= 65535 else None
        if port is not None:
            checked_ports.add(port)
            health = probe(port, 0.2)
            if health_matches(health, fingerprint):
                assert health is not None
                record = runtime_record(workspace, port, health)
                atomic_write_json(state_path, record)
                result = _running_result(workspace, port, health, "run-state")
                result["staleStateCleaned"] = stale_cleaned
                return result

        owned_identity = (
            state.get("serviceId") == SERVICE_ID
            and state.get("workspaceFingerprint") == fingerprint
        )
        recorded_pid = state.get("pid")
        if owned_identity and not pid_checker(recorded_pid):
            try:
                state_path.unlink()
                stale_cleaned = True
            except FileNotFoundError:
                pass
        else:
            warnings.append(
                "Run state could not be matched to a healthy Engine Chat process and was preserved."
            )

    for port in port_list:
        if port in checked_ports:
            continue
        health = probe(port, 0.12)
        if health_matches(health, fingerprint):
            assert health is not None
            atomic_write_json(state_path, runtime_record(workspace, port, health))
            result = _running_result(workspace, port, health, "port-scan")
            result["staleStateCleaned"] = stale_cleaned
            result["warnings"] = warnings
            return result

    return {
        "state": "unverified" if warnings else "stopped",
        "running": False,
        "url": None,
        "host": "127.0.0.1",
        "port": None,
        "pid": None,
        "workspaceFingerprint": fingerprint,
        "staleStateCleaned": stale_cleaned,
        "warnings": warnings,
    }


def public_runtime_state(runtime: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in runtime.items() if not key.startswith("_")}


def status_report(workspace: Path) -> Dict[str, Any]:
    manifest = load_manifest()
    return {
        "operation": "status",
        "ok": True,
        "serviceId": SERVICE_ID,
        "engineChatVersion": PACKAGE_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "workspaceFingerprint": workspace_fingerprint(workspace),
        "installation": inspect_installation(workspace, manifest),
        "runtime": public_runtime_state(inspect_running_state(workspace)),
        "compatibility": compatibility_payload(),
    }


def choose_available_port(
    ports: Optional[Iterable[int]] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    candidates = list(ports if ports is not None else range(PREFERRED_PORT, LAST_PORT + 1))
    collisions: List[Dict[str, Any]] = []
    for port in candidates:
        test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            test_socket.bind(("127.0.0.1", port))
            return port, collisions
        except OSError as error:
            collisions.append(
                {
                    "port": port,
                    "reason": "occupied",
                    "error": error.strerror or str(error),
                }
            )
        finally:
            test_socket.close()
    raise ManagerError(
        "No free loopback port is available in the bounded Engine Chat range.",
        "no_available_port",
        collisions=collisions,
    )


def terminate_matching_instance(
    workspace: Path,
    running: Dict[str, Any],
    probe: Optional[Callable[[int, float], Optional[Dict[str, Any]]]] = None,
    pid_checker: Optional[Callable[[Any], bool]] = None,
) -> None:
    probe = probe or probe_health
    pid_checker = pid_checker or pid_is_alive
    port = running.get("port")
    pid = running.get("pid")
    fingerprint = workspace_fingerprint(workspace)
    if not isinstance(port, int) or not isinstance(pid, int):
        raise ManagerError("Matching runtime identity is incomplete.", "unsafe_restart")
    fresh = probe(port, 0.3)
    if not health_matches(fresh, fingerprint) or fresh.get("pid") != pid:
        raise ManagerError(
            "Restart was refused because the live process identity changed.",
            "unsafe_restart",
        )
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        remove_run_state_if_owned(workspace, pid)
        return

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not pid_checker(pid):
            remove_run_state_if_owned(workspace, pid)
            return
        time.sleep(0.05)
    raise ManagerError(
        "The verified Engine Chat process did not stop after SIGTERM; it was not force-killed.",
        "restart_timeout",
        pid=pid,
    )


def _wait_for_ready(
    workspace: Path,
    port: int,
    child: subprocess.Popen[Any],
    probe: Callable[[int, float], Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    fingerprint = workspace_fingerprint(workspace)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        return_code = child.poll()
        if return_code is not None:
            raise ManagerError(
                "Engine Chat exited before its health endpoint became ready.",
                "launch_failed",
                exitCode=return_code,
            )
        health = probe(port, 0.2)
        if health_matches(health, fingerprint) and health.get("pid") == child.pid:
            return health
        time.sleep(0.05)
    child.terminate()
    raise ManagerError("Engine Chat did not become ready in time.", "launch_timeout")


def launch_engine_chat(
    workspace: Path,
    restart: bool = False,
    on_ready: Optional[Callable[[Dict[str, Any]], None]] = None,
    running_inspector: Optional[Callable[[Path], Dict[str, Any]]] = None,
    popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    probe: Optional[Callable[[int, float], Optional[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    manifest = load_manifest()
    installation = inspect_installation(workspace, manifest)
    if installation["state"] != "current":
        raise ManagerError(
            "Launch requires a current installation; found {}.".format(installation["state"]),
            "installation_not_current",
            installation=installation,
        )

    inspect_running = running_inspector or inspect_running_state
    running = inspect_running(workspace)
    if running.get("running") and not restart:
        result = {
            "operation": "launch",
            "ok": True,
            "action": "reused",
            "reused": True,
            "url": running["url"],
            "host": "127.0.0.1",
            "port": running["port"],
            "pid": running["pid"],
            "workspaceFingerprint": workspace_fingerprint(workspace),
            "compatibility": compatibility_payload(),
        }
        if on_ready:
            on_ready(result)
        return result
    if running.get("running") and restart:
        terminate_matching_instance(workspace, running, probe=probe)

    port, collisions = choose_available_port()
    runtime = runtime_path(workspace)
    server_path = runtime / "server.py"
    if not server_path.is_file():
        raise ManagerError("Installed server payload is missing.", "installation_incomplete")

    command = [
        sys.executable,
        "-u",
        str(server_path),
        "--workspace",
        str(workspace),
        "--port",
        str(port),
        "--state-file",
        str(run_state_path(workspace)),
    ]
    child = popen_factory(command, cwd=str(runtime))
    effective_probe = probe or probe_health
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def forward_sigterm(signum: int, frame: Any) -> None:
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, forward_sigterm)
    try:
        health = _wait_for_ready(workspace, port, child, effective_probe)
        result = {
            "operation": "launch",
            "ok": True,
            "action": "launched",
            "reused": False,
            "url": "http://127.0.0.1:{}/".format(port),
            "host": "127.0.0.1",
            "port": port,
            "pid": health["pid"],
            "workspaceFingerprint": workspace_fingerprint(workspace),
            "portSelection": {
                "preferred": PREFERRED_PORT,
                "selected": port,
                "collisions": collisions,
            },
            "compatibility": compatibility_payload(),
        }
        append_manager_log(workspace, "launch", result)
        if on_ready:
            on_ready(result)
        try:
            exit_code = child.wait()
        except KeyboardInterrupt:
            if child.poll() is None:
                child.terminate()
            exit_code = child.wait()
        result["exitCode"] = exit_code
        return result
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        remove_run_state_if_owned(workspace, child.pid)
        append_manager_log(
            workspace,
            "runtime-exit",
            {"pid": child.pid, "exitCode": child.poll(), "port": port},
        )


def emit(payload: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
        return
    if payload.get("ok"):
        if payload.get("operation") == "status":
            print("Installation: {}".format(payload["installation"]["state"]))
            print("Runtime: {}".format(payload["runtime"]["state"]))
            if payload["runtime"].get("url"):
                print("URL: {}".format(payload["runtime"]["url"]))
        elif payload.get("operation") == "install":
            print("Engine Chat installation: {}".format(payload["stateAfter"]))
        else:
            print("Engine Chat URL: {}".format(payload.get("url")))
        print(compatibility_payload()["supportStatement"])
    else:
        print("Engine Chat error: {}".format(payload.get("message", "Unknown error")), file=sys.stderr)


def error_payload(error: ManagerError) -> Dict[str, Any]:
    result = {
        "ok": False,
        "error": error.code,
        "message": str(error),
        "compatibility": compatibility_payload(),
    }
    result.update(error.details)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Manage Engine Chat 0.1.0-experimental. Tested with Discovery 0.15.21 on macOS; "
            "other versions and platforms are untested."
        )
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)

    for operation in ("status", "install", "launch"):
        command = subparsers.add_parser(operation)
        command.add_argument("--workspace", required=True, help="Absolute Discovery workspace root")
        command.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
        if operation == "install":
            command.add_argument(
                "--repair",
                "--force",
                dest="repair",
                action="store_true",
                help="Explicitly repair a modified or incomplete install and preserve a backup",
            )
        if operation == "launch":
            command.add_argument(
                "--restart",
                action="store_true",
                help="Restart only a freshly verified matching Engine Chat process",
            )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        workspace = resolve_workspace(args.workspace)
        if args.operation == "status":
            emit(status_report(workspace), args.json)
        elif args.operation == "install":
            emit(install_runtime(workspace, repair=args.repair), args.json)
        else:
            emitted = {"value": False}

            def ready(payload: Dict[str, Any]) -> None:
                emit(payload, args.json)
                emitted["value"] = True

            launch_engine_chat(workspace, restart=args.restart, on_ready=ready)
        return 0
    except ManagerError as error:
        emit(error_payload(error), getattr(args, "json", False))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
