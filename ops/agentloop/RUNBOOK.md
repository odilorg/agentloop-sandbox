# agentloop runbook (sandbox pilot)

ChatGPT Work reviews `agent/*` pull requests in `odilorg/agentloop-sandbox`. The agentloop
runner applies the requested corrections through a restricted GitHub App, inside a sandboxed
system service. The owner alone approves and merges. The repository is pinned in code; the
runner refuses any other.

## How it works

```
agent/* PR opened or updated
  ├─ agent-scope (trusted, from main)  → PASS/FAIL check on the head commit
  └─ ChatGPT Work task                 → comment "AGENT-REVIEW v1 / Commit / Verdict / Request"
agentloop.timer (every 2 min) → agentloop@run.service
  stage 1, root, outside the sandbox (ExecStartPre=+):
       read /etc/agentloop/app.pem; prove App = odilorg-agentloop with only contents/
       pull_requests/issues; installation = owner, selected repos; mint a 1-hour token for the
       sandbox only; prove it cannot mint for jahongirnewapp; hand the token and the Claude
       subscription token to /run/agentloop (0600, agentloop)
  stage 2, user agentloop, inside the sandbox:
       1. STOP file in the inbox? → exit
       2. read both tokens into memory, delete the files
       3. for open agent/* PRs without agent-stop / agent-needs-owner:
          take the LATEST review comment that passes ALL of: user odilorg (id 20632218, type User),
          posted via app 1144995 chatgpt-codex-connector (owner openai), never edited, strict schema
          ESCALATE → NEEDS_OWNER + label; PASS / handled / older commit → skip
       4. usage gate: HALT file? daily cap (10 Claude runs/day, UTC)? → skip
       5. claude -p with the subscription token (tools Read/Edit/Write, Edit/Write only on
          docs/pilot/**, no shell, no MCP, fresh config dir each run)
       6. stream must show apiKeySource "none", isUsingOverage false, overageStatus "rejected";
          anything else → HALT file, exit, nothing pushed
       7. wrapper validates the diff: allowed paths, add/modify only, regular 100644 files,
          no binaries, ≤20 files, ≤200 lines, no credential-like text, none of the live tokens
       8. commit (Agent-Request/Round/Base trailers), normal push as the App, reply APPLIED
       rejection → NEEDS_OWNER reply + `agent-needs-owner` label
  at most one request per run; a lock prevents overlapping runs
new commit → agent-scope + ChatGPT re-review → PASS ends the loop
```

## Isolation (agentloop@.service)

| Layer | Setting | Effect |
|---|---|---|
| Identity | system user `agentloop`, no groups, no shell | not the owner; no sudo, docker or adm |
| Files | `ProtectHome=yes`, `ProtectSystem=strict`, `TemporaryFileSystem=/var/lib:ro`, `InaccessiblePaths=` /etc/agentloop /data /srv /var/www /boot, Docker/MySQL/Postgres/Redis paths | owner files, project `.env` files, SSH keys, App key and database data are invisible; everything else read-only |
| Network | `RestrictAddressFamilies=AF_INET AF_INET6`, `IPAddressDeny=` localhost, LAN, Tailscale, Docker ranges (DNS stub allowed) | Internet only: no local databases, Redis, SSH, router, other machines; no Unix sockets |
| Privilege | `NoNewPrivileges`, empty capability set, `@system-service` syscall filter, `ProtectProc=invisible`, kernel protections | cannot escalate or see other users' processes |
| Credentials | App key read only by the root stage; tokens handed over then deleted | the model's process never has the App key; the GitHub token is never in its environment |

`agentloop@isolation.service` proves this from the inside (`isolation.py`, 53 checks, including
a whole-filesystem sweep for readable credential-like files). It never reads a protected file.

## Files

| Path | Owner / mode | Contents |
|---|---|---|
| `/opt/agentloop/` | root 0755 | code + pinned copy of the Claude Code binary (no auto-update) |
| `/etc/agentloop.json` | root 0644 | repo, App id/slug, installation id, path rules — no secrets |
| `/etc/agentloop/app.pem` | root 0600 | App private key (written by `app_setup.py`) |
| `/etc/agentloop/claude-token` | root 0600 | Claude subscription token from `claude setup-token` |
| `/var/lib/agentloop/` | agentloop 0700 | work clone, usage counter, HALT |
| `/var/lib/agentloop-inbox/` | owner, agentloop read | `STOP`, `start.json` |
| `/var/log/agentloop/` | agentloop 0755 | `log.jsonl`, `selftest.json`, `isolation.json` (no secrets) |

## Install and operate (owner, sudo)

```
sudo ops/agentloop/install.sh                         # user, code, units; timer NOT enabled
sudo python3 ops/agentloop/app_setup.py               # once: create + install the App (browser)
claude setup-token                                    # in your own terminal, never in a chat
sudo ops/agentloop/install.sh --claude-token          # paste ALL of it (hidden input; wrapped lines are joined;
                                                      # under 100 characters is rejected as a partial copy)
sudo systemctl start agentloop@isolation              # sandbox proof
sudo systemctl start agentloop@whoami                 # identity checks
sudo systemctl start agentloop@selftest               # GitHub permission matrix
sudo systemctl enable --now agentloop.timer           # start the loop
journalctl -u 'agentloop@*' -n 50                     # what happened; also /var/log/agentloop/log.jsonl
```

Before the first Claude run: claude.ai → Settings → Usage → extra usage **off**. The runner
also checks this on every run and halts itself if Claude reports otherwise.

Start a task as the agent: write `{"branch": "agent/<name>", "title": "...", "request": "..."}`
to `/var/lib/agentloop-inbox/start.json`, then `sudo systemctl start agentloop@start`.

## Diagnostic probe (owner only, once)

`agentloop@probe` makes one tool-less `claude -p "Reply with the single word ok."` call in the
same unit, user and subscription environment: no repository, no GitHub token (the mint stage
hands over only the Claude token), no edits, no comments. It runs even while `HALT` exists and
never removes it; it still counts against the daily cap. It logs only fixed fields: exit code,
`is_error`, result subtype, CLI version, event-type counts, usage fields and a diagnostic
category (`oauth_expired`, `oauth_invalid`, `oauth_scope`, `http_401/403/429`, `credits`,
`fs_eacces`, `unsupported_flag`, `network`, `unknown`). Raw output, prompts and tokens are never logged.
Structured fields are bounded too: the CLI version must match a version pattern, and `apiKeySource`, the
result subtype, `overageStatus` and event names must be on fixed allowlists. Anything else is logged as
`unknown`/`other` and fails the success or subscription check; it is never treated as a passing value.

```
echo 'run one agentloop diagnostic probe' > /var/lib/agentloop-inbox/probe-request
sudo systemctl start agentloop@probe
journalctl -u agentloop@probe -o cat | grep '"event": "probe"'
```
A second probe needs the owner to delete `/var/lib/agentloop/probe-done` first.

## Failures and stopping conditions

| Situation | Behaviour |
|---|---|
| Reviewer says ESCALATE | NEEDS_OWNER reply + `agent-needs-owner` label, on any commit |
| Request out of scope, unsafe, no change produced, bad diff, credential-like text | no commit; NEEDS_OWNER + label |
| Review names an older commit | ignored (`stale_review`); ChatGPT re-reviews the new head |
| Same request again (rerun, crash after push) | skipped: `Agent-Request` trailer or an existing reply marks it handled |
| 3 corrections applied on a PR | NEEDS_OWNER (round limit) |
| Comment from another user, other app, edited, or not matching the schema | ignored (counted in the `idle` log line) |
| Claude exit ≠ 0, `is_error` not false, or subtype ≠ success | output rejected before validate/commit/push; NEEDS_OWNER (`run`) or fatal (`start`) with the diagnostic category |
| 10 Claude runs today | skipped until the next UTC day (`deferred`) |
| Claude not on subscription, overage in use, extra usage on, or usage not reported | HALT file; nothing more runs until the owner deletes `/var/lib/agentloop/HALT` |
| No Claude token | run fails closed; there is no fallback credential |
| Identity/permission check fails, push rejected | unit fails (`fatal`), nothing posted |

The owner resumes a stopped PR by removing `agent-needs-owner`.

## Disable

| Scope | Action |
|---|---|
| One PR | add label `agent-stop` |
| Runner, immediately | `touch /var/lib/agentloop-inbox/STOP` |
| Runner, permanently | `sudo systemctl disable --now agentloop.timer` |
| Everything on this machine | `sudo ops/agentloop/install.sh --uninstall` |
| All App access | GitHub → Settings → Applications → `odilorg-agentloop` → Suspend or Uninstall |
| Claude token | claude.ai → Settings → revoke the setup token |
| Reviewer | disable the ChatGPT Work task |

## Limits

- **The reviewer acts as the owner.** ChatGPT Work's connector posts as `odilorg` with
  contents/workflows write. GitHub cannot stop it from approving or merging; only its task
  instructions do. The runner accepts its comments only when they carry the connector app tag.
- **Same-name check.** Any identity with workflow write (owner, ChatGPT connector) can add a
  `pull_request` workflow whose job is also named `agent-scope`. The App cannot (no workflow
  permission). Path-restriction push rules need an organization-owned repo.
- **The model still holds its own Claude token** (it needs it to run). It has no shell or
  network tool, and the wrapper refuses diffs containing that token or credential-like text.
- **Runs only while this machine is on.** It catches up within 2 minutes of resuming.
- **Repositories need branch rules.** Private repos on the free plan have no rulesets or branch
  protection; there, nothing server-side would stop the App pushing to `main`.
