# CI03-GATES: verification gates

Status: proposed. Depends on the merged `scripts/check.sh`. Used by: every other phase.

## Goal

One list of checks, run the same way by a contributor and by CI, and one clear statement of what a merge requires.

## Merge gate

- **Protection on `main`, read back on 2026-10-02.** The `validate` workflow (`.github/workflows/validate.yml`) is the
  one required check. The branch must be up to date with `main`, the rule applies to administrators, and no approving
  review is required (see G3).
- **Exact commits.** Merge evidence names four commits:
  - the pull request's head commit;
  - the test-merge commit that GitHub checked, when it builds one;
  - the commit the check run reports;
  - the base commit.

  A result for any other commit is stale.

## Two routes

- **Execution:** `./scripts/check.sh` runs the gates.
- **Read-back:** `gh pr checks` reads the result for the exact head.

One does not replace the other.

## Existing gates

These stay. Each one moves behind `./scripts/check.sh` without losing its guarantee.

- **Branch protection** enforces the `validate` workflow.
- **`../../tests/python/test_validate_workflow_policy.py`** checks that the static steps exist and that live acceptance
  is unconditional and runs first.
- **`tools/check_live_acceptance.py`** checks that the receipt's digest equals the skill digest.
- **The byte-equality test in `../../tests/python/test_catalog.py`** checks that `tools.json` equals the catalog.

## Gaps

Each gap names the requirement, the failure it prevents, and the smallest change that closes it.

### G1. One entrypoint for local runs and CI

- **Requirement.** Local runs and CI invoke the same repository-owned entrypoint. The pinned standards' workspace index
  names `scripts/check.sh` as the gate entrypoint.
- **Existing mechanism.** The merged `scripts/check.sh` and `lib/ci/check.bash` check tracked shell, YAML and Markdown,
  scan for secrets and run the Bats suite. The script takes `--dry-run`, `--log-format` and `--help`, and exits 0, 64
  or 69.
- **Smallest change.** Extend that script instead of adding a second one:
  - add the checks `validate` runs today, plus the `schemas` gate (CI-SCHEMA), the `cli` gate (CI02-CLI) and, after
    CI-VENDOR, `verify`;
  - add a way to run one gate, or the static subset, for the hooks;
  - make each `validate.yml` step call it;
  - extend `../../tests/python/test_validate_workflow_policy.py` so that every gate is called and live acceptance stays
    unconditional and first.

### G2. Exact tool versions

- **Requirement.** CI and local runs use the same tool versions.
- **Failure today.** `requirements.txt` holds ranges (`ruff>=0.13,<1`), so CI and a contributor can format differently.
- **Smallest change.** Pin exact versions, taken from what a `validate` run resolves. That includes `check-jsonschema`
  (CI-SCHEMA) and coverage.py (CI-TESTS).

### G3. A review that blocks a merge

- **Requirement.** Every phase pull request is reviewed against its exact head commit before merge.
- **Failure today.** No approving review is required, so a missing or stale review blocks nothing.
- **Smallest change.** One of the following, read back after it is set (the choice is open):
  - require an approving review on `main`;
  - make a review status, posted by the reviewer for the exact head commit, a required check.

### G4. Vendored skills checked on every change

- **Requirement.** A change to a vendored skill is always verified (CI-VENDOR).
- **Failure today.** A change to Markdown files alone skips the gated workflow steps, and a vendored skill is almost all
  Markdown.
- **Smallest change.** The `verify` gate runs unconditionally, under the same assertions the workflow-policy test
  applies to live acceptance.

### G5. Release receipt host

- **Requirement.** CI-ROUTING needs a new live receipt for the exact skill digest, captured on the executor that
  `../../tests/acceptance` declares.
- **Conflict.** That executor is a laptop, and the project's guide says a laptop is not release evidence.
- **Smallest change.**
  1. Name an approved executor and its capture route.
  2. Update `../../tests/acceptance` to declare it.
  3. Capture the receipt there, and read it back.

### G6. The pinned standards' CI evidence model

- **Requirement.** `standards-binding.yaml` requires the `ci` and `smoke-testing` contracts, with `exceptions: []`. The
  pinned binding schema allows only stricter or temporary-block exceptions, so this model is required: it cannot be
  waived or partly adopted.
- **Failure today.** `validate` has none of the following:
  - one final aggregator that runs even when earlier steps fail, and decides the required result;
  - per-job evidence under `reports/ci/<job>.json` (commit, standards revision, runner digest, warning and skip counts);
  - a smoke inventory, `inventory/ci/smoke-tests.yaml`, listing every required job, with a wiring smoke per job;
  - a status post and read-back;
  - zero skipped required tests. Today the dependency install, Ruff and pytest steps are skipped on a Markdown-only
    change, which counts as skipping required tests;
  - no ad hoc tool installs inside a required job.
- **Smallest change.** Make `validate` end in one aggregator step that checks every required evidence record and fails
  on any that is missing, skipped, warning-bearing or for the wrong commit.
- **The pin is incomplete in two places:** it has no result schema, and its example `--profile merge` command is not
  accepted by its own reference `scripts/check.sh`. Both are raised with the shared standards' owners; they do not
  excuse the gap.

### G7. A scheduled check of `main`

- **Requirement.** The routine also checks `main`, not only pull requests.
- **Failure today.** `validate` runs on pull requests and on pushes to `main` only. The committed receipt expires on
  2026-11-01T22:35:26Z. After that, `main` and every pull request fail, and nothing warns before then.
- **Smallest change.**
  - A `schedule` trigger on the same `validate` workflow, which GitHub runs on `main`. This adds no new workflow.
  - `tools/check_live_acceptance.py` reports the days left before the receipt expires, so each scheduled run shows it.
- **Read-back.** `gh run list --workflow validate --branch main --event schedule` shows one run per day.

### G8. Test execution route

- **Requirement.** Tests run on the approved CI surface, never on a laptop.
- **Today.** `validate` runs tests on GitHub-hosted runners. The root gate runs its live checks only inside a Kubernetes
  pod, and the project's agent guide asks for Kubernetes CI.
- **Open.** The route from the GitHub check to that pod job is not defined. It has to be named before a pod-only gate
  can feed `validate`.

## Local and CI

- **Local:** static checks, through the hooks (CI04-HOOKS), and advisory only.
- **CI only:** tests and live checks. A local result never replaces `validate`.

## Steps

1. Extend `scripts/check.sh` as in G1. The new checks live in `lib/ci/check.bash`, and `--help` lists every argument and
   output mode.
2. Switch the `validate.yml` steps to call it, add the final aggregator (G6) and the `schedule` trigger (G7), and extend
   the workflow-policy test.
3. Pin exact tool versions (G2).
4. Open one pull request; the `validate` workflow must pass.
5. Read back:
   - `./scripts/check.sh --dry-run` lists every gate;
   - the pull request's checks show each gate and the aggregator;
   - a scheduled run on `main` appears the next day.
