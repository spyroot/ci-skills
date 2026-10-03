# GAL phases: plan overview

Status: proposed. This page orders the phases; each phase document owns its
own design, steps and gates.

## Terms

- **Phase:** one block of work, delivered as one pull request.
- **Gate:** a check that blocks a merge.
- **Reference:** a skill's document under `references/`.
- **Operation:** one way our code calls an external tool (GAL-REFERENCE).
- **Pointer:** a `uses` entry linking a command or reference to the
  operations it relies on (GAL-SCHEMA).

## Phases

| Phase | Delivers | Depends on |
| --- | --- | --- |
| GAL-GATES | the shared check entrypoint, aggregator, pins | PR #2 |
| GAL-SCHEMA | the record schemas under `schemas/` | nothing |
| GAL-CLI | one command-line contract and its gate | SCHEMA, GATES |
| GAL-VENDOR | `glab` agent skills vendored under `skills/` | GATES, SCHEMA |
| GAL-CATALOG | discover, `list`, `get`, `install` | VENDOR, CLI |
| GAL-TESTS | CI-only test command and coverage report | GATES, CATALOG |
| GAL-ROUTING | three-hop routing; `ci-skills` on top of `glab` | CATALOG |
| GAL-REFERENCE | declared tool operations and their check | CATALOG |
| GAL-HOOKS | advisory local hooks | GATES, VENDOR |

- **GAL-TESTS** has its own delivery pull request for the reusable test
  command. Every phase still carries its own focused tests in its own pull
  request; GAL-TESTS does not defer those tests.
- **GAL-ROUTING** also waits for pull requests #7 and #8, and for an
  approved receipt host (GAL-GATES, G5).

## Order

1. These phase documents land first, in one pull request, so they can be
   read and reviewed before any implementation starts.
2. GAL-GATES, once PR #2 has landed, because every other phase adds gates to
   its entrypoint.
3. GAL-SCHEMA, then GAL-CLI.
4. GAL-VENDOR.
5. A separate package delivery pull request consolidates the one `ci-skills`
   skill after PR #2 and open diagnostics PRs #7, #8 and #13 are integrated,
   before GAL-CATALOG. Its tests, fresh live receipt and exact-head
   `validate` result land in that pull request.
6. GAL-CATALOG.
7. GAL-TESTS, after GAL-CATALOG adds `list`, `get` and `install` to
   `bin/ci-skills`.
8. GAL-REFERENCE and GAL-ROUTING. Both need GAL-CATALOG; GAL-ROUTING also
   needs PRs #7 and #8 and the receipt host.
9. GAL-HOOKS, after GAL-GATES and GAL-VENDOR.

## How a phase lands

- **One pull request per phase.**
- **GAL-TESTS has its own delivery pull request.** Its test command and
  coverage evidence land there; tests for earlier phases stay in those phases'
  pull requests. Reconcile `GAL-TESTS.md` with this order before that delivery
  pull request merges.
- **Files it may change:**
  - its own files;
  - the shared files its gates need: `.github/workflows/validate.yml`,
    `scripts/check.sh` and its library, `requirements.txt`, `schemas/`, and
    `tests/test_validate_workflow_policy.py`.
- **Before merge**, all of these hold for the exact head commit:
  - a review, with its findings fixed in the same pull request. What makes
    a review block a merge is open (GAL-GATES, G3);
  - the `validate` workflow is green (GAL-GATES);
  - the tests that GAL-TESTS lists for the phase pass.
- **After merge**, the phase document's read-back step confirms the result
  on `main`.

## Pull request status

Checked on 2026-10-03. These phases build on them, not around them.

- **#2 (draft): `ci-api`, `ci-binary-build`, `scripts/check.sh`.** Our own
  tools and the existing gate script. GAL-GATES extends its
  `scripts/check.sh`, GAL-CATALOG exposes the package record, and
  GAL-REFERENCE reads the tools' capabilities.
- **#5 (merged), #7 and #8: more Kubernetes diagnostics.** They add:
  - the commands `cilium_node.py`, `ceph_kernel.py` and `ceph_cluster.py`;
  - the `project-binding.md` reference;
  - an Event API fallback.

  GAL-ROUTING lands after the open diagnostics work and routes its commands.
  Each package-changing pull request carries its own fresh live receipt.
- **#9 (draft): an Event API benchmark** under `benchmarks/`. No overlap.
- **#13 (draft): GitLab operations in the diagnostics package.** Reconcile
  its package edits before the package move.
- **#10: field notes.** Merged on 2026-10-02.

## One installable skill

The canonical package is `skills/ci-skills/`, with
`skills/ci-skills/SKILL.md` declaring `name: ci-skills`. Its user target file
is `~/.ci-skills/target.toml`, and Codex installs it at
`~/.codex/skills/ci-skills` (or `$CODEX_HOME/skills/ci-skills`).

PR #2 adds a root `SKILL.md` with the same name and a root `install.sh` that
links the whole checkout. Package delivery moves the diagnostic skill,
PR #2's `bin/ci-api`, `bin/ci-binary-build`, `lib/ci/api.bash`,
`lib/automation/binary_build.bash`, and `lib/core/runtime.bash` into
`skills/ci-skills/`, preserving their relative paths. Root `bin/ci-api` and
`bin/ci-binary-build` become thin checkout adapters. The merged skill
instructions live only in `skills/ci-skills/SKILL.md`; remove the root
`SKILL.md`. `ci-api` owns caller-selected API reads; the diagnostics core
owns target-bound access, collectors and receipts. Keep `scripts/check.sh`
and `lib/ci/check.bash` at the repository root, pointed at the package runtime.

Retarget the digest-verified copy installer as `tools/install_ci_skills.py`;
root `install.sh` becomes its thin adapter and no longer links the checkout.
Keep PR #2's revision-bound dry-run fingerprint, `--apply`,
`--confirm-install FINGERPRINT`, and `--timeout DURATION` checks in that
adapter; the copy installer enforces the same plan before writing.
An existing link requires an explicit confirmed upgrade, backup and read-back
before replacement. `bin/ci-skills` stays a repository maintenance command:
GAL-VENDOR creates it, and GAL-CATALOG adds `list`, `get` and `install`.
It is not a second installed skill command. Each package-byte change changes
the digest and requires a new live receipt. The current tree has not yet made
these moves.

## Open decisions

- **`orbit`.** Its source project carries the GitLab Enterprise Edition
  license; vendor it or leave it out (GAL-VENDOR).
- **Receipt host.** The approved executor and capture route for the release
  receipt (GAL-GATES, G5).
- **Review.** What makes a review block a merge (GAL-GATES, G3).
- **Test route.** How the GitHub check reaches the Kubernetes pod job
  (GAL-GATES, G8).
- **CLI.** How `PARTIAL` exits, and whether the k8s commands move behind
  one `bin/ci-k8s` command (GAL-CLI).
- **Live tool check.** Which executor runs it (GAL-REFERENCE).
- **Coverage floor.** Whether to set one (GAL-TESTS).
