# GAL-HOOKS: local hooks

Status: proposed; none installed. Depends on: GAL-GATES, and GAL-VENDOR for
the vendored-skill gate.

## Goal

Catch before a push what the `validate` workflow would reject, by running
the same entrypoint that CI runs.

## Block

| Part | Value |
| --- | --- |
| Capability | run the static gates before a commit or push |
| Owner | the hook scripts under `scripts/hooks/` |
| Entrypoint | `./scripts/check.sh` (GAL-GATES) |
| Result | the script's result: `PASS`, or the failing gate |
| Read-back | a failing staged file is refused, naming its gate |

## Rules

- **Same entrypoint as CI.** Hooks call `./scripts/check.sh`, the
  entrypoint CI calls (GAL-GATES, G1), with the same pinned tool versions.
- **Static checks only.** That means lint, format check, secret scan,
  neutrality and generated-file checks. Tests run in CI, and a local result
  never replaces `validate`.
- **Fail closed.** A hook refuses the commit or push unless the result is
  `PASS`, so a check that reports `BLOCKED` or crashes also refuses.
- **No rewriting.** Hooks never rewrite files. A failing hook names the
  gate and the command to run.

## Git hooks

### pre-commit

- `./scripts/check.sh --profile static`. It includes the neutrality check,
  which also scans untracked files that are not ignored, so a stray
  untracked file blocks the commit.
- It also refuses staged agent instruction files: `CLAUDE*.md`,
  `AGENT*.md`, `CODEX*.md`, `TEAM_GUIDE.md`, `.claude/`, `.codex/`,
  `.coordination/` and `.agent-review/`. This repository never commits them.
  This is the one hook with no CI step.

### pre-push

- `./scripts/check.sh --gate live-acceptance`. Unlike CI, which reads the
  pushed commit, it reads the working tree. It fails on any skill edit
  until a new receipt is committed (GAL-ROUTING), and on every push once
  the committed receipt is older than `max_receipt_age_days` in
  `acceptance/expected.toml`. Running it at push rather than at commit
  keeps intermediate commits possible.

### commit-msg

- Left to whatever global hook a contributor already runs.

## Installing

- The hooks are tracked scripts under `scripts/hooks/`. One install step
  links them into the directory that `git rev-parse --git-path hooks`
  reports, which also works in a linked worktree.
- If git's `core.hooksPath` points at a global dispatcher that delegates to
  that directory, the linked hooks run under it.
- Never set a repository-local `core.hooksPath`. It overrides the global
  one, and the global hooks, such as a commit-message check, stop running.

## Agent-harness hooks

These apply to coding agents that run hooks before and after an edit, such
as Claude Code's `PreToolUse` and `PostToolUse`. They live in each agent's
local settings, which this repository does not track. A hook's matcher
selects the tool, such as `Edit|Write` or `Bash`; the hook command itself
checks the file path or the command line.

- **Block edits to generated or copied files:**
  - `tools.json`
  - `acceptance/receipts/*.json`
  - the vendored skill trees
  - `skills/vendor.lock.json`

  `skills/vendor.toml` stays editable; it holds the hand declarations.
- **After editing a `*.py` file:** run `ruff format` on that file.
- **After editing `scripts/core/catalog.py`:** run
  `tools/render_manifest.py`.
- **Before `git commit`:** run `./scripts/check.sh --profile static` and
  block unless it reports `PASS`.

## Steps

1. After GAL-GATES lands, add `scripts/hooks/pre-commit` and
   `scripts/hooks/pre-push`. Both call `./scripts/check.sh`.
2. Add the install step that links them into the hooks directory.
3. Open one pull request; the `validate` workflow must pass.
4. Read back: a commit that stages a file failing any static gate is
   refused, and the hook names the failing gate.
