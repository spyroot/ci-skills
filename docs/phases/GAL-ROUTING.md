# GAL-ROUTING: routing, lazy loading and compact representation

Status: proposed. Depends on: GAL-VENDOR and GAL-CATALOG.

## Goal

An agent reaches the one file it needs in three hops and opens a file only
when its tags match, instead of loading every skill up front.

## Why

Sizes measured on 2026-10-02:

| What an agent could load | Bytes |
| --- | --- |
| k8s `SKILL.md` | 5,534 |
| k8s `references/access.md` | 8,691 |
| k8s `tools.json` | 8,996 |
| vendored `glab` `SKILL.md` | 13,547 |
| vendored `glab-stack` `SKILL.md` | 9,208 |
| all of the above | 45,976 |
| the `routing` table in `tools.json`, as single-line JSON | 246 |
| the `storage_report.py` entry, as single-line JSON | 610 |

The last two rows use Python's default JSON separators.

## Three hops

| Hop | Answers | An agent reads |
| --- | --- | --- |
| L0 | which skill | `tools/ci_skills.py list` (GAL-CATALOG) |
| L1 | which command or reference | the skill's `SKILL.md` and its routing |
| L2 | the one thing to read or run | one reference, or `<command> --describe` |

- **L1 sources.** The routing table lives in the skill's `tools.json`, which
  `tools/render_manifest.py` renders from `scripts/core/catalog.py`.
- **Installed copies.** L0 needs a checkout, because `tools/` is not
  installed with a skill. An installed copy starts at L1: its `SKILL.md` and
  `tools.json` carry everything that L1 and L2 need.
- **Matching.** A `load_when` entry is either a status token, which matches
  a report's `status` exactly (for example `BLOCKED`), or a phrase, which
  matches the task text as a case-insensitive substring.
- **Example: a volume or claim is stuck.**
  - L0 points at `k8s-admin-diagnostics`.
  - L1 maps the symptom to `storage_report.py`.
  - L2 is that command's contract.
  - `references/access.md` opens only on `BLOCKED`, and the 13,547-byte
    `glab` skill never loads.

## Block

| Part | Value |
| --- | --- |
| Capability | route an agent to one file |
| Owner | `scripts/core/catalog.py` in the k8s skill |
| Entrypoint | `tools/render_manifest.py` |
| Result | `skill_manifest` (today's `tools.json` shape) |
| Read-back | byte-equality test on `tools.json`; a fresh live receipt |

## Changes to the k8s skill

1. `scripts/core/catalog.py` gains a `REFERENCES` declaration. For each
   reference it records the path, the tags, the `load_when` entries, and an
   optional `points_to` skill. It renders into `tools.json` with the rest of
   the catalog. `depends_on` is derived from the `points_to` values, so the
   link to `glab` is declared once.
2. Routing grows from symptom → command to symptom → command, reference and
   tags.
3. `SKILL.md` becomes the router: access first, the routing table, the
   option tiers, and "load only what matches".
   - Sections 4 to 6 (statuses, correlation, persisting evidence) move
     unchanged to `references/reading-reports.md`.
   - The router keeps the literal `references/access.md` link, which
     `tests/test_skill_package.py` requires.
   - The router keeps the moved sections' safety rules as a short list:
     - `DRY_RUN` is never evidence.
     - `UNKNOWN` is never coerced.
     - Only `--receipt-out` output is committable.
     - Every artifact is reviewed before it is shared.
4. `references/conditional/gitlab-writes.md` is the one conditional
   reference. It says this skill is read-only, and that for a merge request,
   issue, comment or retry the agent loads the `glab` skill. That skill is
   vendored at `skills/glab/`; in an installed set it is installed beside
   this one with `ci_skills.py install glab`.

There are no platform-specific references (OpenShift, EKS and so on).
Nothing in the code reads them, so their content would be invented.

## Steps

How the k8s skill comes to sit on top of `glab`:

1. Declare `REFERENCES` in `scripts/core/catalog.py`:
   - `references/access.md`
   - `references/reading-reports.md`
   - `references/conditional/gitlab-writes.md`, with `points_to: glab`

   Extend each routing entry with its reference and tags.
2. Render `tools.json` with `tools/render_manifest.py`, and change the
   routing test (`test_the_manifest_routes_every_command_and_nothing_else`)
   to read the command set through `.command`.
3. Move `SKILL.md` sections 4 to 6 unchanged into
   `references/reading-reports.md`, keeping the safety list and the
   `references/access.md` link in the router.
4. Write `references/conditional/gitlab-writes.md`, which hands GitLab write
   and how-to work to the `glab` skill.
5. Declare the skill's tags, so that `tools/ci_skills.py list` shows them
   with the derived dependency on `glab`.
6. After the last skill edit, capture a new receipt on the executor that
   `acceptance/expected.toml` declares, with
   `skills/k8s-admin-diagnostics/scripts/access_check.py --publication`
   and `--receipt-out acceptance/receipts/<label>.json`. Commit it.
7. Open one pull request. The `validate` workflow must pass, including live
   acceptance with the new receipt.

## Live receipt

`tools/check_live_acceptance.py` compares the committed receipt's skill
digest with the digest of the checked-out `skills/k8s-admin-diagnostics/`,
and the workflow step that runs it is unconditional. Every change above
alters that digest. So this phase is one pull request that ends with a new
receipt, captured after the pull request's last skill edit. Which host may
serve as release evidence is open (GAL-GATES, G5).

## Gates

- **Kept CI gates.** `tests/test_catalog.py` keeps `tools.json` byte-equal to
  the catalog. Its routing test is changed in step 2 to read `.command`.
- **Live acceptance.** The workflow accepts the new receipt.
- **Tests, run in CI.**
  - A reference with no `load_when` is rejected.
  - A `points_to` must name a skill that `list` reports.
  - `depends_on` equals the set of `points_to` values.
