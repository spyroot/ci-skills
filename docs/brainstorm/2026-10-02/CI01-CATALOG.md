# CI-CATALOG adversarial review

Target: PR #11, head `7b0f9099202aa768bbabf69c569400ed8cfe5d8a`.

## Findings

- **P1 — Skill-name containment is undefined.** `CI-CATALOG.md:24-32`
  bounds optional `PATH`, but not `NAME`. Joining an unchecked name to
  `skills/` can escape into ignored local material, even when the later file
  path check succeeds. Resolve names only from discovered skill records; reject
  separators, `.`/`..` and symlinked skill roots. Add `NAME` traversal tests
  for `get` and `install` beside the `PATH` tests at `:131-136`.

- **P2 — A clean revision is not a verified vendor.** `:33-36` carries the
  installer’s Git revision check forward, but a committed vendored tree could
  differ from `vendor.lock.json`. Run offline vendor `verify` before a
  vendored install; test a clean commit with mismatched bytes.

- **P2 — Declared dependencies are not enforced.** CI-ROUTING makes the k8s
  skill depend on `glab`, while `CI-CATALOG.md:22` installs one named skill.
  Define whether `install k8s-admin-diagnostics` verifies or installs `glab`,
  and test an isolated destination missing that dependency.

- **P2 — Failure output is incomplete.** The proposed `path_outside_skill`
  and unknown-skill results need the same stable JSON/exit-code envelope and
  `safe_next_step` as the existing installer, per `agent-grade-tools.md`.

## Simpler shape

Build `list`, `get` and `install` from one validated discovery map keyed by
skill name. Preserve the old installer wrapper and its five exported names;
verify source and dependency bytes before copying. JSON Schema files are a
separate decision; do not describe them as a delivered gate until implemented.
