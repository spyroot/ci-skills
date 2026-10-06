# CI04-HOOKS: local hooks

Status: proposed. Order and dependencies: CI10-PHASES, Phases.

## Goal

Warn a contributor before a commit or push about what the gate route would
reject, using the one entrypoint that route runs (`./scripts/check.sh`,
CI03-GATES, G1). Hooks are advisory (D-GATE, decided 2026-10-06): they are
never the merge gate, a local `PASS` never replaces the route (CI03-GATES,
G0), and no route exists today. No workflow exists either: `validate` was
deleted in #27 (`1108cca`; CI10-PHASES, Pull request status).

## Block

| Part | Value |
| --- | --- |
| Capability | run the static gates before a commit or push |
| Owner | the hook scripts under `scripts/hooks/` (planned, CI04-HOOKS) |
| Entrypoint | `./scripts/check.sh`; its library returns in block 0 (CI10-PHASES, Order) and grows in CI03-GATES, G1 |
| Result | the script's result: `PASS`, or the failing gate |
| Read-back | a failing staged file is refused, naming its gate |

## Rules

- **Same entrypoint as the gate route.** Hooks call `./scripts/check.sh`,
  the entrypoint the route calls (CI03-GATES, G1), with the same pinned
  tool versions (CI03-GATES, G2).
- **Static checks only.** That means lint, format check, secret scan,
  neutrality and generated-file checks. Tests and live checks run on the
  gate route (CI03-GATES, G0; none as of 2026-10-06), never in a hook.
- **Check what will be committed.** Checks run against a snapshot of the
  index (`git checkout-index --all --prefix=<temporary dir>/`), not the
  working tree. An unstaged fix therefore cannot hide a staged defect.
- **Fail closed.** A hook refuses unless the result is `PASS`. Exit codes 1,
  2, 64 and 69, and a crash, all refuse. A refusal stops only the commit or
  push it ran in; towards the merge it is advisory (D-GATE).
- **No rewriting.** Hooks never rewrite files. A failing hook names the
  gate and the command to run.

## Git hooks

### pre-commit

- The static subset of `./scripts/check.sh`, run on the index snapshot, so
  neutrality sees exactly what the commit will contain. The selector for
  that subset is the one CI03-GATES, G1 adds. Today the script's library,
  as last committed before #26 and restored by block 0 at
  `ci-skills/lib/bash/ci/check.bash` (planned, CI10-PHASES, Order), takes
  only `--dry-run`, the `--log-*` flags and `--help`, and outside
  `--dry-run` it refuses to run off a Kubernetes pod (`tests/bash/check.bats`,
  "check refuses execution outside Kubernetes"). Until G1 lands, a hook
  has nothing it can run.
- It also refuses staged agent instruction files, the patterns the
  repository `.gitignore` lists as never committed: `.claude/`, `.codex/`,
  `.agent-review/`, `AGENT*.md`, `CLAUDE*.md`, `CODEX*.md` and
  `TEAM_GUIDE.md`. Our global ignore file (`core.excludesFile`,
  `~/.gitignore_global`) adds `CODEX_HANDOFF.*`, `CLAUDE_REVIEW.*`,
  `CLAUDE_PATCH.diff`, `.AGENTS.md` and `.AGENT_HANDOFF.*`.
  `.coordination/` is not on that list: it is tracked by decision (three
  files, `git ls-files .coordination`), and the hook never refuses it. This
  refusal is the one check with no counterpart on the gate route.

### pre-push

- The live-acceptance gate, run on the commit being pushed after it is
  exported to a temporary directory, the way the gate route runs it once one
  exists (CI03-GATES, G0).
  - It fails on any skill edit until a new receipt is committed
    (CI08-ROUTING).
  - It fails on every push once the committed receipt is older than
    `max_receipt_age_days` in `tests/acceptance/expected.toml`.
  - Today it would fail every push: the committed receipts no longer match
    the skill digest (CI10-PHASES, Pull request status). That is one more
    reason this phase lands last.
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
  The tracked `Makefile` breaks this rule: its `install-hooks` target, which
  `make`, `make install` and `make bless` all reach, runs
  `git config --local core.hooksPath .githooks` (line 49), and no
  `.githooks/` directory exists, so after it no hook runs at all. Read back
  2026-10-06: the local `core.hooksPath` is unset. The target cannot stay as
  it is once the installer exists (Steps, 2).
- **Nothing is installed until a route exists.** No `scripts/hooks/` exists
  today. This phase is last (CI10-PHASES, Order, item 9) and installs no
  hook until CI03-GATES's route exists (G0, D-GATE, decided 2026-10-06).

## Agent-harness hooks

Agent-harness hooks (for example Claude Code `PreToolUse`/`PostToolUse`) are not tracked here. If used: block edits to
`ci-skills/tools.json`, `tests/acceptance/`, the vendored trees and `vendor/vendor.lock.json` (planned, CI05-VENDOR);
run `ruff format` after a `*.py` edit and `tools/render_manifest.py` after a `ci-skills/lib/core/catalog.py` edit.

## Steps

1. After CI03-GATES lands and its route exists (G0), add
   `scripts/hooks/pre-commit` and `scripts/hooks/pre-push`. Both call
   `./scripts/check.sh`.
2. Add the installer, with the contract above, and resolve the `Makefile`
   `install-hooks` target (Installing): remove it, or point it at the
   installer.
3. Open one pull request; merge per CI10-PHASES, How a phase lands.
4. Read back:
   - a commit that stages a failing file is refused, even when the working
     tree is fixed;
   - the installer reports each hook in place, and `core.hooksPath` unset.

## Delivery, test and proof

1. *Delivery*: `scripts/hooks/pre-commit` and `scripts/hooks/pre-push`
   (planned, CI04-HOOKS); the installer; the `Makefile` `install-hooks`
   target (line 49); the two test files in part 2. The installer's path and
   its library file are not fixed here: CI02-CLI names new commands
   `bin/ci-<noun>` and the layout puts Bash libraries under
   `ci-skills/lib/bash/` (CI10-PHASES, "Layout, decided"), so this phase's
   pull request fixes both (owner: CI04-HOOKS). The commands that run:
   - pre-commit: `git checkout-index --all --prefix=<temporary dir>/`, then
     `./scripts/check.sh` in that directory with the static-subset selector
     CI03-GATES, G1 adds; the selector is not named yet, and the gap is G1's;
   - pre-push, where `$export` holds the pushed commit and `python` is the
     `ci-skills` conda environment's (CI10-PHASES, Publish and install):

     ```text
     python tools/check_live_acceptance.py --root "$export" \
       --expected "$export/tests/acceptance/expected.toml" \
       --receipts "$export/tests/acceptance/receipts" \
       --skill "$export/ci-skills" --json
     ```

   - the installer: a plan run with no arguments, then the same run with
     `--confirm`.
2. *Tests*, written with the block; run status UNVERIFIED (CI03-GATES, G0):
   the pytest and bats suites are never run on the laptop (D-GATE).
   - `tests/bash/hooks.bats` (planned, CI04-HOOKS; named like `check.bats`
     and `install.bats`), in temporary git repositories, every case
     CI06-TESTS lists for this phase:
     - a staged defect with an unstaged fix is refused, because the checks
       run on the index snapshot;
     - pre-commit and pre-push both refuse on exits 1, 2, 64, 69 and 127
       from the entrypoint, and the commit or push does not proceed;
     - a staged agent instruction file is refused: fixture `CLAUDE.md`,
       staged with `git add -f`, and the refusal names it;
     - a clean commit passes: the entrypoint exits 0 and the commit
       proceeds;
     - a refusal is advisory (D-GATE): it stops only that commit or push
       and leaves the index, the working tree and the git configuration
       unchanged.
   - `tests/bash/hooks_install.bats` (planned, CI04-HOOKS), the installer's
     full matrix (CI06-TESTS, Rules: planning, environment, results,
     cleanup, read-back), including:
     - an existing hook it did not install gives `hook_exists`;
     - a failed link cleans up and fails;
     - in a linked worktree it installs into the common directory;
     - with a global dispatcher present, delegation reads back;
     - `core.hooksPath` stays unset;
     - a second run is a no-op that writes nothing.
   - No Python test: the hooks this phase adds are shell and call existing
     tools. Should the installer become a Python main under CI02-CLI, its
     matrix moves to `tests/python/test_<module>.py` with the same cases.
   - Agent-harness hooks are outside the deliverable, so they have no tests
     here (CI06-TESTS).
3. *Smoke*: this phase changes no live behaviour. The static read-back, on
   this laptop, after the installer ran with `--confirm`:
   - `git config --local core.hooksPath` prints nothing and exits 1 (unset,
     as read back on 2026-10-06);
   - `ls -l "$(git rev-parse --git-common-dir)/hooks/"` lists an executable
     `pre-commit` and `pre-push`;
   - one refusal reproduced in a temporary repository with the hooks
     installed: `git add -f CLAUDE.md` beside one clean staged file, then
     `git commit -m fixture` exits non-zero and names `CLAUDE.md`; after
     `git rm --cached CLAUDE.md` the same commit proceeds.
4. *Evidence*: no receipt. This phase adds no live check and changes no byte
   under `ci-skills/`, so the skill digest and the committed receipts are
   untouched. The static evidence is the output of these commands:
   - `git config --local core.hooksPath` (no output, exit 1);
   - `ls -l "$(git rev-parse --git-common-dir)/hooks/"`;
   - the refusal transcript from part 3;
   - the installer's second run, reporting a no-op;
   - `./scripts/check.sh --dry-run` (exit 0, the planned gate list;
     CI03-GATES, Steps, read back).
5. *Verification*: the checker and its expected line, unchanged by this
   phase because no skill byte changes:

   ```text
   python tools/check_live_acceptance.py --root . \
     --expected tests/acceptance/expected.toml \
     --receipts tests/acceptance/receipts --skill ci-skills
   ```

   expected `Live acceptance: PASS` with exit 0. Today it is not `PASS`: the
   committed receipts no longer match the skill digest (CI10-PHASES, Pull
   request status), and only a fresh receipt changes that, not this phase.
