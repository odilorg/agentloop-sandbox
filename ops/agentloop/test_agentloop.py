#!/usr/bin/env python3
"""Offline tests for agentloop: reviewer authentication, comment schema, usage guard,
diff validation and the run loop's decisions. No network, no Claude, no GitHub."""
import json, os, re, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentloop as al

SHA = "a" * 40
APP = {"id": 1144995, "slug": "chatgpt-codex-connector", "owner": {"login": "openai"}}
OWNER = {"login": "odilorg", "id": 20632218, "type": "User"}


def review(verdict="FIX", sha=SHA, request="Add a line to docs/pilot/sandbox.md", test=False):
    return "\n".join(["AGENT-REVIEW v1", f"Commit: {sha}", f"Verdict: {verdict}"] + (["Test: simulated"] if test else [])
                     + ["Request:", request])


def comment(body, user=OWNER, app=APP, cid=100, created="2026-10-19T01:00:00Z", updated=None):
    return {"id": cid, "body": body, "user": dict(user), "performed_via_github_app": app,
            "created_at": created, "updated_at": updated or created}


def config(tmp, **kw):
    c = {"repo": al.SANDBOX, "app_slug": "odilorg-agentloop", "test_mode": False, "dry_run": False,
         "max_files": 20, "max_changes": 200, "max_rounds": 3, "max_runs_per_day": 10,
         "allowed_re": re.compile(r"docs/pilot/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\.md"),
         "state_dir": Path(tmp), "log_dir": Path(tmp), "inbox": Path(tmp) / "inbox", "workdir": Path(tmp) / "work"}
    c.update(kw)
    return c


class Schema(unittest.TestCase):
    def test_valid(self):
        r = al.parse_review(review())
        self.assertEqual((r["Commit"], r["Verdict"], r["simulated"]), (SHA, "FIX", False))

    def test_escalate_and_pass(self):
        self.assertEqual(al.parse_review(review("ESCALATE", request="touches README"))["Verdict"], "ESCALATE")
        self.assertEqual(al.parse_review(review("PASS", request=""))["Verdict"], "PASS")

    def test_rejects(self):
        bad = [
            "x" + review(),                                           # text before the header
            review().replace("AGENT-REVIEW v1", "AGENT-REVIEW v2"),
            review(sha="A" * 40), review(sha="a" * 39),
            review(verdict="APPROVE"),
            review(verdict="FIX", request=""),                        # FIX needs a request
            review(verdict="ESCALATE", request=""),
            review().replace("Verdict: FIX\n", "Verdict: FIX\nNote: extra\n"),   # unknown line before Request
            review().replace("Commit:", "Commit:  "),
            review(test=True),                                        # "Test: simulated" outside test mode
            review(request="x" * 4001),
            review() + "\n" + "y" * 6000,
            "AGENT-REVIEW v1\nCommit: " + SHA,
            "", None,
        ]
        for b in bad:
            self.assertIsNone(al.parse_review(b), repr(b)[:80])

    def test_simulated_only_in_test_mode(self):
        self.assertTrue(al.parse_review(review(test=True), test_mode=True)["simulated"])


class Reviewer(unittest.TestCase):
    def setUp(self):
        self.c = config(tempfile.mkdtemp())

    def trusted(self, cm, test_mode=False):
        c = {**self.c, "test_mode": test_mode}
        rv = al.parse_review(cm["body"], test_mode)
        return bool(rv) and al.trusted_reviewer(c, cm, rv)

    def test_app_and_owner(self):
        self.assertTrue(self.trusted(comment(review())))

    def test_valid_app_wrong_user(self):
        for user in ({"login": "mallory", "id": 999, "type": "User"},
                     {"login": "odilorg", "id": 999, "type": "User"},          # spoofed login, wrong id
                     {"login": "mallory", "id": 20632218, "type": "User"},     # right id, wrong login
                     {"login": "odilorg", "id": 20632218, "type": "Bot"}):
            self.assertFalse(self.trusted(comment(review(), user=user)), user)

    def test_wrong_app(self):
        for app in (None, {**APP, "id": 1}, {**APP, "slug": "other"}, {**APP, "owner": {"login": "someone"}}):
            self.assertFalse(self.trusted(comment(review(), app=app)), app)

    def test_edited(self):
        self.assertFalse(self.trusted(comment(review(), updated="2026-10-19T01:05:00Z")))

    def test_simulated(self):
        sim = comment(review(test=True), app=None)
        self.assertFalse(self.trusted(sim))                                  # not in normal mode
        self.assertTrue(self.trusted(sim, test_mode=True))
        self.assertFalse(self.trusted(comment(review(test=True), app=None, user={"login": "mallory", "id": 5, "type": "User"}), test_mode=True))
        self.assertFalse(self.trusted(comment(review(), app=None), test_mode=True))   # owner-typed without the marker
        self.assertFalse(self.trusted(comment(review(test=True)), test_mode=True))    # app comment claiming simulation


class Config(unittest.TestCase):
    def test_repo_pinned(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"repo": "odilorg/jahongirnewapp", "app_id": 1, "installation_id": 1, "app_slug": "x",
                       "allowed_path_regex": "x", "claude_edit_globs": []}, f)
        with mock.patch.object(al, "CONFIG", Path(f.name)):
            with self.assertRaises(al.Fatal):
                al.load_config()
            Path(f.name).write_text(json.dumps({"repo": al.SANDBOX, "app_id": 1, "installation_id": 1, "app_slug": "x",
                                                "allowed_path_regex": "x", "claude_edit_globs": [], "max_runs_per_day": 99}))
            self.assertEqual(al.load_config()["max_runs_per_day"], 10)       # cannot be raised above 10


class Usage(unittest.TestCase):
    def setUp(self):
        self.c = config(tempfile.mkdtemp())

    def info(self, **kw):
        return {"apiKeySource": "none", "isUsingOverage": False, "overageStatus": "rejected", **kw}

    def test_ok(self):
        al.check_usage(self.c, self.info())

    def test_fail_closed(self):
        for bad in ({"isUsingOverage": None}, {"isUsingOverage": True}, {"overageStatus": None},
                    {"overageStatus": "allowed"}, {"apiKeySource": "ANTHROPIC_API_KEY"}, {"apiKeySource": None}):
            (self.c["state_dir"] / "HALT").unlink(missing_ok=True)
            with self.assertRaises(al.Fatal, msg=bad):
                al.check_usage(self.c, self.info(**bad))
            self.assertTrue((self.c["state_dir"] / "HALT").exists())
            self.assertIn("halted", al.usage_gate(self.c))                  # later runs refuse until the owner clears it

    def test_daily_cap(self):
        for _ in range(10):
            self.assertIsNone(al.usage_gate(self.c))
        self.assertIn("daily cap", al.usage_gate(self.c))
        u = json.loads((self.c["state_dir"] / "usage.json").read_text())
        u["day"] = "2000-01-01"
        (self.c["state_dir"] / "usage.json").write_text(json.dumps(u))
        self.assertIsNone(al.usage_gate(self.c))                            # new day resets

    def test_no_token_no_fallback(self):
        with self.assertRaises(al.Fatal):
            al.run_claude(self.c, "x", None)


class Validate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = config(self.tmp)
        self.repo = self.c["workdir"] / "repo"
        self.repo.mkdir(parents=True)
        self.ident = {"token": "ghs_" + "T" * 36, "bot_login": "odilorg-agentloop[bot]", "bot_id": 1}
        self.g("init", "-q")
        (self.repo / "docs/pilot").mkdir(parents=True)
        (self.repo / "docs/pilot/sandbox.md").write_text("hello\n")
        (self.repo / "README.md").write_text("r\n")
        self.g("add", "-A")
        self.g("commit", "-qm", "base")

    def g(self, *a):
        return al.git(self.c, self.ident, *a)

    def test_ok(self):
        (self.repo / "docs/pilot/sandbox.md").write_text("hello\nmore\n")
        self.assertEqual(al.validate(self.c, self.ident), (["docs/pilot/sandbox.md"], None))

    def test_rejects(self):
        cases = {
            "out of scope": lambda: (self.repo / "README.md").write_text("x\n"),
            "workflow": lambda: (self.repo / ".github/workflows").mkdir(parents=True) or
                                (self.repo / ".github/workflows/x.yml").write_text("x\n"),
            "delete": lambda: (self.repo / "docs/pilot/sandbox.md").unlink(),
            "private key": lambda: (self.repo / "docs/pilot/sandbox.md").write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n"),
            "github token": lambda: (self.repo / "docs/pilot/sandbox.md").write_text("t ghs_" + "x" * 36 + "\n"),
            "claude token": lambda: (self.repo / "docs/pilot/sandbox.md").write_text("sk-ant-oat01-abcdefghijklmnop\n"),
            "exact secret": lambda: (self.repo / "docs/pilot/sandbox.md").write_text("plainSECRETvalue\n"),
            "nothing": lambda: None,
        }
        for name, act in cases.items():
            self.g("reset", "-q", "--hard")
            self.g("clean", "-qfdx")
            act()
            paths, why = al.validate(self.c, self.ident, secrets=("plainSECRETvalue",))
            self.assertIsNone(paths, name)
            self.assertTrue(why, name)


class RunLoop(unittest.TestCase):
    """cmd_run decisions with a fake GitHub API and a fake apply step."""

    def setUp(self):
        self.c = config(tempfile.mkdtemp())
        (self.c["inbox"]).mkdir()
        self.ident = {"token": "t", "bot_login": "odilorg-agentloop[bot]", "bot_id": 1}
        self.labels, self.replies, self.applied_calls, self.commits = [], [], [], []
        self.pr = {"number": 7, "head": {"sha": SHA, "ref": "agent/x", "repo": {"full_name": al.SANDBOX}},
                   "base": {"ref": "main"}, "labels": []}
        self.comments = []

    def fake_api_ok(self, method, path, token, data=None):
        if method == "GET" and path == f"/repos/{al.SANDBOX}":
            return {"default_branch": "main"}, ""
        if method == "POST" and path.endswith("/labels"):
            self.labels += data["labels"]
            return {}, ""
        if method == "POST" and path.endswith("/comments"):
            self.replies.append(data["body"])
            return {}, ""
        raise AssertionError(path)

    def fake_api_all(self, path, token):
        if "/pulls?state=open" in path:
            return [self.pr]
        if path.endswith("/comments"):
            return self.comments
        if path.endswith("/commits"):
            return [{"commit": {"message": m}} for m in self.commits]
        raise AssertionError(path)

    def fake_apply(self, c, ident, branch, head, rid, rnd, text, ct):
        self.applied_calls.append(rid)
        return "b" * 40, None

    def run_once(self):
        with mock.patch.object(al, "identity", return_value=self.ident), \
             mock.patch.object(al, "take", return_value="sk-ant-oat-test"), \
             mock.patch.object(al, "api_ok", self.fake_api_ok), mock.patch.object(al, "api_all", self.fake_api_all), \
             mock.patch.object(al, "apply_request", self.fake_apply):
            return al.cmd_run(self.c)

    def test_apply_once_then_duplicate_skipped(self):
        self.comments = [comment(review(), cid=101)]
        self.run_once()
        self.assertEqual(self.applied_calls, ["101"])
        self.assertIn("Status: APPLIED", self.replies[0])
        self.commits.append("docs(agent): x\n\nAgent-Request: 101\n")      # what the push left behind
        self.run_once()
        self.assertEqual(self.applied_calls, ["101"])

    def test_wrong_user_ignored(self):
        self.comments = [comment(review(), user={"login": "mallory", "id": 5, "type": "User"})]
        self.run_once()
        self.assertEqual((self.applied_calls, self.replies), ([], []))

    def test_edited_ignored(self):
        self.comments = [comment(review(), updated="2026-10-19T02:00:00Z")]
        self.run_once()
        self.assertEqual(self.applied_calls, [])

    def test_stale(self):
        self.comments = [comment(review(sha="c" * 40))]
        self.run_once()
        self.assertEqual(self.applied_calls, [])

    def test_escalate(self):
        self.comments = [comment(review("ESCALATE", request="README change requested"))]
        self.run_once()
        self.assertEqual(self.applied_calls, [])
        self.assertEqual(self.labels, ["agent-needs-owner"])
        self.assertIn("Status: NEEDS_OWNER", self.replies[0])

    def test_round_limit(self):
        self.commits = [f"x\n\nAgent-Request: {i}\n" for i in (1, 2, 3)]
        self.comments = [comment(review(), cid=104)]
        self.run_once()
        self.assertEqual(self.applied_calls, [])
        self.assertIn("round limit", self.replies[0])

    def test_latest_review_only(self):
        self.comments = [comment(review(), cid=101, created="2026-10-19T01:00:00Z"),
                         comment(review("PASS", request=""), cid=102, created="2026-10-19T01:10:00Z")]
        self.run_once()
        self.assertEqual(self.applied_calls, [])

    def test_stop_label_and_file(self):
        self.comments = [comment(review())]
        self.pr["labels"] = [{"name": "agent-stop"}]
        self.run_once()
        self.assertEqual(self.applied_calls, [])
        self.pr["labels"] = []
        (self.c["inbox"] / "STOP").touch()
        self.run_once()
        self.assertEqual(self.applied_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
