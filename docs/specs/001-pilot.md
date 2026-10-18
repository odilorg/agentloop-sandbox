# Spec 001 — sandbox pilot: Markdown corrections

| Field | Value |
|---|---|
| Status | Approved once merged to `main` by the owner |
| Risk | low |
| Allowed paths | Paths matching exactly `docs/pilot/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*\.md`. This is the same expression `agent-scope` enforces. Only **added** or **modified** regular files with a textual diff |
| Limits | At most 20 files and 200 changed lines per PR |
| Forbidden | Everything else, including renames, deletions, `.github/`, `.claude/`, `CLAUDE.md`, `.mcp.json`, `.gitmodules`, `docs/specs/` |
| Tests | none (Markdown only) |
| Max applied corrections per PR | 3 (up to 4 reviews) |

## Outcome
An agent applies a reviewer's concrete, in-scope Markdown change requests to
`docs/pilot/sandbox.md` on the PR's own `agent/*` branch, then replies with the commit SHA.

## Invariants
1. Only files matching the allowed paths change. No deletions, renames, symlinks, submodules or binaries.
2. Changes go only to the PR's existing `agent/*` branch, as an ordinary fast-forward push. Never `main`, never a new branch, never a force push.
3. A request is applied at most once. Its commit carries `Agent-Request: <comment id>` and `Agent-Round: <n>`.
4. Requests come only from the reviewer app's `AGENT-REVIEW v1` comments with `Verdict: FIX` whose `Commit:` equals the current PR head.
5. `Suggestions` and out-of-scope requests are never applied. They are escalated to the owner.

## Acceptance
- The requested text appears exactly, the reply names the new SHA, and the `agent-scope` check passes.
- Requests that violate an invariant produce a `NEEDS_OWNER` comment and no commit.
