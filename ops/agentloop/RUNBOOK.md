# agentloop runbook

ChatGPT Work reviews `agent/*` pull requests. The agentloop runner on the owner's machine
applies the requested corrections through a restricted GitHub App. The owner alone approves
and merges.

## How it works

```
agent/* PR opened or updated
  ├─ agent-scope (trusted, from main)  → PASS/FAIL check on the head commit
  └─ ChatGPT Work task                 → comment "AGENT-REVIEW v1 / Commit / Verdict / Request"
agentloop.timer (every 2 min, systemd --user)
  └─ agentloop.py run
       1. STOP file? → exit
       2. mint a 1-hour App token; prove: App = odilorg-agentloop, permissions only
          contents/pull_requests/issues, installation = this one repo only  (else exit 1)
       3. for open agent/* PRs without agent-stop / agent-needs-owner labels:
          take the LATEST review comment posted via app 1144995 chatgpt-codex-connector (owner openai)
          skip unless Verdict FIX, Commit == current head, and the request is not already handled
       4. claude -p on the owner's subscription (apiKeySource must be "none", no overage):
          tools Read/Edit/Write only, Edit/Write limited to the allowed paths, no shell, no MCP
       5. wrapper validates the diff: allowed paths only, add/modify only, regular 100644 files,
          no binaries, ≤20 files, ≤200 lines
       6. commit (trailers Agent-Request, Agent-Round), normal push to the PR branch as the App,
          reply "AGENT-REPLY v1 … Status: APPLIED"
       any rejection → reply Status NEEDS_OWNER + label agent-needs-owner (runner stops on that PR)
  at most one request per run; a lock prevents overlapping runs
new commit → agent-scope + ChatGPT re-review → PASS ends the loop, FIX starts the next round
```

## Credentials and permissions

| Who | Identity | Can | Cannot (enforced by) |
|---|---|---|---|
| Implementer | GitHub App `odilorg-agentloop`, installed on one repo; 1-hour tokens | create/push `agent/*` branches, open PRs, comment | workflows (no `workflows` permission: GitHub refuses); `main`, force push, branch delete, other branches (rulesets, no bypass); merge (R-main: code-owner review + check); approve own PR (GitHub); settings/rulesets/collaborators (no admin permission); other repos (installation scope) |
| Model | Claude Code on the owner's Max login | edit allowed paths in a scratch clone | shell, network tools, MCP, other files (tool allowlist + wrapper) |
| Reviewer | ChatGPT Work via `chatgpt-codex-connector`, **acting as the owner** | comment | only its task instructions restrain it (see Limits) |
| Owner | `odilorg` (admin) | approve as code owner, merge | push to `main` directly (R-main, bypass via PR only) |

Local secrets: `~/.config/agentloop/app.pem` (mode 0600), written only by `app_setup.py`.
Never in a repository, never printed. `~/.config/agentloop/config.json` holds IDs only.

## Install and operate

```
ops/agentloop/install.sh                         # copies to ~/.local/share/agentloop, timer not enabled
python3 ops/agentloop/app_setup.py               # once: create + install the App (owner, browser)
~/.local/share/agentloop/agentloop.py whoami     # identity checks
~/.local/share/agentloop/selftest.py             # permission matrix (sandbox only)
systemctl --user enable --now agentloop.timer    # start
journalctl --user -u agentloop -n 50             # what happened; also ~/.local/state/agentloop/log.jsonl
```

Start a task as the agent (opens an `agent/*` PR as the App):
`agentloop.py start agent/<name> "<title>" <request-file>`

## Failures and stopping conditions

| Situation | Behaviour |
|---|---|
| Request out of scope, unsafe, no change produced, bad diff | no commit; NEEDS_OWNER reply + `agent-needs-owner` label |
| Review names an older commit | ignored (`stale_review` in the log); ChatGPT re-reviews the new head |
| Same request seen again (rerun, crash after push) | skipped: commit trailer `Agent-Request` or an existing reply marks it handled |
| 3 corrections applied on a PR | NEEDS_OWNER (round limit) |
| Comment not posted via the pinned ChatGPT app, or wrong format | ignored |
| Identity/permission check fails, Claude not on subscription, overage active, push rejected | run exits 1 (`fatal` in log), nothing posted; next run retries the checks |
| Claude fails or times out | NEEDS_OWNER |

The owner resumes a stopped PR by removing `agent-needs-owner`.

## Disable

| Scope | Action |
|---|---|
| One PR | add label `agent-stop` |
| Runner, immediately | `touch ~/.config/agentloop/STOP` |
| Runner, permanently | `systemctl --user disable --now agentloop.timer` |
| All App access | GitHub → Settings → Applications → `odilorg-agentloop` → Suspend or Uninstall |
| Reviewer | disable the ChatGPT Work task |

## Limits

- **The reviewer acts as the owner.** ChatGPT Work's connector posts as `odilorg` with
  contents/workflows write. GitHub cannot stop it from approving or merging; only its task
  instructions do. Its actions are visible as `performed_via_github_app: chatgpt-codex-connector`.
- **Same-name check.** Any identity with workflow write (owner, ChatGPT connector) can add a
  `pull_request` workflow whose job is also named `agent-scope`. The App cannot (no workflow
  permission). Path-restriction push rules need an organization-owned repo.
- **Runs only while the owner's machine is on.** It catches up within 2 minutes of resuming.
- **Repositories need branch rules.** Private repos on the free plan have no rulesets or branch
  protection; there, nothing server-side would stop the App pushing to `main`.
