# GAL-TESTS: testing strategy

Status: proposed. Covers every GAL phase. Each phase's pull request carries
its own tests; this page says which tests and why.

## Rules

- **Tests run in CI.** They run in the `validate` workflow; a local run is
  limited to static checks and never replaces CI.
- **Unit tests are offline, deterministic and isolated.** They use no live
  cluster, network, credentials or package installation (pinned standards,
  `unit-testing` contract).
- **No `skip`.** A test that does not apply is excluded by a documented
  rule, never skipped at runtime. The suite has no skips today.
- **Mutating entrypoints get the full checklist.** Every changed mutating
  entrypoint has tests for dry-run, unsafe-apply refusal, a negative path,
  cleanup, read-back and idempotency; missing coverage blocks merge (pinned
  `unit-testing` contract, "Coverage Gate"). In these phases the mutating
  entrypoints are:
  - `update` (GAL-VENDOR)
  - `install` (GAL-CATALOG)
  - the hook install step (GAL-HOOKS)
- **External commands are faked, never called.** `tests/conftest.py`
  already provides `fake_bin`, `install_executable`, `run_script` (fakes
  first on `PATH`) and `call_journal`. A fake `glab` reuses that kit.
- **Tests ride with their capability.** No phase lands its tests in a
  separate pull request.

## Layers

| Layer | Tool | Runs | Proves |
| --- | --- | --- | --- |
| Unit | pytest | CI | one module, every reason token |
| Contract | pytest | CI | declarations agree with each other |
| Shell | bats | CI | `./scripts/check.sh` and the hooks |
| Offline smoke | pytest | CI | real entrypoint on the real tree |
| Live | receipt | declared executor | the k8s skill, live |

Contract tests compare declarations that must agree: the workflow and the
gate registry, the catalog and `tools.json`, and the declared references
and the files on disk.

## Coverage targets

- **Reason tokens.** Every reason token and status a module can emit has at
  least one test that produces it.
- **Mutating entrypoints.** Each meets the six-item checklist above.
- **Lines.** Line coverage is measured with coverage.py and reported in CI.
  No threshold exists today; whether to set a floor is an open decision.

## Existing coverage

Observed 2026-10-02:

- 174 test functions in 22 files, no skips, and no coverage measurement.
- `tools/install_k8s_admin_diagnostics.py` already has tests for dry-run,
  refusal, a negative path, a successful read-back and idempotency. It has
  none for:
  - staging cleanup after a failed copy;
  - the `installed_digest_mismatch` read-back failure;
  - `pyyaml_unavailable`.

  GAL-CATALOG changes the installer, so these three tests become required
  in that pull request.

## GAL-GATES

- **Contract.**
  - Every registry gate has an id, a command and a profile, and ids are
    unique. The registry parses with the standard library alone.
  - Every registry gate is called by exactly one `validate.yml` step.
  - Live acceptance and `verify` stay unconditional, run before the gated
    steps, and never set `continue-on-error`. This extends
    `tests/test_validate_workflow_policy.py`.
  - Every `requirements.txt` line pins an exact version.
- **Shell (bats).**
  - `--help` lists every argument and output mode; an unknown option exits
    non-zero.
  - `--profile static` runs only static gates, checked through the call
    journal of fake tools.
  - A failing gate makes the run fail and names the gate. A gate that exits
    2 (`BLOCKED`) also fails it.
  - `--json` returns the documented result shape.

## GAL-VENDOR

- **Unit, with a fake `glab`.**
  - `verify` passes on a fixture tree and lock.
  - Each reason token:
    - one flipped byte gives `digest_mismatch`;
    - a deleted `LICENSE` gives `file_missing`;
    - a stray file gives `unexpected_file`;
    - a corrupt lock gives `lock_unreadable`.
  - `update` writes the trees and the lock. The lock's `tree` equals
    `tree_digest`, and each `files` hash equals that file's SHA-256.
  - **Dry-run:** `update --dry-run` reports the changes and leaves every
    byte unchanged.
  - **Refusal:**
    - an unknown name gives `skill_unknown`;
    - with no `glab`, `update` gives `glab_unavailable` and `verify` still
      passes.
  - **Retry:**
    - a fake `glab` that fails fewer times than `attempts` ends in `PASS`;
    - one that always fails gives `fetch_failed`, with every attempt's
      error.
  - **Cleanup:** a failure injected at each transaction step leaves the
    previous trees and lock byte-identical. The injected failures are a
    staged name mismatch, an unwritable lock directory and a failed rename.
  - **Idempotency:** a second `update` with unchanged upstream changes
    nothing.
  - Declared tags, license and notice survive an `update`.
  - `verify` runs with the PyYAML import blocked, which proves it is
    standard-library only.
- **Contract.** `.markdownlint-cli2.yaml` ignores every vendored skill that
  the lock lists.
- **Offline smoke.** `tools/ci_skills.py verify --json` on the committed
  vendored tree reports `PASS`.

## GAL-CATALOG

- **Unit.**
  - `list`:
    - one record per skill, with the per-source fields present or absent
      as declared;
    - an empty skills directory lists nothing and passes;
    - malformed frontmatter reports a named error.
  - `get`:
    - with no `PATH`, prints `SKILL.md`;
    - refuses `../x`, an absolute path and an escaping symbolic link with
      `path_outside_skill`.
  - `install`, against the full checklist:
    - **Dry-run** writes nothing.
    - **Refusal:** an unverified revision, an existing destination and a
      symbolic link are refused.
    - **Negative path:** a missing `SKILL.md`.
    - **Cleanup:** the staging directory is removed after a failed copy.
      This is new.
    - **Read-back:** the installed digest equals the source digest, and a
      mismatch reports `installed_digest_mismatch`. The mismatch test is
      new.
    - **Idempotency:** a second install is refused and leaves the
      destination unchanged.
- **Regression.** `tests/test_installer.py` and
  `tests/test_installed_package.py` pass unchanged against the wrapper.
- **Offline smoke.** The installed-package smoke also installs a vendored
  skill and compares digests.
- **Outside CI.** `get glab` is compared byte for byte with
  `glab skills get glab` where `glab` is installed; CI does not install it.

## GAL-ROUTING

- **Contract.**
  - Every `REFERENCES` path exists in the skill, and each reference has at
    least one `load_when` entry.
  - Every `points_to` names a skill that `list` reports, and `depends_on`
    equals the set of `points_to` values.
  - Every routing entry names a declared command and a declared reference.
    The routing test covers every command, read through `.command`.
  - `tools.json` stays byte-equal to the catalog (existing).
  - The `SKILL.md` router keeps the `references/access.md` link (existing)
    and each of the four safety rules (new).
  - `references/reading-reports.md` holds the moved sections' headings.
- **Unit (matching).**
  - A status token matches only that exact status.
  - A phrase matches the task text as a case-insensitive substring.
  - Text that matches nothing loads nothing.
- **Live.** A new receipt from the declared executor, accepted by the live
  acceptance gate (existing, with `tests/test_live_acceptance.py`).

## GAL-HOOKS

- **Shell (bats), in temporary git repositories:**
  - pre-commit calls `./scripts/check.sh --profile static`, checked through
    a fake script's journal;
  - it refuses the commit when that script exits 1, 2 or 127, so it fails
    closed;
  - it refuses staged agent instruction files and allows others;
  - pre-push calls the live-acceptance gate.
- **The install step, against the checklist:**
  - **Dry-run** writes nothing.
  - **Placement:** it links into `git rev-parse --git-path hooks`, both in
    a normal repository and in a linked worktree.
  - It leaves `core.hooksPath` unset.
  - **Idempotency:** a second run changes nothing.

## Open decisions

- **Line coverage floor.** Whether to set one, now that coverage would be
  measured.
- **`bats` in CI.** CI needs `bats` on the runner. The pinned standards
  forbid ad hoc tool installs inside a required job, so it needs either a
  pinned runner image or a recorded exception (GAL-GATES, G6).
