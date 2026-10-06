# CI08-ROUTING: routing, lazy loading and compact representation

Status: proposed. Order and dependencies: CI10-PHASES, Phases.

## Goal

An agent reaches the one file it needs in three hops and opens a file only
when its tags match, instead of loading every skill up front.

## Why

Sizes measured on 2026-10-06 with `wc -c` and `jq`; `tools.json` declares 17
commands:

| What an agent could load | Bytes |
| --- | --- |
| `ci-skills/SKILL.md` (229 lines) | 12,501 |
| `ci-skills/tools.json` | 26,432 |
| `ci-skills/references/access.md` | 9,443 |
| `ci-skills/references/project-binding.md` | 4,323 |
| all of the above | 52,699 |
| the `routing` table in `tools.json`, as single-line JSON | 983 |
| the `storage_report.py` entry, as single-line JSON | 811 |

The last two rows use Python's default JSON separators. The vendored `glab`
skills are not in the tree (planned, CI05-VENDOR), so the table no longer
carries their sizes.

## Three hops

The hops are implemented by `ci-skills/bin/reference.py next` (planned,
CI09-REFERENCE, section 3, measured); this phase supplies the tags it reads
and the `SKILL.md` sentence that points at it.

- **Sources.** The navigator's tree is derived (CI09-REFERENCE, section 3)
  from the reference indexes and from `tools.json`, which
  `tools/render_manifest.py` renders from `ci-skills/lib/core/catalog.py`:
  its `routing` phrases, `use_when`, `subcommands` and each command's
  `requires_authorities`. This phase derives the tags in the catalog, once,
  so `tools.json` carries them and the navigator reads them.
- **Installed copies.** CI09-REFERENCE lists `ci-skills/bin/reference.py`
  among the installed entrypoints (section 2, Block, knowledge kind), so an
  installed copy will answer `next` without a checkout. `bin/ci-skills list`
  (planned, CI01-CATALOG) needs a checkout, because `tools/` is not installed
  with a skill.
- **Matching `load_when`** (stated once, here). A
  `load_when` entry matches in this order: an exact status token against a
  report's `status` (for example `BLOCKED`); else a case-insensitive
  substring over the task text; then a case-insensitive substring over the
  `load_when` entries of a reference's `index.json` (planned,
  CI09-REFERENCE), which are full keyword paths. The ranking of `next` choices is the navigator's own
  (CI09-REFERENCE, section 3, Matching).
- **Example.** CI09-REFERENCE's measured menus (section 3):
  - `next gitlab ci-yaml trigger`, 904 bytes: five children and the `get`
    leaf;
  - `next gitlab pipeline`, 622 bytes: `needs:pipeline`,
    `needs:pipeline:job`, `gitlab_job.py --describe` and
    `gitlab_pipeline.py --describe`.

  An agent with a pipeline question runs `next gitlab pipeline`, then the
  `next` or leaf it chose; `references/access.md` opens only on `BLOCKED`.

## Block

| Part | Value |
| --- | --- |
| Capability | route an agent to one file |
| Owner | `ci-skills/lib/core/catalog.py` in `ci-skills` |
| Entrypoint | `tools/render_manifest.py` |
| Result | `skill_manifest` (today's `tools.json` shape) |
| Read-back | byte-equality test on `tools.json`; `bin/reference.py next gitlab --json`; a fresh live receipt |

## Changes to `ci-skills`

1. `ci-skills/lib/core/catalog.py`: tags derived in the catalog, consumed
   from CI09-REFERENCE's `REFERENCES`, which records per reference its
   `kind` (`operations` or `knowledge`), `index` path and `domain`. For each
   reference this phase adds:
   - the tags;
   - the `load_when` entries;
   - an optional `points_to` skill;
   - the tool operations it describes, in `uses` (CI07-SCHEMA owns the
     pointer schema and the closed-world rule; CI09-REFERENCE owns the
     operations the ids name).

   Each command declares its `uses` too. Everything renders into
   `tools.json` with the rest of the catalog. `depends_on` is derived from
   the `points_to` values, so the link to `glab` is declared once. The
   navigator reports each choice's size as `bytes` (CI09-REFERENCE,
   section 3); for the references in the table above those are the measured
   sizes. How a tag is derived for a reference that has no index
   (`access.md`, `project-binding.md`, and `reading-reports.md`, planned,
   this phase) is not stated by any phase document; the gap is this phase's
   to close before step 1.
2. Routing grows from symptom → command to symptom → command, reference and
   tags.
3. `SKILL.md` becomes the router: access first, one sentence that points at
   `ci-skills/bin/reference.py next` and says that `index.json` and
   `tools.json` are inputs of the tool, never reading material
   (CI09-REFERENCE, Loading, measured), the routing table, the option tiers,
   and "load only what matches".
   - Sections 4 to 6 (statuses, correlation, persisting evidence) move
     unchanged to `references/reading-reports.md` (planned, this phase).
   - The router keeps the literal `references/access.md` link, which
     `tests/python/test_skill_package.py` requires.
   - The router keeps the moved sections' safety rules as a short list:
     - `DRY_RUN` is never evidence.
     - `UNKNOWN` is never coerced.
     - Only `--receipt-out` output is committable.
     - Every artifact is reviewed before it is shared.
4. `references/conditional/gitlab-writes.md` (planned, this phase) is the
   one conditional reference. The skill is not read-only: `catalog.py`
   declares `mutates: True` on five commands (`gitlab_issue.py`,
   `gitlab_milestone.py`, `gitlab_runner.py`, `gitlab_wiki.py` and
   `k8s_verify_mtu_consistency.py`), which `tools.json` renders as
   `read_only: false` per command and `read_only: false` for the manifest.
   The reference covers the GitLab work no command in `tools.json` declares,
   today a merge request, a comment and a retry (CI11-TOOLS plans
   `gitlab_pipeline.py start` with `retry` and `gitlab_mr.py check`), and
   points at the `glab` skill with `points_to: glab`. That skill is vendored
   at `vendor/skills/glab/` (planned, CI05-VENDOR); from a repository
   checkout, `bin/ci-skills install glab --skills-dir DIR --apply --confirm-plan DIGEST`
   (planned, CI01-CATALOG) installs it beside this skill. By this phase,
   `install.sh`, `tools/install_ci_skills.py` and `bin/ci-skills install`
   call the same `tools/skillkit/install.py` core (planned, CI01-CATALOG).
   Its `depends_on` check refuses either installation path with
   `dependency_missing` until `glab` is beside this skill (CI01-CATALOG), so
   an installed copy never points at an absent skill.

There are no platform-specific references (OpenShift, EKS and so on).
Nothing in the code reads them, so their content would be invented.

## Steps

How `ci-skills` comes to sit on top of `glab`:

1. In `ci-skills/lib/core/catalog.py`, add the derived tags, the `load_when`
   entries and `uses` to the `REFERENCES` entries CI09-REFERENCE declares,
   and add the entries for `references/reading-reports.md` and
   `references/conditional/gitlab-writes.md`, the latter with
   `points_to: glab`. Every reference under `ci-skills/references/` is
   covered: `access.md`, `project-binding.md`, `reading-reports.md` and
   `conditional/gitlab-writes.md`. Extend each routing entry with its
   reference and tags.
2. Render `tools.json` with `tools/render_manifest.py`, and change the
   routing test (`test_the_manifest_routes_every_command_and_nothing_else`,
   which compares the `routing` values with the command set today) to read
   the command set through `.command`.
3. Move `SKILL.md` sections 4 to 6 unchanged into
   `references/reading-reports.md` and add the router sentence, keeping the
   safety list and the `references/access.md` link in the router.
4. Write `references/conditional/gitlab-writes.md`, which hands the GitLab
   work no command declares to the `glab` skill.
5. Declare the skill's tags, so that `bin/ci-skills list` (planned,
   CI01-CATALOG) shows them with the derived dependency on `glab`.
6. After the last skill edit, capture a new receipt on the D-SMOKE executor
   (`mac.lan`, `tests/acceptance/expected.toml`) with
   `ci-skills/bin/access_check.py --publication --receipt-out
   tests/acceptance/receipts/operator-laptop.json`, replacing the committed
   receipt. Commit it. It stays non-`PASS` until CI03-GATES G0 settles the
   `required_checks` read-back (D-GATE).
7. Open one pull request; merge per CI10-PHASES, How a phase lands.

## Live receipt

Pull request #21 (merged, `d6bba3d`) pointed the checker at `ci-skills`.
This phase changes the package again, so it needs a fresh receipt from the
D-SMOKE executor (CI10-PHASES, Decisions taken). The tree carries `cilium_node.py`,
`ceph_kernel.py`, `ceph_cluster.py` and `references/project-binding.md`, so
this phase derives tags for them as well; the new receipt covers this pull
request's final package bytes.

## Gates

- **Kept gates.** `tests/python/test_catalog.py` keeps `tools.json`
  byte-equal to the catalog. Its routing test is changed in step 2 to read
  `.command`.
- **Live acceptance.** `tools/check_live_acceptance.py` accepts the new
  receipt. The `validate` workflow that ran it was deleted in #27
  (`1108cca`); the route that runs it is D-GATE (CI03-GATES, G0; none as of
  2026-10-06).
- **Closed world** (CI07-SCHEMA, Pointers): `REFERENCES` paths, `points_to`,
  `uses`.
- **Tests**, as listed in CI06-TESTS and in the next section: written with
  the block; run status UNVERIFIED (CI03-GATES, G0).

## Delivery, test and proof

1. *Delivery*: `ci-skills/lib/core/catalog.py` (the derived tags,
   `load_when`, `points_to` and `uses` on the `REFERENCES` entries and on
   each command), the rendered `ci-skills/tools.json`, the router sentence in
   `ci-skills/SKILL.md`, `ci-skills/references/reading-reports.md` and
   `ci-skills/references/conditional/gitlab-writes.md`. The command that
   runs, with the `ci-skills` conda environment's Python (CI10-PHASES,
   Publish and install): `tools/render_manifest.py`, then
   `tools/render_manifest.py --check`, which reports `CURRENT`. On
   2026-10-06 that check fails before it reads the catalog, because
   `SKILL_RELATIVE` in `tools/render_manifest.py` still names
   `skills/ci-skills`; block 0 of CI10-PHASES re-points it.
2. *Tests*, written with the block; run status UNVERIFIED (CI03-GATES, G0).
   The cases are the ones CI06-TESTS lists for this phase:
   - `tests/python/test_catalog.py` (exists):
     - every `REFERENCES` path exists in the packaged skill;
     - every `points_to` names a discovered skill;
     - every `uses` id resolves to a declared operation;
     - `depends_on` equals the set of `points_to` values;
     - a dangling entry fails;
     - the routing test reads the command set through `.command` (step 2);
     - a status token matches only that exact status;
     - a phrase matches the task text as a case-insensitive substring;
     - a phrase absent from the task text matches a full keyword path in
       `index.json` `load_when`;
     - text that matches nothing loads nothing.

     The module that evaluates `load_when` is not named by any phase
     document (owner: this phase); its cases sit beside the declarations
     they read until it is named.
   - `tests/python/test_render_manifest.py` (exists): `--check` reports
     `CURRENT` for the rendered manifest (existing case).
   - `tests/python/test_skill_package.py` (exists): `SKILL.md` keeps the
     `references/access.md` link (existing case) and the four safety rules,
     and `references/reading-reports.md` holds the moved sections.
   - `tests/bash/install.bats` and `tests/python/test_installer.py` (both
     exist): `install.sh` and `bin/ci-skills install` refuse an absent or
     mismatched `glab` dependency without partial writes; each succeeds once
     the matching dependency is installed.
   - Live: the receipt of parts 4 and 5, captured on this laptop (D-SMOKE).
3. *Smoke*: this phase changes no live behaviour. The static read-back, on
   the D-SMOKE executor: `ci-skills/bin/reference.py next gitlab --json`
   lists the `commands` area this phase's tags derive, beside
   CI09-REFERENCE's `ci-yaml` (section 3, measured), and
   `wc -l ci-skills/SKILL.md` reads back fewer than 500 lines (229 on
   2026-10-06, before the split).
4. *Evidence*: no receipt for the static read-back; the static evidence is
   the `reference.py next gitlab --json` output and the `wc -l` count. This
   phase changes skill bytes, so it also needs a fresh receipt from the
   D-SMOKE executor: `tests/acceptance/receipts/operator-laptop.json`,
   captured in step 6. The checker compares its `execution_host` and
   identities with the declared executor, `skill.digest` with the tree
   digest of `ci-skills`, `status`, `kind`, `publication`, `targets`, the
   `live_checks` named in `required_live_checks`, and `captured_at` against
   `max_receipt_age_days`.
5. *Verification*: `tools/check_live_acceptance.py --root . --expected
   tests/acceptance/expected.toml --receipts tests/acceptance/receipts
   --skill ci-skills --json` reports `"status": "PASS"` and exits 0.
