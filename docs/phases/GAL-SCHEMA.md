# GAL-SCHEMA: record schemas

Status: proposed. Depends on: nothing. Used by GAL-VENDOR, GAL-CATALOG,
GAL-ROUTING, GAL-REFERENCE and GAL-CLI.

## Goal

Every record a command reads or writes has exactly one schema, so producers
and consumers cannot drift, and every change to a record's shape is
versioned.

## Schemas

All schemas live under `schemas/`, one file per record kind, named
`<kind>.schema.json`. They use JSON Schema draft 2020-12 and the style of the
shared standards' own schemas: `$schema`, `$id`, `title`, `type`, `required`,
`properties` and `$defs`.

| Schema | Validates | Written by |
| --- | --- | --- |
| `skill-frontmatter` | the YAML block in each `SKILL.md` | authors |
| `skill-manifest` | a local skill's `tools.json` | `render_manifest.py` |
| `skill-index` | `bin/ci-skills list --json` | discovery |
| `vendor-lock` | `skills/vendor.lock.json` | `ci-skills update` |
| `vendor-declarations` | `skills/vendor.toml` | maintainers |
| `tool-operations` | `bin/ci-skills tools --json` | GAL-REFERENCE |
| `command-contract` | each command's `--describe` | GAL-CLI |
| `command-result` | each command's JSON result | GAL-CLI |

Upstream authors write the frontmatter of vendored skills.

## Rules every schema follows

- **Identity.** Every record carries `kind`, a constant per schema, and
  `schema_version`.
- **Names.** Names match `^[a-z0-9]+(-[a-z0-9]+)*$`. A skill's `name` equals
  its directory name, which `tests/test_skill_package.py` already checks.
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
calls are described by operations (GAL-REFERENCE). One declaration links
them:

- **Operation ids.** Each tool operation has an id `<tool>:<operation>`, for
  example `gh:api.get`, `kubectl:auth.can-i` or `kubectl:exec.cilium-health`.
- **Declared once, by the user of the operation.** Each command in
  `skill-manifest`, and each entry of a skill's `REFERENCES`, lists the
  operations it uses in `uses`. For example, `references/access.md` uses
  `gh:auth.status`, `glab:auth.status`, `kubectl:auth.whoami` and
  `kubectl:auth.can-i`.
- **Computed back, never stored.** `bin/ci-skills tools NAME --describe ID`
  adds `used_by`, the commands and references that list that operation.
- **Closed world.** Every `uses` id resolves to a declared operation, and
  every declared operation is used at least once; anything else fails the
  `schemas` gate, so neither list goes stale.

## Validation

- **Tool.** `check-jsonschema`, the validator the shared standards' own check
  uses, pinned to an exact version in `requirements.txt`. Version 0.38.0 is
  in the toolchain contract today.
- **Gate.** A `schemas` gate in `./scripts/check.sh` (GAL-GATES):
  - checks every schema against the 2020-12 metaschema;
  - validates `tools.json` and `skills/vendor.lock.json`;
  - validates `skills/vendor.toml`, parsed to JSON first;
  - validates the frontmatter of every `SKILL.md`, extracted first.
- **At runtime.** Producers validate before they write, and
  `bin/ci-skills list` validates before it prints, with the `jsonschema`
  library that `check-jsonschema` installs. An invalid record exits 65
  (invalid data) and names the file, the JSON path and the rule. `verify`
  checks hashes only and stays on the standard library; full schema
  validation is the `schemas` gate's job.
- **Tests.** Each schema has fixtures: one valid record, one per missing
  required field, and one per broken conditional rule (GAL-TESTS).

## Versions

- `schema_version` is `MAJOR.MINOR`. A schema pins its MAJOR and accepts any
  MINOR, for example `"pattern": "^1\\.[0-9]+$"`.
- **MINOR:** an added optional field or enum value. Old consumers keep
  working.
- **MAJOR:** a removed or renamed field, a new required field, or a narrower
  type. Old consumers break.

How a version is promoted:

1. A new shape starts at `0.<minor>` while its phase is in review. Nothing
   outside that phase may depend on it.
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
| Owner | `schemas/` |
| Entrypoint | the `schemas` gate of `./scripts/check.sh` |
| Result | `PASS`, or each failing file, JSON path and rule |
| Read-back | the gate re-validates every committed record |

## Steps

1. Add `schemas/` with `skill-frontmatter` and `skill-manifest` at `1.0`,
   since their records exist today. The other schemas arrive with their
   phases at `0.<minor>` and become `1.0` when those phases merge.
2. Pin `check-jsonschema` in `requirements.txt`, and add the `schemas` gate.
3. Add the fixture tests.
4. Open one pull request; the `validate` workflow must pass.
5. Read back: the gate lists every record it validated, with its schema
   and version.
