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
