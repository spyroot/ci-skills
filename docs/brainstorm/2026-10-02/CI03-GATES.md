# CI-GATES adversarial review

Target: PR #11, head `7b0f9099202aa768bbabf69c569400ed8cfe5d8a`.
Live `main` protection currently requires `validate`, strict freshness and
administrator enforcement; required approving reviews are unset.

## Findings

- **P1 — G6 is not optional.** `CI-GATES.md:89-109` offers partial adoption
  or an exception for the pinned CI and smoke evidence model. The project
  binding requires both contracts with `exceptions: []`; its pinned schema
  permits only `stricter-or-temporary-block-only` exceptions. A weakening
  exception cannot make missing aggregation, smoke inventory, evidence or
  status read-back pass. Implement the required model or leave the gate unmet.
- **P1 — Test execution route is undefined.** `validate.yml:9,81-95` runs
  Python tests on `ubuntu-latest`, while the current instruction requires
  authoritative tests in Kubernetes. `CI-GATES.md:111-125` also prescribes
  local lint gates despite the no-laptop-gates rule. Name the approved GitHub
  to Kubernetes execution route before treating either result as authority.
- **P1 — “Exact head check” needs two SHAs.** `:15-16` assumes the required
  check is always on the PR head. GitHub may require the test-merge commit's
  check when it has one. Record the PR head SHA, tested merge SHA, check-run
  SHA and base SHA, then reject stale evidence under GitHub's actual rule.
  See GitHub's required-check guidance:
  https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
- **P1 — Review has no merge control.** G3 (`:65-71`) records a reviewed SHA
  but does not make missing or stale review fail a required status. Live
  branch protection does not require approval. Define the enforcement and
  read it back before relying on independent review.
- **P2 — Wrapper and aggregator are underspecified.** G1 adds per-gate
  `check.sh --gate` calls but no final `always()` aggregator, closed-world
  registry check, evidence schema, or status post/read-back. The local index
  currently names `gh pr checks` as read-back, not `scripts/check.sh` as an
  execution wrapper. Distinguish those routes and update the local index when
  the wrapper exists. The planned CLI also needs bounded JSON, stable exit
  codes and `safe_next_step` per `agent-grade-tools.md`.

## Simplification

Make `validate` the single required final result, backed by all required
evidence and smoke records. Preserve every existing gate while moving its
command to the wrapper. Prove a missing, skipped, warning-bearing or wrong-SHA
record makes that final result fail.
