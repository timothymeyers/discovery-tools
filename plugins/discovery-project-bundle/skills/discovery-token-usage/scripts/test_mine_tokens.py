"""Tests for TokenJournal discovery in mine_tokens.py.

The journal lives under a per-app-channel support directory, so a Discovery
App Preview install writes somewhere a stable install never does. These tests
pin the resolution order (explicit > env var > newest existing channel) and
the missing/empty distinction: a journal that is absent must never be reported
as zero interactive usage.
"""
import json
import os
import tempfile
import unittest

import mine_tokens


def _journal_path(home, channel):
    return os.path.join(home, "Library", "Application Support", channel,
                        "telemetry", "token-usage.jsonl")


def _write_journal(home, channel, records, mtime=None):
    path = _journal_path(home, channel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


RECORD = {"source": "vscode-otel", "model": "claude-sonnet-5",
          "realInputTokens": 120, "realOutputTokens": 30}


class JournalResolutionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self._saved = {k: os.environ.get(k)
                       for k in ("HOME", mine_tokens.JOURNAL_ENV_VAR)}
        os.environ["HOME"] = self.home
        os.environ.pop(mine_tokens.JOURNAL_ENV_VAR, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_stable_journal_only(self):
        expected = _write_journal(self.home, "DiscoveryApp", [RECORD])
        path, candidates = mine_tokens.resolve_journal()
        self.assertEqual(path, expected)
        self.assertEqual(len(candidates), 2)

    def test_preview_journal_only(self):
        expected = _write_journal(self.home, "DiscoveryAppPreview", [RECORD])
        path, _ = mine_tokens.resolve_journal()
        self.assertEqual(path, expected)

    def test_both_journals_select_newest_deterministically(self):
        _write_journal(self.home, "DiscoveryApp", [RECORD], mtime=1_000_000)
        preview = _write_journal(self.home, "DiscoveryAppPreview", [RECORD],
                                 mtime=2_000_000)
        self.assertEqual(mine_tokens.resolve_journal()[0], preview)

        stable = _write_journal(self.home, "DiscoveryApp", [RECORD],
                                mtime=3_000_000)
        self.assertEqual(mine_tokens.resolve_journal()[0], stable)

    def test_explicit_override_wins_over_both_channels(self):
        _write_journal(self.home, "DiscoveryApp", [RECORD])
        _write_journal(self.home, "DiscoveryAppPreview", [RECORD])
        explicit = os.path.join(self.home, "elsewhere.jsonl")
        with open(explicit, "w") as fh:
            fh.write(json.dumps(RECORD) + "\n")
        path, candidates = mine_tokens.resolve_journal(explicit)
        self.assertEqual(path, explicit)
        self.assertEqual(candidates, [explicit])

    def test_env_var_override(self):
        _write_journal(self.home, "DiscoveryApp", [RECORD])
        explicit = os.path.join(self.home, "from-env.jsonl")
        with open(explicit, "w") as fh:
            fh.write(json.dumps(RECORD) + "\n")
        os.environ[mine_tokens.JOURNAL_ENV_VAR] = explicit
        self.assertEqual(mine_tokens.resolve_journal()[0], explicit)

    def test_explicit_argument_beats_env_var(self):
        env_path = os.path.join(self.home, "from-env.jsonl")
        arg_path = os.path.join(self.home, "from-arg.jsonl")
        os.environ[mine_tokens.JOURNAL_ENV_VAR] = env_path
        self.assertEqual(mine_tokens.resolve_journal(arg_path)[0], arg_path)

    def test_no_journal_anywhere_reports_candidates(self):
        path, candidates = mine_tokens.resolve_journal()
        self.assertIsNone(path)
        self.assertEqual(candidates, [_journal_path(self.home, "DiscoveryApp"),
                                      _journal_path(self.home,
                                                    "DiscoveryAppPreview")])


class JournalStateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_journal_is_not_reported_as_empty(self):
        rows, _bad, _rec, state = mine_tokens.mine_journal(
            os.path.join(self.tmp, "absent.jsonl"))
        self.assertEqual(rows, [])
        self.assertEqual(state, "missing")

    def test_none_path_is_missing(self):
        self.assertEqual(mine_tokens.mine_journal(None)[3], "missing")

    def test_present_but_empty_journal(self):
        path = os.path.join(self.tmp, "empty.jsonl")
        with open(path, "w") as fh:
            fh.write("\n")
        rows, _bad, _rec, state = mine_tokens.mine_journal(path)
        self.assertEqual(rows, [])
        self.assertEqual(state, "empty")

    def test_all_lines_corrupt_is_not_reported_as_empty(self):
        path = os.path.join(self.tmp, "corrupt.jsonl")
        with open(path, "w") as fh:
            fh.write("{not json at all\n")
        rows, bad, _rec, state = mine_tokens.mine_journal(path)
        self.assertEqual(rows, [])
        self.assertEqual(bad, 1)
        self.assertEqual(state, "corrupt")

    def test_populated_journal(self):
        path = os.path.join(self.tmp, "full.jsonl")
        with open(path, "w") as fh:
            fh.write(json.dumps(RECORD) + "\n")
        rows, _bad, _rec, state = mine_tokens.mine_journal(path)
        self.assertEqual(state, "ok")
        self.assertEqual(len(rows), 1)
        self.assertEqual(mine_tokens.j_in(rows[0]), 120)


if __name__ == "__main__":
    unittest.main()
