# CI-TESTS: testing strategy

Status: proposed. Covers every GAL phase. Each phase's pull request carries
its own tests; this page says which tests and why.

## Rules

- **Tests run in CI.** A local run is limited to static checks and never
  replaces CI.
- **Unit tests are offline, deterministic and isolated.** No live cluster,
  network, credentials or package installation (pinned standards,
  `unit-testing` contract).
- **No skipped required tests.** The suite has no `skip` markers today.
  But the workflow skips pytest entirely on a Markdown-only change, and that
  also counts as skipping required tests (CI03-GATES, G6).
- **Mutating commands get the full matrix.** The pinned `unit-testing`
  contract lists the tests every mutating command needs. The mutating
  commands here are `update`, `install` and the hook installer. The matrix
  covers:
  - **planning:** default dry-run, explicit dry-run, zero mutation in a
    dry-run, apply only with `--confirm`, apply only with a valid plan, and
    refusing a plan whose input fingerprint changed;
  - **environment:** a missing tool, a missing input, a terminal
    authentication failure, and bounded retry;
  - **results:** the success and failure schemas, and secret redaction;
  - **cleanup:** on success, on failure, on timeout and on TERM or INT, and
    a cleanup failure changing the final result;
  - **read-back:** a read-back mismatch, and an idempotent second run.
- **Idempotency.** The same input run twice is a no-op that reports `PASS`
  and writes nothing. A different input against an existing target is
  refused, never overwritten.
- **External commands are faked, never called.** `tests/conftest.py`
  provides `fake_bin`, `install_executable` and `call_journal`. Its
  `run_script` drives only the k8s skill's scripts, so a sibling `run_tool`
  fixture drives `bin/ci-skills` and the hook scripts with the same fake
  `PATH`.
- **Tests ride with their capability.** Each phase carries its focused tests;
  CI-TESTS separately delivers only the reusable test command and coverage
  report.

## Layers

| Layer | Tool | Runs | Proves |
| --- | --- | --- | --- |
| Unit | pytest | CI | one module, every reason token |
| Contract | pytest | CI | declarations agree with each other |
| Shell | bats | CI | `./scripts/check.sh` and the hooks |
| Offline smoke | pytest | CI | real entrypoint on the real tree |
| Live | receipt | approved executor | the k8s skill, live |

Contract tests compare declarations that must agree:

- the workflow and the gate registry;
- the catalog and `tools.json`;
- records and their schemas;
- `uses` pointers and declared operations.

## Coverage targets

- **Reason tokens.** Every reason token and status a command can emit has
  at least one test that produces it.
- **Mutating commands.** Each covers the full matrix above.
- **Lines.** coverage.py, pinned in `requirements.txt` (CI03-GATES, G2),
  runs as `coverage run -m pytest` inside the tests gate and prints a
  report. No threshold exists today; whether to set one is an open decision.

## Existing coverage

Observed 2026-10-02:

- 174 test functions in 22 files, no skips, and no coverage measurement.
- The existing diagnostics installer has tests for dry-run, refusal, a
  negative path and a successful read-back. It has none for:
  - staging cleanup after a failed copy;
  - the `installed_digest_mismatch` read-back failure;
  - `pyyaml_unavailable`.
- **Second runs.** Today the installer refuses any existing destination,
  so a second run is a refusal, not a no-op. Package delivery preserves
  its tests while changing the package path. CI01-CATALOG's `install` makes
  the same digest a no-op and refuses a different one.

## `ci-skills` package delivery

The separate delivery pull request in CI-PHASES tests:

- the sole `ci-skills` entry and path assertions;
- the thin checkout adapters and installed diagnostics and PR #2 commands
  from outside the checkout;
- confirmed upgrade from an existing link, refusal without confirmation,
  revision-bound fingerprint, timeout, and installed digest read-back;
- manifest equality in CI and a fresh live receipt for the final package.

## CI03-GATES

- **Contract.**
  - Registry gates have unique ids, and every gate is called by exactly one
    `validate.yml` step.
  - Live acceptance and `verify` stay unconditional and first.
  - The `schedule` trigger is present.
  - The final aggregator fails when an evidence record is missing, skipped,
    warning-bearing or for the wrong commit.
  - Every `requirements.txt` line pins an exact version.
- **Shell (bats).**
  - `--help` lists every argument and output mode; an unknown option exits
    64.
  - The static subset runs only static gates.
  - A failing gate fails the run and names the gate.

## CI-SCHEMA

- Every schema validates against the 2020-12 metaschema.
- Per schema, fixtures for:
  - one valid record;
  - one record per missing required field;
  - one record per broken conditional rule.
- **The version gate.** A schema changed without a version bump fails, and
  so does a MAJOR bump without the new file.
- **Runtime.** An invalid record makes a producer exit 65, naming the file,
  the JSON path and the rule.

## CI02-CLI

The contract test runs over every entrypoint discovery finds:

- `--help` and `--describe` exit 0 with an empty environment and no network;
- `--describe` validates against `command-contract`;
- options accepted equal options described;
- a shared option name keeps one meaning;
- the default run of a mutating command writes nothing;
- exit codes come only from the shared table.

## CI-VENDOR

- **Unit, with a fake `glab`.**
  - `verify` passes on a fixture tree and lock.
  - Each reason token:
    - one flipped byte gives `digest_mismatch`;
    - a deleted `LICENSE` gives `file_missing`;
    - a stray file gives `unexpected_file`;
    - a corrupt lock gives `lock_unreadable`;
    - a symbolic link in a staged or a committed tree gives
      `symlink_unexpected`;
    - an undeclared name gives `skill_unknown`.
  - The lock's `tree` equals `tree_digest`, and each `files` hash equals
    that file's SHA-256.
  - The full matrix for `update`, including:
    - with no `glab`, `update` gives `glab_unavailable` and `verify` still
      passes;
    - a fake `glab` that fails fewer times than `attempts` ends in `PASS`,
      and one that always fails gives `fetch_failed` with every attempt's
      error.
  - **Interrupted transaction:** a process killed between the tree renames
    and the lock rename leaves a marker. The next run restores the previous
    trees and lock, and reports that it recovered.
  - Declared tags, license and notice survive an `update --confirm`.
  - `verify` runs with the PyYAML import blocked, which proves it is
    standard-library only.
- **Contract.** `.markdownlint-cli2.yaml` ignores every vendored skill that
  the lock lists.
- **Offline smoke.** `bin/ci-skills verify --json` on the committed vendored
  tree reports `PASS`.

## CI01-CATALOG

- **Discovery.**
  - The walk order is the direct children of `skills/` in name order;
    the repository root is not a second skill.
  - Exactly one `ci-skills` record resolves to `skills/ci-skills/`.
  - `list --skills-dir DIR` reads a copied installed package; an unconverted
    symbolic link reports `symlink_unexpected` with the upgrade command.
  - Nested and hidden directories are skipped.
  - Each error token: `frontmatter_missing`, `frontmatter_invalid`,
    `name_mismatch`, `manifest_invalid`, `lock_entry_missing` and
    `symlink_unexpected`.
  - `list` exits 65 when any record fails, and still prints the valid ones.
- **Containment.**
  - As `NAME`, `get` and `install` refuse `../x`, `a/b`, `.` and a
    symlinked skill root, all with `skill_unknown`.
  - As `PATH`, `get` refuses `../x`, an absolute path and an escaping
    symbolic link, with `path_outside_skill`.
- **The full matrix for `install`, including:**
  - a vendored skill with bytes that do not match its lock gives
    `vendor_unverified`;
  - a missing dependency gives `dependency_missing`;
  - the same digest twice is a no-op;
  - a different digest gives `destination_differs`;
  - a failed copy removes the staging directory;
  - a read-back mismatch gives `installed_digest_mismatch`.
- **Regression.** `tests/test_installer.py` and
  `tests/test_installed_package.py` pass against the compatibility wrapper;
  package delivery updates path assertions for `skills/ci-skills/` first.
- **Offline smoke.** The installed-package smoke also installs a vendored
  skill and compares digests. `bin/ci-skills` runs from the repository as
  a maintenance command; installed-package smoke exercises the diagnostics
  and PR #2 tools from outside the checkout.
- **Outside CI.** `get glab` is compared byte for byte with
  `glab skills get glab` where `glab` is installed.

## CI-ROUTING

- **Closed world.**
  - Every `REFERENCES` path exists.
  - Every `points_to` names a discovered skill.
  - Every `uses` id resolves.
  - `depends_on` equals the set of `points_to` values.
  - A dangling entry fails.
- **The router.** `SKILL.md` keeps the `references/access.md` link and the
  four safety rules, and `references/reading-reports.md` holds the moved
  sections.
- **Install dependency.** Both `install.sh` and `bin/ci-skills install`
  refuse an absent or mismatched `glab` dependency without partial writes;
  each succeeds once the matching dependency is installed.
- **Matching.**
  - A status token matches only that exact status.
  - A phrase matches the task text as a case-insensitive substring.
  - Text that matches nothing loads nothing.
- **Live.** A new receipt from the approved executor, accepted by the live
  acceptance gate.

## CI-REFERENCE

- **Declarations.** Every call site's argument prefix maps to a declared
  operation, found by scanning the code. No unbounded `api` or `exec`
  operation is labelled read-only.
- **The parser.**
  - Recorded `__complete` output from `glab` 1.120.0, `gh` 2.98.0 and
    `kubectl` v1.36.4 parses as expected.
  - Hostile fixtures are bounded or dropped: directive lines, active-help
    lines, huge output and a hanging command.
- **Results.** `tool_missing` carries a safe next step. `used_by` is
  computed, and matches the `uses` pointers.

## CI04-HOOKS

- **Shell (bats), in temporary git repositories.**
  - A staged defect with an unstaged fix is refused, because the checks
    run on the index snapshot.
  - pre-commit and pre-push both refuse on exits 1, 2, 64, 69 and 127, and
    the commit or push does not proceed.
  - Staged agent instruction files are refused.
- **The full matrix for the installer, including:**
  - an existing hook it did not install gives `hook_exists`;
  - a failed link cleans up and fails;
  - in a linked worktree it installs into the common directory;
  - with a global dispatcher present, delegation reads back;
  - `core.hooksPath` stays unset.

Agent-harness hooks are outside the deliverable, so they have no tests here.

## Open decisions

- **Coverage.** Whether to set a line-coverage floor, once coverage is
  measured.
- **`bats` in CI.** PR #2 runs its Bats suite in an approved CI image that
  holds every declared tool. Whether `validate` uses that image depends on
  the test route (CI03-GATES, G8); the pinned standards forbid ad hoc tool
  installs inside a required job.
