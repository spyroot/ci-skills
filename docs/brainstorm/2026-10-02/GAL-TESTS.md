# CI-TESTS adversarial review

Target: PR #11, head `3045bb62769e85919b765b6f6c3870464d861cc1`.
The claimed 174 test functions in 22 files matches the current tree. This is
a plan review; the new tests and gates have not run.
Head continuity: the next PR head, `e60cc60613ddf0f606938984312cbed29edb8515`,
adds CI-REFERENCE and a CI-PHASES link; CI-TESTS is unchanged. Its `validate`
check passed, but the findings below remain open.

## Findings

- **P1 — Six coverage checks are not the full required test matrix.**
  `CI-TESTS.md:15-22` calls its six coverage categories the full checklist,
  but the pinned `unit-testing` contract separately requires default dry-run,
  explicit apply confirmation, plan/fingerprint validation, missing-tool and
  environment handling, bounded retries, result schemas, redaction, signal
  and timeout cleanup, and a no-op second execution. CI-VENDOR's `update`
  and CI-CATALOG's `install` interfaces offer optional `--dry-run` but no
  confirmation. Align the CLI defaults and add the applicable pinned cases;
  the six categories remain the minimum coverage gate, not a substitute.
- **P1 — Existing installer idempotency is misstated.** `CI-TESTS.md:56-64,
  141-142` calls its second install idempotent while specifying a refusal.
  The current installer refuses any existing destination
  (`install_k8s_admin_diagnostics.py:67-75`). The pinned unit-testing
  contract requires same-state re-execution to be a read-back no-op without
  writes. Define same-digest no-op and different-digest refusal, then test a
  real two-run path. CI-CATALOG's compatibility plan must agree.
- **P1 — Hook installer misses its own six-check rule.** `:15-22` names hook
  installation as a mutating entrypoint, but `:179-184` covers only dry-run,
  placement and idempotency. Add unsafe-apply refusal, negative path,
  cleanup and independent read-back, including an existing target, failed
  link, linked worktree and global dispatcher.
- **P1 — Pre-push has no fail-closed negative test.** `:172-178` tests
  pre-commit exits 1, 2 and 127, but only checks that pre-push calls live
  acceptance. Test those exit codes and a denied read-back for pre-push, and
  prove no push proceeds. This is required by `CI-HOOKS.md:28-29,45-52`.
- **P2 — The fake-command helper points at the wrong entrypoint.**
  `:23-25` says `conftest.run_script` can drive fake `glab`, but that helper
  invokes only the k8s skill's scripts (`tests/conftest.py:86-116`). Add a
  shared runner for `tools/ci_skills.py`, or use an explicit bounded
  subprocess fixture with fake PATH.
- **P2 — Coverage measurement has no execution route.** `:48-49` promises
  coverage.py output, but `requirements.txt`, `validate.yml` and CI-GATES
  define no pinned coverage dependency, command or report step. Define them
  before claiming measured coverage; a threshold remains an optional choice.
- **P2 — Automatic agent mutations are unclassified.** `:19-22` omits the
  agent hooks in `CI-HOOKS.md:68-86` that run `ruff format` and manifest
  rendering. If delivered, cover their mutation and recovery paths; if not,
  mark them explicitly outside the phase deliverable.

## Gate continuity

The existing Markdown-only workflow path skips the pytest steps; `CI-TESTS`
counting zero pytest skip markers does not satisfy the pinned CI requirement
for zero skipped required tests. The approved Kubernetes execution route and
the invalid G6 exception choice remain in the CI-GATES review. `bats` must
come from an approved pinned runner/toolbox; the binding schema does not allow
an exception that weakens a required gate.
