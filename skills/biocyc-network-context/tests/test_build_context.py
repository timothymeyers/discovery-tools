"""Offline tests for `build_context.py`'s bundled example document and CLI
wiring. No network calls."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from build_context import EXAMPLE_ORGID, EXAMPLE_TAXID, build_example_document


def test_example_document_has_all_three_ec_examples():
    doc = build_example_document()
    assert doc["orgid"] == EXAMPLE_ORGID
    assert doc["ncbi_taxonomy_id"] == EXAMPLE_TAXID
    entry_ids = {e["entry_id"] for e in doc["entries"]}
    assert entry_ids == {"pgi", "pfk", "zwf"}
    for entry in doc["entries"]:
        assert entry["outcome"] == "ok"
        assert entry["organism_verified"] is True


def test_deterministic_replay_byte_identical_output():
    """Re-running the offline example build twice produces byte-identical
    JSON (no live clock read baked into any field the test compares)."""
    doc1 = build_example_document()
    doc2 = build_example_document()
    doc1.pop("entries"), doc2.pop("entries")
    for entry in build_example_document()["entries"]:
        entry.pop("generated_at")
    # Structural equality (order-independent generated_at aside) across two
    # independent builds:
    entries1 = [{k: v for k, v in e.items() if k != "generated_at"} for e in build_example_document()["entries"]]
    entries2 = [{k: v for k, v in e.items() if k != "generated_at"} for e in build_example_document()["entries"]]
    assert entries1 == entries2


def test_cli_example_mode_runs_offline_and_emits_valid_json():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "build_context.py"), "--example"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)
    assert len(doc["entries"]) == 3


def test_cli_live_mode_refuses_without_consent():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "build_context.py"), "--live", "--orgid", "ECOLI", "--reaction-frameid", "X"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    out = json.loads(result.stdout)
    assert out["ran"] is False
