# CI01-CATALOG: discover, list, get and install every skill

Status: proposed. Order and dependencies: CI10-PHASES, Phases.

## Goal

One interface over local and vendored skills, named after `glab skills`:
`list`, `get` and `install`, beside CI05-VENDOR's `update`. `list` returns
skill records, including the repository's `ci-skills` package; its commands
are described by that package's `tools.json`. Package delivery landed before
this phase (#21, merged, d6bba3d); the paths that still read
`skills/ci-skills` (the installer source, the renderer and the test roots)
are block 0 of CI10-PHASES, Order.

## Block

| Part | Value |
| --- | --- |
| Capability | discover, list, read and install skills |
| Owner | `tools/skillkit/discover.py`, `tools/skillkit/install.py` (planned, CI01-CATALOG) |
| Entrypoint | `bin/ci-skills list`, `get` and `install` (planned, CI05-VENDOR) |
| Result | `skill_index`, the file's bytes, `skill_install` |
| Read-back | `install` compares the installed and source digests |

`bin/ci-skills`, the root maintenance command, is created by CI05-VENDOR with
`verify` and `update` and extended here with `list`, `get` and `install`; it
is outside the installed `ci-skills` package. Each record kind has one schema
(CI07-SCHEMA): `skill-index` for `skill_index`, and the `command-result`
schema of `skill_install`.

## Discovery

This phase adds `tools/skillkit/discover.py`, which builds one validated map
of skills, keyed by name. Every verb works from that map, and nothing outside
it can be named. Discovery only reads: it writes nothing and uses no network.

### Walk

1. Start at the repository root. The one local skill is `ci-skills/`: its
   `SKILL.md`, `tools.json`, `bin/`, `lib/` and `references/`. The root
   `SKILL.md` from PR #2 was consolidated into it and removed by package
   delivery (#21, merged, d6bba3d). Root `bin/ci-*` commands are tools, not
   another skill record. There is no `skills/` directory.
2. Then take each directory directly under `vendor/skills/` (planned,
   CI05-VENDOR) that holds a `SKILL.md`, in name order.
3. Skip nested directories, hidden directories and `__pycache__`; they are
   not skills.
4. Refuse a symbolic link anywhere in a skill (`symlink_unexpected`), as the
   copy installer already does (`skill_symlink_unexpected` in
   `tools/install_ci_skills.py`). Package delivery (#21, merged, d6bba3d)
   replaced PR #2's link installer; an existing link is migrated by a
   confirmed upgrade (`--upgrade --apply --confirm-upgrade FINGERPRINT`).
5. With `--skills-dir DIR`, walk the direct children of `DIR` the same way as
   `vendor/skills/`, so an installed set can be listed too.

### Read, per skill

1. **`SKILL.md`.** The frontmatter is the block between a first line `---`
   and the next line `---`. It is parsed as YAML and checked against
   `skill-frontmatter` (CI07-SCHEMA). Its `name` must equal the directory
   name.
2. **`tools.json`**, for a local skill, checked against `skill-manifest`. It
   supplies the tags and routing (CI08-ROUTING), the references from the
   catalog's `REFERENCES` table, each with its `kind`, `operations` or
   `knowledge` (CI09-REFERENCE), and the `uses` pointers, whose schema is
   CI07-SCHEMA's and whose operations CI09-REFERENCE declares.
3. **The lock and declarations**, for a vendored skill: its entry in
   `vendor/vendor.lock.json` and in `vendor/vendor.toml` (planned,
   CI05-VENDOR), each checked against its schema.
4. **The merged record**, checked against `skill-index` before anything is
   printed.

### Errors

- Each problem names the file, the field and the rule:
  - `frontmatter_missing`
  - `frontmatter_invalid`
  - `name_mismatch`
  - `manifest_invalid`
  - `lock_entry_missing`
  - `symlink_unexpected`
- `list` prints every valid record and lists each invalid one under
  `errors`. If any record failed, it exits 65.
- Output is sorted and bounded. Nothing is read from a skill's body except
  its frontmatter.

## Interface

```text
bin/ci-skills list [--skills-dir DIR] [--json | --yaml | --human]
bin/ci-skills get NAME [PATH]
bin/ci-skills install NAME [--skills-dir DIR] [--confirm]
                           [--json | --yaml | --human]
```

Exit codes, the result envelope and `safe_next_step` follow CI02-CLI.

- **`NAME`.** Resolved only from the discovered map. A name with a path
  separator, `.`, `..`, or a symlinked skill root is refused with
  `skill_unknown`, and never joined to a path.
- **`get`.** Prints `SKILL.md` unless `PATH` names another file in the
  skill. `PATH` must resolve inside the skill directory: an absolute path, a
  `..` segment, or a symbolic link that leads outside is refused with
  `path_outside_skill`.
- **`install` plans by default** and copies only with `--confirm`
  (CI02-CLI). It also:
  - runs `verify` first for a vendored skill, refusing with
    `vendor_unverified` on any mismatch;
  - checks every skill named in `depends_on` is already installed in the
    destination with a matching digest, refusing with `dependency_missing`
    and naming the `install` command to run first;
  - when the destination already holds the same digest, does nothing and
    returns `PASS`; a different digest is refused with
    `destination_differs`;
  - keeps today's defaults: `--skills-dir` defaults to `$CODEX_HOME/skills`
    or `~/.codex/skills` (`skills_directory` in `tools/install_ci_skills.py`),
    and the source revision must be verified (`skill_identity` in
    `core/provenance.py` marks it `verified: true` only from a clean Git
    subtree).
- **Wrapper.** `tools/install_ci_skills.py` is today's installer (#21,
  merged, d6bba3d); `install.sh` forwards every argument to it. Today it:
  - resolves `SOURCE` to `skills/ci-skills`, the path before the layout
    decided on 2026-10-04 (CI10-PHASES, Layout), puts its `scripts/` on
    `sys.path` and imports `_included`, `skill_identity` and `tree_digest`
    from `core.provenance`; block 0 repoints it;
  - plans by default and applies only with `--apply --timeout Ns` and the
    plan's fingerprint (`--confirm-install` or `--confirm-upgrade`),
    refusing a changed one (`installation_fingerprint_changed`);
  - refuses an existing destination or a symbolic link there
    (`destination_exists`, `destination_symlink`; both lifted by
    `--upgrade`), a non-directory there (`destination_not_directory`), and
    an unverified source subtree (`source_revision_unverified`);
  - on apply, takes an advisory lock in the skills directory, writes a
    journal, copies into a staging directory, swaps it into place keeping
    the previous copy or link as a hidden sibling, reads back the installed
    digest (`installed_digest_mismatch` on a difference), and reconciles an
    interrupted run with `--recover --apply --confirm-recover`.

  This phase moves that logic into `tools/skillkit/install.py`, taking the
  skill name as a parameter, and keeps the command as a thin main over it
  while its callers migrate. The wrapper keeps `SKILL_NAME`, `install`,
  `package_files`, `skills_directory` and `tree_digest`, which
  `tests/python/test_installer.py` and `tests/python/test_installed_package.py`
  load from the module by path (`load_module`, `tests/python/conftest.py`),
  and it puts its own directory on `sys.path` so that `import skillkit`
  works when a test loads it that way.

## Index record

`list` builds the index on every call; nothing is committed, so it cannot go
stale. Its schema is `skill-index` (CI07-SCHEMA).

```json
{
  "schema_version": "1.0",
  "kind": "skill_index",
  "skills": [
    {
      "name": "ci-skills",
      "path": "ci-skills",
      "source": "local",
      "entry": "SKILL.md",
      "manifest": "tools.json",
      "description": "...",
      "tags": [],
      "references": [],
      "depends_on": []
    },
    {
      "name": "glab",
      "path": "vendor/skills/glab",
      "source": "glab-bundled",
      "entry": "SKILL.md",
      "description": "...",
      "tags": ["gitlab", "merge-request", "issue", "pipeline"],
      "upstream": {"project": "gitlab-org/cli", "license": "MIT"}
    }
  ]
}
```

Where each field comes from:

| Field | Local skill | Vendored skill |
| --- | --- | --- |
| `name`, `description` | `SKILL.md` frontmatter | `SKILL.md` frontmatter |
| `source` | `local` | the lock (CI05-VENDOR) |
| `entry` | `SKILL.md` | `SKILL.md` |
| `manifest` | `tools.json` | absent |
| `tags` | `tools.json` (CI08-ROUTING) | `vendor/vendor.toml` |
| `references` | `tools.json`, the `REFERENCES` table, each entry with its `kind` (CI09-REFERENCE) | absent |
| `depends_on` | derived from the `points_to` values in `tools.json` (CI08-ROUTING) | absent |
| `upstream` | absent | the lock and `vendor/vendor.toml` |

`list` adds nothing a skill does not declare. Until CI09-REFERENCE and
CI08-ROUTING, the local skill's `references`, `tags` and `depends_on` are
empty. Historical package size measurements are in CI08-ROUTING; measure the
consolidated record before setting an output budget.

## Why this shape

- **One CLI with `glab`'s verbs.** Readers of `glab skills` meet the same
  words, and each verb maps to one block. Two verbs differ:
  - `install` defaults to `$CODEX_HOME/skills`, where `glab` defaults to
    `.agents/skills/`; `--scope repo` on the installer reaches that layout
    (Codex metadata and install scopes, below);
  - `update` rewrites this repository's vendored copy and lock, where
    `glab` refreshes installed copies.
- **A computed index.** A committed index would be one more generated file
  to keep current. Computing it reads a few frontmatters, one lock, one
  declaration file and the manifests.
- **When to revisit.** Past roughly 20 skills, add `--search TEXT` to
  `list`, reusing the diagnostics command's existing filter name.

## Codex metadata and install scopes

The implementation map (CI10-PHASES) assigns this phase the Codex metadata
and the install scopes. The layout and the scope table come from the Codex
skill documentation (<https://learn.chatgpt.com/docs/build-skills>) and the
Agent Skills specification (<https://agentskills.io/specification>); nothing
below goes beyond them.

- **`ci-skills/agents/openai.yaml`** (planned, CI01-CATALOG) is rendered by
  `tools/render_manifest.py` beside `tools.json`, from the catalog and the
  `SKILL.md` frontmatter, and byte-compared by the same manifest test
  (`test_the_rendered_manifest_matches_the_module` in
  `tests/python/test_catalog.py`). Its schema is `openai-agent-metadata`
  (CI07-SCHEMA). Its fields:
  - `interface.display_name`;
  - `interface.short_description`, from the skill `description`;
  - `interface.icon_small`, `./assets/ci-skills-small.svg`;
  - `interface.icon_large`, `./assets/ci-skills.png`;
  - `interface.brand_color`; we supply the artwork and the colour, none is
    invented;
  - `interface.default_prompt`;
  - `policy.allow_implicit_invocation: true`, because every mutating command
    plans by default and needs `--apply --confirm-plan DIGEST`
    (`core/gitlab_actions.py:406`);
  - `dependencies.tools: []`, no MCP dependency; the required CLIs are
    declared per command in `tools.json` `required_tools`.

  The fields the frontmatter does not carry (`display_name`, `brand_color`,
  `default_prompt`) come from the catalog declaration this phase adds, so the
  render has one source per field.
- **`ci-skills/assets/`** (planned, CI01-CATALOG) holds only the two icon
  files. The icon paths are relative to the skill root, as in the Codex
  layout, so the `assets/` of the implementation map is `ci-skills/assets/`.
- **`--scope`** on `tools/install_ci_skills.py`, and through `install.sh`,
  which forwards every argument to it. It joins the mutually exclusive
  location group that `--skills-dir` and `--destination` form today; the
  default stays today's behaviour. Destinations:
  - `repo`: `<git root>/.agents/skills/ci-skills`;
  - `user`: `$HOME/.agents/skills/ci-skills`;
  - `admin`: `/etc/codex/skills/ci-skills`;
  - `codex-home`: `$CODEX_HOME/skills/ci-skills`, or
    `~/.codex/skills/ci-skills`; today's default;
  - `claude-user`: `~/.claude/skills/ci-skills`;
  - `claude-project`: `.claude/skills/ci-skills`.

  `--skills-dir PATH` stays for any other location and is mutually exclusive
  with `--scope`. This repository keeps no `.agents/skills/` self-copy:
  `ci-skills/` is the source of truth. Whether `bin/ci-skills install` takes
  `--scope` too is not declared here; decided with D-SCOPE (Open decisions).

## Steps

1. Move the installer logic from `tools/install_ci_skills.py` into
   `tools/skillkit/install.py`, taking the skill name as a parameter. Keep
   that command as the wrapper described above until callers migrate, and
   add `--scope` to it.
2. Add `tools/skillkit/discover.py` with the walk and read above.
3. Extend CI05-VENDOR's repository-level `bin/ci-skills` with `list`, `get`
   and `install`. Each verb has `--help` and `--describe` (CI02-CLI). This
   maintenance command is outside the installed `ci-skills` package.
4. Add the `openai.yaml` render to `tools/render_manifest.py` and the two
   icon files under `ci-skills/assets/`, then render once (Codex metadata
   and install scopes).
5. After the last skill edit, capture a fresh publication receipt on the
   declared executor (Delivery, test and proof, part 4).
6. Add the tests (Delivery, test and proof, part 2), and open one pull
   request; merge per CI10-PHASES, How a phase lands.
7. Read back: `bin/ci-skills list` shows exactly one `ci-skills` record,
   plus `glab` and `glab-stack`. Comparing `bin/ci-skills get glab` with
   `glab skills get glab` needs `glab`, which no gate route installs
   (CI03-GATES, G0), so that comparison runs on this laptop, where `glab`
   1.120.0 was observed on 2026-10-02 (CI05-VENDOR).

## Gates

- **Tests.** Written with the block (Delivery, test and proof, part 2); run
  status UNVERIFIED until a gate route exists (CI03-GATES, G0; decided
  2026-10-06, D-GATE).
- **Receipt.** This phase adds `ci-skills/agents/openai.yaml` and
  `ci-skills/assets/` inside the skill tree, so the skill digest changes and
  the committed publication receipt no longer matches; capture a fresh one
  after the last skill edit (Delivery, test and proof, part 4; CI03-GATES,
  G5).

## Delivery, test and proof

1. *Delivery*. Created: `tools/skillkit/discover.py`,
   `tools/skillkit/install.py`, `ci-skills/agents/openai.yaml` (generated),
   `ci-skills/assets/ci-skills-small.svg`, `ci-skills/assets/ci-skills.png`,
   `tests/python/test_skillkit_discover.py`,
   `tests/python/test_skillkit_install.py` and
   `tests/python/test_ci_skills_cli.py`. Changed: `bin/ci-skills` (the three
   verbs), `tools/install_ci_skills.py` and `install.sh` (`--scope`; the thin
   main), `tools/render_manifest.py` (the second render), `core/catalog.py`
   (the metadata fields the frontmatter does not carry),
   `tests/python/test_catalog.py`, `tests/python/test_render_manifest.py`,
   `tests/python/test_installer.py`, `tests/python/test_installed_package.py`,
   `tests/bash/install.bats` and `tests/acceptance/receipts/operator-laptop.json`
   (recaptured). The commands that run: `bin/ci-skills list --json`;
   `bin/ci-skills get glab`;
   `bin/ci-skills install glab --skills-dir DIR --confirm --json`;
   `tools/render_manifest.py`;
   `tools/install_ci_skills.py --scope user --dry-run --json`.
2. *Tests*, written with the block; run status UNVERIFIED (CI03-GATES, G0).
   The cases CI06-TESTS lists for this phase, by file:
   - `tests/python/test_skillkit_discover.py` (new):
     - the walk order is `ci-skills/`, then the direct children of
       `vendor/skills/` in name order; the repository root is not a second
       skill;
     - exactly one `ci-skills` record resolves to `ci-skills`;
     - `list --skills-dir DIR` reads a copied installed package; an
       unconverted symbolic link reports `symlink_unexpected` with the
       upgrade command;
     - nested and hidden directories are skipped;
     - each error token: `frontmatter_missing`, `frontmatter_invalid`,
       `name_mismatch`, `manifest_invalid`, `lock_entry_missing` and
       `symlink_unexpected`;
     - `list` exits 65 when any record fails, and still prints the valid
       ones;
     - each local record carries its references with their `kind`.
   - `tests/python/test_ci_skills_cli.py` (new; the `run_tool` fixture,
     CI06-TESTS):
     - as `NAME`, `get` and `install` refuse `../x`, `a/b`, `.` and a
       symlinked skill root, all with `skill_unknown`;
     - as `PATH`, `get` refuses `../x`, an absolute path and an escaping
       symbolic link, with `path_outside_skill`;
     - `get NAME` prints the `SKILL.md` bytes unchanged, and `get NAME PATH`
       prints that file;
     - each verb's `--help` and `--describe` exit 0 with an empty
       environment (CI02-CLI);
     - the `--json` output of `list` validates against `skill-index`
       (CI07-SCHEMA).
   - `tests/python/test_skillkit_install.py` (new), the full matrix for a
     mutating command (CI06-TESTS, Rules), including:
     - a vendored skill with bytes that do not match its lock gives
       `vendor_unverified`;
     - a missing dependency gives `dependency_missing`;
     - the same digest twice is a no-op `PASS` that writes nothing;
     - a different digest gives `destination_differs`;
     - a failed copy removes the staging directory;
     - a read-back mismatch gives `installed_digest_mismatch`;
     - the default run plans and writes nothing, `--confirm` applies, and a
       plan whose input fingerprint changed is refused;
     - a missing input (`SKILL.md` absent); `install` uses no tool, network
       or credential, so the authentication and retry rows do not apply;
     - cleanup on success, failure, timeout and TERM or INT, and a cleanup
       failure changing the final result;
     - the success and failure records validate against the `skill_install`
       schema, with secret redaction.
   - `tests/python/test_installer.py` and `tests/bash/install.bats`, the
     regression on the wrapper:
     - the existing cases pass against the compatibility wrapper; block 0
       (CI10-PHASES, Order) repoints their `skills/ci-skills` path
       assertions first;
     - each `--scope` name resolves to its documented destination; `--scope`
       with `--skills-dir` is refused by the parser; no `--scope` keeps
       `skills_directory` (`test_default_destination_uses_codex_home`);
     - `install.sh --scope codex-home --dry-run` reads back the same
       `destination` as a run without `--scope`.
   - `tests/python/test_catalog.py` and `tests/python/test_render_manifest.py`:
     - `ci-skills/agents/openai.yaml` is byte-equal to its render (the
       manifest test extended), and `--check` reports `STALE` when either
       file is stale, without writing;
     - the rendered file validates against `openai-agent-metadata`
       (CI07-SCHEMA);
     - `interface.short_description` equals the frontmatter `description`,
       and `dependencies.tools` is empty.
   - `tests/python/test_installed_package.py`, the offline smoke: it also
     installs a vendored skill and compares digests; `bin/ci-skills` runs
     from the repository as a maintenance command; the smoke exercises the
     diagnostics and PR #2 tools from outside the checkout.
   - Outside any gate: `get glab` is compared byte for byte with
     `glab skills get glab` where `glab` is installed.
3. *Smoke*. This phase touches no live target: `list`, `get` and `install`
   read the tree and the destination only, so the proof is static read-back
   on the executor `tests/acceptance/expected.toml` declares (`mac.lan`,
   D-SMOKE, decided 2026-10-06), after merge, from a clean checkout of
   `main`:
   - `bin/ci-skills list --json` reads back exactly one record with
     `name: ci-skills`, `source: local` and `path: ci-skills`, plus `glab`
     and `glab-stack` with `source: glab-bundled`, an empty `errors`, exit 0;
   - `tools/install_ci_skills.py --upgrade --dry-run --json`, then
     `--upgrade --apply --confirm-upgrade FINGERPRINT --timeout 10s --json`,
     brings today's default destination, `~/.codex/skills/ci-skills`, which
     holds an old-layout copy (CI10-PHASES, Publish and install), to the
     current digest; `bin/ci-skills install ci-skills --confirm --json` then
     reads back `PASS` with that digest and writes nothing, the no-op;
   - `conda run -n ci-skills python ~/.codex/skills/ci-skills/bin/access_check.py --describe`
     prints `kind: command_contract`, byte-equal to the checkout's
     `ci-skills/bin/access_check.py --describe` because `install` read back
     the same digest and the contract holds only catalog content;
   - `bin/ci-skills install glab --confirm --json` reads back `PASS` and a
     `digest` equal to the `glab` tree digest in `vendor/vendor.lock.json`
     (or `destination_differs`, where `~/.codex/skills/glab` already holds
     other bytes); `bin/ci-skills list --skills-dir ~/.codex/skills --json`
     then lists `ci-skills` and `glab`;
   - `tools/render_manifest.py --check` reads back `CURRENT` for `tools.json`
     and `agents/openai.yaml`.
4. *Evidence*. The skill digest changes (part 1 adds files under
   `ci-skills/`), so the publication receipt is recaptured on `mac.lan`,
   from the clean checkout of `main` that part 3 names (a dirty checkout
   gives `verified: false` and fails the revision check), with
   `ci-skills/bin/access_check.py --publication --receipt-out tests/acceptance/receipts/operator-laptop.json`.
   The checker compares, from that receipt: `kind` (`access_check`),
   `schema_version`, `status` (`PASS`), `publication` (`true`),
   `execution_host` against `[[executors]].host`, `skill.digest` against the
   digest recomputed from `ci-skills/`, `skill.revision` (`verified: true`
   and equal to `tested_revision`), `captured_at` against
   `max_receipt_age_days`, the receipt against its own redacted form,
   `targets` against `[targets]`, the identities in `surfaces` and
   `credential_sources` against `[executors.identities]`, the `live_checks`
   named in `required_live_checks` with `job_url` and `ceph_namespace`, and
   the `required_checks` read-back. `required_checks`
   still names `validate`, deleted in #27 (1108cca); what the receipt reads
   back instead is not declared here; decided in CI03-GATES, G0 (D-GATE).
   The offline verbs have no receipt: the static evidence is the
   `bin/ci-skills list --json` and `--describe` output of part 3. A
   `[[smoke_cases]]` entry for them needs `--receipt-out` on
   `bin/ci-skills`, which this document does not declare; decided with
   CI03-GATES, G5.
5. *Verification*. The checker:

   ```text
   tools/check_live_acceptance.py --root . --expected tests/acceptance/expected.toml \
     --receipts tests/acceptance/receipts --skill ci-skills --json
   ```

   prints `"status": "PASS"` (`Live acceptance: PASS` without `--json`);
   `tools/render_manifest.py --check` prints `CURRENT`;
   `tools/check_project_neutrality.py --root . --json` reports `PASS`. The
   pytest and bats suites are never run on this laptop; which surface runs
   them is CI03-GATES, G0.

## Open decisions

- **D-SCOPE, the install scope default for Codex.** `$HOME/.agents/skills`
  is the documented path; `~/.codex/skills` is today's default
  (`skills_directory` in `tools/install_ci_skills.py`). Until decided,
  `--scope codex-home` and no `--scope` behave the same.
