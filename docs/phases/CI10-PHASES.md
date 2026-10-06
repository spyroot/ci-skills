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

The operator fixed the layout in the tree on 2026-10-04: thin mains in
`ci-skills/bin/`, the Python library in `ci-skills/lib/core/`, the Bash
libraries in `ci-skills/lib/bash/{core,ci,automation}/`, references in
`ci-skills/references/`, the generated `tools.json` beside `SKILL.md`.
Maintenance-only Python lives in `tools/skillkit/` and imports `core` from
`ci-skills/lib`; thin maintenance mains live in `tools/`. Vendored skills
live in `vendor/skills/<name>/`, vendored references in
`ci-skills/references/vendor/<name>/`, the vendor declarations and the one
lock in `vendor/`. Every tool is a small main that calls the library; a second
implementation of a behaviour the library has is a defect.

## Phases

| Phase | Delivers | Depends on |
| --- | --- | --- |
| CI03-GATES | the gate route (G0, D-GATE), check entrypoint, aggregator, pins | nothing |
| CI07-SCHEMA | the record schemas under `schemas/` | nothing |
| CI02-CLI | one command-line contract and its gate | CI07-SCHEMA, CI03-GATES |
| CI05-VENDOR | vendored trees: `glab` skills and upstream references, one lock | CI03-GATES, CI07-SCHEMA, CI02-CLI |
| CI01-CATALOG | discover, `list`, `get`, `install` | CI05-VENDOR, CI07-SCHEMA, CI02-CLI |
| CI06-TESTS | the CI-only test command and coverage report | CI03-GATES, CI01-CATALOG |
| CI09-REFERENCE | tool operations, knowledge references, the navigator | CI01-CATALOG, CI07-SCHEMA, CI05-VENDOR |
| CI08-ROUTING | `REFERENCES` and tags in the catalog; the `SKILL.md` router | CI05-VENDOR, CI01-CATALOG, CI09 |
| CI11-TOOLS | the tool catalogue and the port of the source repo's scripts | CI02-CLI, CI07-SCHEMA, CI09-REFERENCE |
| CI04-HOOKS | advisory local hooks | CI03-GATES, CI05-VENDOR |

## Order

0. **Block 0, prerequisite repairs**, before anything else: one shared
   locator for the Python library (`ci-skills/bin/_bootstrap.py` and one
   import per main; `tools/*.py` and `tests/python/conftest.py` pointed at
   `ci-skills/lib`), the Bash mains and libraries re-pointed to
   `lib/bash/...`, the root adapters re-pointed to `ci-skills/bin/`, the
   test roots and the installer source fixed, the foreign content behind the
   three neutrality violations removed. Exit criteria: `tools/render_manifest.py
   --check` reports CURRENT, `ci-skills/bin/ci-api --help` exits 0, every
   `ci-skills/bin/*.py --describe` prints its contract with no `PYTHONPATH`,
   and the neutrality checker reports PASS.
1. These phase documents, in one pull request.
2. CI03-GATES, with G0: as of 2026-10-06 the operator decided "no gate for
   now"; the gap is recorded, not closed.
3. CI07-SCHEMA, then CI02-CLI.
4. CI05-VENDOR.
5. CI01-CATALOG.
6. CI06-TESTS.
7. CI09-REFERENCE, then CI08-ROUTING on top of it.
8. CI11-TOOLS, one tool per pull request in the catalogue's order.
9. CI04-HOOKS.

## How a phase lands

- **One pull request per block.** A tool is a block.
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
- **Before merge**, for the exact head commit: a review with its findings
  fixed in the same pull request (`qa` per `TEAM_GUIDE.md`), the static checks
  green (`ruff`, markdownlint, `git diff --check`, gitleaks, neutrality,
  manifest byte-equality), and the tests the phase lists written. Whether
  they ran depends on D-GATE.
- **After merge**, the phase document's read-back step confirms the result
  on `main`.

## Implementation map: what is added, changed, refactored and gated

One line per item; the owning phase doc carries the design. Everything here
is traceable to an operator sentence, a verified defect, or a source script.

### Add

| Item | Where | Owner |
| --- | --- | --- |
| shared library locator | `ci-skills/bin/_bootstrap.py`, one import per main | block 0 |
| skill-root leaf module | `ci-skills/lib/core/paths.py` | block 0 |
| bounded HTTP helper (stdlib) | `ci-skills/lib/core/http.py`, used by Harbor and the reference fetch | CI11-TOOLS |
| plan, apply, read-back skeleton | `ci-skills/lib/core/action.py`, extracted from `gitlab_actions.py` | CI11-TOOLS |
| parallel reads helper | `ci-skills/lib/core/collect.py` (the executor it already uses, made shared) | CI11-TOOLS |
| references and navigator | `core/reference.py`, `core/navigate.py`, `bin/reference.py` | CI09-REFERENCE |
| maintenance package | `tools/skillkit/{transaction,fetch,reference_update,vendor,discover}.py` | CI05, CI09 |
| record schemas | `schemas/<kind>.schema.json`, one per record kind | CI07-SCHEMA |
| Codex metadata and scopes | `ci-skills/agents/openai.yaml` (generated), `assets/`, `--scope` | CI01-CATALOG |
| vendor declarations and lock | `vendor/vendor.toml`, `vendor/vendor.lock.json` | CI05-VENDOR |
| new tools | the CI11-TOOLS catalogue, one pull request each | CI11-TOOLS |

### Modify

| Item | Change |
| --- | --- |
| `core/catalog.py` | `OFFLINE_COMMANDS`, `REFERENCES`, `harbor` authority, `references` in `manifest()`, exit table |
| `core/cli.py` | `offline_parser`, `execute_offline`; one GitLab command flow used by every GitLab main |
| `core/report.py` | `emit(..., verbatim=...)`; human fields for reference records |
| `core/provenance.py` | `VENDOR_DIRECTORIES`, an `excluded` parameter, public `file_digest`, `included_files` |
| `core/target.py`, `core/credentials.py`, `core/access.py` | `[harbor]` table, credential chain, access read-back |
| `tools/install_ci_skills.py`, `install.sh` | `--scope`; the transaction from `tools/skillkit` |
| `tools/render_manifest.py` | current paths; renders `agents/openai.yaml` beside `tools.json` |
| `tests/python/conftest.py` and the path-bound tests | `SKILL_ROOT = ci-skills`, `SCRIPT_ROOT = ci-skills/bin` |
| `ci-skills/SKILL.md` | one navigator sentence; description terms for implicit invocation |
| `Makefile`, `pyproject.toml`, `scripts/bash/core/result.bash` | neutrality-marker content removed |

### Refactor (second implementations removed; one owner each)

| Behaviour | Keep | Remove or fold |
| --- | --- | --- |
| GitLab command flow | `core/cli.py` | the copies in `bin/gitlab_access.py`, `bin/gitlab_pipeline.py` |
| exit codes | `core/status.py`, rendered to `lib/bash/core/exit_codes.bash` | the two Bash tables |
| GitLab GET transport | `core/gitlab_api.py` | `lib/bash/ci/api.bash` (`ci-api` becomes a Python main) |
| binary build plan | `core/binary_build.py` (ported) | `lib/bash/automation/binary_build.bash` |
| result envelope | `core/report.py` | `scripts/bash/core/result.bash`, hand-built envelopes |
| diagnostic logger | `core/cli.py` `log_event` | `mtu_consistency.EventLogger`, `runtime.bash` `ci_log` |
| argument-error envelope | `core/cli.py` `MachineArgumentParser` | `StructuredParser`, `NodeParser` |
| secret patterns, token-file rule, revision check, digest | the Python owners in `core/` | the Bash copies |
| advisory file lock, plan fingerprint | `tools/skillkit/transaction.py`, `core/action.py` | the per-tool copies |

### Modularity

`ci-skills/lib/core/` is flat today (30 modules) and CI11 adds about ten.
Before the first CI11 tool lands, group by domain, imports updated in one
pull request and nothing else changed: `core/` keeps the shared layer
(`cli`, `catalog`, `status`, `report`, `runtime`, `http`, `paths`,
`provenance`, `portable`, `credentials`, `target`, `access`, `action`,
`collect`); `core/gitlab/`, `core/k8s/` (collectors, Cilium, Ceph, node
reads, MTU), `core/ocp/`, `core/harbor/`, `core/reference/` hold the domain
code. Abstractions, contract first and only where two implementations exist:
`Action` (plan, apply, read-back), `Authority` (one access check per
authority name, a registry instead of branches), `ReferenceSource`,
`Chunker`, `Joiner`; none is added speculatively.

### Publish and install

The skill is published by copying it (digest-verified) into a scope path:
`$HOME/.agents/skills` (documented Codex user scope), `<repo>/.agents/skills`
(repo scope, committed by a consuming repository), `/etc/codex/skills`
(admin), `~/.claude/skills` or `.claude/skills` (Claude Code),
`~/.codex/skills` (today's default, undocumented upstream). A release is a
tag on `main` plus the receipt the gate route produces once one exists. Known
install issues, verified 2026-10-06: the installed copy under
`~/.codex/skills` carries both `bin/` and `scripts/` and must be reinstalled
after block 0; no installed Python main can import `core` until the locator
lands; the installer's source path is stale; `~/.claude/skills/k8s-admin-diagnostics`
is an old-named copy; Python 3.11 or newer is required (the documented
interpreter is the `ci-skills` conda environment).

### Lock every open-ended specification with a schema

Every record a command reads or writes has one JSON Schema (2020-12) under
`schemas/`, closed (`additionalProperties: false`), with `kind` and
`schema_version`: `command-contract` (`--describe`), `command-result` (one
file per report kind: the 17 catalog kinds today plus `reference_next`,
`reference_record`, `reference_verify`, `install_plan`, `vendor_check`,
`vendor_update`, `live_acceptance`, the receipts), `reference-index`,
`skill-manifest`, `skill-index`, `vendor-lock`, `vendor-declarations`,
`openai-agent-metadata`. A kind without a schema cannot be emitted; a schema
change without a version bump fails the `schemas` gate (CI07-SCHEMA).

### Machine-readable output, one shape

Every command prints one JSON document on stdout (YAML with `--yaml`, a
human summary on a terminal or with `--human`): `schema_version`, `kind`,
`status` (`PASS`, `PARTIAL`, `BLOCKED`, `DRY_RUN`, `PLANNED`, `UNKNOWN`),
`captured_at`, `execution_host`, `skill` (digest and revision claim),
`target_source`, `records`, `errors` (`source`, `reason`, `safe_next_step`),
`summary` (`record_count`, `error_count`); `access` on authority-bound
commands; `plan_digest` on plans; `readback` and `cleanup` on applies.
Diagnostics go to stderr, JSON Lines with `--log-format json`. Exit codes
come from the one table in `core/status.py` (today 0 and 2; CI02-CLI's
five-code table is the open decision D-EXIT).

### Gates that make every tool conform

- **`cli` contract gate** (CI02-CLI), over every entrypoint the catalog
  declares, never a glob: `--help` exits 0 with an empty environment and no
  network; `--describe` validates against `command-contract`; the options
  the parser accepts equal the options the catalog declares (introspection,
  not help-text scraping); a shared option name keeps one help text; a
  mutating command's default run writes nothing (temporary working
  directory, fake `PATH`); `--json` and `--yaml` outputs validate against the
  kind's `command-result` schema on a mocked run; exit codes are a subset of
  the one table.
- **`schemas` gate** (CI07-SCHEMA): every schema against the metaschema;
  every committed record (`tools.json`, `index.json`, the lock, the
  declarations, `agents/openai.yaml`) against its schema; closed world for
  `uses`, `REFERENCES` and index files.
- **`manifest` gate** (exists): `tools.json` and `agents/openai.yaml`
  byte-equal to their render.
- **`neutrality` gate** (exists, failing on `main` until block 0).
- **`reference` gate** (CI09): `bin/reference.py verify` on every vendored
  tree; the vendored tree excluded from the executed-code digest.
- **static** (exist as tools): `ruff check`, `ruff format --check`,
  markdownlint-cli2, shellcheck, shfmt, yamllint, gitleaks, `git diff --check`.
- **tests**: pytest and bats, written with each block; the route that runs
  them is D-GATE (none as of 2026-10-06).

All of these are profiles of one entrypoint, `scripts/check.sh` (CI03-GATES,
G1), so a contributor and any future CI route run the same list.

## Pull request status, read back 2026-10-06

- Merged: #11 (phase docs), #16 (tools, target protocol, live gates), #21
  (package delivery, `d6bba3d`), #26 (deleted `lib/ci`), #27 (deleted
  `.github/workflows/validate.yml`, `1108cca`).
- Closed unmerged: #2 (its tools shipped through #16).
- Open, draft, conflicting: #23 (live proof for every command; touches the
  deleted workflow and the old `skills/` tree), #28 (command inventory).
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
- 2026-10-06, placement: runtime code in `ci-skills/lib/core/`,
  maintenance-only code in `tools/skillkit/`.
- 2026-10-06, D-SMOKE: live smoke runs from this laptop against the test
  project and the live cluster; the receipt's read-back is the proof
  (CI06-TESTS, CI03-GATES G5).

## Open decisions

- **`orbit`.** Its source project carries the GitLab Enterprise Edition
  license; vendor it or leave it out (CI05-VENDOR).
- **Receipt host** and **live tool check executor** (CI03-GATES, G5;
  CI09-REFERENCE).
- **Review.** What makes a review block a merge (CI03-GATES, G3).
- **CLI.** How `PARTIAL` exits, and whether the k8s commands move behind one
  `bin/ci-k8s` command (CI02-CLI).
- **Coverage floor** (CI06-TESTS).
- **Repository license** (D-LICENSE, CI09-REFERENCE).
- **Install scope default** for Codex (`$HOME/.agents/skills` is the
  documented path; `~/.codex/skills` is today's default).
