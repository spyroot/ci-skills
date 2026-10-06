# CI04-HOOKS: local hooks

Status: proposed; none installed. Depends on: CI03-GATES for the entrypoint,
and CI-VENDOR for its `verify` gate.

## Goal

Warn a contributor before a commit or push about what the `validate`
workflow would reject, using the entrypoint CI runs. Hooks are advisory:
`validate` is the merge gate, and a local `PASS` never replaces it.

## Block

| Part | Value |
| --- | --- |
| Capability | run the static gates before a commit or push |
| Owner | the hook scripts under `scripts/hooks/` |
| Entrypoint | `./scripts/check.sh` (PR #2, extended in CI03-GATES) |
| Result | the script's result: `PASS`, or the failing gate |
| Read-back | a failing staged file is refused, naming its gate |

## Rules

- **Same entrypoint as CI.** Hooks call `./scripts/check.sh`, the entrypoint
  CI calls (CI03-GATES, G1), with the same pinned tool versions.
- **Static checks only.** That means lint, format check, secret scan,
  neutrality and generated-file checks. Tests and live checks run in CI.
- **Check what will be committed.** Checks run against a snapshot of the
  index (`git checkout-index --all --prefix=<temporary dir>/`), not the
  working tree. An unstaged fix therefore cannot hide a staged defect.
- **Fail closed.** A hook refuses unless the result is `PASS`. Exit codes 1,
  2, 64 and 69, and a crash, all refuse.
- **No rewriting.** Hooks never rewrite files. A failing hook names the
  gate and the command to run.

## Git hooks

### pre-commit

- The static subset of `./scripts/check.sh`, run on the index snapshot, so
  neutrality sees exactly what the commit will contain.
- It also refuses staged agent instruction files: `CLAUDE*.md`,
  `AGENT*.md`, `CODEX*.md`, `TEAM_GUIDE.md`, `.claude/`, `.codex/`,
  `.coordination/` and `.agent-review/`. This repository never commits them.
  This is the one hook with no CI step.

### pre-push

- The live-acceptance gate, run on the commit being pushed after it is
  exported to a temporary directory, as CI runs it.
  - It fails on any skill edit until a new receipt is committed
    (CI-ROUTING).
  - It fails on every push once the committed receipt is older than
    `max_receipt_age_days` in `tests/acceptance`.
  - Running it at push rather than at commit keeps intermediate commits
    possible.

### commit-msg

- Left to whatever global hook a contributor already runs.

## Installing

- **Where.** Into `$(git rev-parse --git-common-dir)/hooks/`, which also
  serves linked worktrees. Not into `git rev-parse --git-path hooks`: when a
  global `core.hooksPath` is set, that returns the global hook directory.
  A global dispatcher that delegates per repository reads
  `<git-common-dir>/hooks/<name>`.
- **Contract (CI02-CLI).** The installer:
  - has `--help`;
  - plans by default and installs only with `--confirm`;
  - refuses to replace a hook it did not install, with `hook_exists` and a
    safe next step;
  - does nothing on a second run;
  - reads back that each hook is in place and executable, and that
    `core.hooksPath` was not set.
- **Never set a repository-local `core.hooksPath`.** It overrides the global
  one, and the global hooks, such as a commit-message check, stop running.

## Agent-harness hooks

These are outside this phase's deliverable. They live in each agent's local
settings, which this repository does not track. Recommended, for coding
agents that run hooks before and after an edit (such as Claude Code's
`PreToolUse` and `PostToolUse`):

- **Block edits to generated or copied files:**
  - `tools.json`
  - `tests/acceptance`
  - the vendored skill trees
  - `vendor/vendor.lock.json`
- **Keep `vendor/vendor.toml` editable;** it holds the hand declarations.
- **After a `*.py` edit:** run `ruff format` on that file.
- **After an edit to `ci-skills/lib/core/catalog.py`:** run
  `tools/render_manifest.py`.

A hook's matcher selects the tool, such as `Edit|Write` or `Bash`; the hook
command itself checks the file path or command line.

## Steps

1. After CI03-GATES lands, add `scripts/hooks/pre-commit` and
   `scripts/hooks/pre-push`. Both call `./scripts/check.sh`.
2. Add the installer, with the contract above.
3. Open one pull request; the `validate` workflow must pass.
4. Read back:
   - a commit that stages a failing file is refused, even when the working
     tree is fixed;
   - the installer reports each hook in place.
