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
| GAL-TESTS | CI-run `bin/ci-skills test` for offline skill tests and coverage evidence | GATES, CATALOG |
| GAL-ROUTING | three-hop routing; `k8s-diag` on top of `glab` | CATALOG |
| GAL-REFERENCE | declared tool operations and their check | CATALOG |
| GAL-HOOKS | advisory local hooks | GATES, VENDOR |

- **GAL-TESTS** has its own delivery pull request for the reusable test
  command. Every phase still carries its own focused tests in its own pull
  request; GAL-TESTS does not defer those tests.
- **GAL-ROUTING** also waits for pull requests #5, #7 and #8, and for an
  approved receipt host (GAL-GATES, G5).

## Order

1. These phase documents land first, in one pull request, so they can be
   read and reviewed before any implementation starts.
2. GAL-GATES, once PR #2 has landed, because every other phase adds gates to
   its entrypoint.
3. GAL-SCHEMA, then GAL-CLI.
4. GAL-VENDOR.
5. GAL-CATALOG.
6. GAL-TESTS, after GAL-CATALOG provides `bin/ci-skills`.
7. GAL-REFERENCE and GAL-ROUTING. Both need GAL-CATALOG; GAL-ROUTING also
   needs PRs #5, #7 and #8 and the receipt host.
8. GAL-HOOKS, after GAL-GATES and GAL-VENDOR.

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

## Open pull requests

Checked on 2026-10-02. These phases build on them, not around them.

- **#2 (draft): `ci-api`, `ci-binary-build`, `scripts/check.sh`.** Our own
  tools and the existing gate script. GAL-GATES extends its
  `scripts/check.sh`, GAL-CATALOG lists its tools, and GAL-REFERENCE reads
  their capabilities.
- **#5, #7 and #8: more k8s diagnostics.** They add:
  - the commands `cilium_node.py`, `ceph_kernel.py` and `ceph_cluster.py`;
  - the `project-binding.md` reference;
  - an Event API fallback.

  GAL-ROUTING lands after them and routes their commands, so one live
  receipt covers every skill change.
- **#9 (draft): an Event API benchmark** under `benchmarks/`. No overlap.
- **#10: field notes.** Merged on 2026-10-02.

## Short names

Decided on 2026-10-02, in the style of the `bin/ci-*` tools:

| Today | Decided |
| --- | --- |
| `skills/k8s-admin-diagnostics/` | `skills/k8s-diag/` |
| `tools/install_k8s_admin_diagnostics.py` | `bin/ci-skills install` |
| the catalog command | `bin/ci-skills` |

Renaming the skill changes its digest, so the rename lands inside
GAL-ROUTING, under the same new receipt. Installed copies under the old
name keep working until they are reinstalled.

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
