# CI10-PHASES: plan overview and order

Status: proposed. This page orders the phases; each phase document owns its
own design, steps and gates. Read back against the tree at `e85c7c8` on
2026-10-06.

## Terms

- **Phase:** one block of work, delivered as one pull request per block.
- **Gate:** a check that blocks a merge.
- **Reference:** a skill's document under `references/`, of kind
  `operations` or `knowledge` (CI07-SCHEMA, CI09-REFERENCE).
- **Operation:** one way our code calls an external tool (CI09-REFERENCE).
- **Pointer:** a `uses` entry linking a command or reference to the
  operations it relies on (CI07-SCHEMA).
- **Navigator:** the one command that answers "what can this skill do about
  X, and where do I look next" (CI09-REFERENCE, section 3).

## Layout, decided

In the tree today (fixed on 2026-10-04): thin mains in `ci-skills/bin/`, the
Python library in `ci-skills/lib/python/core/`, the Bash libraries in
`ci-skills/lib/bash/{core,ci,automation}/`, references in
`ci-skills/references/`, the generated `tools.json` beside `SKILL.md`.
Decided, created by the phases named: maintenance-only Python in
`tools/skillkit/` importing `core` from `ci-skills/lib` (CI07 first, then CI05, CI09, CI01),
thin maintenance mains in `tools/` plus the one root command `bin/ci-skills`
(CI05 creates it, CI01 extends it), vendored skills in `vendor/skills/<name>/`
with the declarations and the one lock in `vendor/` (CI05), vendored
references in `ci-skills/references/vendor/<name>/` (CI09), schemas in
`schemas/` (CI07). Every tool is a small main that calls the library; a
second implementation of a behaviour the library has is a defect.

## Phases

| Phase | Delivers | Depends on |
| --- | --- | --- |
| CI03-GATES | the gate route (G0, D-GATE), check entrypoint, aggregator, pins | nothing |
| CI07-SCHEMA | the record schemas under `schemas/` | nothing |
| CI02-CLI | one command-line contract and its gate | CI07-SCHEMA, CI03-GATES |
| CI05-VENDOR | vendored trees: `glab` skills and upstream references, one lock | CI03-GATES, CI07-SCHEMA, CI02-CLI |
| CI01-CATALOG | discover, `list`, `get`, `install` | CI05-VENDOR, CI07-SCHEMA, CI02-CLI |
| CI06-TESTS | the CI-only test command and coverage report | CI03-GATES, CI01-CATALOG |
| CI09-REFERENCE | tool operations, knowledge references, the navigator | CI01, CI07, CI05, CI02 (navigator: block 0) |
| CI08-ROUTING | tags in the catalog; the `SKILL.md` router (`REFERENCES` is CI09's) | CI05, CI01, CI09 |
| CI11-TOOLS | the tool catalogue and the port of the source repo's scripts | CI02-CLI, CI07-SCHEMA, CI09-REFERENCE |
| CI04-HOOKS | advisory local hooks | CI03-GATES, CI05-VENDOR |

## Order

0. **Block 0, the layout**, before anything else, as `TEAM_GUIDE.md` draws
   it: skill commands in `ci-skills/bin/`; the Python library in
   `ci-skills/lib/python/core/`, put on the path by one locator
   (`ci-skills/bin/_bootstrap.py`, one import per main; `tools/*.py` and
   `tests/python/conftest.py` point at `ci-skills/lib/python`); the Bash
   libraries in `ci-skills/lib/bash/<purpose>/` (`core` generic, `api` and
   `automation` behind the two Bash commands, `ci` only for CI-only code);
   development and build executables in `scripts/`. Dependencies flow from
   executables into libraries, never back, and a local build script does not
   import CI-specific behavior. Also: the root adapters re-pointed to
   `ci-skills/bin/`, the test roots and the installer source fixed, the unused
   Bash copies in `scripts/bash/core/` removed, the neutrality-marker strings
   removed. `scripts/check.sh`'s library stays deleted. Exit criteria:
   `tools/render_manifest.py --check` reports CURRENT; `ci-skills/bin/ci-api
   --help` exits 0; every `ci-skills/bin/*.py --describe` prints its contract
   with no `PYTHONPATH`; the neutrality checker reports PASS;
   `tools/check_live_acceptance.py --help` shows the current default paths;
   the installed copy under `~/.codex/skills` is reinstalled (today it
   carries both `bin/` and `scripts/`).
1. These phase documents, in one pull request (#29).
2. CI03-GATES (G0 records D-GATE).
3. CI07-SCHEMA, then CI02-CLI.
4. CI05-VENDOR.
5. CI01-CATALOG.
6. CI06-TESTS.
7. CI09-REFERENCE, then CI08-ROUTING on top of it.
8. CI11-TOOLS, one tool per pull request in the catalogue's order.
9. CI04-HOOKS.

## How a phase lands

- **A tool is a block** (Terms: one pull request per block).
- **Every phase document carries a "Delivery, test and proof" section** with
  these five parts, each concrete:
  1. *Delivery*: the files created or changed and the command that runs.
  2. *Tests*: the unit and contract test files and cases.
  3. *Smoke*: the exact command with its fixed arguments, the declared target
     and the read-back that proves the action (CI06-TESTS, "Live smoke").
  4. *Evidence*: the receipt path and the fields the checker compares.
  5. *Verification*: the checker command and its expected `PASS`.
- **Files it may change:** its own files; the shared files its gates need
  (`scripts/check.sh` and its library, `requirements.txt`, `schemas/`,
  `tests/python/test_validate_workflow_policy.py`, the workflow file the gate
  route names once one exists).
- **Before merge**, for the exact head commit: one read-only `qa` review
  with its findings fixed in the same pull request, the static checks
  green (`ruff`, markdownlint, `git diff --check`, gitleaks, neutrality,
  manifest byte-equality), and the tests the phase lists written. Whether
  they ran depends on D-GATE.
- **After merge**, the phase document's read-back step confirms the result
  on `main`.
- **A release** is a tag on `main` plus the receipt the gate route produces
  once one exists.

## Implementation map: what is added, changed, refactored and gated

One line per item; the owning phase doc carries the design. Everything here
is traceable to a request we recorded, a verified defect, or a source script.

### Add

| Item | Where | Owner |
| --- | --- | --- |
| shared library locator | `ci-skills/bin/_bootstrap.py`, one import per main | block 0 |
| skill-root leaf module | `ci-skills/lib/python/core/paths.py` | block 0 |
| bounded HTTP helper (stdlib) | `core/http.py` (reference fetch first; Harbor reuses it) | CI09-REFERENCE |
| plan, apply, read-back skeleton | `lib/python/core/action.py`, extracted from `gitlab_actions.py` | CI11-TOOLS |
| parallel reads helper | `lib/python/core/collect.py` (the executor it already uses, made shared) | CI11-TOOLS |
| references and navigator | `core/{reference,navigate,tool_operations}.py`, `bin/reference.py` | CI09-REFERENCE |
| maintenance package | `tools/skillkit/{schema,transaction,vendor}.py` | CI07, CI05 |
| maintenance package, continued | `tools/skillkit/{reference_update,install,discover}.py` | CI09, CI01 |
| record schemas | `schemas/<kind>.schema.json`, one per record kind | CI07-SCHEMA |
| schema validator | `tools/skillkit/schema.py`, thin main `tools/check_schemas.py` (`schema_check`) | CI07-SCHEMA |
| GitHub GET owner | `core/github_api.py` (the `gh api` GET of `core/access.py:181-185`, extracted) | CI02-CLI |
| Codex metadata and scopes | `ci-skills/agents/openai.yaml` (generated), `assets/`, `--scope` | CI01-CATALOG |
| vendor declarations and lock | `vendor/vendor.toml`, `vendor/vendor.lock.json` | CI05-VENDOR |
| new tools | the CI11-TOOLS catalogue, one pull request each | CI11-TOOLS |

### Modify

| Item | Change |
| --- | --- |
| `core/catalog.py` | `OFFLINE_COMMANDS`, `REFERENCES`, `harbor` authority, manifest `references`, per-verb `mutates` |
| `core/cli.py` | `offline_parser`, `execute_offline`; one GitLab command flow used by every GitLab main |
| `core/report.py` | `emit(..., verbatim=...)`; human fields for reference records |
| `core/provenance.py` | `VENDOR_DIRECTORIES`, an `excluded` parameter, public `file_digest`, `included_files` |
| `core/target.py`, `core/credentials.py`, `core/access.py` | `[harbor]` table, credential chain, access read-back |
| `tools/install_ci_skills.py`, `install.sh` | `--scope`; the transaction from `tools/skillkit` |
| `tools/render_manifest.py` | current paths; renders `agents/openai.yaml` beside `tools.json` |
| `tools/check_live_acceptance.py` | current default paths; `[[smoke_cases]]` verification (CI03-GATES, G5) |
| `tests/python/conftest.py`, path-bound tests | `SCRIPT_ROOT = ci-skills/bin`, `LIB_ROOT = ci-skills/lib/python` |
| `ci-skills/SKILL.md` | one navigator sentence; description terms for implicit invocation |
| `Makefile`, `pyproject.toml`; `scripts/bash/core/*.bash` | neutrality strings removed; unused copies removed |

### Refactor (second implementations removed; one owner each)

| Behaviour | Keep | Remove or fold |
| --- | --- | --- |
| GitLab command flow | `core/cli.py` | the copies in `bin/gitlab_access.py`, `bin/gitlab_pipeline.py` |
| exit codes | `core/status.py`, rendered to `lib/bash/core/exit_codes.bash` | `runtime.bash`'s table |
| GitLab GET transport | `core/gitlab_api.py` | `lib/bash/api/api.bash` (`ci-api` becomes a Python main) |
| binary build plan | `core/binary_build.py` (ported) | `lib/bash/automation/binary_build.bash` |
| result envelope | `core/report.py` | hand-built envelopes |
| diagnostic logger | `core/cli.py` `log_event` | `mtu_consistency.EventLogger`, `runtime.bash` `ci_log` |
| argument-error envelope | `core/cli.py` `MachineArgumentParser` | `StructuredParser`, `NodeParser` |
| secret patterns | `core/runtime.py:32-94` | none left; the Bash copy went with `scripts/bash/core/result.bash` |
| single-token-file rule | `core/credentials.py:41-51` | `ci-skills/lib/bash/core/runtime.bash:55-75` |
| clean-checkout revision check | `core/provenance.py:106-130` | `ci-skills/lib/bash/core/runtime.bash:97-118` |
| SHA-256 of stdin | `core/provenance.py` | `lib/bash/core/runtime.bash:79-91` |
| advisory file lock | `tools/skillkit/transaction.py` | `tools/install_ci_skills.py:95`, `core/gitlab_api.py:211-259` |
| plan fingerprint | `core/action.py` | `gitlab_actions.py:87`, `mtu_consistency.py:464`, `binary_build.bash:137-141` |

### Modularity

`ci-skills/lib/python/core/` is flat (28 modules today) and CI11 adds about ten.
Module paths in every phase document are flat (`core/<name>.py`) until the
split into domain packages is decided (Open decisions, Modularity); the
pull request that splits them changes imports and nothing else. Abstractions
are introduced contract first and only where two implementations exist:
`Action` (plan, apply, read-back), `Authority` (one access check per
authority name), `ReferenceSource`, `Chunker`, `Joiner`; none is added
speculatively (CI09 keeps one implementation of each until a second source).

### Publish and install

The skill is published by copying it, digest-verified, into a scope path;
the scope table, `--scope` and the install-scope default are CI01-CATALOG
("Codex metadata and install scopes" and its open decision D-SCOPE). Python
3.11 or newer is required (the documented interpreter is the `ci-skills`
conda environment). Block 0 repairs the install issues verified on
2026-10-06 (its exit criteria).

### Lock every open-ended specification with a schema

Every record a command reads or writes has one closed JSON Schema (2020-12)
under `schemas/` with `kind` and `schema_version`; the inventory is the
schemas table of CI07-SCHEMA. A kind without a schema cannot be emitted; a
schema change without a version bump fails the `schemas` gate.

### Machine-readable output, one shape

Every command prints one JSON document on stdout (YAML with `--yaml`, a
human summary on a terminal or with `--human`) whose fields are locked by
`schemas/command-result.schema.json` (CI07-SCHEMA); diagnostics go to
stderr (CI02-CLI). Today `core/report.py:52-61` emits `target` and `filters`
and no `execution_host`, `skill` or `target_source`; that delta is the first
`command-result` version. Exit codes come from the one table in
`core/status.py` (today 0 and 2; D-EXIT).

### Gates that make every tool conform

One gate per owner: `cli` (CI02-CLI), `schemas` (CI07-SCHEMA), `verify`
(CI05-VENDOR), `tests` (CI06-TESTS), `reference` (CI09-REFERENCE, section 5),
`manifest`,
`neutrality` and the static tools (CI03-GATES, Existing gates); all are
profiles of one entrypoint, `scripts/check.sh` (CI03-GATES, G1), once block 0
restores its library, so a contributor and any future CI route run the same
list. Tests run on the gate route (D-GATE).

## Pull request status, read back 2026-10-06

- Merged: #11 (phase docs), #16 (tools, target protocol, live gates), #21
  (package delivery, `d6bba3d`), #26 (deleted `lib/ci`), #27 (deleted
  `.github/workflows/validate.yml`, `1108cca`).
- Closed unmerged: #2 (its tools shipped through #16).
- Open: #29 (draft, this document set, clean against `main`); #23 (live
  proof for every command) and #28 (command inventory), drafts in conflict
  with `main`.
- No workflow exists on GitHub; branch protection on `main` is disabled; the
  committed receipts no longer match the skill digest (62 files now, 53 in
  the receipt) and expire 2026-11-03.

## Decisions taken

- 2026-10-02: the k8s skill's short name question is moot; the one package is
  `ci-skills`.
- 2026-10-06, D-GATE: no gate for now. Every test and receipt claim stays
  unverified until a route exists.
- 2026-10-06, D-DIGEST: `references/vendor/**` is excluded from the
  executed-code digest; the lock digests it on its own.
- 2026-10-06, D-HOME: knowledge references live in CI09-REFERENCE.
- 2026-10-06, D-LAYOUT: the vendored tree mirrors the keyword path, with
  card, part and chunk tiers (CI09-REFERENCE, section 2).
- 2026-10-06, placement: runtime code in `ci-skills/lib/python/core/`,
  maintenance-only code in `tools/skillkit/`.
- 2026-10-06, D-SMOKE: live smoke runs from this laptop against the test
  project and the live cluster; the receipt's read-back is the proof
  (CI06-TESTS, CI03-GATES G5).

## Open decisions

Names and owners only; each owner document carries the question.

- `orbit` license: vendor it or leave it out (CI05-VENDOR).
- Review: what makes a review block a merge (CI03-GATES, G3).
- D-EXIT: CI02's five-code exit table or today's 0 and 2, and how `PARTIAL`
  exits; whether the k8s commands move behind one `bin/ci-k8s` (CI02-CLI).
- D-CONFIRM: the spelling of the plan-bound confirmation on the maintenance
  verbs (CI02-CLI).
- Gate library home: `ci-skills/lib/bash/ci/check.bash`, a CI-only module (deleted on
  purpose; block 0 does not restore it)
  (every gate edit then moves the skill digest and forces a receipt
  recapture), or a path outside `ci-skills/` (gate edits leave the digest
  alone) (CI03-GATES, G1).
- Coverage floor (CI06-TESTS).
- D-LICENSE: repository license or per-tree notices (CI09-REFERENCE).
- D-SCOPE: the install scope default for Codex (CI01-CATALOG).
- Modularity: split flat `core/` into `core/{gitlab,k8s,ocp,harbor,reference}/`
  before the first CI11 tool, or keep it flat (this document, Modularity).
