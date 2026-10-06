# CI01-CATALOG: discover, list, get and install every skill

Status: proposed. Depends on: CI05-VENDOR, CI07-SCHEMA and CI02-CLI. Followed by: CI08-ROUTING.

## Goal

One interface over local and vendored skills, named after `glab skills`:
`list`, `get` and `install`, beside CI05-VENDOR's `update`. `list` returns
skill records, including the repository's `ci-skills` package; its commands
are described by that package's `tools.json`. Package consolidation
(CI10-PHASES) lands before this phase.

## Block

| Part | Value |
| --- | --- |
| Capability | discover, list, read and install skills |
| Owner | `tools/skillkit/discover.py`, `tools/skillkit/install.py` |
| Entrypoint | `bin/ci-skills list`, `get` and `install` |
| Result | `skill_index`, the file's bytes, `skill_install` |
| Read-back | `install` compares the installed and source digests |

## Discovery

`tools/skillkit/discover.py` builds one validated map of skills, keyed by
name. Every verb works from that map, and nothing outside it can be named.
Discovery only reads: it writes nothing and uses no network.

### Walk

1. Start at `skills/`. The only local package is `ci-skills`;
   the root `SKILL.md` from PR #2 is consolidated and removed before this
   phase. Root `bin/ci-*` commands are tools, not another skill record.
2. Take each directory directly under `skills/` that holds a
   `SKILL.md`, in name order.
3. Skip nested directories, hidden directories and `__pycache__`; they are
   not skills.
4. Refuse a symbolic link anywhere in a skill (`symlink_unexpected`), as the
   copy installer already does. Package delivery replaces PR #2's link
   installer and defines a confirmed migration for an existing link.
5. With `--skills-dir DIR`, walk copied installed skills the same way, so an
   installed set can be listed too.

### Read, per skill

1. **`SKILL.md`.** The frontmatter is the block between a first line `---`
   and the next line `---`. It is parsed as YAML and checked against
   `skill-frontmatter` (CI07-SCHEMA). Its `name` must equal the directory
   name.
2. **`tools.json`**, for a local skill, checked against `skill-manifest`. It
   supplies the tags, references, routing and tool operations used
   (CI08-ROUTING).
3. **The lock and declarations**, for a vendored skill: its entry in
   `vendor/vendor.lock.json` and in `vendor/vendor.toml`, each checked
   against its schema.
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
    or `~/.codex/skills`, and the source revision must be verified.
- **Wrapper.** `tools/install_ci_skills.py`, established by package delivery,
  stays a thin wrapper over `skillkit/install.py` while its callers migrate.
  It keeps
  `SKILL_NAME`, `install`, `package_files`, `skills_directory` and
  `tree_digest`, which `tests/python/test_installer.py` and
  `tests/python/test_installed_package.py` load from the module by path. It puts its
  own directory on `sys.path` so that `import skillkit` works when a test
  loads it that way.

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
      "path": "skills/glab",
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
| `references`, `depends_on` | `tools.json` (CI08-ROUTING) | absent |
| `upstream` | absent | the lock and `vendor/vendor.toml` |

`list` adds nothing a skill does not declare. Until CI08-ROUTING, the local
skill's `tags`, `references` and `depends_on` are empty. Historical package
size measurements are in CI08-ROUTING; measure the consolidated record before
setting an output budget.

## Why this shape

- **One CLI with `glab`'s verbs.** Readers of `glab skills` meet the same
  words, and each verb maps to one block. Two verbs differ:
  - `install` defaults to `$CODEX_HOME/skills`, where `glab` defaults to
    `.agents/skills/`;
  - `update` rewrites this repository's vendored copy and lock, where
    `glab` refreshes installed copies.
- **A computed index.** A committed index would be one more generated file
  to keep current. Computing it reads a few frontmatters, one lock, one
  declaration file and the manifests.
- **When to revisit.** Past roughly 20 skills, add `--search TEXT` to
  `list`, reusing the diagnostics command's existing filter name.

## Steps

1. Move the installer logic from `tools/install_ci_skills.py` into
   `tools/skillkit/install.py`, taking the skill name as a parameter. Keep
   that command as the wrapper described above until callers migrate.
2. Add `tools/skillkit/discover.py` with the walk and read above.
3. Extend CI05-VENDOR's repository-level `bin/ci-skills` with `list`, `get`
   and `install`. Each verb has `--help` and `--describe` (CI02-CLI). This
   maintenance command is outside the installed `ci-skills` package.
4. Add the tests CI06-TESTS lists, and open one pull request; the
   `validate` workflow must pass.
5. Read back: `bin/ci-skills list` shows exactly one `ci-skills` record,
   plus `glab` and `glab-stack`. Comparing `bin/ci-skills get glab` with
   `glab skills get glab` needs `glab`, which CI does not install, so that
   comparison runs where `glab` is installed.

## Gates

- **Tests.** As listed in CI06-TESTS, run in CI.
- **Receipt.** This phase does not change `ci-skills`, so the
  package-delivery receipt remains valid until expiry. If this phase changes
  package bytes, capture a fresh receipt after the last edit (CI03-GATES).
