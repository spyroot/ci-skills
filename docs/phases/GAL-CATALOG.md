# GAL-CATALOG: list, get and install every skill

Status: proposed. Depends on: GAL-VENDOR. Followed by: GAL-ROUTING.

## Goal

One interface over local and vendored skills, named after `glab skills`:
`list`, `get` and `install`, beside GAL-VENDOR's `update`. It also lists
the repository's own tools: the root `SKILL.md` and the `bin/ci-*`
commands that PR #2 adds.

## Block

| Part | Value |
| --- | --- |
| Capability | list, read and install skills |
| Owner | `tools/skillkit/discover.py`, `tools/skillkit/install.py` |
| Entrypoint | `bin/ci-skills list`, `get` and `install` |
| Result | `skill_index`, the file's bytes, `skill_install` |
| Read-back | `install` compares the installed and source digests |

## Interface

```text
bin/ci-skills list [--json | --yaml]
bin/ci-skills get NAME [PATH]
bin/ci-skills install NAME [--skills-dir DIR] [--dry-run]
                                [--json | --yaml]
```

- `get` prints `SKILL.md` unless `PATH` names another file in the skill.
  `PATH` must resolve inside the skill directory: an absolute path, a `..`
  segment, or a symbolic link that leads outside is refused with
  `path_outside_skill`.
- `install` keeps the contract of today's installer: the default
  `--skills-dir` is `$CODEX_HOME/skills` or `~/.codex/skills`, an existing
  destination is refused rather than overwritten, and the source revision
  must be verified. `--skills-dir` selects another agent's directory.
- `tools/install_k8s_admin_diagnostics.py` stays as a thin wrapper over
  `skillkit/install.py`. It keeps `SKILL_NAME`, `install`, `package_files`,
  `skills_directory` and `tree_digest`, which `tests/test_installer.py` and
  `tests/test_installed_package.py` load from the module by path. It puts
  its own directory on `sys.path` so that `import skillkit` works when a
  test loads it that way.
- Exit codes follow GAL-VENDOR.

## Index record

`list` builds the index on every call from what each skill declares.
Nothing is committed, so the index cannot go stale.

```json
{
  "schema_version": "1.0",
  "kind": "skill_index",
  "skills": [
    {
      "name": "k8s-admin-diagnostics",
      "path": "skills/k8s-admin-diagnostics",
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
| `source` | `local` | the lock (GAL-VENDOR) |
| `entry` | `SKILL.md` | `SKILL.md` |
| `manifest` | `tools.json` | absent |
| `tags` | `tools.json` (GAL-ROUTING) | `skills/vendor.toml` |
| `references`, `depends_on` | `tools.json` (GAL-ROUTING) | absent |
| `upstream` | absent | the lock and `skills/vendor.toml` |

`list` adds nothing that a skill does not declare. Until GAL-ROUTING, a
local skill's `tags`, `references` and `depends_on` are empty. Measured as
single-line JSON in this shape with the real descriptions, a record is
about 470 bytes (k8s), 565 (`glab-stack`) and 785 (`glab`).

## Why this shape

- **One CLI with `glab`'s verbs.** Readers of `glab skills` meet the same
  words, and each verb maps to one block. Two verbs behave differently:
  - `install` defaults to `$CODEX_HOME/skills`, where `glab` defaults to
    `.agents/skills/`.
  - `update` rewrites this repository's vendored copy and lock, where
    `glab` refreshes installed copies.
- **A computed index.** A committed index would be one more generated file
  to keep current, plus one more check. Computing it reads a few
  frontmatters, one lock, one declaration file and one manifest.
- **When to revisit.** Past roughly 20 skills, add `--search TEXT` to
  `list`, reusing the name of the k8s skill's existing filter.

## Steps

How the vendored skills join the local one behind a single interface:

1. Move the installer logic from `tools/install_k8s_admin_diagnostics.py`
   into `tools/skillkit/install.py`, taking the skill name as a parameter.
   The old script becomes a wrapper that keeps its five names.
2. Add `tools/skillkit/discover.py`. It walks `skills/*/SKILL.md` and the
   root `SKILL.md`, reads the frontmatter, and merges in the lock and
   declarations for a vendored skill or the manifest for a local one,
   producing one record per skill.
3. Add the `list`, `get` and `install` verbs to `bin/ci-skills`, each
   with `--help` that lists every argument and output mode.
4. Add the tests below and open one pull request; the `validate` workflow
   must pass.
5. Read back: `bin/ci-skills list` shows `glab`, `glab-stack` and
   `k8s-admin-diagnostics`. Comparing `bin/ci-skills get glab` with
   `glab skills get glab` needs `glab`, which CI does not install, so that
   comparison runs where `glab` is installed, outside CI.

## Gates

- **Tests, run in CI.**
  - Each verb, and an unknown skill name.
  - An empty skills directory.
  - The record shape, including the per-source fields above.
  - `get` refusing `../x`, an absolute path, and a symbolic link that
    leaves the skill.
  - The installer wrapper still passing `tests/test_installer.py` and
    `tests/test_installed_package.py`.
- **Receipt.** Nothing under `skills/k8s-admin-diagnostics/` changes, so the
  committed live receipt still applies until it expires (GAL-VENDOR).

## Open decision

- Whether the `skill_index` and `skill_vendor_lock` shapes also get JSON
  Schema files. Today each shape is declared once in code and versioned by
  `schema_version`.
