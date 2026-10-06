# doc-editor-CI07-SCHEMA

Saved 2026-10-06 from the session transcript `~/.claude/projects/-Users-spyroot-dev-ci-skills/279dd962-305b-4ab4-835e-7e6e872c8f60/subagents/agent-ad8ef683e93717470.jsonl`. Editor report for CI07-SCHEMA (retry). Agent output, kept verbatim; a log, not present state.

---

Edited exactly one file: docs/phases/CI07-SCHEMA.md (237 insertions, 53 deletions; no git write commands; no other file touched).

## Edits, by section

- **Header**: now "Status: proposed. Order and dependencies: CI10-PHASES, Phases." (hand-kept "Used by" list dropped).
- **Schemas**: `schemas/` marked "(planned, this phase; the directory exists today and is empty)" (verified: `/bin/ls -la schemas` shows an empty dir). "A kind without a schema cannot be emitted; a schema change without a version bump fails the `schemas` gate (decided 2026-10-06)". Table rebuilt with five columns (Schema | `kind` | Validates | Producer | Lands in); rows added per finding 1: `reference-index` (`reference_index`, `tools/update_reference.py`, CI09), `smoke-case` (`[[smoke_cases]]` entries, verified by `tools/check_live_acceptance.py`, CI03-GATES G5, D-SMOKE), `openai-agent-metadata` (`ci-skills/agents/openai.yaml`, `tools/render_manifest.py`, CI01). Finding 2: `command-result` row is now the envelope (the file CI10 names, `schemas/command-result.schema.json`) plus "one file per report kind", followed by the kind list: the 17 `report_kind` values read from `ci-skills/tools.json` (count verified 17), `live_acceptance`, `skill_install`, `skill_vendor_check`, `skill_vendor_update`, `reference_next`, `reference_record`, `reference_verify`, `schema_check`, and the tree-derived kinds below. A receipt validates against its kind's file (`core/portable.py` rewrites values, never the shape). Notes list per row.
- **Rules**: Identity gains the one exception for the two upstream shapes (`SKILL.md` frontmatter; `agents/openai.yaml`, whose fields the Agent Skills specification fixes, CI01-CATALOG.md:220).
- **Pointers** (finding 3): ownership stated once ("this document owns the `uses` pointer schema, CI09-REFERENCE owns the operations inventory, CI08-ROUTING consumes them", decided 2026-10-06); new "Reference entries" bullet with `references[].kind` (`operations` | `knowledge`, CI10 Terms) and `index` (relative path of `index.json`, CI09 Layout); "by the user of the operation" reworded to "by the command or reference that uses the operation"; Closed world extended with the two items CI08-ROUTING.md:181 now delegates here (`REFERENCES` paths exist, `points_to` names a discovered skill).
- **Validation**: Tool (pin per CI03 G2; check-jsonschema 0.38.0 verified installed, `/opt/homebrew/bin/check-jsonschema --version`); new Validator bullet (`tools/skillkit/schema.py`, `tools/check_schemas.py`, both "(planned, this phase)"; result kind `schema_check`); Gate bullet now runs `tools/check_schemas.py --root . --json`, lists every committed record, closed world, and the version rule; `ci-skills/lib/bash/ci/check.bash` marked "(planned, CI10-PHASES, block 0)" (tree has only `api.bash`; CI10 Order 0 restores it). "At runtime" kept, exit 65 qualified "(CI02-CLI's table, under D-EXIT)", plus the installed-producer gap sentence.
- **Versions**: `summary.record_count` exists today (`tests/acceptance/receipts/issue-open-bug-applied.json`); `truncated` is an added optional envelope field, a MINOR bump of `command-result` (CI11-TOOLS.md:294 "both fields of command-result"); envelope delta from `core/report.py:52-61` (CI10, "Machine-readable output, one shape"); `reference-index` 0.1 -> 1.0 cited as the first case of promotion rule 1.
- **Block**: Owner/Entrypoint/Result updated to the module, main and `schema_check`.
- **Steps**: step 1 lists the schemas whose records exist today at 1.0 with the stamping lines (`ci-skills/lib/core/catalog.py:26`, `core/report.py:52`, `tools/check_live_acceptance.py:615`, `tools/check_project_neutrality.py:52`); step 4 (finding 5) now "Open one pull request; merge per CI10-PHASES, How a phase lands."
- **New section "Delivery, test and proof"** (last `##`): 1 Delivery (every schema file path, `tools/skillkit/{__init__,schema}.py`, `tools/check_schemas.py`, `requirements.txt`, the check.sh library; argv `tools/check_schemas.py --root . --json`); 2 Tests (`tests/python/test_schema.py`; every case of CI06-TESTS.md:188-200 carried with the extra cases you listed; one `tests/bash/check.bats` case per CI03-GATES.md:281; `uses`/`REFERENCES`/index closed-world cases ride with CI08/CI09; "Written with the block; run status UNVERIFIED (CI03-GATES, G0; D-GATE ...)"); 3 Smoke (no live behaviour, static read-back over `tools.json` and the receipts); 4 Evidence ("no receipt; the static evidence is `tools/check_schemas.py --root . --json` output"); 5 Verification (`"status": "PASS"` with one entry per validated record, plus CI10's pre-merge static checks).

## Finding 4 (kind strings cross-checked, current texts)

`skill_install` (CI01-CATALOG.md:22,29), `skill_vendor_check`/`skill_vendor_update` (CI05-VENDOR.md:45), `skill_vendor_lock` (CI05:94), `skill_index` (CI01:156), `tool_operations` and `reference_verify` (CI09-REFERENCE.md:24,177 — both were absent from CI09 at my first read and appeared during the session), `reference_index` (CI09:256), `command_contract`/`skill_manifest` (catalog.py:592,646). All used verbatim.

## Finding 6

Only the header's "Used by" list read wrongly after the mechanical rename (now replaced). Case-sensitive `grep -E "CI-[A-Z]"` and a grep for "validate workflow" over the file are empty.

## Choices no source dictates (reject on sight if unwanted)

- `schema_check` as the checker's result kind (pattern: `manifest_check`, `tools/render_manifest.py:67`).
- Names `tools/skillkit/schema.py`, `tools/check_schemas.py`, `tests/python/test_schema.py` (patterns: `check_live_acceptance.py`/`test_live_acceptance.py`, `check_project_neutrality.py`/`test_neutrality.py`). Placement in `tools/skillkit/` follows CI10's 2026-10-06 placement decision (every validating producer is checkout-side; `schemas/` is outside the installed skill tree).
- The "Lands in" column replaces per-cell "(planned, CIxx)" suffixes — needed to keep every table row <= 120 columns.
- File names: report kinds use the kind verbatim (`schemas/skill_install.schema.json`, as the decision's `<kind>.schema.json`), while CI09 names hyphenated files for underscore kinds (`schemas/tool-operations.schema.json` for `tool_operations`, CI09:24; `schemas/reference-index.schema.json`). Not unified; the operator's call.
- "an `operations` entry has no index file to name" is derived from CI09's Layout (only the vendored knowledge tree has an `index.json`).
- Fixture design: valid record = the committed one where it exists, else a minimal record in the test module; missing-field/broken-rule records derived from it.

## Kinds added beyond the decision's list, from the tree

`project_neutrality` (`tools/check_project_neutrality.py:53`), `manifest_check`/`manifest_render` (`tools/render_manifest.py:67,77`), `gitlab_runner_smoke_cleanup` (nested `smoke_cleanup` in `tests/acceptance/receipts/runner-create-applied.json`, read at `tools/check_live_acceptance.py:392-404`; no producer anywhere under `ci-skills/` — grep empty).

## Gaps marked in the document instead of invented

- `kind` strings of `vendor-declarations` (owner CI05-VENDOR) and `smoke-case` (owner CI03-GATES G5).
- Where a `smoke-case` record's identity fields sit (`expected.toml` has no `kind`/`schema_version`; its `[[gitlab_receipts]]` entries use `kind` for the receipt kind) — owner CI03-GATES G5.
- How an installed producer (`--receipt-out`, `ci-skills/bin/reference.py`) validates without `schemas/` — owner this document's PR.
- Whether the envelope delta (`execution_host`, `skill`, `target_source`) is MINOR or MAJOR — follows from CI02-CLI binding them optional or required.
- The producer of `gitlab_runner_smoke_cleanup` — owner the runner smoke of CI11-TOOLS (CI11:388-390).
- `manifest_check`/`manifest_render` carry `kind` without `schema_version` today, against Identity; the stamp lands with CI01-CATALOG, which owns `tools/render_manifest.py` changes (CI10 Modify table).

## For other owners (not applied; outside the one-file scope)

- docs/phases/CI02-CLI.md:207 still lists "Add the `command-contract` and `command-result` schemas" — an overlap with this document's step 1, which adds the files (records exist today); noted in the doc as "this step adds the files, CI02-CLI binds its commands to them".
- docs/phases/CI10-PHASES.md: the Implementation map "Add" table has no row for the validator module/main, and the Layout paragraph's `tools/skillkit/` owner list ("CI05, CI09, CI01") omits CI07, which now creates the package first.
- Finding I could not apply as literally stated: none. The `check.bats` attribution first cited CI06-TESTS; CI06's CI03 section became a pointer during the session, so it now cites CI03-GATES.md:281 where the case lives.

## Session facts

Every sibling phase doc changed under me (mtimes 14:15-14:28, other agents); I re-verified each citation against the current text after the last change before finishing (CI03 now has `### G0` at line 63, so that citation is grounded). `git status` also shows others' edits to README.md, scripts/README.md, docs/gitlab-toolbelt-plan.md and CI11-TOOLS.md — not mine.

## Lint

`/opt/homebrew/bin/markdownlint-cli2 docs/phases/CI07-SCHEMA.md` (v0.23.2): `Summary: 0 issues in 0 files`. Also: no line over 120 columns (awk), no trailing spaces, `git diff --check` clean, file not among `tools/check_project_neutrality.py` findings.
