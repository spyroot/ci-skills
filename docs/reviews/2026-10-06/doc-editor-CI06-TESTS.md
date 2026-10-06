# doc-editor-CI06-TESTS

Saved 2026-10-06 from the session transcript `~/.claude/projects/-Users-spyroot-dev-ci-skills/279dd962-305b-4ab4-835e-7e6e872c8f60/subagents/agent-ab928df0d95f8bbd2.jsonl`. Editor report for CI06-TESTS (retry). Agent output, kept verbatim; a log, not present state.

---

Edited exactly one file: docs/phases/CI06-TESTS.md (293 lines; `git diff --stat`: 206 insertions, 251 deletions against HEAD). No git write commands, no other file touched.

## Edits, by section

- **Header.** Now "Status: proposed. Order and dependencies: CI10-PHASES, Phases." plus one sentence: each phase's tests are listed in that phase's "Delivery, test and proof" section; this page owns the rules, layers, smoke contract and test command.
- **Rules.**
  - D-GATE bullet is the finding-2 sentence verbatim: "Unit and contract tests run on the gate route (D-GATE; CI03-GATES, G0)." followed by "Locally only the static checks run" citing the pinned `agent-workspace` contract (verified at the pinned revision, `docs/agents/contracts/agent-workspace.md:172`).
  - Unit-test isolation bullet now cites the pinned `unit-testing` contract, "Default Boundary" (verified).
  - "No skipped required tests": present-tense "the workflow skips pytest" (deleted in #27) rewritten as the deleted `validate` workflow's behaviour, which the gate route must not repeat (CI03-GATES, G6); "no `skip`, `skipif` or `xfail` marker" verified by grep over `tests/`.
  - Mutating matrix bullet: states it defines "the full matrix" (later mentions now say "(Rules)"); **extra correction beyond the numbered findings, flagged:** "The mutating commands here are `update`, `install` and the hook installer" was present tense about planned things; replaced with "the five `core/catalog.py` marks `mutates: True`" (count verified: 5) plus the planned ones (CI05-VENDOR, CI01-CATALOG, CI04-HOOKS, CI11-TOOLS actions).
  - "External commands are faked": `run_script` now described as driving the mains under `SCRIPT_ROOT`, a path that does not exist today (`conftest.py:17-19`; block 0 re-points it); `run_tool` and `bin/ci-skills` marked planned (CI05-VENDOR), hook scripts planned (CI04-HOOKS).
- **Layers.** The four "CI" cells are "gate route (D-GATE)". Contract list: "the workflow and the gate registry" (neither exists; "registry" has no owner in CI03 or the pinned `ci` contract) became the gate list `./scripts/check.sh --dry-run` prints (CI03-GATES, Steps) and the workflow the gate route names once one exists; schema and `uses` rows marked planned.
- **Live smoke.** Decision cited once as "D-SMOKE (CI10-PHASES, Decisions taken)"; "Decided by the operator" and `expected.toml:36-38` dropped; added the sentence this document owns and lacked: the local guide's "a laptop is not release evidence" applies to unit and contract tests on the gate route (D-GATE; CI03-GATES, G0 and G8), not to live smoke. Cites the pinned `smoke-testing` contract's "Required Invariants" for independent read-back (verified).
- **Targets.** One sentence naming the keys only: `[targets]` `gitlab`/`github`, `job_url`, `[targets.kubernetes]` `context`/`server`, `ceph_namespace`, `[[executors]]`, `[[gitlab_receipts]]` `target_path`. No literal values, no line numbers; Harbor: "declared there before the first Harbor smoke case (CI11-TOOLS)"; the host name dropped.
- **A smoke case.** `[[smoke_cases]]` marked planned with owners (checker verification: CI03-GATES G5; schema row: CI07-SCHEMA) and "today the file has `[[gitlab_receipts]]` only" (verified). Finding 1: new paragraph "Today `gitlab_job.py` takes `--job-url URL` and `--search` and declares no subcommand (`ci-skills/bin/gitlab_job.py`, `core/catalog.py`); CI11-TOOLS adds the verbs (`get`, per its one query grammar) and owns the compatibility rule for the committed receipt." (CI11 has no "compatibility" text yet, so it is written as ownership, not as an existing statement.)
- **Per-class read-backs.** The seven one-liners from finding 5, verbatim; the `fetch` example is gone.
- **Receipt fields.** "The fields `command-result` locks (CI07-SCHEMA, planned); the checker compares them" plus `plan`, `plan_digest`, `readback` (`action`, `id`, compared fields, `verified`), `result_action`; verified all four exist in today's receipts.
- **Verification.** Separates what the checker compares today for `[[gitlab_receipts]]` (host, identities, targets, kind/operation, `result_action` + `readback.verified`, the APPLIED/NO_OP pair sharing `plan_digest` and resource identity, digest, age; from `tools/check_live_acceptance.py:320-420, 480-617`) from the planned `[[smoke_cases]]` additions (CI03-GATES G5) and D-DIGEST (planned; `core/provenance.py:43-45` excludes only caches today).
- **Coverage targets.** "coverage.py, pinned in `requirements.txt`" was false (the file holds three ranges, no coverage line); now "this phase adds ... an exact pin (CI03-GATES, G2 names it as this phase's)" and the `tests` gate marked as this phase's delivery.
- **Existing coverage.** Observed 2026-10-06: 398 `def test_` functions in 40 files (`grep -c '^def test_'`, sum verified; 0 indented ones), 39 `@test` cases in 4 bats files; cannot import today with file:line (`conftest.py:17-19` resolves `REPO_ROOT` to `tests/`, `SCRIPT_ROOT` to `tests/skills/ci-skills/scripts`; `import_script_module` at `:34-39`); `tests/bash/check.bats` sources the `lib/ci/check.bash` deleted in #26; no execution surface (D-GATE; CI03-GATES, G0). Installer gaps re-verified (no test names `installed_digest_mismatch`, `pyyaml_unavailable` or staging). "Package delivery preserves its tests" became "#21 (merged) kept those tests and moved the package path."
- **`ci-skills` package delivery section:** deleted (finding 7).
- **Open decisions.** The bats bullet no longer cites PR #2 (closed unmerged) or `validate` (deleted): now "`bats` on the gate route" with the 4 files/39 cases, CI03-GATES G1 as runner owner, image choice = D-GATE (CI03-GATES, G0 and G8), and the ad hoc install prohibition attributed to the pinned `blockers` contract (`blockers.md:69` at the pin; the `ci`, `unit-testing`, `smoke-testing` and `agent-workspace` contracts carry no such line).
- **New "## Delivery, test and proof"** (line 224, unnumbered heading, before "Open decisions"), five parts:
  1. Delivery: the `tests` gate of `./scripts/check.sh` (CI03-GATES, G1) and the coverage pin (G2); argv block `coverage run -m pytest -q tests/python/test_installed_package.py`, `coverage run -a -m pytest -q --ignore=tests/python/test_installed_package.py tests/python`, `coverage report` (two-run split, `-q`, paths and step names from the deleted workflow at `1108cca^:.github/workflows/validate.yml:107,111`). Three gaps named with owners instead of invented: `scripts/check.sh:6` sources `lib/ci/check.bash` deleted in #26 (block 0); G1 names no single-gate argument (G1); G1's library path `ci-skills/lib/bash/ci/check.bash` (planned, CI03-GATES) is inside the digested tree, so inside/outside decides the digest effect (G1).
  2. Tests: five meta-tests in `tests/bash/check.bats` derived from its existing fixture/stub pattern and guards (dry-run lists `tests`, Kubernetes guard exit 69, call order via journal, failing stub names the gate, missing `coverage` exits 69); the exact-pin test CI03 lists covers the new line. States "written with the block; run status UNVERIFIED (CI03-GATES, G0)".
  3. Smoke: no live behaviour; static read-back = pytest's final `N passed` lines and `coverage report`'s `TOTAL`; report file path undeclared, gap owned by this phase against CI03-GATES G6's evidence layout.
  4. Evidence: "No receipt: the static evidence is the output of `./scripts/check.sh` running the `tests` gate".
  5. Verification: the checker command with the four real flags (`main()` at `:624-647`) and expected `Live acceptance: PASS` (`:679`, exit 0 `:684`); observed 2026-10-06 it exits `ModuleNotFoundError: No module named 'core'` (`:450-453` inserts `skills/ci-skills/scripts`), and receipts no longer match the digest (CI10-PHASES, Pull request status).

Disclosure: in the argv block, `-a` (`coverage run --append`) is the only token taken from coverage.py's CLI rather than from the tree or the document; everything else is the document's own `coverage run -m pytest` form or the deleted workflow's.

## Per-phase lists: collapsed vs kept (finding 10, done last)

- Collapsed to "Tests: CIxx-NAME, Delivery, test and proof.": CI01-CATALOG (:299), CI04-HOOKS (:130), CI05-VENDOR (:272), CI08-ROUTING (:186), CI09-REFERENCE (:490, numbered "## 7." — my first anchored grep missed it; re-run loosely), and CI02-CLI (:224) and CI03-GATES (:253), which gained their headings between my first and final pass and were collapsed on the final pass.
- Kept unchanged: CI07-SCHEMA (no heading as of the last pass). CI11-TOOLS gained a heading (:880) but this document never had a CI11 list.
- Deleted "Agent-harness hooks are outside the deliverable..." (removed with the CI04 collapse).

## Findings not applied as written, with evidence

- **Finding 8:** neither "(G0, G8)" nor "pinned mutating matrix" occurs in the file (grep); the phrase lives in CI11-TOOLS:152. Finding 2's rewrite covers the G0 sentence; "the full matrix" mentions now point to Rules, where it is defined.
- **Finding 7, "lines 78-80":** no package-delivery pointer there (that is the Harbor bullet). The two real pointers (Existing coverage; CI01 Regression) were handled; the CI01 one disappeared with the collapse.
- **Finding 10, `skills/` → `vendor/skills/`:** the line was removed by the CI01 collapse rather than edited.
- **New section, part 2:** listed only CI06's meta-tests, not "the knowledge-kind tests the document already lists for CI09": CI09-REFERENCE owns them in its own section 5 and section 7 (:490), and this document's old CI09 list was the operations kind anyway; one list, one owner.
- **Finding 1 "compatibility rule":** written as CI11-TOOLS ownership, because CI11's text does not yet contain it (grep for "compatib" empty).

## Tree facts worth relaying

- `scripts/check.sh:6` and `tests/bash/check.bats:5` both source `lib/ci/check.bash`, deleted in #26 (`88bd9f6`); CI03-GATES G1 still describes that library as merged.
- `tools/check_live_acceptance.py` cannot run today (`ModuleNotFoundError: No module named 'core'`, `:450-453`); its `--expected`/`--receipts`/`--skill` defaults (`:632-644`) point at the old `acceptance/` and `skills/ci-skills` paths.
- CI11-TOOLS still heads its GitLab rows "`gitlab_job.py fetch`" (:522, :543) while its grammar names `get` (:200); this document follows the brief's `get`.

## Checks

- `/opt/homebrew/bin/markdownlint-cli2 docs/phases/CI06-TESTS.md`: `Summary: 0 issues in 0 files` (markdownlint-cli2 v0.23.2, markdownlint v0.41.1).
- No line over 120 columns (awk), `git diff --check` clean, 0 trailing spaces, project-neutrality marker count 0 in the file, no "operator"/agent names in the file (only the `agent-workspace` contract name).
