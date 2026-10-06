# GAL phases: local task record

The deliverables are the phase docs in `docs/phases/`. This file holds only agent-side state
until the queue record exists. It is local and ignored, and is never committed.

## Deliverables

State observed 2026-10-02.

| Deliverable | Where | State |
| --- | --- | --- |
| GAL-VENDOR, GAL-CATALOG, GAL-ROUTING, GAL-HOOKS | `docs/phases/` on branch `nero/gal-phase-docs` (worktree `.internal/worktrees/gal-phase-docs`) | written, static checks clean; QA running; PR pending |
| `/init` guide | `CLAUDE.md` (local, ignored) | corrected from the 2026-10-02 reviews; hooks moved to `docs/phases/GAL-HOOKS.md` |
| Review record | `.internal/reviews/GAL-phase-docs.review.md` | structure, correctness and concision done; QA pending |
| Approved plan text | `~/.claude/plans/snappy-imagining-raccoon.md` | kept unchanged as the approval record; superseded by `docs/phases/` |
| Queue items, one per phase | `.internal/queue/claude/ready/GAL-*.yaml` | four ready records created 2026-10-02; Claude has not claimed them |
| Implementation PRs, one per phase | — | not started; wait for the docs PR and the decisions below |

## Answers given, verbatim

| # | Question | Answer |
| --- | --- | --- |
| Q1 | What does "embedded on top" mean? | "Vendor a copy" |
| Q2 | What does "lazy loading memory" mean? | "3) index which pointers , machine readble schema where is what tags" |
| Q3 | Which hook installs or refreshes the glab skill? | "ci-skills is our so remap all glab under ci-skill glab list" |
| Q4 | Queue bootstrap is blocked; how to proceed? | "provider[] we use gitlab and codex creating now queue" |

Later instructions:

- "all should go to docs/ pr polish clear name not a zoo of doc"
- "all must have name that realted to phase GAL-ROUTING GAL-SCHEMA < examples"

## What changed from the approved plan

All of these are now in `docs/phases/`, and merging the docs PR approves them.

- **Phases.**
  - GAL-VENDOR
  - GAL-CATALOG
  - GAL-ROUTING (the index plus the `SKILL.md` split, one live receipt)
  - GAL-HOOKS
- **One CLI.** `tools/ci_skills.py` with `verify`, `update`, `list`, `get` and `install`. There is
  no `vendor_skills.py`.
- **Runtime index.** The index is computed by `list`. There is no committed `skills/index.json`
  and no `render_index.py`.
- **Code layout.** The library is `tools/skillkit/`, not `tools/core/`, because of the `core` module
  collision. Each vendored skill carries its own `LICENSE`.
- **CI and environment.**
  - `.markdownlint-cli2.yaml` holds the `ignores`.
  - `verify` always runs in CI.
  - `environment.yml` is added.
  - Tests run in CI only.
- **orbit** is held (D1).

## Open decisions

- **D1:** vendor `orbit` under the GitLab EE license, or leave it out (GAL-VENDOR).
- **D2:** who captures the GAL-ROUTING receipt on `mac.lan`, and when. Heisenberg suspects a
  conflict here: `TEAM_GUIDE.md:127-128` says "Do not use the HOTT team laptop as release
  evidence", while `../../tests/acceptance` declares only `mac.lan`.
- **D3:** the authoritative CI surface. The candidates are:
  - GitHub `validate` (`../../tests/acceptance` `required_checks`)
  - GitLab (Q4)
  - "approved Kubernetes CI" (`AGENTS.md:9`)
- **D4:** the allocated host for tests outside CI. The pinned `agent-workspace.md:172` says "use
  allocate host. ASK, Note, Memorize".
- **D5:** JSON Schema files for `skill_index` and `skill_vendor_lock`, or not (GAL-CATALOG; Q2).
- **D6:** who owns structure and commands, `TEAM_GUIDE.md` (pinned `project-consumer.md`) or
  `AGENTS.md`. Also the missing-map rule (curator C3, C4).
- **D7:** `./scripts/check.sh` is required by the global rules but does not exist (GAL-HOOKS
  step 1 proposes the shared script).

## Session notes

- 2026-10-02 coordinator read-back: the four phase docs are staged in the
  `nero/gal-phase-docs` worktree, not committed. The current GitHub PR inventory
  contains no phase-docs PR. The Claude ready lane contains four phase records;
  Codex ready is empty. The QA report for the staged docs is not recorded here.
- The latest project instruction selects GitHub for this repository's PR and
  queue route. D3 still needs the required Kubernetes test-execution route
  reconciled with `validate.yml`'s `ubuntu-latest` job before a gate claim.

- `~/dev/standards/bin/agent-tools.sh` ran late (2026-10-02) and reported `missing: []`.
- The main checkout carries other work on `nero/field-notes`. Its untracked `Makefile` fails the
  local neutrality scan.

## Decisions taken

- 2026-10-02: the k8s skill's short name is `k8s-diag` ("k8s-diag is ok"). The rename
  `skills/k8s-admin-diagnostics/` → `skills/k8s-diag/` is a step of GAL-ROUTING, under its
  single new receipt. Recorded in PR #11 at head `ddd512fd5f90743c2bb32122fa69c3d3993973e7`.
