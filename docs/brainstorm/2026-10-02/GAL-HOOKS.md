# GAL-HOOKS adversarial review

Target: PR #11, head `7b0f9099202aa768bbabf69c569400ed8cfe5d8a`.

## Findings

- **P1 — Install path targets global hooks here.** `GAL-HOOKS.md:60-66`
  uses `git rev-parse --git-path hooks`; with the current global
  `core.hooksPath`, that resolves to the operator’s global hook directory,
  not this repo’s hook storage. The existing dispatcher delegates to the
  common Git directory’s `hooks/`. Derive that directory from
  `git rev-parse --git-common-dir`, preserve any existing target, and read
  back delegation in both primary and linked worktrees.
- **P1 — Staged failures can escape.** `:37-39,95-96` promises a bad staged
  file is refused, but neutrality and manifest checks read working-tree
  bytes. An unstaged repair can hide the staged defect. Run staged checks
  against an index snapshot, or narrow the hook to advisory working-tree
  checks and rely on exact-commit CI.
- **P1 — Local gates conflict with the operator route.** `:25-27,37` runs
  project lint and policy gates on the laptop, while current instructions
  permit local inspection/dispatch and require authoritative gates in
  Kubernetes. Define the approved dispatch route before making those hooks
  blocking. A local PASS cannot replace the required `validate` result.
- **P2 — Installer contract is absent.** `:89-90` says only “add the install
  step.” Specify `--help`, dry-run, idempotent behavior, bounded JSON, stable
  exit codes, safe next steps, preservation of existing hooks and read-back,
  following `agent-grade-tools.md`.

## Simpler path

Reuse `scripts/check.sh` as the only gate owner. Hook wrappers should select
an approved route, not reimplement commands. Test a staged/working-tree
split, pre-existing hook target, linked worktree and missing dispatcher.
