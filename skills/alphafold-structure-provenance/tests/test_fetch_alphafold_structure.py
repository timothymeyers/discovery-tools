#!/usr/bin/env python3
"""Offline, mocked tests for fetch_alphafold_structure.py.

No network access is used. `http_get` is monkeypatched to simulate the
AlphaFold DB API/file server using fixture JSON files, so these tests run
fully offline and fast. They exist to lock in the core provenance rule this
skill exists to teach: `latestVersion` (never `allVersions`) must drive the
downloaded filename, and malformed/missing `latestVersion` plus download
failures must be handled explicitly rather than silently mis-filed.

Run with: python -m unittest discover -s . -p "test_*.py" -v
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "fetch_alphafold_structure.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_module():
    spec = importlib.util.spec_from_file_location("fetch_alphafold_structure", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fas = _load_module()


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


class LatestVersionDrivesFilenameTest(unittest.TestCase):
    """Prove latestVersion, not allVersions, drives the downloaded filename,
    and that non-latest version numbers (still present in allVersions) 404
    while the latestVersion file returns 200 -- mirroring the v4/v5 (404) vs.
    v6 (200) behavior recorded in docs/dx12_structure_quality_methods.md.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmpdir.name)
        self.metadata = _load_fixture("metadata_v6_latest.json")
        self.calls = []

        def fake_http_get(url, timeout=30.0):
            self.calls.append(url)
            if "v6." in url or "_v6." in url:
                return 200, b"FAKE-STRUCTURE-BYTES-V6"
            if any(f"_v{n}." in url for n in (2, 3, 4, 5)):
                return 404, None
            return 500, None

        self._orig_http_get = fas.http_get
        fas.http_get = fake_http_get

    def tearDown(self):
        fas.http_get = self._orig_http_get
        self.tmpdir.cleanup()

    def test_only_latest_version_file_is_fetched_and_named(self):
        # Seed the metadata cache file directly so fetch_metadata() never
        # needs network access either.
        (self.cache_dir / "P00000_prediction.json").write_text(json.dumps(self.metadata))

        result = fas.run("P00000", self.cache_dir, ["model"], sleep_seconds=0)

        self.assertEqual(result["latest_version"], 6)
        self.assertEqual(result["all_versions"], [2, 3, 4, 5, 6])

        expected_path = self.cache_dir / "AF-P00000-F1-model_v6.cif"
        self.assertTrue(expected_path.exists())
        self.assertEqual(expected_path.read_bytes(), b"FAKE-STRUCTURE-BYTES-V6")
        self.assertEqual(result["artifacts"]["model"]["status"], "fetched")

        # Only the v6 (latestVersion) URL was ever requested for the file
        # download -- allVersions entries 2-5 must never be used to build a
        # download URL.
        file_calls = [c for c in self.calls if "/files/" in c]
        self.assertEqual(len(file_calls), 1)
        self.assertIn("_v6.", file_calls[0])
        for stale_version in (2, 3, 4, 5):
            self.assertNotIn(f"_v{stale_version}.", file_calls[0])

    def test_non_latest_versions_404_latest_returns_200(self):
        (self.cache_dir / "P00000_prediction.json").write_text(json.dumps(self.metadata))
        latest_version = fas.resolve_latest_version(self.metadata, "P00000")

        for stale_version in (4, 5):
            url = fas.FILE_URL.format(acc="P00000", artifact="model", version=stale_version, ext="cif")
            status, raw = fas.http_get(url)
            self.assertEqual(status, 404)
            self.assertIsNone(raw)

        url = fas.FILE_URL.format(acc="P00000", artifact="model", version=latest_version, ext="cif")
        status, raw = fas.http_get(url)
        self.assertEqual(status, 200)
        self.assertIsNotNone(raw)


class MalformedOrMissingLatestVersionTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_missing_latest_version_raises(self):
        metadata = _load_fixture("metadata_missing_latest_version.json")
        with self.assertRaises(SystemExit) as ctx:
            fas.resolve_latest_version(metadata, "P00001")
        self.assertIn("No latestVersion field", str(ctx.exception))

    def test_malformed_latest_version_raises(self):
        metadata = _load_fixture("metadata_malformed_latest_version.json")
        with self.assertRaises(SystemExit) as ctx:
            fas.resolve_latest_version(metadata, "P00002")
        self.assertIn("Malformed latestVersion", str(ctx.exception))

    def test_missing_latest_version_stops_run_before_any_download(self):
        (self.cache_dir / "P00001_prediction.json").write_text(
            json.dumps(_load_fixture("metadata_missing_latest_version.json"))
        )

        download_calls = []

        def fake_http_get(url, timeout=30.0):
            download_calls.append(url)
            return 200, b"SHOULD-NOT-BE-CALLED"

        orig = fas.http_get
        fas.http_get = fake_http_get
        try:
            with self.assertRaises(SystemExit):
                fas.run("P00001", self.cache_dir, ["model"], sleep_seconds=0)
        finally:
            fas.http_get = orig

        self.assertEqual(download_calls, [])


class DownloadErrorPathTest(unittest.TestCase):
    """Cover artifact-download failure paths: HTTP error status and a
    simulated network exception, neither of which should crash the run or
    silently mark a failed fetch as succeeded.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmpdir.name)
        self.metadata = _load_fixture("metadata_v6_latest.json")
        (self.cache_dir / "P00000_prediction.json").write_text(json.dumps(self.metadata))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_http_error_status_is_reported_as_failed_not_fetched(self):
        def fake_http_get(url, timeout=30.0):
            return 404, None

        orig = fas.http_get
        fas.http_get = fake_http_get
        try:
            result = fas.run("P00000", self.cache_dir, ["model"], sleep_seconds=0)
        finally:
            fas.http_get = orig

        self.assertEqual(result["artifacts"]["model"]["status"], "failed")
        self.assertEqual(result["artifacts"]["model"]["http_status"], 404)
        expected_path = self.cache_dir / "AF-P00000-F1-model_v6.cif"
        self.assertFalse(expected_path.exists())

    def test_network_exception_during_download_propagates_not_swallowed(self):
        def raising_http_get(url, timeout=30.0):
            raise ConnectionError("simulated network failure")

        orig = fas.http_get
        fas.http_get = raising_http_get
        try:
            with self.assertRaises(ConnectionError):
                fas.run("P00000", self.cache_dir, ["model"], sleep_seconds=0)
        finally:
            fas.http_get = orig

        expected_path = self.cache_dir / "AF-P00000-F1-model_v6.cif"
        self.assertFalse(expected_path.exists())


if __name__ == "__main__":
    unittest.main()
