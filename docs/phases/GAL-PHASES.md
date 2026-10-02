# GAL phases: plan overview

Status: proposed. This page orders the phases; each phase document owns its
own design, steps and gates.

## Phases

| Phase | Delivers | Depends on |
| --- | --- | --- |
| GAL-GATES | `./scripts/check.sh`, the gate registry, exact pins | nothing |
| GAL-VENDOR | `glab` agent skills vendored under `skills/` | nothing |
| GAL-CATALOG | `list`, `get` and `install` over every skill | GAL-VENDOR |
| GAL-ROUTING | three-hop routing; the k8s skill on top of `glab` | CATALOG |
| GAL-HOOKS | local hooks that call `./scripts/check.sh` | GAL-GATES |
| GAL-REFERENCE | tool capability index; decision pending | CATALOG |

GAL-ROUTING also depends on GAL-VENDOR, through GAL-CATALOG.

## Order

1. These phase documents land first, in one pull request, so they can be
   read and reviewed before any implementation starts.
2. GAL-GATES and GAL-VENDOR are independent and can proceed in parallel.
3. GAL-CATALOG follows GAL-VENDOR.
4. GAL-ROUTING follows GAL-CATALOG. It is the only phase that changes
   `skills/k8s-admin-diagnostics/`, so it ends with a new live receipt.
5. GAL-HOOKS follows GAL-GATES.

## How a phase lands

- One pull request per phase, changing only that phase's files.
- Before merge, all of these hold:
  - an independent review of the exact head commit, with its findings fixed
    in the same pull request;
  - the `validate` workflow is green for that commit (GAL-GATES);
  - the phase document's own gates pass, including the tests that
    GAL-TESTS lists for that phase.
- After merge, the phase document's read-back step confirms the result on
  `main`.

## Open pull requests

Checked on 2026-10-02. These phases build on them, not around them.

- **#2 (draft): `ci-api`, `ci-binary-build`, `scripts/check.sh`.** Our own
  tools and the existing gate script. GAL-GATES extends its
  `scripts/check.sh`, GAL-CATALOG lists its tools, and GAL-REFERENCE reads
  their capabilities.
- **#5, #7 and #8: more k8s diagnostics.** They add `cilium_node.py`,
  `ceph_kernel.py`, `ceph_cluster.py`, the `project-binding.md` reference
  and an Event API fallback. GAL-ROUTING lands after them and routes their
  commands, so one live receipt covers every skill change.
- **#9 (draft): an Event API benchmark** under `benchmarks/`. No overlap.
- **#10: field notes.** No overlap.

## Short names

Proposed, in the style of the `bin/ci-*` tools:

| Today | Proposed |
| --- | --- |
| `skills/k8s-admin-diagnostics/` | `skills/k8s-diag/` |
| `tools/install_k8s_admin_diagnostics.py` | `bin/ci-skills install` |
| the catalog command | `bin/ci-skills` |

Renaming the skill changes its digest, so the rename lands inside
GAL-ROUTING, under the same new receipt. Installed copies under the old
name keep working until they are reinstalled.

## Open decisions

- `orbit`: its source project carries the GitLab Enterprise Edition
  license; vendor it or leave it out (GAL-VENDOR).
- Which host may capture the release receipt that GAL-ROUTING needs
  (GAL-GATES, G5).
- Whether to adopt the pinned standards' CI evidence model now (GAL-GATES,
  G6).
- Whether tests may run anywhere besides CI (GAL-GATES).
- Whether the record shapes also get JSON Schema files (GAL-CATALOG).
- The short names above.
- How we collect, store and index the tools our skills call: runtime
  discovery plus declared capabilities is proposed (GAL-REFERENCE).
