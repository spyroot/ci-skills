# CI07-SCHEMA: record schemas

Status: proposed. Order and dependencies: CI10-PHASES, Phases.

## Goal

Every record a command reads or writes has exactly one schema, so producers
and consumers cannot drift, and every change to a record's shape is
versioned.

## Schemas

All schemas live under `schemas/` at the repository root (planned, this
phase; `command-contract`, `skill-index`, `skill-manifest`, `reference-next`, `reference-section` and
`gitlab-pipeline-watch` are implemented so far), one file per record kind,
named `<kind>.schema.json`. They use JSON Schema draft 2020-12 and the style
of the shared standards' own schemas: `$schema`, `$id`, `title`, `type`,
`required`, `properties` and `$defs`. A kind without a schema cannot be
emitted, and a schema change without a version bump fails the `schemas` gate
(CI10-PHASES, Implementation map). This table is the inventory CI10-PHASES points at
("Lock every open-ended specification with a schema"); every row names the
record's `kind` and its producer, and the last column names the phase that
adds the record, or `today` where the record exists in the tree.

| Schema | `kind` | Validates | Producer | Status |
| --- | --- | --- | --- | --- |
| `skill-frontmatter` | none, upstream shape | the YAML block in each `SKILL.md` | authors | missing |
| `skill-manifest` | `skill_manifest` | `ci-skills/tools.json` | `tools/render_manifest.py` | implemented |
| `skill-index` | `skill_index` | `bin/ci-skills list --json` | `tools/skillkit/discover.py` | implemented |
| `reference-next` | `reference_next` | `next` answers and the shared pointer | `core/navigate.py` | implemented |
| `reference-section` | `reference_section` | `reference.py get` answers | `core/navigate.py` | implemented |
| `gitlab-pipeline-watch` | `gitlab_pipeline_watch` | `watch` results | `core/gitlab_pipelines.py` | implemented |
| `vendor-lock` | `skill_vendor_lock` | `vendor/vendor.lock.json` | `bin/ci-skills update` | missing |
| `vendor-declarations` | not named yet | `vendor/vendor.toml` | by hand | missing |
| `tool-operations` | `tool_operations` | `bin/ci-skills tools --json` | `core/tool_operations.py` | missing |
| `reference-index` | `reference_index` | a reference's `index.json` | `tools/update_reference.py` | missing |
| `openai-agent-metadata` | none, upstream shape | `agents/openai.yaml` | `tools/render_manifest.py` | missing |
| `smoke-case` | not named yet | each `[[smoke_cases]]` entry of `expected.toml` | by hand | missing |
| `command-contract` | `command_contract` | each command's `--describe` | `core/catalog.py` | implemented |
| `command-result` | none, the envelope | the fields every report kind shares | `core/report.py` | missing |

Implemented, each checked with `check-jsonschema --schemafile <schema> <record>` (plus `--base-uri` where a schema
reuses another's `$defs`):

- [`schemas/command-contract.schema.json`](../../schemas/command-contract.schema.json), built from the
  `--describe` output of all 15 Python commands; it accepts all 15 and refuses an unknown field, a missing `kind`
  and a malformed option name.
- [`schemas/skill-index.schema.json`](../../schemas/skill-index.schema.json), built from CI01-CATALOG's index
  record; it accepts that section's example and the `ci-skills` record read from `ci-skills/SKILL.md` and
  `tools.json`, and refuses a local skill without its manifest, a vendored skill with one, an unknown field, an
  unknown error rule and an invalid name. Its producer, `bin/ci-skills list`, is not built yet (CI01-CATALOG).
- [`schemas/skill-manifest.schema.json`](../../schemas/skill-manifest.schema.json), built from
  `ci-skills/tools.json`; it accepts that file and refuses an unknown field, an entry without `purpose`, an option
  name without dashes, `read_only` as text and an unknown authority.
- [`schemas/reference-next.schema.json`](../../schemas/reference-next.schema.json) and
  [`schemas/reference-section.schema.json`](../../schemas/reference-section.schema.json), built from CI09-REFERENCE
  section 3's renders; they accept all of them and a 1.1 answer, and refuse an unknown field, a capability without
  `read_back`, a read choice without `when`, an unavailable verb with a pointer, 13 choices, a 101-character
  summary, an unknown action or error reason, MAJOR 2 and an unknown tier. `reference-section` reuses the choice
  and pointer of `reference-next`, so its commands add `--base-uri`. Their producer, `reference.py`, is not built
  yet (CI09-REFERENCE).
- [`schemas/gitlab-pipeline-watch.schema.json`](../../schemas/gitlab-pipeline-watch.schema.json), built from the
  pipeline watch result in CI11-TOOLS, Combos; it accepts that result and refuses an unknown `via` and a depth
  over 5. Its producer, `gitlab_pipeline.py watch`, is not built yet (CI11-TOOLS).

Every row marked missing is this phase's to implement; a record kind without its schema cannot be emitted.

### Adding a schema

A new schema is one file, `schemas/<schema>.schema.json`, named as its row in the table above. It must include:

- `"$schema": "https://json-schema.org/draft/2020-12/schema"`, an `$id` under
  `https://github.com/spyroot/ci-skills/schemas/`, `title` set to the record kind, and a `description` that names
  the record and the producer that writes it;
- `type: object` and `additionalProperties: false` at every object level, so a field the schema does not name is
  refused;
- `required` listing every field the record always carries, with `kind` as `const` and `schema_version` as its
  MAJOR pattern, for example `^1\.[0-9]+$` (Versions), so a MINOR addition never breaks a reader;
- maps (option names, exit codes, status tokens, command names) as `propertyNames` plus `additionalProperties`,
  never as a fixed key list;
- shapes taken from real records: the producer's output when it exists (`command-contract`: all 15 `--describe`
  outputs; `skill-manifest`: `ci-skills/tools.json`), otherwise the owning phase's record definition
  (`skill-index`: CI01-CATALOG, Index record). A field the real records hold in two shapes accepts both:
  `subcommands` is a map for the Python commands and a list for the Bash ones.

Proof, before the row says implemented:

1. `check-jsonschema --check-metaschema schemas/<schema>.schema.json` passes.
2. `check-jsonschema --schemafile schemas/<schema>.schema.json <record>` accepts every real record.
   A schema that reuses another's `$defs` names it by file, and its commands add
   `--base-uri "file://$PWD/schemas/<schema>.schema.json"` so the reference resolves locally.
3. The same command refuses at least three broken copies: an unknown field, a missing required field, and one
   value that breaks a declared rule.
4. The table row says `implemented`, and the producer's phase document links the file.

Example, the top of `schemas/skill-index.schema.json` (the file adds `description` and `$defs`):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/spyroot/ci-skills/schemas/skill-index.schema.json",
  "title": "skill_index",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "kind", "skills"],
  "properties": {
    "schema_version": {"type": "string", "pattern": "^1\\.[0-9]+$"},
    "kind": {"const": "skill_index"},
    "skills": {"type": "array", "items": {"$ref": "#/$defs/skill"}},
    "errors": {"type": "array", "items": {"$ref": "#/$defs/error"}}
  }
}
```

| one file per report kind | the kinds below | a command's result and its receipt | the command | see below |

Notes on the rows:

- **`skill-frontmatter`.** Upstream authors write the frontmatter of
  vendored skills.
- **`vendor-declarations`.** CI09-REFERENCE adds the `[references.<name>]`
  fields with a MINOR bump.
- **`reference-index`.** One `index.json` per vendored reference, under
  `ci-skills/references/vendor/<name>/` (CI09-REFERENCE, Layout).
- **`openai-agent-metadata`.** The file is `ci-skills/agents/openai.yaml`,
  rendered beside `tools.json`.
- **`smoke-case`.** The entries are declared by hand in
  `tests/acceptance/expected.toml` before the smoke (CI11-TOOLS), and
  `tools/check_live_acceptance.py` verifies them (CI03-GATES, G5; decided
  2026-10-06, D-SMOKE). `expected.toml` carries no `kind` or
  `schema_version` today, and its `[[gitlab_receipts]]` entries use `kind`
  for the receipt they expect; where a `smoke-case` record's identity fields
  sit is settled by CI03-GATES, G5, which adds the entries.
- **`command-contract`.** Today the Python commands describe themselves;
  CI02-CLI adds `--describe` to `bin/ci-*`.
- **`command-result`.** CI10-PHASES names the file
  (`schemas/command-result.schema.json`, "Machine-readable output, one
  shape") and CI02-CLI binds every command to it; each report kind's own
  file carries the envelope and adds the kind's `records` shape, so "the
  `command-result` schema of `skill_install`" (CI01-CATALOG) is
  `schemas/skill_install.schema.json`.
- **Kinds not named yet.** No source names the `kind` of
  `vendor-declarations` (owner: CI05-VENDOR) or `smoke-case` (owner:
  CI03-GATES, G5); each owner names it when the record is added, and this
  table takes the string.

The report kinds, one `<kind>.schema.json` each:

- the 17 `report_kind` values `ci-skills/tools.json` declares today:
  `access_check`, `ceph_cluster`, `ceph_kernel`, `ci_api`, `ci_binary_build`,
  `cilium_node`, `cilium_status`, `event_trace`, `gitlab_access`,
  `gitlab_issue`, `gitlab_job`, `gitlab_milestone`, `gitlab_pipeline`,
  `gitlab_runner`, `gitlab_wiki`, `k8s_verify_mtu_consistency` and
  `storage_report`. A receipt is the same record in portable form
  (`core/portable.py` rewrites values, never the shape), so it validates
  against its kind's file;
- `gitlab_runner_smoke_cleanup`, the `smoke_cleanup` record nested in a
  `gitlab_runner` receipt (`tests/acceptance/receipts/runner-create-applied.json`,
  read by `tools/check_live_acceptance.py`); no producer of it is in the
  tree today, which its owner, the runner smoke of CI11-TOOLS, closes;
- the maintenance results that exist today: `live_acceptance`
  (`tools/check_live_acceptance.py`), `project_neutrality`
  (`tools/check_project_neutrality.py`), `manifest_check` and
  `manifest_render` (`tools/render_manifest.py`);
- `schema_check`, the result of `tools/check_schemas.py` (planned, this
  phase; the name follows `manifest_check`);
- `skill_install` (`bin/ci-skills install`, planned, CI01-CATALOG);
- `skill_vendor_check` and `skill_vendor_update` (`bin/ci-skills verify` and
  `update`, planned, CI05-VENDOR);
- `reference_verify` (`ci-skills/bin/reference.py verify`, planned,
  CI09-REFERENCE); `reference_next` and `reference_section` are in the table above.

## Rules every schema follows

- **Identity.** Every record we generate carries `kind`, a constant per
  schema, and `schema_version`. The two upstream shapes carry neither: the
  `SKILL.md` frontmatter, and `agents/openai.yaml`, whose fields the Agent
  Skills specification fixes (CI01-CATALOG).
- **Names.** Names match `^[a-z0-9]+(-[a-z0-9]+)*$`. A skill's `name` equals
  its directory name, which `tests/python/test_skill_package.py` already checks.
- **Paths.** Relative POSIX paths inside the owning skill. No leading `/`,
  and no `..` segment.
- **Digests.** 64 lowercase hexadecimal characters. A tree digest names its
  algorithm (`sha256-tree-v1`).
- **Closed or open.** Records we generate reject unknown fields
  (`additionalProperties: false`). Upstream frontmatter allows them, because
  we do not control it.
- **Per-source fields.** `skill-index` uses `if`/`then`: a local skill
  carries `manifest`, `references` and `depends_on`, and a vendored skill
  carries `upstream`, never the other way round.

## Pointers between references and tools

A skill's references are its documents under `references/`. The tools it
calls are described by operations (CI09-REFERENCE). One declaration links
them. Ownership (CI10-PHASES, Terms): this document owns the `uses` pointer
schema, CI09-REFERENCE owns the operations inventory the pointers name, and
CI08-ROUTING consumes them.

- **Operation ids.** Each tool operation has an id `<tool>:<operation>`, for
  example `gh:api.get`, `kubectl:auth.can-i` or `kubectl:exec.cilium-health`.
- **Declared once, by the command or reference that uses the operation.**
  Each command in `skill-manifest`, and each entry of a skill's
  `REFERENCES`, lists the operations it uses in `uses`. For example,
  `references/access.md` uses `gh:auth.status`, `glab:auth.status`,
  `kubectl:auth.whoami` and `kubectl:auth.can-i`.
- **Reference entries.** Each `references[]` entry of `skill-manifest` and
  `skill-index` carries `kind`, `operations` or `knowledge` (CI10-PHASES,
  Terms). A `knowledge` entry also carries `index`, the relative path of its
  `index.json` (CI09-REFERENCE, Layout), which `reference-index` validates;
  an `operations` entry has no index file to name.
- **Computed back, never stored.** `bin/ci-skills tools NAME --describe ID`
  adds `used_by`, the commands and references that list that operation.
- **Closed world.** Every `uses` id resolves to a declared operation, and
  every declared operation is used at least once; every `REFERENCES` path
  exists in the packaged skill, and every `points_to` names a skill that
  discovery finds (CI08-ROUTING, Gates, delegates these two here). Anything
  else fails the `schemas` gate, so no list goes stale.

## Validation

- **Tool.** `check-jsonschema`, the validator the shared standards' own check
  uses, pinned to an exact version in `requirements.txt` (CI03-GATES, G2).
  The toolchain contract (`agent-tools.sh`) lists it; version 0.38.0 is the
  installed one, read back on 2026-10-06.
- **Validator.** `tools/skillkit/schema.py` (planned, this phase) loads a
  schema by kind, validates one record and names the file, the JSON path and
  the rule on failure, and applies the version rule below. It is the first
  module of the package CI10-PHASES places maintenance-only code in, because
  every producer that validates runs from a checkout. `tools/check_schemas.py`
  (planned, this phase) is its thin main: it walks the committed records,
  calls the module and prints the result, `--json` or `--yaml` for agents
  and a summary on a terminal; its result kind is `schema_check`.
- **Gate.** The `schemas` gate of `scripts/check.sh` (CI03-GATES, G1, which
  also owns how one gate is selected; the script's library,
  `ci-skills/lib/bash/ci/check.bash` (planned, CI10-PHASES, block 0), is
  restored first) runs `tools/check_schemas.py --root . --json`, which:
  - checks every schema against the 2020-12 metaschema;
  - validates every committed record against its schema: `ci-skills/tools.json`,
    each receipt under `tests/acceptance/receipts/` and, as their phases add
    them, `vendor/vendor.lock.json`, `vendor/vendor.toml` and
    `ci-skills/agents/openai.yaml` (parsed to JSON first), each `index.json`,
    the `[[smoke_cases]]` entries, and the frontmatter of every `SKILL.md`
    (extracted first);
  - applies the closed world of `uses`, `REFERENCES` and `points_to` (above)
    and of each index file (CI09-REFERENCE, section 5), once those records
    exist;
  - fails a changed schema without a version bump, and a MAJOR bump without
    the new file (Versions).
- **At runtime.** Producers validate before they write, and
  `bin/ci-skills list` validates before it prints, with the `jsonschema`
  library that `check-jsonschema` installs. An invalid record exits 65
  (invalid data; CI02-CLI's table, under D-EXIT) and names the file, the JSON
  path and the rule. `verify` checks hashes only and stays on the standard
  library; full schema validation is the `schemas` gate's job. `schemas/` is
  outside the skill tree CI01-CATALOG installs, so an installed producer
  (`--receipt-out`, `ci-skills/bin/reference.py`) has no schema to read; how
  it validates is not settled by any source (owner: this document, in its
  pull request).
- **Tests.** Each schema has fixtures: one valid record, one per missing
  required field, and one per broken conditional rule (CI06-TESTS; the files
  and cases are in Delivery, test and proof).

## Versions

- `schema_version` is `MAJOR.MINOR`. A schema pins its MAJOR and accepts any
  MINOR, for example `"pattern": "^1\\.[0-9]+$"`.
- **MINOR:** an added optional field or enum value. Old consumers keep
  working. `summary.record_count` is in today's receipts
  (`tests/acceptance/receipts/issue-open-bug-applied.json`); `truncated`,
  which CI11-TOOLS' `list` verb reports beside it, is an added optional
  envelope field: a MINOR bump of `command-result`.
- **MAJOR:** a removed or renamed field, a new required field, or a narrower
  type. Old consumers break. Today's envelope (`core/report.py:52-61`) emits
  `target` and `filters` and no `execution_host`, `skill` or
  `target_source`; CI10-PHASES calls that delta the first `command-result`
  version, and whether it is MINOR or MAJOR follows from whether CI02-CLI
  binds those fields as optional or required.

### Adding what we missed

We will miss something. A miss is added as a MINOR change while existing readers keep working, in one pull request
that changes the schema, the producer and the one owning document together:

| We missed | Change | Version |
| --- | --- | --- |
| a field | add it as optional; producers emit it from the new MINOR on | MINOR |
| an enum value (a choice kind, a pointer action, a relation) | add the value | MINOR |
| a reserved segment or a node | add it; refuse a node named like a reserved segment | MINOR |
| a field's name, type or meaning, or making it required | a new MAJOR file beside the old one (step 3 below) | MAJOR |

A reader built for an older MINOR skips a choice, pointer or relation whose enum value it does not know, ignores
fields it does not know, and never fails the whole answer for them.

Exhibit, a miss we already know of: the priority and severity sections say their meaning lives in another reference,
and no relation can say so. The fix is one enum value in `schemas/reference-next.schema.json`, shown in that file's
own formatting:

```diff
         "rel": {
           "enum": [
-            "infers"
+            "infers",
+            "meaning_in"
           ]
         },
```

The producer then answers with `"schema_version": "1.1"`. Once the handbook's triage page is declared as reference
`gitlab-handbook-triage` (CI09-REFERENCE section 3, Declarations), the priority cluster may carry this relation:

```json
{"from": "priority-labels", "rel": "meaning_in", "to": "gitlab-handbook-triage.priority", "evidence": "priority-labels"}
```

The `^1\.[0-9]+$` pattern accepts both 1.0 and 1.1 answers; nothing else changes.

`reference-next`, `reference-section` and `gitlab-pipeline-watch` start at 1.0 although their producers are not
built yet: we fixed their shape by example, so they are locked contracts, not shapes in review, and step 1
below does not apply to them.

How a version is promoted:

1. A new shape starts at `0.<minor>` while its phase is in review. Nothing
   outside that phase may depend on it. `reference-index` is the first case:
   `0.1` in CI09-REFERENCE's review, `1.0` on its merge.
2. It becomes `1.0` when its phase merges.
3. A MAJOR bump adds a new file, for example `skill-index.v2.schema.json`,
   beside the old one. Producers write the new MAJOR, and consumers read
   both until every producer has moved. A separate pull request then deletes
   the old file.
4. When a second project needs a schema, it moves to the shared standards
   repository, which owns shared schemas, and this repository pins it
   through `standards-binding.yaml`.

The `schemas` gate fails a pull request that changes a schema without
changing its version, or bumps MAJOR without adding the new file.

## Block

| Part | Value |
| --- | --- |
| Capability | define and check every record shape |
| Owner | `schemas/` and `tools/skillkit/schema.py` (planned, this phase) |
| Entrypoint | `tools/check_schemas.py --root . --json`; the `schemas` gate of `scripts/check.sh` (CI03-GATES, G1) |
| Result | `schema_check`: `PASS`, or each failing file, JSON path and rule |
| Read-back | the gate re-validates every committed record |

## Steps

1. Add `schemas/` with the schemas whose records exist in the tree today, at
   `1.0`: `skill-manifest`, `command-contract`, `command-result`, the 17
   catalog kinds, `gitlab_runner_smoke_cleanup`, `live_acceptance` and
   `project_neutrality`, whose records already stamp `schema_version` `1.0`
   (`ci-skills/lib/core/catalog.py:26`, `core/report.py:52`,
   `tools/check_live_acceptance.py:615`, `tools/check_project_neutrality.py:52`);
   `skill-frontmatter`, whose records carry no identity fields (Identity);
   and `manifest_check` and `manifest_render`, which carry `kind` without
   `schema_version` today (`tools/render_manifest.py:67,77`), against
   Identity; that file's changes are CI01-CATALOG's (CI10-PHASES, Modify), so
   the stamp lands there. `schema_check` is added for the new main. The other
   schemas arrive with their phases at `0.<minor>` and become `1.0` when
   those phases merge. CI02-CLI, step 1, lists `command-contract` and
   `command-result` too: this step adds the files, and CI02-CLI binds its
   commands to them.
2. Add `tools/skillkit/schema.py` and `tools/check_schemas.py`, pin
   `check-jsonschema` in `requirements.txt` (CI03-GATES, G2), and add the
   `schemas` gate to the library of `scripts/check.sh` (CI03-GATES, G1).
3. Add the tests (Delivery, test and proof, part 2).
4. Open one pull request; merge per CI10-PHASES, How a phase lands.
5. Read back: `tools/check_schemas.py --root . --json` on `main` lists every
   record it validated, with its schema and version.

## Delivery, test and proof

1. *Delivery*: `schemas/skill-frontmatter.schema.json`,
   `schemas/skill-manifest.schema.json`, `schemas/command-contract.schema.json`,
   `schemas/command-result.schema.json`, one `schemas/<kind>.schema.json` for
   each of the 17 catalog kinds listed in Schemas, and
   `schemas/gitlab_runner_smoke_cleanup.schema.json`,
   `schemas/live_acceptance.schema.json`,
   `schemas/project_neutrality.schema.json`,
   `schemas/manifest_check.schema.json`, `schemas/manifest_render.schema.json`
   and `schemas/schema_check.schema.json`; `tools/skillkit/__init__.py` and
   `tools/skillkit/schema.py`; `tools/check_schemas.py`; `requirements.txt`
   (the `check-jsonschema` pin) and the library of `scripts/check.sh` (the
   `schemas` gate). The command that runs:
   `tools/check_schemas.py --root . --json`.
2. *Tests*: `tests/python/test_schema.py`, which loads `tools/skillkit/schema.py`
   and drives `tools/check_schemas.py` the way `tests/python/test_live_acceptance.py`
   and `tests/python/test_neutrality.py` drive their tools, with these cases
   (CI06-TESTS, "CI07-SCHEMA", every one carried here):
   - every file under `schemas/` validates against the 2020-12 metaschema;
   - per schema, one valid record passes: the committed record where one
     exists (`ci-skills/tools.json`, one receipt per kind, a `--describe`
     output, `tools/check_live_acceptance.py --json`), else a minimal record
     held in the test module;
   - per schema, one record per missing required field fails, derived from
     the valid record by removing that field, naming the file, the JSON path
     and the rule;
   - per schema, one record per broken conditional rule fails: a local
     `skill-index` entry carrying `upstream`, a vendored one carrying
     `manifest`, a `knowledge` reference entry without `index`;
   - closed world: an unknown field fails every record we generate and
     passes upstream frontmatter;
   - a record whose `kind` has no file under `schemas/` is refused;
   - the version gate: a schema changed without a version bump fails, and a
     MAJOR bump without the new `<kind>.v2.schema.json` fails, in a
     temporary git repository with `main` and a branch;
   - runtime: an invalid record makes the main exit 65, naming the file, the
     JSON path and the rule (D-EXIT; each producer's own wiring is tested in
     the phase that owns the producer);
   - `--help` exits 0 with an empty environment and lists every argument and
     output mode; `--json` and `--yaml` carry the same data (CI02-CLI).

   `tests/bash/check.bats` gains one case: a failing `schemas` gate fails the
   run and names the gate (the shape of CI03-GATES, Delivery, test and
   proof). The `uses`,
   `REFERENCES` and index-file closed-world cases ride with CI08-ROUTING and
   CI09-REFERENCE (CI06-TESTS, their sections), since those records do not
   exist yet. Written with the block; run status UNVERIFIED (CI03-GATES, G0;
   D-GATE, decided 2026-10-06: no gate for now, and the pytest and bats suites
   are never run on the laptop).
3. *Smoke*: this phase changes no live behaviour and declares no
   `[[smoke_cases]]` entry. The static read-back is
   `tools/check_schemas.py --root . --json` over `ci-skills/tools.json` and
   every receipt under `tests/acceptance/receipts/`: each is listed with its
   schema and `schema_version` `1.0`, and the result is `PASS`.
4. *Evidence*: no receipt for the schema check itself; the static evidence
   is `tools/check_schemas.py --root . --json` output. The `schemas` gate in
   `ci-skills/lib/bash/ci/check.bash` is a byte change inside `ci-skills/`,
   so the receipts are recaptured on the D-SMOKE executor (CI03-GATES, G5).
5. *Verification*: `tools/check_schemas.py --root . --json` prints
   `"status": "PASS"` with one entry per validated record (file, schema,
   `schema_version`), and the static checks CI10-PHASES names before merge
   are green.
