import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gather  # noqa: E402
import offload  # noqa: E402

RUN = "aaaabbbbccccddddeeeeffff00001111"
# Fake credential assembled at runtime so secret scanners don't flag the fixture.
FAKE_TOKEN = "x" * 26
OTHER = "99998888777766665555444433332222"


def build(root: Path) -> dict:
    repo, home, app = root / "proj", root / "copilot", root / "app"
    (repo / ".discovery/agent-runs").mkdir(parents=True)
    (repo / ".discovery/sessions").mkdir(parents=True)
    (repo / ".discovery/agent-runs" / f"{RUN}.json").write_text(json.dumps({
        "runId": RUN, "agentId": "catalog/x", "taskId": "DX-1",
        "startedAt": "2026-11-12T20:00:00+00:00", "completedAt": "2026-11-12T20:10:00+00:00"}))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)

    def session(sid, cwd, prompt, extra=""):
        d = home / "session-state" / sid
        (d / "files").mkdir(parents=True)
        (d / "workspace.yaml").write_text(f"id: {sid}\ncwd: {cwd}\n")
        (d / "events.jsonl").write_text("\n".join([
            json.dumps({"type": "session.start", "timestamp": "2026-11-12T20:00:05Z"}),
            json.dumps({"type": "user.message", "timestamp": "2026-11-12T20:00:06Z", "data": {"content": prompt}}),
            json.dumps({"type": "assistant.message", "timestamp": "2026-11-12T20:05:00Z", "data": {"content": extra}}),
        ]) + "\n")
        (d / "files/out.txt").write_text("contact jane.doe@example.com or 509-375-2400; size 594 595 1098\n")
    session("s-run", repo, "# Task Instructions You have been assigned task **#DX-1**", f"run {RUN} token ghp_{'A' * 30}")
    session("s-wiq", repo / "data", "Ask work-iq to answer: who said what?", "Jane Doe said hi")
    session("s-else", root / "elsewhere", "unrelated")

    ws = app / "user-data/User/workspaceStorage/h1"
    (ws / "chatSessions").mkdir(parents=True)
    (ws / "workspace.json").write_text(json.dumps({"folder": repo.as_uri()}))
    (ws / "chatSessions/c1.jsonl").write_text(json.dumps({"t": f"about {RUN}", "Authorization": "Bearer " + FAKE_TOKEN}) + "\n")
    ws2 = app / "user-data/User/workspaceStorage/h2/chatSessions"
    ws2.mkdir(parents=True)
    (ws2.parent / "workspace.json").write_text(json.dumps({"folder": (root / "elsewhere").as_uri()}))
    (ws2 / "x.jsonl").write_text("other project\n")

    (home / "logs").mkdir(parents=True)
    (home / "logs/process-1.log").write_text(f"start {RUN}\n")
    (home / "logs/process-2.log").write_text(f"start {OTHER}\n")
    tele = app / "telemetry/logs"
    tele.mkdir(parents=True)
    (tele / "discovery-express.jsonl.1").write_text(f'{{"runId":"{RUN}","m":"a"}}\n{{"runId":"{OTHER}"}}\n')
    (tele / "discovery-express.jsonl").write_text(f'{{"runId":"{RUN}","m":"a"}}\n{{"runId":"{RUN}","m":"b"}}\n')
    (app / "telemetry/token-usage.jsonl").write_text("\n".join([
        json.dumps({"timestamp": "2026-11-12T20:03:00Z", "tokens": 5}),
        json.dumps({"timestamp": "2026-11-01T00:00:00Z", "tokens": 9}),
        json.dumps({"_sentinel": True, "timestamp": "2026-11-12T20:03:00Z"})]) + "\n")
    (app / "user-data/logs/20261112").mkdir(parents=True)
    (app / "user-data/logs/20261112/terminal.log").write_text(f"cd {repo}\ncd /tmp\n")
    return {"repo": repo, "home": home, "app": app}


class GatherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.p = build(root)
        os.environ["GATHER_EXHAUST_SALT_FILE"] = str(root / "salt")
        self.out = self.p["repo"] / ".discovery/exhaust"

    def tearDown(self):
        self.tmp.cleanup()

    def run_g(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gather.main(["--workspace", str(self.p["repo"]), "--copilot-home", str(self.p["home"]),
                              "--app-support", str(self.p["app"]), *args])
        return rc, buf.getvalue()

    def test_dry_run_writes_nothing(self):
        rc, out = self.run_g()
        self.assertEqual(rc, 0)
        self.assertFalse(self.out.exists())
        self.assertIn("DRY RUN", out)

    def test_apply_filters_scrubs_links_and_commits(self):
        rc, out = self.run_g("--apply", "--name", "Jane Doe")
        self.assertEqual(rc, 0, out)
        o = self.out
        self.assertTrue((o / "agent-sessions/s-run/events.jsonl").exists())
        self.assertTrue((o / "agent-sessions/s-wiq/events.jsonl").exists())   # cwd in a subfolder
        self.assertFalse((o / "agent-sessions/s-else").exists())
        self.assertTrue((o / "chat/sessions/c1.jsonl").exists())
        self.assertFalse((o / "chat/sessions/x.jsonl").exists())
        self.assertTrue((o / "logs/copilot-process/process-1.log").exists())
        self.assertFalse((o / "logs/copilot-process/process-2.log").exists())
        de = (o / "telemetry/discovery-express.jsonl").read_text().splitlines()
        self.assertEqual(len(de), 2)
        self.assertNotIn(OTHER, "".join(de))
        tu = (o / "telemetry/token-usage.jsonl").read_text().splitlines()
        self.assertEqual(len(tu), 1)
        self.assertIn("time-window", tu[0])
        self.assertEqual((o / "logs/app-terminal/20261112.log").read_text().strip(), f"cd {self.p['repo']}")
        txt = (o / "agent-sessions/s-run/files/out.txt").read_text()
        self.assertNotIn("jane.doe@example.com", txt)
        self.assertNotIn("509-375-2400", txt)
        self.assertIn("594 595 1098", txt)              # not a phone number
        self.assertNotIn("ghp_", (o / "agent-sessions/s-run/events.jsonl").read_text())
        self.assertNotIn(FAKE_TOKEN, (o / "chat/sessions/c1.jsonl").read_text())
        wiq = (o / "agent-sessions/s-wiq/events.jsonl").read_text()
        self.assertNotIn("Jane Doe", wiq)
        self.assertIn("<person-", wiq)
        links = json.loads((o / "links.json").read_text())
        self.assertEqual(links["runs"][RUN]["session_state"][0]["id"], "s-run")
        self.assertEqual(links["session_state"]["s-wiq"]["kind"], "workiq")
        self.assertTrue((o / "README.md").exists())
        log = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=self.p["repo"], capture_output=True, text=True).stdout
        self.assertIn("exhaust: gather", log)
        self.assertIn("privacy=scrub", log)

    def test_second_run_copies_nothing_and_no_commit(self):
        self.run_g("--apply")
        before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.p["repo"], capture_output=True, text=True).stdout
        rc, out = self.run_g("--apply")
        self.assertIn("nothing changed", out)
        after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.p["repo"], capture_output=True, text=True).stdout
        self.assertEqual(before, after)

    def test_logs_append_and_never_delete(self):
        self.run_g("--apply")
        tele = self.p["app"] / "telemetry/logs/discovery-express.jsonl"
        tele.write_text(f'{{"runId":"{RUN}","m":"c"}}\n')       # rotated: old lines gone from source
        (self.p["app"] / "telemetry/logs/discovery-express.jsonl.1").unlink()
        self.run_g("--apply")
        lines = (self.out / "telemetry/discovery-express.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 3)

    def test_exclude_sensitive_and_no_downgrade(self):
        self.run_g("--apply", "--privacy", "exclude-sensitive")
        self.assertFalse((self.out / "agent-sessions/s-wiq").exists())
        self.run_g("--apply", "--privacy", "scrub")
        self.assertTrue((self.out / "agent-sessions/s-wiq").exists())
        f = self.out / "agent-sessions/s-run/files/out.txt"
        rc, out = self.run_g("--apply", "--privacy", "raw")
        self.assertNotIn("jane.doe@example.com", f.read_text())   # kept stricter
        self.run_g("--apply", "--privacy", "raw", "--allow-downgrade")
        self.assertIn("jane.doe@example.com", f.read_text())

    def test_exclude_sensitive_redacts_process_log_lines(self):
        (self.p["home"] / "logs/process-1.log").write_text(
            f'start {RUN}\n  "text": "Ask work-iq to answer: secret"\n  "content": "M365 reply"\nINFO plugin workiq loaded\nok\n')
        self.run_g("--apply", "--privacy", "exclude-sensitive")
        t = (self.out / "logs/copilot-process/process-1.log").read_text()
        self.assertNotIn("secret", t)
        self.assertIn("M365 reply", t)          # ordinary conversation kept
        self.assertNotIn("workiq", t)
        self.assertIn(RUN, t)
        self.assertIn("ok", t)

    def test_scrub_placeholders_are_stable(self):
        s = gather.Scrubber("scrub", ["Jane Doe"], b"salt")
        self.assertEqual(s.text("Jane Doe"), s.text("jane doe"))
        self.assertEqual(s.text("a@b.com"), gather.Scrubber("scrub", [], b"salt").text("a@b.com"))

    def test_no_commit_stages_only(self):
        rc, out = self.run_g("--apply", "--no-commit")
        self.assertIn("staged", out)
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.p["repo"], capture_output=True, text=True).stdout
        self.assertIn(".discovery/exhaust/manifest.json", staged)

    def test_large_file_needs_decision(self):
        old = offload.WARN_BYTES
        offload.WARN_BYTES = 10
        try:
            rc, out = self.run_g()
            self.assertEqual(rc, 3)
            self.assertIn("LARGE", out)
            rc, out = self.run_g("--apply", "--large-file-policy", "archive")
            self.assertEqual(rc, 0, out)
            self.assertTrue((self.out / "chat/sessions/c1.jsonl.gz").exists())
            cfg = json.loads((self.out / "config.json").read_text())
            self.assertEqual(cfg["large_file_default"], "archive")
        finally:
            offload.WARN_BYTES = old


class OffloadTests(unittest.TestCase):
    def test_git_refused_over_hard_limit(self):
        with tempfile.TemporaryDirectory() as t:
            old = offload.HARD_BYTES
            offload.HARD_BYTES = 5
            try:
                with self.assertRaises(offload.OffloadError):
                    offload.apply_choice("git", repo=Path(t), dest=Path(t) / "f", rel_repo="f", data=b"0123456789")
            finally:
                offload.HARD_BYTES = old

    def test_blob_requires_config(self):
        with tempfile.TemporaryDirectory() as t, self.assertRaises(offload.OffloadError):
            offload.apply_choice("blob", repo=Path(t), dest=Path(t) / "f", rel_repo="f", data=b"x", blob_cfg={})

    def test_skip(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(offload.apply_choice("skip", repo=Path(t), dest=Path(t) / "f", rel_repo="f", data=b"x"),
                             ("skipped-large", None))


if __name__ == "__main__":
    unittest.main()
