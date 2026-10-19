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


FAKE_TOKEN = "sk-ant-oat01-" + "Q" * 40
ERR_TEXT = "OAuth token has expired. Please obtain a new token " + FAKE_TOKEN


def fake_claude(tmp, stream, stderr="", exit_code=0):
    """Write a stand-in Claude binary: prints `stream` (list of dicts / raw strings), records its argv."""
    lines = [json.dumps(x) if isinstance(x, dict) else x for x in stream]
    script = Path(tmp) / "fake-claude"
    argv_file = Path(tmp) / "argv.json"
    script.write_text(f"""#!/usr/bin/env python3
import json, sys
if sys.argv[1:] == ["--version"]:
    print("2.1.303 (Claude Code)"); sys.exit(0)
open({str(argv_file)!r}, "w").write(json.dumps(sys.argv[1:]))
sys.stdout.write({chr(10).join(lines)!r} + chr(10))
sys.stderr.write({stderr!r})
sys.exit({exit_code})
""")
    script.chmod(0o755)
    return str(script), argv_file


INIT = {"type": "system", "subtype": "init", "apiKeySource": "none", "claude_code_version": "2.1.303"}
RATE_OK = {"type": "rate_limit_event", "rate_limit_info": {"isUsingOverage": False, "overageStatus": "rejected"}}
CONTRADICTORY = {"type": "result", "subtype": "success", "is_error": True, "result": ERR_TEXT}
GOOD = {"type": "result", "subtype": "success", "is_error": False, "result": "done"}


class Diagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = config(self.tmp, claude_edit_globs=["docs/pilot/**"])
        (self.c["workdir"] / "repo").mkdir(parents=True)

    def logged(self, fn, *a):
        out = []
        with mock.patch("builtins.print", lambda *x, **k: out.append(" ".join(map(str, x)))):
            try:
                result = fn(*a)
            except al.Fatal as e:
                result = e
        text = "\n".join(out) + (self.c["log_dir"] / "log.jsonl").read_text() if (self.c["log_dir"] / "log.jsonl").exists() else "\n".join(out)
        return result, text

    def assert_clean(self, text):
        for leak in (FAKE_TOKEN, "sk-ant-", "QQQQ", "OAuth token has expired", "Please obtain", "Unauthorized",
                     "You are applying"):
            self.assertNotIn(leak, text)

    def test_contradictory_success_is_error_exit1(self):
        self.c["claude_bin"], _ = fake_claude(self.tmp, [INIT, CONTRADICTORY], stderr="Error: 401 Unauthorized", exit_code=1)
        info = al.run_claude(self.c, "req", FAKE_TOKEN)
        self.assertEqual((info["result"], info["is_error"], info["exit"]), ("success", True, 1))
        self.assertTrue(al.run_failed(info))
        self.assertEqual(info["diagnostic"], ["oauth_expired", "http_401"])
        self.assertEqual(info["cli_version"], "2.1.303")
        self.assertEqual(info["events"], {"system:init": 1, "result:success": 1})
        self.assert_clean(json.dumps(info))

    def test_failure_rejected_in_every_form(self):
        base = {"exit": 0, "is_error": False, "result": "success"}
        self.assertFalse(al.run_failed(base))
        for bad in ({"exit": 1}, {"is_error": True}, {"is_error": None}, {"result": "error_max_turns"}, {"result": None}):
            self.assertTrue(al.run_failed({**base, **bad}), bad)

    def test_claude_step_logs_no_secrets_and_halts_without_usage(self):
        self.c["claude_bin"], _ = fake_claude(self.tmp, [INIT, CONTRADICTORY, "non-json " + FAKE_TOKEN],
                                              stderr="401 Unauthorized " + FAKE_TOKEN, exit_code=1)
        result, text = self.logged(al.claude_step, self.c, "secret request text You are applying", FAKE_TOKEN)
        self.assertIsInstance(result, al.Fatal)
        self.assertTrue((self.c["state_dir"] / "HALT").exists())
        self.assertIn('"is_error": true', text)
        self.assertIn('"diagnostic": ["oauth_expired", "http_401"]', text)
        self.assert_clean(text)

    def test_existing_halt_never_overwritten(self):
        (self.c["state_dir"] / "HALT").write_text("original reason")
        with self.assertRaises(al.Fatal):
            al.check_usage(self.c, {"apiKeySource": "none", "isUsingOverage": None, "overageStatus": None})
        self.assertEqual((self.c["state_dir"] / "HALT").read_text(), "original reason")

    def test_no_writes_on_failure(self):
        calls = []
        info = {"exit": 1, "is_error": True, "result": "success", "diagnostic": ["oauth_invalid"],
                "apiKeySource": "none", "isUsingOverage": False, "overageStatus": "rejected"}
        with mock.patch.object(al, "prepare_clone", return_value=SHA), \
             mock.patch.object(al, "claude_step", return_value=(info, None)), \
             mock.patch.object(al, "validate", lambda *a, **k: calls.append("validate")), \
             mock.patch.object(al, "git", lambda *a, **k: calls.append(("git",) + a[2:])), \
             mock.patch.object(al, "api_ok", lambda *a, **k: calls.append("api")):
            sha, why = al.apply_request(self.c, {"token": "t"}, "agent/x", SHA, "1", 1, "req", FAKE_TOKEN)
        self.assertIsNone(sha)
        self.assertIn("oauth_invalid", why)
        self.assertEqual(calls, [])                                         # no validate, commit, push or API write

    def poisoned_streams(self):
        """One stream per structured field, each carrying a fake token where a fixed value belongs."""
        yield "claude_code_version", [{**INIT, "claude_code_version": FAKE_TOKEN}, RATE_OK, GOOD]
        yield "apiKeySource", [{**INIT, "apiKeySource": FAKE_TOKEN}, RATE_OK, GOOD]
        yield "result.subtype", [INIT, RATE_OK, {**GOOD, "subtype": FAKE_TOKEN}]
        yield "result.is_error", [INIT, RATE_OK, {**GOOD, "is_error": FAKE_TOKEN}]
        yield "isUsingOverage", [INIT, {"type": "rate_limit_event",
                                        "rate_limit_info": {"isUsingOverage": FAKE_TOKEN, "overageStatus": "rejected"}}, GOOD]
        yield "overageStatus", [INIT, {"type": "rate_limit_event",
                                       "rate_limit_info": {"isUsingOverage": False, "overageStatus": FAKE_TOKEN}}, GOOD]

    def test_structured_fields_bounded_and_fail_closed(self):
        for field, stream in self.poisoned_streams():
            with self.subTest(field=field):
                self.tearDown_state()
                self.c["claude_bin"], _ = fake_claude(self.tmp, stream)
                info = al.run_claude(self.c, "req", FAKE_TOKEN)
                self.assert_clean(json.dumps(info))
                self.assertTrue(al.run_failed(info) or al.usage_problems(info), field)   # never coerced to passing
                self.assertIn("unknown", json.dumps(info))
                result, text = self.logged(al.claude_step, self.c, "req", FAKE_TOKEN)
                halt = self.c["state_dir"] / "HALT"
                self.assert_clean(text + (halt.read_text() if halt.exists() else "") + str(result))

    def test_event_names_bounded(self):
        self.c["claude_bin"], _ = fake_claude(self.tmp, [INIT, RATE_OK, {"type": FAKE_TOKEN.lower(), "subtype": FAKE_TOKEN},
                                                         {"type": "system", "subtype": FAKE_TOKEN.lower()}, GOOD])
        info = al.run_claude(self.c, "req", FAKE_TOKEN)
        self.assertEqual(info["events"], {"system:init": 1, "rate_limit_event": 1, "other:other": 1,
                                          "system:other": 1, "result:success": 1})
        self.assert_clean(json.dumps(info))
        self.assertNotIn("qqqq", json.dumps(info).lower())

    def tearDown_state(self):
        for name in ("HALT", "usage.json", "log.jsonl"):
            for d in (self.c["state_dir"], self.c["log_dir"]):
                (d / name).unlink(missing_ok=True)

    def test_classify(self):
        cases = {"Invalid bearer token": "oauth_invalid", "OAuth token has expired": "oauth_expired",
                 "HTTP 403 Forbidden": "http_403", "429 Too Many Requests": "http_429",
                 "EACCES: permission denied, mkdir": "fs_eacces", "error: unknown option '--foo'": "unsupported_flag",
                 "getaddrinfo ENOTFOUND api.anthropic.com": "network", "Credit balance is too low": "credits",
                 "something odd": "unknown", "": "no_error_text"}
        for text, cat in cases.items():
            self.assertIn(cat, al.classify(text), text)


class Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = config(self.tmp)
        self.c["inbox"].mkdir()
        (self.c["state_dir"] / "HALT").write_text("phase 2 halt")
        self.rt = Path(self.tmp) / "run"
        self.rt.mkdir()

    def run_probe(self, stream, exit_code=0, trigger=al.PROBE_PHRASE):
        self.c["claude_bin"], self.argv = fake_claude(self.tmp, stream, exit_code=exit_code)
        if trigger is not None:
            (self.c["inbox"] / "probe-request").write_text(trigger + "\n")
        (self.rt / "claude-token").write_text(FAKE_TOKEN)
        boom = mock.Mock(side_effect=AssertionError("probe must not call GitHub or git"))
        out = []
        with mock.patch.object(al, "RUNTIME", self.rt), mock.patch.object(al, "api", boom), \
             mock.patch.object(al, "api_ok", boom), mock.patch.object(al, "git", boom), \
             mock.patch("builtins.print", lambda *x, **k: out.append(" ".join(map(str, x)))):
            try:
                rc = al.cmd_probe(self.c)
            except al.Fatal as e:
                rc = e
        return rc, "\n".join(out)

    def test_requires_owner_trigger(self):
        rc, _ = self.run_probe([INIT, RATE_OK, GOOD], trigger=None)
        self.assertIsInstance(rc, al.Fatal)
        rc, _ = self.run_probe([INIT, RATE_OK, GOOD], trigger="yes")
        self.assertIsInstance(rc, al.Fatal)
        self.assertFalse((self.c["state_dir"] / "probe-done").exists())

    def test_success_once_only_read_only(self):
        rc, text = self.run_probe([INIT, RATE_OK, GOOD])
        self.assertEqual(rc, 0)
        self.assertIn('"usage_confirmed": true', text)
        argv = json.loads(self.argv.read_text())
        self.assertEqual(argv[argv.index("--tools") + 1], "")               # no tools at all
        self.assertNotIn("--allowedTools", argv)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertFalse((self.rt / "claude-token").exists())               # handoff consumed
        self.assertEqual((self.c["state_dir"] / "HALT").read_text(), "phase 2 halt")   # HALT untouched
        rc, _ = self.run_probe([INIT, RATE_OK, GOOD])
        self.assertIsInstance(rc, al.Fatal)                                 # once only

    def test_failure_reports_categories_only(self):
        rc, text = self.run_probe([INIT, CONTRADICTORY], exit_code=1)
        self.assertEqual(rc, 1)
        self.assertIn('"usage_confirmed": false', text)
        self.assertIn("oauth_expired", text)
        for leak in (FAKE_TOKEN, "sk-ant-", "Please obtain"):
            self.assertNotIn(leak, text)
        self.assertEqual((self.c["state_dir"] / "HALT").read_text(), "phase 2 halt")
        self.assertTrue((self.c["state_dir"] / "probe-done").exists())      # a failed probe still counts

    def test_poisoned_fields_fail_closed_without_leaks(self):
        for field, stream in Diagnostics.poisoned_streams(None):
            with self.subTest(field=field):
                (self.c["state_dir"] / "probe-done").unlink(missing_ok=True)
                rc, text = self.run_probe(stream)
                self.assertEqual(rc, 1, field)
                for leak in (FAKE_TOKEN, "sk-ant-", "QQQQ"):
                    self.assertNotIn(leak, text + (self.c["state_dir"] / "HALT").read_text())
                self.assertEqual((self.c["state_dir"] / "HALT").read_text(), "phase 2 halt")

    def test_counts_against_cap(self):
        (self.c["state_dir"] / "usage.json").write_text(json.dumps(
            {"day": al.datetime.datetime.now(al.datetime.timezone.utc).strftime("%Y-%m-%d"), "runs": 10}))
        rc, _ = self.run_probe([INIT, RATE_OK, GOOD])
        self.assertIsInstance(rc, al.Fatal)


class InstallerToken(unittest.TestCase):
    """install.sh --claude-token input handling, run through bash with only its two token functions."""
    FULL = "sk-ant-oat01-" + "A1b2_C3d-" * 10 + "xyz01"     # 108 characters, fake

    def check(self, pasted):
        script = Path(__file__).with_name("install.sh")
        funcs = (f'eval "$(sed -n -e \'/^read_token()/,/^}}/p\' -e \'/^TOKEN_MIN_LEN=/p\' -e \'/^token_ok()/,/^}}/p\' {script})"; '
                 't="$(read_token)"; if token_ok "$t"; then echo "ok ${#t}"; else echo "bad ${#t}"; fi')
        r = subprocess.run(["bash", "-c", funcs], input=pasted, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.stderr, "")
        return r.stdout.strip()

    def test_full_length_accepted(self):
        self.assertEqual(len(self.FULL), 108)
        self.assertEqual(self.check(self.FULL + "\n"), "ok 108")

    def test_truncated_79_rejected(self):
        self.assertEqual(self.check(self.FULL[:79] + "\n"), "bad 79")

    def test_wrapped_paste_joined(self):
        self.assertEqual(self.check(self.FULL[:79] + "\n" + self.FULL[79:] + "\n"), "ok 108")
        self.assertEqual(self.check(self.FULL[:79] + "\n" + self.FULL[79:]), "ok 108")   # no final newline

    def test_malformed_rejected(self):
        for bad in (self.FULL[:50] + " " + self.FULL[50:], self.FULL + "\nextra text", "sk-ant-api03-" + "x" * 100,
                    "", self.FULL.replace("sk-ant-oat01-", "")):
            self.assertTrue(self.check(bad + "\n").startswith("bad"), repr(bad[:20]))

    def test_message_never_prints_token(self):
        src = Path(__file__).with_name("install.sh").read_text()
        block = src[src.index('if [ "${1:-}" = "--claude-token" ]'):src.index('if [ "${1:-}" = "--uninstall" ]')]
        uses = re.findall(r"\$\{?#?t\b\}?", block)
        self.assertEqual(sorted(set(uses)), ["$t", "${#t}"])                  # only the length is ever shown
        self.assertEqual([l.strip() for l in block.splitlines() if re.search(r"\$t\b", l) and "#t" not in l],
                         ['if ! token_ok "$t"; then', "umask 077; printf '%s\\n' \"$t\" > /etc/agentloop/claude-token"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
