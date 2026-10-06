# GAL-VENDOR adversarial review

Target: PR #11, head `7b0f9099202aa768bbabf69c569400ed8cfe5d8a`.

## Findings

- **P1 — Symlinks can evade verification.** `GAL-VENDOR.md:102` reuses
  `core.provenance._included`, which skips symlinks (`provenance.py:52-63`).
  The existing installer rejects symlinks (`install_k8s_admin_diagnostics.py:
  39-46`). Reject them in staged and committed vendor trees and test both
  negative paths; a matching lock alone must not make a linked tree safe.
- **P2 — Interrupted updates lack recovery.** The rollback at
  `GAL-VENDOR.md:141-154` covers handled failures, but a killed process
  between tree and lock renames leaves partial state. Add a transaction marker
  and next-invocation recovery, with an interrupted-state fixture.
- **P2 — Bootstrap order violates the project runtime rule.** `:172-181`
  runs the Python updater before adding `environment.yml`. Create the conda
  environment and its declared dependencies before that first run.
- **P2 — Discovery and failure contracts are brittle.** `glab skills list`
  is experimental and has human output but no JSON mode in installed glab
  1.120.0. Declare the two allowed bundled sources in `skills/vendor.toml`,
  or pin and fixture-test parsing. The `verify`/`update` result tokens at
  `:116-137` also need stable JSON, exit codes and `safe_next_step` for each
  failure, as `agent-grade-tools.md` requires.

## Smallest gate

Keep `orbit` out until its license is resolved. Run offline `verify`
unconditionally in the required CI result; test byte drift, symlink insertion,
missing glab, interrupted transaction and read-back after update. Do not
publish a passing result from local checks alone.
