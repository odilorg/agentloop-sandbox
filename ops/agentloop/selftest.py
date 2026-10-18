#!/usr/bin/env python3
"""Permission matrix for the agentloop GitHub App. Sandbox only.

Every forbidden operation must fail. The first one that succeeds stops the test (exit 2).
The App token is never printed.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentloop as al

c = al.load_config()
if c["repo"] != al.SANDBOX:
    sys.exit("selftest runs only against " + al.SANDBOX)
ident = al.identity(c)
tok, R, T = ident["token"], c["repo"], time.strftime("%H%M%S")
results = []


def rec(name, ok, detail=""):
    results.append({"test": name, "result": "PASS" if ok else "FAIL", "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + name + (f" :: {detail}" if detail else ""), flush=True)


def forbidden(name, ok_, detail):
    rec(name, not ok_, detail)
    if ok_:
        print("forbidden operation SUCCEEDED — stopping", flush=True)
        json.dump(results, open(c["state_dir"] / "selftest.json", "w"), indent=1)
        sys.exit(2)


def g(*a):
    return al.git(c, ident, *a, check=False)


def first_err(r):
    for l in (r.stderr or "").splitlines():
        if any(k in l for k in ("GH0", "refusing", "rejected", "denied", "declined")):
            return l.strip()[:140]
    return (r.stderr or "").strip()[-140:]


# 0. identity
rec("identity is the App, one repo, no workflow/admin permission",
    set(ident["app_perms"]) <= set(al.ALLOWED_APP_PERMS) and ident["repos"] == [R],
    f"{ident['bot_login']} perms={ident['app_perms']} repos={ident['repos']}")
for other in ("odilorg/jahongirnewapp", "odilorg/jahongir-crm-next"):
    st, _, _ = al.api("GET", f"/repos/{other}", tok)
    forbidden(f"token cannot read {other}", st == 200, f"HTTP {st}")
st, _, _ = al.api("POST", f"/app/installations/{c['installation_id']}/access_tokens", al.app_jwt(c),
                  {"repositories": ["jahongirnewapp"]})
forbidden("cannot mint a token for jahongirnewapp", st == 201, f"HTTP {st}")

# 1. allowed: own agent/* branch + Markdown commit + PR + comment
meta, _ = al.api_ok("GET", f"/repos/{R}", tok)
al.prepare_clone(c, ident, meta["default_branch"])
work = c["workdir"] / "repo"
with open(work / "docs/pilot/sandbox.md", "a") as f:
    f.write(f"\n## App selftest {T}\n\nSynthetic line pushed by the agentloop App.\n")
g("commit", "-qam", f"docs(pilot): app selftest {T}")
br = f"agent/app-selftest-{T}"
r = g("push", "-q", "origin", f"HEAD:refs/heads/{br}")
rec("push Markdown to a new agent/* branch", r.returncode == 0, first_err(r))
if r.returncode:
    sys.exit(1)
sha = g("rev-parse", "HEAD").stdout.strip()
commit, _ = al.api_ok("GET", f"/repos/{R}/commits/{sha}", tok)
rec("commit is attributed to the App bot", (commit.get("author") or {}).get("login") == ident["bot_login"],
    (commit.get("author") or {}).get("login"))
pr, _ = al.api_ok("POST", f"/repos/{R}/pulls", tok, {"title": f"test: App selftest {T}", "head": br, "base": "main",
                                                       "body": "agentloop App selftest. Do not merge."})
rec("open a PR as the App", pr["user"]["login"] == ident["bot_login"], f"#{pr['number']} by {pr['user']['login']}")
cm, _ = al.api_ok("POST", f"/repos/{R}/issues/{pr['number']}/comments", tok, {"body": "agentloop selftest: PR discussion works."})
rec("comment on the PR as the App", cm["user"]["login"] == ident["bot_login"])

# 2. forbidden
r = g("push", "-q", "origin", "HEAD:refs/heads/main")
forbidden("push to main", r.returncode == 0, first_err(r))
r = g("push", "-q", "--force", "origin", f"origin/main:refs/heads/{br}")
forbidden("force-push an agent/* branch", r.returncode == 0, first_err(r))
r = g("push", "-q", "origin", f":refs/heads/{br}")
forbidden("delete an agent/* branch", r.returncode == 0, first_err(r))
r = g("push", "-q", "origin", f"origin/main:refs/heads/other-{T}")
forbidden("create a branch outside agent/*", r.returncode == 0, first_err(r))
(work / ".github/workflows").mkdir(parents=True, exist_ok=True)
(work / ".github/workflows/app-selftest.yml").write_text("name: x\non: workflow_dispatch\njobs: {}\n")
g("add", ".github/workflows/app-selftest.yml")
g("commit", "-qm", "test: workflow change")
r = g("push", "-q", "origin", f"HEAD:refs/heads/{br}")
forbidden("push a workflow file (git)", r.returncode == 0, first_err(r))
st, body, _ = al.api("PUT", f"/repos/{R}/contents/.github/workflows/app-selftest2.yml", tok,
                     {"message": "test", "content": "bmFtZTogeAo=", "branch": br})
forbidden("write a workflow file (API)", st in (200, 201), f"HTTP {st}")
st, body, _ = al.api("PUT", f"/repos/{R}/contents/docs/pilot/selftest-main-{T}.md", tok,
                     {"message": "test", "content": "dGVzdAo=", "branch": "main"})
forbidden("write to main through the contents API", st in (200, 201), f"HTTP {st}")
for n in (pr["number"], 1):
    st, body, _ = al.api("PUT", f"/repos/{R}/pulls/{n}/merge", tok, {"merge_method": "merge"})
    forbidden(f"merge PR #{n}", st == 200, f"HTTP {st}: {(body or {}).get('message', '')[:90]}")
st, body, _ = al.api("POST", f"/repos/{R}/pulls/{pr['number']}/reviews", tok, {"event": "APPROVE"})
forbidden("approve its own PR", st == 200, f"HTTP {st}: {(body or {}).get('message', '')[:90]}")
st, body, _ = al.api("PUT", f"/repos/{R}/collaborators/someone-else", tok, {"permission": "push"})
forbidden("change repository access", st in (201, 204), f"HTTP {st}")
st, body, _ = al.api("DELETE", f"/repos/{R}/rulesets/25340789", tok)
forbidden("delete a ruleset", st == 204, f"HTTP {st}")
st, body, _ = al.api("PUT", f"/repos/{R}/actions/permissions/workflow", tok, {"default_workflow_permissions": "write"})
forbidden("change Actions settings", st == 204, f"HTTP {st}")

main_now = al.api_ok("GET", f"/repos/{R}/branches/main", tok)[0]["commit"]["sha"]
rec("main unchanged", main_now == "17dacbf104fe2c9a07d96b27f54525d6518d7459", main_now[:12])
json.dump(results, open(c["state_dir"] / "selftest.json", "w"), indent=1)
print(f"\n{sum(r['result'] == 'PASS' for r in results)}/{len(results)} PASS; PR #{pr['number']} left open (do not merge)")
sys.exit(0 if all(r["result"] == "PASS" for r in results) else 1)
