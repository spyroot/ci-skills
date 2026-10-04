# CI Skills phases: plan overview

Status: proposed. This page orders the phases; each phase document owns its
own design, steps and gates.

## Terms

- **Phase:** one block of work, delivered as one pull request.
- **Gate:** a check that blocks a merge.
- **Reference:** a skill's document under `references/`.
- **Operation:** one way our code calls an external tool (CI-REFERENCE).
- **Pointer:** a `uses` entry linking a command or reference to the
  operations it relies on (CI-SCHEMA).

## Phases

| Phase | Delivers | Depends on |
| --- | --- | --- |
| CI03-GATES | check entrypoint, aggregator, pins | merged `scripts/check.sh` |
| CI-SCHEMA | the record schemas under `schemas/` | nothing |
| CI02-CLI | one command-line contract and its gate | SCHEMA, GATES |
| CI-VENDOR | `glab` agent skills vendored under `skills/` | GATES, SCHEMA |
| CI01-CATALOG | discover, `list`, `get`, `install` | VENDOR, CLI |
| CI-TESTS | CI-only test command and coverage report | GATES, CATALOG |
| CI-ROUTING | three-hop routing; `ci-skills` on top of `glab` | CATALOG |
| CI-REFERENCE | declared tool operations and their check | CATALOG |
| CI04-HOOKS | advisory local hooks | GATES, VENDOR |

- **CI-TESTS** has its own delivery pull request for the reusable test
  command. Every phase still carries its own focused tests in its own pull
  request; CI-TESTS does not defer those tests.
- **CI-ROUTING** also waits for an approved receipt host (CI03-GATES, G5).

## Order

1. These phase documents land first, in one pull request, so they can be
   read and reviewed before any implementation starts.
2. CI03-GATES extends the merged `scripts/check.sh`, because every other phase
   adds gates to it.
3. CI-SCHEMA, then CI02-CLI.
4. CI-VENDOR.
5. A separate package delivery pull request consolidates the one `ci-skills`
   skill from the source work merged through PR #16, before CI01-CATALOG.
   Its tests, fresh live receipt and exact-head `validate` result land in
   that pull request.
6. CI01-CATALOG.
7. CI-TESTS, after CI01-CATALOG adds `list`, `get` and `install` to
   `bin/ci-skills`.
8. CI-REFERENCE and CI-ROUTING. Both need CI01-CATALOG; CI-ROUTING also
   needs the receipt host.
9. CI04-HOOKS, after CI03-GATES and CI-VENDOR.

## How a phase lands

- **One pull request per phase.**
- **CI-TESTS has its own delivery pull request.** Its test command and
  coverage evidence land there; tests for earlier phases stay in those phases'
  pull requests. Reconcile `CI-TESTS.md` with this order before that delivery
  pull request merges.
- **Files it may change:**
  - its own files;
  - the shared files its gates need: `.github/workflows/validate.yml`,
    `scripts/check.sh` and its library, `requirements.txt`, `schemas/`, and
    `../../tests/python/test_validate_workflow_policy.py`.
- **Before merge**, all of these hold for the exact head commit:
  - a review, with its findings fixed in the same pull request. What makes
    a review block a merge is open (CI03-GATES, G3);
  - the `validate` workflow is green (CI03-GATES);
  - the tests that CI-TESTS lists for the phase pass.
- **After merge**, the phase document's read-back step confirms the result
  on `main`.

## Pull request status

Checked on 2026-10-04. PR #16 merged the root tools, Kubernetes diagnostics,
Event API benchmark, GitLab operations and live receipts from PRs #2, #7, #8,
PR #9, PR #13 and PR #14. Those source PRs are closed. PR #15 merged the CI-TESTS
phase entry. The package delivery described below remains proposed.

## One installable skill

The canonical package is `../../ci-skills`, with
`../../ci-skills` declaring `name: ci-skills`. Its user target file
is `~/.ci-skills/target.toml`, and Codex installs it at
`~/.codex/skills/ci-skills` (or `$CODEX_HOME/skills/ci-skills`).

Package delivery moves the diagnostic skill,
the merged `bin/ci-api`, `bin/ci-binary-build`, `lib/ci/api.bash`,
`lib/automation/binary_build.bash`, and `lib/core/runtime.bash` into
`../../ci-skills`, preserving their relative paths. Root `bin/ci-api` and
`bin/ci-binary-build` become thin checkout adapters. The merged skill
instructions live only in `../../ci-skills`; remove the root
`SKILL.md`. `ci-api` owns caller-selected API reads; the diagnostics core
owns target-bound access, collectors and receipts. Keep `scripts/check.sh`
and `lib/ci/check.bash` at the repository root, pointed at the package runtime.

Retarget the digest-verified copy installer as `tools/install_ci_skills.py`;
root `install.sh` becomes its thin adapter and no longer links the checkout.
Keep the existing revision-bound dry-run fingerprint, `--apply`,
`--confirm-install FINGERPRINT`, and `--timeout DURATION` checks in that
adapter; the copy installer enforces the same plan before writing.
An existing link requires an explicit confirmed upgrade, backup and read-back
before replacement. `bin/ci-skills` stays a repository maintenance command:
CI-VENDOR creates it, and CI01-CATALOG adds `list`, `get` and `install`.
It is not a second installed skill command. Each package-byte change changes
the digest and requires a new live receipt.

## Open decisions

- **`orbit`.** Its source project carries the GitLab Enterprise Edition
  license; vendor it or leave it out (CI-VENDOR).
- **Receipt host.** The approved executor and capture route for the release
  receipt (CI03-GATES, G5).
- **Review.** What makes a review block a merge (CI03-GATES, G3).
- **Test route.** How the GitHub check reaches the Kubernetes pod job
  (CI03-GATES, G8).
- **CLI.** How `PARTIAL` exits, and whether the k8s commands move behind
  one `bin/ci-k8s` command (CI02-CLI).
- **Live tool check.** Which executor runs it (CI-REFERENCE).
- **Coverage floor.** Whether to set one (CI-TESTS).
