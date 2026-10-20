#!/usr/bin/env python3
"""One-time owner setup: create the agentloop GitHub App from a manifest and record its installation.

Run with sudo on the machine that will run agentloop (after install.sh), then open http://127.0.0.1:8765/ in a browser
that is signed in to GitHub as the repository owner. Two clicks: "Create GitHub App", then
"Install" with "Only select repositories" -> the one repository.

The App's private key goes straight to /etc/agentloop/app.pem (root, mode 0600), which the
agentloop user cannot read; only the root mint stage of the unit uses it. It is never printed.
IDs (no secrets) go to /etc/agentloop.json. The client secret and webhook secret GitHub also returns are discarded (unused).
"""
import html, json, os, secrets, sys, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = sys.argv[1] if len(sys.argv) > 1 else "odilorg/agentloop-sandbox"
NAME = sys.argv[2] if len(sys.argv) > 2 else "odilorg-agentloop"
KEYDIR = Path("/etc/agentloop")
CONFIG = Path("/etc/agentloop.json")
PORT = 8765
STATE = secrets.token_urlsafe(16)
MANIFEST = {
    "name": NAME,
    "url": f"https://github.com/{REPO}",
    "description": "Applies reviewer-requested corrections to agent/* branches. Cannot change workflows or settings.",
    "hook_attributes": {"url": f"https://github.com/{REPO}", "active": False},
    "redirect_url": f"http://127.0.0.1:{PORT}/created",
    "setup_url": f"http://127.0.0.1:{PORT}/installed",
    "public": False,
    "default_permissions": {"contents": "write", "pull_requests": "write", "issues": "write", "metadata": "read"},
    "default_events": [],
}
done = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def page(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<!doctype html><meta charset=utf-8><body style='font:16px sans-serif;max-width:40em;margin:3em auto'>{body}".encode())

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/":
            return self.page(
                f"<h2>Create the agentloop GitHub App</h2><p>Permissions: contents, pull requests, issues (write); "
                f"metadata (read). No workflows, no administration, no webhooks.</p>"
                f"<form action='https://github.com/settings/apps/new?state={STATE}' method=post>"
                f"<input type=hidden name=manifest value='{html.escape(json.dumps(MANIFEST), quote=True)}'>"
                f"<button style='font-size:18px'>Continue to GitHub</button></form>")
        if u.path == "/created":
            if q.get("state") != STATE or "code" not in q:
                return self.page("State mismatch. Start again.", 400)
            req = urllib.request.Request(f"https://api.github.com/app-manifests/{q['code']}/conversions", method="POST",
                                         headers={"Accept": "application/vnd.github+json", "User-Agent": "agentloop-setup"})
            with urllib.request.urlopen(req, timeout=30) as r:
                app = json.load(r)
            KEYDIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            key = KEYDIR / "app.pem"
            fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(app["pem"])
            done.update(app_id=app["id"], app_slug=app["slug"], permissions=app.get("permissions"))
            print(f"App created: {app['slug']} (id {app['id']}), permissions {app.get('permissions')}; key saved", flush=True)
            self.send_response(302)
            self.send_header("Location", f"https://github.com/apps/{app['slug']}/installations/new")
            return self.end_headers()
        if u.path == "/installed":
            if "installation_id" not in q or "app_id" not in done:
                return self.page("Missing installation id. Start again.", 400)
            done["installation_id"] = int(q["installation_id"])
            cfg = CONFIG
            current = json.loads(cfg.read_text()) if cfg.exists() else {}
            current.update({"repo": REPO, "app_id": done["app_id"], "app_slug": done["app_slug"],
                            "installation_id": done["installation_id"]})
            current.setdefault("allowed_path_regex", r"docs/pilot/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\.md")
            current.setdefault("claude_edit_globs", ["docs/pilot/**"])
            cfg.write_text(json.dumps(current, indent=2) + "\n")
            cfg.chmod(0o644)
            print(f"Installed: installation {done['installation_id']}; config written to {cfg}", flush=True)
            self.page("<h2>Done</h2><p>You can close this tab and tell Claude “app ready”.</p>")
            raise KeyboardInterrupt
        self.page("Not found", 404)


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("run with sudo: the App key must land in a root-only file")
    print(f"Open http://127.0.0.1:{PORT}/ in a browser signed in to GitHub as the owner.", flush=True)
    srv = HTTPServer(("127.0.0.1", PORT), H)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
