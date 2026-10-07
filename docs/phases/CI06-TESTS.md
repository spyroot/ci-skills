# CI06-TESTS: testing strategy

Status: proposed. Order and dependencies: CI10-PHASES, Phases. Covers every
CIxx phase: each phase's pull request carries its own tests, listed in that
phase's "Delivery, test and proof" section, and this page owns the rules, the
layers, the smoke contract and the test command.

## Rules

- **Unit and contract tests run on the gate route** (D-GATE; CI03-GATES, G0).
  Locally only the static checks run: the pinned `agent-workspace` contract
  says never to run tests on the laptop.
- **Live smoke runs on this laptop**, the executor `tests/acceptance/expected.toml`
  declares, against the test project and the live cluster (D-SMOKE, below).
- **Unit tests are offline, deterministic and isolated.** No live cluster,
  network, credentials or package installation (pinned `unit-testing`
  contract, "Default Boundary").
- **No skipped required tests.** The suite has no `skip`, `skipif` or `xfail`
  marker today. The deleted `validate` workflow (#27) skipped pytest entirely
  on a Markdown-only change, which also counts as skipping required tests;
  the gate route must not (CI03-GATES, G6).
- **Mutating commands get the full matrix.** The pinned `unit-testing`
  contract ("Required Tests for a Mutating Entrypoint") lists the tests every
  mutating command needs; this bullet defines the matrix the rest of this
  document calls "the full matrix". The mutating commands today are the five
  `core/catalog.py` marks `mutates: True`; planned are `update` (CI05-VENDOR),
  `install` (CI01-CATALOG), the hook installer (CI04-HOOKS) and the
  CI11-TOOLS actions. The matrix covers:
  - **planning:** default dry-run, explicit dry-run, zero mutation in a
    dry-run, apply only with the plan-bound confirmation (CI02-CLI, item 6),
    apply only with a valid plan, and
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
- **External commands are faked, never called.** `tests/python/conftest.py`
  provides `fake_bin`, `install_executable` and `call_journal`. Its
  `run_script` drives only the Python mains under its `SCRIPT_ROOT`, a path
  that does not exist today (`conftest.py:17-19`; block 0 points it at
  `ci-skills/bin/`, CI10-PHASES, Order). A sibling `run_tool` fixture
  (planned, with `bin/ci-skills`: CI05-VENDOR) drives `bin/ci-skills` and
  the hook scripts (planned, CI04-HOOKS) with the same fake `PATH`.
- **Tests ride with their capability.** Each phase carries its focused tests;
  CI06-TESTS separately delivers only the reusable test command and coverage
  report (Delivery, test and proof).

## Layers

| Layer | Tool | Runs | Proves |
| --- | --- | --- | --- |
| Unit | pytest | gate route (D-GATE) | one module, every reason token |
| Contract | pytest | gate route (D-GATE) | declarations agree with each other |
| Shell | bats | gate route (D-GATE) | `./scripts/check.sh` and the hooks |
| Offline smoke | pytest | gate route (D-GATE) | real entrypoint on the real tree |
| Live smoke | receipt | this laptop, test project, live cluster | each tool executed, output locked, read back |

Contract tests compare declarations that must agree:

- the gate list `./scripts/check.sh --dry-run` prints (CI03-GATES, Steps) and
  the workflow the gate route names, once one exists (D-GATE);
- the catalog and `tools.json`;
- records and their schemas (planned, CI07-SCHEMA);
- `uses` pointers and declared operations (planned, CI07-SCHEMA and
  CI09-REFERENCE).

## Live smoke: the proof that a tool did what it says

D-SMOKE (CI10-PHASES, Decisions taken): live smoke runs from this laptop, the
executor `tests/acceptance/expected.toml` declares, against the targets
declared there, and the proof is not an exit code but the tool's own output
with what it read back (the pinned `smoke-testing` contract, "Required
Invariants", asks for independent read-back). We are not testing the CI
system; we are proving that the action executed and that the output is
consistent. The local guide's sentence that a laptop is not release evidence
applies to unit and contract tests, which run on the gate route (D-GATE;
CI03-GATES, G0 and G8), not to live smoke: a smoke receipt is accepted only
from the declared executor, and only by the checker (Verification, below).

### Targets, declared once in `tests/acceptance/expected.toml`

`[targets]` declares `gitlab`, the host every GitLab case reads and writes,
and `github`, the repository of the publication receipt; `job_url` the one
completed job the read cases use; `[targets.kubernetes]` the `context` and
`server`; `ceph_namespace` the namespace of the Ceph check; `[[executors]]`
the host and identities a receipt must come from; and each
`[[gitlab_receipts]]` entry, in `target_path`, the project a write case acts
on. Harbor is declared there before the first Harbor smoke case (CI11-TOOLS),
never chosen by a tool.

### A smoke case

One per tool, declared in `expected.toml` as `[[smoke_cases]]` (planned:
today the file has `[[gitlab_receipts]]` only; the checker's `[[smoke_cases]]`
verification is CI03-GATES G5's, and the smoke-case schema row is
CI07-SCHEMA's) with `tool`, `operation`, the fixed `args` (no random values;
the receipt records their digest and the checker rejects a receipt whose
arguments differ), the expected `status`, and the `readback` fields that
prove the action. The case is run with `--receipt-out
tests/acceptance/receipts/<tool>-<case>.json`, which writes the sanitized
form (host paths digested, no credential values; `core/portable.py`).

Today `gitlab_job.py` takes `--job-url URL` and `--search` and declares no
subcommand (`ci-skills/bin/gitlab_job.py`, `core/catalog.py`); CI11-TOOLS
adds the verbs (`get`, per its one query grammar) and owns the compatibility
rule for the committed receipt.

What the read-back is, per class of tool (CI11-TOOLS owns the per-tool smoke
cases):

- **Read:** the records by id and count.
- **Logs:** the bounded trace with the declared marker.
- **Track:** the status sequence to a terminal state.
- **Mutating:** plan digest, `APPLIED`, field-by-field `GET` read-back,
  second run `NO_OP`.
- **Build and push:** registry digest equals the reported digest.
- **ISO:** sha256, `HEAD` 200, server stopped.
- **Concurrent reads:** per-read durations beside wall time.

### What a receipt carries (the fields the checker compares)

The fields `command-result` locks (CI07-SCHEMA, planned); the checker
compares them. Smoke-specific on top: `plan`, `plan_digest`, `readback`
(`action`, `id`, the compared fields, `verified`) and `result_action`
(`APPLIED` or `NO_OP`). Today's operation receipts under
`tests/acceptance/receipts/` already hold all four.

### Verification

`tools/check_live_acceptance.py --root . --expected tests/acceptance/expected.toml
--receipts tests/acceptance/receipts --skill ci-skills --json` compares every
receipt with what `expected.toml` declares. Today, for `[[gitlab_receipts]]`:
the executor host and identities, the targets, the kind and operation,
`result_action` with `readback.verified`, the `APPLIED` and `NO_OP` pair
sharing one `plan_digest` and one resource identity, the skill digest and the
age (`max_receipt_age_days`); a declared host or operation with no receipt,
and a receipt nobody declared, fail it. `[[smoke_cases]]` adds the
fixed-argument digest and the read-back fields each case names (planned,
CI03-GATES G5), so that a tool with no smoke case, or a case with no receipt,
fails the check; D-DIGEST narrows the digest to code (`references/vendor/**`
excluded, planned: today `core/provenance.py` excludes only caches). Unit
tests mock; a receipt is the only evidence that a tool ran live.

## Coverage targets

- **Reason tokens.** Every reason token and status a command can emit has
  at least one test that produces it.
- **Mutating commands.** Each covers the full matrix (Rules).
- **Lines.** This phase adds coverage.py to `requirements.txt` as an exact
  pin (CI03-GATES, G2 names it as this phase's; today the file holds three
  ranges and no coverage line) and runs `coverage run -m pytest` inside the
  `tests` gate (Delivery, test and proof), which prints a report. No
  threshold exists today; whether to set one is an open decision.

## Existing coverage

Observed 2026-10-06:

- 398 `def test_` functions in 40 files under `tests/python/` and 39 `@test`
  cases in 4 files under `tests/bash/`; no `skip`, `skipif` or `xfail`
  marker; no coverage measurement.
- Before block 0 the suite could not import: `tests/python/conftest.py`
  resolved `REPO_ROOT` to `tests/` and `SCRIPT_ROOT` to `tests/skills/ci-skills/scripts`,
  so `import_script_module` found no `core`. Block 0 fixed it (`LIB_ROOT` is
  `ci-skills/lib/python`). The Bats suite's
  `tests/bash/check.bats` sources `../lib/ci/check.bash`, a path that does
  not exist either: the library was deleted in #26.
- No execution surface exists (D-GATE; CI03-GATES, G0).
- The installer (`tools/install_ci_skills.py`) has tests for dry-run,
  refusal, a negative path and a successful read-back
  (`tests/python/test_installer.py`). No test names or asserts:
  - the staging directory's removal after a failed copy;
  - the `installed_digest_mismatch` read-back failure;
  - `pyyaml_unavailable`.
- **Second runs.** Today the installer refuses any existing destination
  (`destination_exists`), so a second run is a refusal, not a no-op. #21
  (merged) kept those tests and moved the package path. CI01-CATALOG's
  `install` makes the same digest a no-op and refuses a different one.

## CI03-GATES

Tests: CI03-GATES, Delivery, test and proof.

## CI07-SCHEMA

Tests: CI07-SCHEMA, Delivery, test and proof.

## CI02-CLI

Tests: CI02-CLI, Delivery, test and proof.

## CI05-VENDOR

Tests: CI05-VENDOR, Delivery, test and proof.

## CI01-CATALOG

Tests: CI01-CATALOG, Delivery, test and proof.

## CI08-ROUTING

Tests: CI08-ROUTING, Delivery, test and proof.

## CI09-REFERENCE

Tests: CI09-REFERENCE, Delivery, test and proof.

## CI04-HOOKS

Tests: CI04-HOOKS, Delivery, test and proof.

## Delivery, test and proof

1. *Delivery.* This phase adds the `tests` gate to the one entrypoint,
   `./scripts/check.sh` (CI03-GATES, G1), and an exact coverage.py pin to
   `requirements.txt` (CI03-GATES, G2). The gate runs, from the repository
   root, the two pytest runs the deleted workflow had
   (`1108cca^:.github/workflows/validate.yml`, steps "Isolated
   installed-package smoke" and "Tests"), under coverage and on today's
   paths:

   ```text
   coverage run -m pytest -q tests/python/test_installed_package.py
   coverage run -a -m pytest -q --ignore=tests/python/test_installed_package.py tests/python
   coverage report
   ```

   Two gaps, none of them closed here by invention: `scripts/check.sh:6`
   sources `lib/ci/check.bash`, deleted in #26, so the entrypoint cannot run
   until CI03-GATES, G1, restores it; G1 names no argument
   that runs one gate, so the argv that runs only `tests` is G1's to name.
   If the gate library's home is `ci-skills/lib/bash/ci/check.bash`
   (CI10-PHASES, Gate library home), it sits inside the digested tree
   (`core/provenance.py`, `_included`), and adding the `tests` gate moves the
   skill digest (part 5).
2. *Tests*, written with the block; run status UNVERIFIED (CI03-GATES, G0).
   In `tests/bash/check.bats`, on the fixture and stub pattern it already
   uses (`make_check_run_fixture`, `make_success_stubs`):
   - `--dry-run` lists the `tests` gate and calls no `coverage` or `pytest`
     (the deleted library listed it as `unit`: `check.bats`, first case);
   - outside Kubernetes (`KUBERNETES_SERVICE_HOST` unset) the gate exits 69
     with `Kubernetes execution is required`, the guard the suite already
     asserts;
   - with a stub `coverage` on `PATH`, the gate calls the three lines above
     in that order and nothing else (call journal);
   - a stub `coverage` that exits non-zero fails the run and names the
     `tests` gate;
   - no `coverage` on `PATH` exits 69, like the suite's other blocked cases.

   The literal 69 is the restored library's blocked code; once D-EXIT is
   decided (CI02-CLI), the cases assert the one table's blocked code.

   The exact-pin contract test CI03-GATES lists covers the new
   `requirements.txt` line; no second test.
3. *Smoke.* This phase changes no live behaviour and declares no smoke case.
   Its static read-back is the gate's own summary: pytest's final `N passed`
   line for each run and the `TOTAL` line of `coverage report`, in the check
   output. Where the report is written as a file is not declared anywhere;
   this phase names it when CI03-GATES G6 fixes the evidence layout
   (`reports/ci/<job>.json`).
4. *Evidence.* No receipt: the static evidence is the output of
   `./scripts/check.sh` running the `tests` gate (the three lines above),
   kept by the gate route once one exists (D-GATE).
5. *Verification.* The `tests` gate is a byte change under `ci-skills/`
   (part 1), so the receipts are recaptured on the D-SMOKE executor
   (CI03-GATES, G5); then `tools/check_live_acceptance.py --root . --expected
   tests/acceptance/expected.toml --receipts tests/acceptance/receipts
   --skill ci-skills` prints `Live acceptance: PASS` (exit 0).
   Observed 2026-10-06, that command exits with
   `ModuleNotFoundError: No module named 'core'`
   (`tools/check_live_acceptance.py:450-453` inserts `skills/ci-skills/scripts`,
   which does not exist; block 0 re-points `tools/*.py`), and the committed
   receipts no longer match the tree's digest (CI10-PHASES, Pull request
   status), so the line reads `PASS` only once both are repaired.

## Open decisions

- **Live steps read from a third-party binary.** CI05-VENDOR's smoke reads
  `glab skills get` and `glab skills list`, which are not tools of ours, and
  a receipt comes only from a tool run (Live smoke); which record carries
  their result is open here.
- **Coverage.** Whether to set a line-coverage floor, once coverage is
  measured.
- **`bats` on the gate route.** `tests/bash/` holds 4 Bats files (39 cases)
  and CI03-GATES G1 names `scripts/check.sh` as their runner. Which image
  runs them, holding every declared tool, is the gate route's choice (D-GATE;
  CI03-GATES, G0 and G8): the pinned `blockers` contract forbids installing
  tools ad hoc inside a required CI job.
