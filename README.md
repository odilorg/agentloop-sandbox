# agentloop-sandbox

A **disposable** public sandbox for testing an automated review loop between
ChatGPT Work (reviewer) and a Claude Code Routine (implementer). The owner-approved
design is tracked privately.

- **Synthetic content only.** No production code, data, credentials or secrets, now or later.
- Nothing here is deployed or used by any real system.
- **Branch rules are planned, not yet in force.** Until they are applied and verified,
  `main` is not protected. Once applied: only the owner merges, and the agent may write only
  to `agent/**` branches.
- When the pilot ends, this repository is archived or deleted.

## Layout

| Path | Purpose |
|---|---|
| `docs/specs/` | Owner-approved specs. A spec counts as approved only once it is merged to `main`. |
| `docs/pilot/` | The only files an agent may change, per the spec. |
| `.github/workflows/agent-scope.yml` | Trusted scope check. Runs from `main` (`pull_request_target`); never checks out or runs PR code. |
| `.github/CODEOWNERS` | The owner reviews everything. |

## Off switches

| Switch | Stops |
|---|---|
| Turn the Routine off (or delete it) | All agent runs, whatever identity they use |
| Uninstall the Claude GitHub App from this repo | Routine triggers and Routine access to this repo |
| Disable the ChatGPT Work task | New reviews and fix requests |
| Remove the machine account's access | Only actions taken **as the machine account**. It does **not** stop a Routine that acts through the owner's own GitHub connection; use the first two switches for that |
| Archive the repo | All writes |
