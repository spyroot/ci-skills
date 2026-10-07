# CI03-GATES: verification gates

Status: proposed. Order and dependencies: CI10-PHASES, Phases.

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

Superseded 2026-10-06: no workflow, protection disabled (CI10-PHASES, Pull request status); the route is D-GATE (G0).

## Two routes

- **Execution:** `./scripts/check.sh <profile>` runs the gates. Block 0 restores its library and G1 adds the profiles.
- **Read-back:** `gh pr checks <pr> --json name,state` reads the result for the exact head, once a gate route exists
  (G0).

One does not replace the other.

## Existing gates

These stay. Each one moves behind `./scripts/check.sh` without losing its guarantee. The status of each is as read back
on 2026-10-06 (CI10-PHASES, Pull request status) or observed in the tree that day.

- **Branch protection on `main`.** Disabled, and no workflow exists (G0). Recreate the workflow the gate route (G0)
  names; the step list to restore is at `1108cca^:.github/workflows/validate.yml`.
- **`tests/python/test_validate_workflow_policy.py`** asserts that the static steps exist and that live acceptance is
  unconditional (lines 15-57). It reads `.github/workflows/validate.yml` (line 8), deleted in #27, so it cannot pass
  until that workflow is recreated.
- **`tools/check_live_acceptance.py`** checks that each receipt's `skill.digest` equals the skill's `tree_digest`, with
  the other fields listed in "Delivery, test and proof", part 4. Today it reports `skill_digest_mismatch` on every
  committed receipt (observed with `PYTHONPATH=ci-skills/lib`; without it the import of `core` at line 479 fails until
  block 0 re-points `tools/*.py`).
- **The byte-equality test in `tests/python/test_catalog.py`** (`test_the_rendered_manifest_matches_the_module`, line
  121) checks that `tools.json` equals the catalog's render.
- **`manifest`**: `tools/render_manifest.py --check` prints `CURRENT` or `STALE` (lines 69-71). Today it fails on the
  stale `skills/ci-skills/scripts` path; block 0 re-points it (CI10-PHASES, Order). Tool exists; gate id pending G1.
- **`neutrality`**: `tools/check_project_neutrality.py --root . --json` reports `PASS` or `FAIL` (line 54). Today it
  reports `FAIL` on three files, `Makefile`, `pyproject.toml` and `scripts/bash/core/result.bash`; block 0 removes the
  content (CI10-PHASES, Modify). Tool exists; gate id pending G1.
- **Static tools** (CI10-PHASES, "Gates that make every tool conform"): `ruff check`, `ruff format --check`,
  markdownlint-cli2, shellcheck, shfmt, yamllint, gitleaks and `git diff --check`. Each: tool exists; gate id pending
  G1. The restored library names its checks `whitespace`, `bash-n`, `shellcheck`, `shfmt`, `yaml`, `markdown`,
  `secrets` and `unit` (`88bd9f6^:lib/ci/check.bash:134`) and does not run Ruff.
- **Tests** (pytest and bats): the route that runs them is D-GATE (G0).

## Gaps

Each gap names the requirement, the failure it prevents, and the smallest change that closes it.

### G0. Gate route (D-GATE)

- **Decided 2026-10-06, D-GATE (CI10-PHASES, Decisions taken):** none for now; the gap is recorded, not closed.
- **Facts, read back from GitHub on 2026-10-06.** `.github/workflows/validate.yml` was deleted in PR #27 (commit
  `1108cca`); `.github/workflows` answers 404; branch protection on `main` is disabled (`gh api` 404); no `validate`
  workflow exists; the committed receipts no longer match the skill digest (`tools/check_live_acceptance.py` reports
  `skill_digest_mismatch` on `main`).
- **Three routes, roles rather than alternatives.**
  1. **The GitHub preflight, owned by G1.** Recreate `.github/workflows/validate.yml` (planned, CI03-GATES G1) from
     `1108cca^:.github/workflows/validate.yml`. A verbatim restore is red; the edits:
     - its shell list (lines 80-89) names `skills/ci-skills/...` paths (lines 83-86) that now live under `ci-skills/`,
       and `lib/ci/check.bash` (line 85), which block 0 restores at `ci-skills/lib/bash/ci/check.bash` (planned,
       CI10-PHASES, Order, block 0, restored from `88bd9f6^`);
       the restored library lists tracked shell files itself (`git ls-files`,
       `88bd9f6^:lib/ci/check.bash:104-105`), so the step calls the entrypoint instead of carrying a list;
     - `bats --tap tests/*.bats` (line 95) becomes `tests/bash/*.bats`;
     - `pytest -q tests/test_installed_package.py` (line 107) and `--ignore=tests/test_installed_package.py` (line
       111) become `tests/python/...`;
     - the acceptance call (lines 116-119) needs `--expected tests/acceptance/expected.toml`,
       `--receipts tests/acceptance/receipts` and `--skill ci-skills`, because the checker's defaults are
       `<root>/acceptance/...` and `<root>/skills/ci-skills` (`tools/check_live_acceptance.py`, `main`, lines 624-657);
     - the `non_markdown_count` conditions (lines 70, 74, 98, 102, 106 and 110) violate the pinned `ci.md` rule
       `skipped_required_tests: 0` (G6) and go.
  2. **The home GitLab CI aggregator**, posting one required context to GitHub for the exact SHA (the pinned `ci.md`,
     "GitHub Reflection"; the aggregator requirement is G6). Nothing exists, and its inventory is unverified.
  3. **Local scripts**, as the advisory pre-commit body only (CI04-HOOKS), never the merge gate.
- **Gap.** Three tracked files still name `validate` as the required check: `tests/acceptance/expected.toml:26`
  (`required_checks = ["validate"]`, which the checker enforces at lines 311-317, so a fresh publication receipt
  reports `required_check_absent:validate` until a check of that name exists),
  `.coordination/pr-coordinator-policy.md:3-5` and `.coordination/pr-coordinator.toml:37,49`
  (`approved_pipeline_route` at the deleted workflow; `required = ["validate"]`). We change them only under the
  approval lock on contracts, once the route gives the check its name.

### G1. One entrypoint for local runs and CI

- **Requirement.** Local runs and any gate route invoke the same repository-owned entrypoint. The pinned standards'
  workspace index names `scripts/check.sh` as the gate entrypoint (`templates/internal/INDEX.yaml`, `gates.entrypoint`).
- **Existing mechanism.** `scripts/check.sh:5-6` sources `lib/ci/check.bash`, deleted in #26 (commit `88bd9f6`); no
  `check.bash` exists at any path, so the script cannot run today; it was deleted on purpose, and block 0
  (CI10-PHASES, Order) does not restore it. The deleted library (`88bd9f6^:lib/ci/check.bash`) sourced
  `skills/ci-skills/lib/core/runtime.bash` (line 7), today `ci-skills/lib/bash/core/runtime.bash`, and verified
  `skills/ci-skills/SKILL.md` (lines 102-103 and 128-129), today `ci-skills/SKILL.md`. It checks tracked shell
  files with
  `bash -n`, ShellCheck and shfmt, checks tracked whitespace, YAML and Markdown, scans Git history for secrets and runs
  the Bats suite (lines 116-127). It takes `--dry-run`, `--log-format`, `--log-level`, `--log-file`, `--run-id` and
  `--help` (lines 17-22); its help text says it exits 0, 64 or 69 (lines 26-28), and it also returns 66 for an
  unreadable log directory (line 74). The constants are `ci-skills/lib/bash/core/runtime.bash:6-9`. The restored
  library must also change:
  - the Bats glob: `bats --tap tests` (line 127) becomes `tests/bash/*.bats`;
  - the exit codes: from the one table in `core/status.py` (0 and 2, `status.py:13`), rendered to
    `ci-skills/lib/bash/core/exit_codes.bash` (planned, CI10-PHASES Refactor), in place of `runtime.bash`'s 64, 65, 66
    and 69 and the second Bash table in `scripts/bash/core/exit_codes.bash`; the code for a failed gate is D-EXIT
    (CI02-CLI);
  - its Kubernetes-pod guard (`KUBERNETES_SERVICE_HOST`, lines 185-187) becomes a per-profile rule that G1 decides.

  The other local scripts, checked against the tree on 2026-10-06: the tracked `bless.sh` sources sixteen
  `automation/lib/...` files (lines 14-45), and no `automation/` directory exists; the tracked `Makefile`'s default goal
  `bless` (line 9) depends on `install-hooks`, which sets `core.hooksPath .githooks` (lines 48-49), a directory that
  does not exist, and its `toolchain`, `conda`, `pretty`, `k8s-test` and `toolbox` targets name scripts and libraries
  under `scripts/toolchain/`, `scripts/toolbox/`, `scripts/ci/`, `lib/bash/toolchain/` and `automation/lib/` that do
  not exist (lines 10-27, 75-130 and 176-189). Which of `bless.sh` and `scripts/check.sh` becomes the hook body is
  CI04-HOOKS's to settle: the repository structure lists `bless.sh` as the pre-hook, and CI04-HOOKS names
  `scripts/check.sh`.
- **Smallest change.** Extend that script instead of adding a second one:
  - recreate the workflow the gate route (G0) names; the step list to restore is at
    `1108cca^:.github/workflows/validate.yml`, and each restored step calls the entrypoint instead of carrying its own
    command list;
  - add the `schemas` gate (CI07-SCHEMA), the `cli` gate (CI02-CLI), the `manifest` and `neutrality` gates (Existing
    gates) and, after their phases, `verify` (CI05-VENDOR), `tests` (CI06-TESTS) and `reference` (CI09-REFERENCE);
  - give `scripts/check.sh` `--describe` and a `command-contract` record like every catalog entrypoint (CI02-CLI,
    "Which commands");
  - add profiles: one gate alone, the static subset for the hooks (CI04-HOOKS), and the merge profile that runs every
    required gate. The pinned `smoke-testing` contract's example is `--profile merge --category unit` (its line 85),
    and the pinned reference `scripts/check.sh` accepts no `--profile` (G6), so G1 fixes the syntax; this document
    writes `scripts/check.sh <profile>`;
  - extend `tests/python/test_validate_workflow_policy.py` so that every gate is called by exactly one step and live
    acceptance stays unconditional and first.

### G2. Exact tool versions

- **Requirement.** Every gate route and local run use the same tool versions.
- **Failure today.** `requirements.txt` holds ranges (`ruff>=0.13,<1`, `pytest>=8,<10`, `PyYAML>=6,<7`), so a route
  and a contributor can format differently. The step list to restore pins yamllint 1.38.0, markdownlint-cli2 0.23.2,
  actionlint 1.7.12 and gitleaks 8.30.1 (`1108cca^:.github/workflows/validate.yml:39-62`) but installs bats and shfmt
  unpinned through apt inside the job (lines 78-79), which the pinned `blockers.md` prohibits ("Prohibited Responses
  to a Blocker", line 69).
- **Smallest change.** Pin exact versions in `requirements.txt`, taken from what a run of the recreated workflow (G0)
  resolves. That includes `check-jsonschema` (CI07-SCHEMA) and coverage.py (CI06-TESTS).

### G3. A review that blocks a merge

- **Requirement.** Every phase pull request is reviewed against its exact head commit before merge (CI10-PHASES, How a
  phase lands).
- **Failure today.** Branch protection is disabled (G0), so no approving review is required and a missing or stale
  review blocks nothing.
- **Smallest change.** One of the following, read back after it is set (open: CI10-PHASES, Open decisions, "Review"):
  - require an approving review on `main`;
  - make a review status, posted by the reviewer for the exact head commit, a required check.

### G4. Vendored skills checked on every change

- **Requirement.** A change to a vendored skill is always verified (CI05-VENDOR).
- **Failure today.** In the step list to restore, a change to Markdown files alone skips the gated steps (G6), and a
  vendored skill is almost all Markdown.
- **Smallest change.** The `verify` gate runs unconditionally, under the same assertions the workflow-policy test
  applies to live acceptance.

### G5. Release receipt host

- **Requirement.** CI08-ROUTING and every CI11-TOOLS tool need a live receipt for the exact skill digest, captured on
  the executor that `tests/acceptance/expected.toml` declares.
- **Decided 2026-10-06, D-SMOKE (CI10-PHASES, Decisions taken).** Live smoke runs from the laptop executor that
  `expected.toml` declares (`[[executors]]`, host `mac.lan`, lines 36-38) against the targets declared there; the proof
  is the tool's own read-back with fixed arguments. CI06-TESTS, "Live smoke" owns the smoke contract.
- **This phase adds** the `[[smoke_cases]]` verification to `tools/check_live_acceptance.py`, owned here: per case it
  compares the digest of the declared fixed arguments, `execution_host`, the read-back fields the case names and, for a
  mutation, `result_action` `APPLIED` then `NO_OP`. CI07-SCHEMA gains the smoke-case schema row. Its tests are written
  with the block; run status UNVERIFIED until a route exists (G0); the pytest and bats suites are never run on the
  laptop.
- **Read-back.** `tools/check_live_acceptance.py` accepts receipts only from the declared executor and identities
  (`_check_receipt`, lines 221 and 246).

### G6. The pinned standards' CI evidence model

- **Requirement.** `standards-binding.yaml` requires the `ci` and `smoke-testing` contracts, with `exceptions: []`. The
  pinned binding schema allows only stricter or temporary-block exceptions, so this model is required: it cannot be
  waived or partly adopted.
- **Failure today.** No workflow exists (G0). The step list to restore (`1108cca^:.github/workflows/validate.yml`) has
  none of the following:
  - one final aggregator that runs even when earlier steps fail, and decides the required result (`ci.md`, "Final
    Required-Result Aggregator");
  - per-job evidence under `reports/ci/<job>.json` (planned, CI03-GATES G6): commit, standards revision, runner digest,
    warning and skip counts (`ci.md`, "Evidence");
  - a smoke inventory, `inventory/ci/smoke-tests.yaml` (planned; it lists required jobs, so its owner is the route
    that runs them, G0 route 2, where nothing exists yet), with a wiring smoke per job (`smoke-testing.md`);
  - a status post and read-back (`ci.md`, "GitHub Reflection");
  - zero skipped required tests (`ci.md`, "Warning and Skip Policy"): its dependency install, Bash tools and Bats,
    Ruff, installed-package smoke and pytest steps are conditional on `non_markdown_count` (lines 70, 74, 98, 102, 106
    and 110), which counts as skipping required tests;
  - no ad hoc tool installs inside a required job (`blockers.md`, "Prohibited Responses to a Blocker", line 69): lines
    78-79 install bats and shfmt.
- **Smallest change.** Make the recreated workflow end in one aggregator step that checks every required evidence
  record and fails on any that is missing, skipped, warning-bearing or for the wrong commit.
- **The pin is incomplete in two places:** it has no result schema, and its example `--profile merge` command
  (`smoke-testing.md:85`) is not accepted by its own reference `scripts/check.sh`. Both are raised with the shared
  standards' owners; they do not excuse the gap.

### G7. A scheduled check of `main`

- **Requirement.** The routine also checks `main`, not only pull requests.
- **Failure today.** No workflow exists (G0); the step list to restore triggers on pull requests and pushes to `main`
  only (`1108cca^:.github/workflows/validate.yml:3-6`). The committed receipts expire at `captured_at` plus
  `max_receipt_age_days`; after that every pull request fails and nothing warns before then (G7 makes the checker
  report days left).
- **Smallest change.**
  - A `schedule` trigger on the workflow G1 recreates, which GitHub runs on `main`. This adds no new workflow.
  - `tools/check_live_acceptance.py` reports the days left before each receipt expires, so each scheduled run shows it.
- **Read-back.** Once the workflow exists: `gh run list --workflow validate --branch main --event schedule` shows one
  run per day.

### G8. Test execution route

- **Requirement.** Tests run on the gate route, never on a laptop (CI06-TESTS, Rules).
- **Today.** No route (G0). The deleted workflow ran pytest and Bats on GitHub-hosted runners
  (`1108cca^:.github/workflows/validate.yml:73-111`); the deleted library runs its checks only inside a Kubernetes pod
  (`88bd9f6^:lib/ci/check.bash:185-187`), and the local guide asks for Kubernetes CI.
- **Open.** The route from a GitHub check to that pod job is not defined. It has to be named before a pod-only profile
  can feed the gate route; the per-profile guard is G1's.

## Local and CI

- **Local:** static checks, through the hooks (CI04-HOOKS), advisory only; never the merge gate (G0, route 3).
- **Gate route (G0):** the unit, contract, shell and offline-smoke layers (CI06-TESTS, Layers); their run status is
  UNVERIFIED until a route exists (G0).
- **Laptop executor (D-SMOKE):** live smoke against the declared targets (CI06-TESTS, "Live smoke"; G5).

A local static result never replaces the gate route's result.

## Steps

1. Restore the library in block 0 (CI10-PHASES, Order), then extend `scripts/check.sh` as in G1. The new checks live
   in `ci-skills/lib/bash/ci/check.bash`, and `--help` lists every argument and output mode.
2. Recreate the workflow the gate route (G0) names from `1108cca^:.github/workflows/validate.yml` with G0's edit list;
   make each step call the entrypoint, add the final aggregator (G6) and the `schedule` trigger (G7), and extend the
   workflow-policy test.
3. Pin exact tool versions (G2).
4. Open one pull request; merge per CI10-PHASES, How a phase lands.
5. Read back:
   - `./scripts/check.sh <profile> --dry-run` lists the profile's gates without running them;
   - once the workflow exists, `gh pr checks <pr> --json name,state` for the exact head shows each gate and the
     aggregator;
   - a scheduled run on `main` appears the next day.

## Delivery, test and proof

1. **Delivery.**
   - Changed: `scripts/check.sh` (sources `ci-skills/lib/bash/ci/check.bash`); `ci-skills/lib/bash/ci/check.bash`
     (restored in block 0; this phase adds the profiles, the gates and the guard rule, G1); `requirements.txt` (G2);
     `tools/check_live_acceptance.py` (the smoke-case verification, G5; the days left, G7);
     `tests/python/test_validate_workflow_policy.py`, `tests/bash/check.bats` and `tests/python/test_live_acceptance.py`
     (part 2).
   - Created: `.github/workflows/validate.yml`, recreated from `1108cca^` with G0's edit list and ending in the
     aggregator step (G6), and the aggregator's inputs, `reports/ci/<job>.json` per required job (G6). The smoke
     inventory `inventory/ci/smoke-tests.yaml` (G6) lists required jobs, so the route that runs required jobs (G0,
     route 2) declares it; nothing exists there yet.
   - The command that runs: `scripts/check.sh <profile>`. `scripts/check.sh <profile> --dry-run` lists the profile's
     gates without running them (the library's dry run prints one `checks` list, `88bd9f6^:lib/ci/check.bash:180`).
2. **Tests.** Written with the block; run status UNVERIFIED (CI03-GATES, G0).
   - `tests/python/test_validate_workflow_policy.py` (exists; it reads the deleted workflow at line 8, so it targets
     the recreated one):
     - registry gates have unique ids, and every gate is called by exactly one workflow step;
     - live acceptance and `verify` stay unconditional and first;
     - the `schedule` trigger is present;
     - the final aggregator fails when an evidence record is missing, skipped, warning-bearing or for the wrong commit;
     - every `requirements.txt` line pins an exact version (placed here; no better-named file exists).
   - `tests/bash/check.bats` (exists; its `setup` resolves `../scripts/check.sh` and `../lib/ci/check.bash` from
     `tests/bash/`, lines 4-5, so `tests/scripts/check.sh` and `tests/lib/ci/check.bash`, neither of which exists; it
     is re-pointed to `scripts/check.sh` and `ci-skills/lib/bash/ci/check.bash`):
     - `--help` lists every argument and output mode; an unknown option exits 64 (the code follows the one table,
       D-EXIT, CI02-CLI);
     - the static subset runs only static gates;
     - a failing gate fails the run and names the gate;
     - its existing cases stay: a dry run lists the planned gates without running tests; a log file inside the
       checkout, a symlinked log file and a log file outside it (lines 54-78); the verified source revision on a
       clean tree, a tracked change, a staged change and an untracked file (lines 80-114); a head swap during the run
       is refused (line 116). The case "refuses execution outside Kubernetes" (line 48) follows the per-profile guard
       G1 decides.
   - `tests/python/test_live_acceptance.py` (exists), for the G5 checker extension, following
     `test_each_defect_is_refused_with_its_own_reason` (line 242): a receipt whose arguments digest differs from the
     declared case is refused; one from a host other than the declared executor is refused; one missing a read-back
     field the case names is refused; a mutation whose second receipt is not `NO_OP` is refused; the days left before
     expiry are reported (G7).
3. **Smoke.**
   - Static read-back: `scripts/check.sh <profile>` for each profile G1 defines (the static subset and merge); each
     run ends in the result line that names every gate it ran. The restored library prints
     `{"status":"passed","commit":"<sha>","checks":[...]}` (`88bd9f6^:lib/ci/check.bash:133-134`); the `cli` gate
     (CI02-CLI) brings `scripts/check.sh` under the one result shape (`status: PASS`, CI10-PHASES, "Machine-readable
     output, one shape").
   - Gate route read-back: `gh pr checks <pr> --json name,state` for the exact head, once the GitHub preflight exists
     (G0, route 1). Until then there is nothing to read back and the run status is UNVERIFIED.
   - No live target: this phase adds no tool to the catalogue, so it declares no smoke case (CI06-TESTS, "A smoke
     case": one per tool).
4. **Evidence.** No receipt of its own: the static evidence is the `scripts/check.sh <profile>` output and, once the
   workflow exists, the `gh pr checks` output for the head commit. The phase does change the skill digest:
   `ci-skills/lib/bash/ci/check.bash` lies inside the digested tree (`tree_digest` walks the skill root and excludes
   only `__pycache__`, `.pyc`, `.pyo` and `.DS_Store`: `_included` and `tree_digest`,
   `ci-skills/lib/python/core/provenance.py:43-75`; D-DIGEST excludes `references/vendor/**`), so the receipts under
   `tests/acceptance/receipts/`, already `skill_digest_mismatch` (CI10-PHASES, Pull request status), are recaptured
   on the declared executor (D-SMOKE) after the last skill edit.
   The fields the checker compares on the publication receipt (`tests/acceptance/receipts/operator-laptop.json`),
   from `_check_common` and `_check_receipt` (`tools/check_live_acceptance.py:169-319`): `schema_version`, `status`,
   `skill.digest`, `captured_at` against `max_receipt_age_days`, sanitization, `kind`, `publication`,
   `execution_host`, `skill.revision.verified` with `tested_revision`, per surface `identity`, `status`,
   `credential_source` and `target`, `targets`, each `required_live_checks` entry's `access_proven`, `status` and
   `readback_sha256`, the `gitlab_job` read-back (`job_url`, `job_id`, `pipeline_id`, `runner_id`,
   `trace_line_count`) and `required_checks`.
5. **Verification.**
   - The checker command and its expected `PASS` line (line 679; exit 0, line 684):

     ```text
     tools/check_live_acceptance.py --root . --expected tests/acceptance/expected.toml \
       --receipts tests/acceptance/receipts --skill ci-skills
     Live acceptance: PASS
     ```

     With `--json` the line is `"status": "PASS"`. Observed 2026-10-06 on this branch: without `PYTHONPATH` the
     import of `core` fails (`ModuleNotFoundError`, line 479; block 0 re-points `tools/*.py`); with
     `PYTHONPATH=ci-skills/lib` it reports `BLOCKED`, `skill_digest_mismatch` on all 17 receipts and
     `runner_smoke_cleanup_unproven` on `runner-create-applied.json`.
   - `tools/render_manifest.py --check` prints `CURRENT` (today it fails on the stale `skills/ci-skills/scripts` path;
     block 0).
   - `tools/check_project_neutrality.py --root . --json` prints `"status": "PASS"` (today `FAIL` on three files;
     block 0).
   - `tests/python/test_validate_workflow_policy.py`, `tests/bash/check.bats` and `tests/python/test_live_acceptance.py`
     run on the gate route only; UNVERIFIED until one exists (G0).
