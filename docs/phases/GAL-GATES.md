# GAL-GATES: verification gates

Status: proposed. Depends on: nothing. Used by: every other phase.

## Goal

One list of checks, run the same way by a contributor and by CI, and one
clear statement of what a merge requires.

## Merge gate

- `main` is protected. The `validate` workflow
  (`.github/workflows/validate.yml`) is the one required check; the branch
  must be up to date with `main`, and the rule applies to administrators.
- A merge needs `validate` green for the exact head commit of the pull
  request. A result for any other commit does not count.

## Existing gates

These stay, and nothing below replaces them.

- **Branch protection** enforces the `validate` workflow.
- **`tests/test_validate_workflow_policy.py`** checks that the static steps
  exist and that live acceptance is unconditional and runs first.
- **`tools/check_live_acceptance.py`** checks that the receipt's digest
  equals the skill digest.
- **The byte-equality test in `tests/test_catalog.py`** checks that
  `tools.json` equals the catalog.

## Gaps

Each gap names the requirement, the failure it prevents, and the smallest
change that closes it.

### G1. One entrypoint for local runs and CI

- **Requirement.** Local runs and CI invoke the same repository-owned
  entrypoint. The pinned standards' workspace index names `scripts/check.sh`
  as the gate entrypoint.
- **Existing mechanism.** PR #2 (draft) adds `scripts/check.sh` and
  `lib/ci/check.bash`. It checks tracked shell, YAML and Markdown, scans for
  secrets and runs the Bats suite. It takes `--dry-run`, `--log-format` and
  `--help`, and exits 0, 64 (usage) or 69 (blocked).
- **Smallest change.** Extend that script instead of adding a second one:
  - add the checks `validate` runs today: Ruff, neutrality, live
    acceptance, and, after GAL-VENDOR, vendored-skill `verify`;
  - add a way to run one gate, or the static subset, for the hooks;
  - make each `validate.yml` step call it, and extend
    `tests/test_validate_workflow_policy.py` so that live acceptance stays
    unconditional and first.
- **Conflict to settle.** PR #2 runs its live gate only inside a Kubernetes
  pod, while `validate` runs on a GitHub-hosted runner. Either the script
  also accepts the GitHub runner, or `validate` dispatches to the pod job.

### G2. Exact tool versions

- **Requirement.** CI and local runs use the same tool versions.
- **Failure today.** `requirements.txt` holds ranges
  (`ruff>=0.13,<1`), so CI and a contributor can format differently.
- **Smallest change.** Pin exact versions, taken from what a `validate` run
  resolves; the registry names the pinned binaries.

### G3. Review of the exact head commit

- **Requirement.** Every phase pull request is reviewed against its exact
  head commit before merge.
- **Failure today.** Nothing records which commit a review covered.
- **Smallest change.** The review names the head commit; a new commit
  needs a new review of what changed.

### G4. Vendored skills checked on every change

- **Requirement.** A change to a vendored skill is always verified
  (GAL-VENDOR).
- **Failure today.** A change to Markdown files only skips the gated
  workflow steps, and a vendored skill is almost all Markdown.
- **Smallest change.** The `verify` gate runs unconditionally, under the
  same assertions the workflow-policy test applies to live acceptance.

### G5. Release receipt host

- **Requirement.** GAL-ROUTING needs a new live receipt, captured on the
  executor that `acceptance/expected.toml` declares.
- **Open decision.** Whether that executor may serve as release evidence,
  or which host should.

### G6. The pinned standards' CI evidence model

The pinned `ci` and `smoke-testing` contracts require several things that
`validate` does not do today:

- one final aggregator that decides the required result;
- per-job evidence under `reports/ci/<job>.json` (commit, standards
  revision, runner digest, warning and skip counts);
- a smoke inventory, `inventory/ci/smoke-tests.yaml`, listing every
  required job;
- a wiring smoke per job;
- a status post and read-back;
- no skipped required tests;
- no ad hoc tool installs inside a required job.

The pin does not yet define everything this needs: it has no result
schema, and its example `--profile merge` command is not accepted by its
own reference `scripts/check.sh`.

**Open decision.** Adopt this model now, in part, or record an exception
in `standards-binding.yaml`, whose `exceptions` list is empty today.

## Local and CI

- Static checks may run locally. Tests run only in CI. A local result
  never replaces `validate`.
- Whether tests may also run on an allocated host is an open decision.

## Steps

1. After PR #2 lands, extend its `scripts/check.sh`. The new checks live in
   `lib/ci/check.bash`, and `--help` lists every argument and output mode.
2. Switch the `validate.yml` steps to call it, and extend the
   workflow-policy test as in G1.
3. Pin exact tool versions (G2).
4. Open one pull request; `validate` must pass.
5. Read back: `./scripts/check.sh --dry-run` lists every gate locally, and
   the pull request's checks show each one.
