#!/usr/bin/env python3
"""Permission matrix for the agentloop GitHub App. Sandbox only. Run as agentloop@selftest.service.

Rules for every forbidden test:
- it acts only on objects this run creates (a throwaway agent/* branch and PR, new files);
- an unexpected success could at worst add a harmless Markdown file to the sandbox;
- operations whose success would change protections or access (rulesets, collaborators,
  Actions settings) are NOT attempted; they are checked by inspecting the token's permissions.
The first forbidden operation that succeeds stops the test (exit 2). The token is never printed.
"""
import json, time
import agentloop as al

MAIN_SHA = "17dacbf104fe2c9a07d96b27f54525d6518d7459"


def main(c):
    ident = al.identity(c)
    tok, R, T = ident["token"], c["repo"], time.strftime("%H%M%S")
    results = []

    def rec(name, ok, detail=""):
        results.append({"test": name, "result": "PASS" if ok else "FAIL", "detail": detail})
        print(("PASS  " if ok else "FAIL  ") + name + (f" :: {detail}" if detail else ""), flush=True)

    def save():
        (c["log_dir"] / "selftest.json").write_text(json.dumps(results, indent=1))

    class Stop(Exception):
        pass

    def forbidden(name, ok_, detail):
        rec(name, not ok_, detail)
        if ok_:
            print("forbidden operation SUCCEEDED — stopping", flush=True)
            raise Stop

    def g(*a):
        return al.git(c, ident, *a, check=False)

    def first_err(r):
        for l in (r.stderr or "").splitlines():
            if any(k in l for k in ("GH0", "refusing", "rejected", "denied", "declined")):
                return l.strip()[:140]
        return (r.stderr or "").strip()[-140:]

    try:
        return run(c, ident, tok, R, T, rec, forbidden, g, first_err, results, save)
    except Stop:
        save()
        return 2


def run(c, ident, tok, R, T, rec, forbidden, g, first_err, results, save):
    # 0. identity (inspection only)
    perms = ident["token_perms"]
    rec("token permissions are exactly contents/pull_requests/issues write + metadata read",
        perms == {**al.TOKEN_PERMS, "metadata": "read"}, str(perms))
    rec("App has no administration, workflows, secrets, actions or members permission",
        set(ident["app_perms"]) <= set(al.ALLOWED_APP_PERMS), str(ident["app_perms"]))
    rec("token reaches only the sandbox", ident["repos"] == [R], str(ident["repos"]))
    rec("installation cannot mint a token for jahongirnewapp (mint stage)",
        ident["other_repo_mint_status"] != 201, f"HTTP {ident['other_repo_mint_status']}")
    for other in ("odilorg/jahongirnewapp", "odilorg/jahongir-crm-next"):
        st, _, _ = al.api("GET", f"/repos/{other}", tok)
        forbidden(f"token cannot read {other}", st == 200, f"HTTP {st}")

    # 1. allowed: new agent/* branch + Markdown commit + PR + comment
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
        save()
        return 1
    sha = g("rev-parse", "HEAD").stdout.strip()
    commit, _ = al.api_ok("GET", f"/repos/{R}/commits/{sha}", tok)
    rec("commit is attributed to the App bot", (commit.get("author") or {}).get("login") == ident["bot_login"],
        (commit.get("author") or {}).get("login"))
    pr, _ = al.api_ok("POST", f"/repos/{R}/pulls", tok, {"title": f"test: App selftest {T}", "head": br,
                                                           "base": "main", "body": "agentloop App selftest. Do not merge."})
    rec("open a PR as the App", pr["user"]["login"] == ident["bot_login"], f"#{pr['number']} by {pr['user']['login']}")
    cm, _ = al.api_ok("POST", f"/repos/{R}/issues/{pr['number']}/comments", tok,
                      {"body": "agentloop selftest: PR discussion works."})
    rec("comment on the PR as the App", cm["user"]["login"] == ident["bot_login"])

    # 2. forbidden, on this run's own objects only
    r = g("push", "-q", "origin", "HEAD:refs/heads/main")
    forbidden("push to main", r.returncode == 0, first_err(r))
    r = g("push", "-q", "--force", "origin", f"origin/main:refs/heads/{br}")
    forbidden("force-push its agent/* branch", r.returncode == 0, first_err(r))
    r = g("push", "-q", "origin", f":refs/heads/{br}")
    forbidden("delete its agent/* branch", r.returncode == 0, first_err(r))
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
    forbidden("write a new file to main through the contents API", st in (200, 201), f"HTTP {st}")
    st, body, _ = al.api("PUT", f"/repos/{R}/pulls/{pr['number']}/merge", tok, {"merge_method": "merge"})
    forbidden(f"merge its own PR #{pr['number']}", st == 200, f"HTTP {st}: {(body or {}).get('message', '')[:90]}")
    st, body, _ = al.api("POST", f"/repos/{R}/pulls/{pr['number']}/reviews", tok, {"event": "APPROVE"})
    forbidden("approve its own PR", st == 200, f"HTTP {st}: {(body or {}).get('message', '')[:90]}")
    rec("rulesets, collaborators, Actions settings: not attempted (no administration permission)",
        "administration" not in ident["app_perms"], "inspection only")

    main_now = al.api_ok("GET", f"/repos/{R}/branches/main", tok)[0]["commit"]["sha"]
    rec("main unchanged", main_now == MAIN_SHA, main_now[:12])
    save()
    print(f"\n{sum(r['result'] == 'PASS' for r in results)}/{len(results)} PASS; PR #{pr['number']} left open (do not merge)")
    return 0 if all(r["result"] == "PASS" for r in results) else 1
