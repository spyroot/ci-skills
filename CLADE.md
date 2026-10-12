# ci-skills

The tracked `standards-binding.yaml` pins the exact shared standards revision from
`/Users/spyroot/dev/standards` and a mandatory first read, after confirming the list of
contracts and, most importantly, acknowledging that all standards are mandatory.

Claude and Codex run on this same machine and read the same files.
Never copy anything into this  file — two copies drift and have to be tracked, one pointer never
does. If it is not a pointer, it does not belong here.

Your lane: `/Users/spyroot/dev/ci-skills/.internal/queue/claude/{ready,active,done}`
Use pr-coordinator skill to create queues use `.coordination/pr-coordinator-policy.md`
All agents in `~/.codex/agents` and skills `~/.codex/skills`

Delegated implementation and disposable-sandbox lifecycle are governed by
`/Users/spyroot/dev/ci-skills/.internal/queue/README.md`; the current phase boundary is governed by
`/Users/spyroot/dev/ci-skills/TEAM_GUIDE.md`.

[https://github.com/spyroot/ci-skills](https://github.com/spyroot/ci-skills)

## Local Codex Pointer

Before acting in this repository:

1. Read `/Users/spyroot/dev/ci-skills/standards-binding.yaml`.
2. Resolve and read the pinned shared standards manifest.
3. Read `/Users/spyroot/dev/ci-skills/.internal/MAP.md`.
4. Read `/Users/spyroot/dev/ci-skills/.internal/INDEX.yaml`,
5. `/Users/spyroot/dev/ci-skills/.internal/POLICY.md`,
`/Users/spyroot/dev/ci-skills/.internal/STATE.md`,
`/Users/spyroot/dev/ci-skills/.internal/queue/p0.yaml`, and
`/Users/spyroot/dev/ci-skills/.internal/queue/README.md`.
6. Use only the Claude or shared queue lane assigned by the active task.

@TEAM_GUIDE.md

Authority
/Users/spyroot/dev/standards/bin/agent-tools.sh
/Users/spyroot/dev/standards/bin/agent-tools.sh

Read TEAM_GUIDE.md first: it holds the binding project rules that `standards-binding.yaml` names.
`AGENTS.md` holds layout, style and commit conventions, NON-NEGOTIABLE mandatory rules and is shared
with Codex. Both are local and gitignored, so a fresh clone lacks them. `README.md` is the user-facing
protocol, and `docs/phases/CI-*.md` are the phase plans (PR #11 until it merges). This file adds what
spans several files.

@AGENTS.md

## Commands

Python 3.11+: CI pins 3.11 in `.github/workflows/validate.yml`, and
both `tools/check_live_acceptance.py` and
`core/target.py` import `tomllib`. Here `python3` on PATH is 3.10 and
the repo has no `environment.yml` Run
Python from a 3.11+ conda environment by full path, creating it rather
than blocking (`TEAM_GUIDE.md`:
conda and full tool paths). Dependencies are in `requirements.txt`;
there is no build step. The workflow owns the step
list and its CI run is authoritative. These are the local equivalents
of the static checks:


Never edit operator WIP without DIRECT approval.
Never edit AGENTS.md CLAUDE.md or CODEX_HANDOFF.md without receiving DIRECT approval.

```bash
python tools/check_project_neutrality.py --root . --json   #
forbidden-marker scan (see below)
python tools/check_live_acceptance.py --root . --json      # receipt
vs expected.toml and the skill digest
python tools/render_manifest.py --check                    #
tools.json vs catalog.py (CI: via pytest)
ruff check . && ruff format --check .
```

Tests run in CI, not on the laptop. The pinned Standards
`agent-workspace.md` says "Never run test on laptop, use CI".
CI runs these two commands:

- `pytest -q tests/test_installed_package.py`: the installed-copy
smoke test, run from outside the source tree.
- `pytest -q --ignore=tests/test_installed_package.py`: the rest of the suite.

To select a single test, use `<file>::<test_function>`.

CI also runs actionlint, yamllint, markdownlint-cli2, gitleaks and
`git diff --check`; the scope and pinned versions are
in `validate.yml`. On a Markdown-only change
(`tools/classify_changed_paths.py`), CI skips the dependency install,
Ruff
and pytest. Neutrality and live acceptance always run, and live
acceptance is never allowed to fail.

## Architecture

The layout is described in `AGENTS.md` and `TEAM_GUIDE.md`.

- Each `skills/k8s-admin-diagnostics/scripts/*.py` is a
`build_parser()` plus one `core.cli.execute` call. Most pass a
  collector; `access_check.py` passes `live_checks=True`.
- `scripts/core/` is imported as the top-level package `core`. Tests
put `scripts/` on `sys.path`
  (`tests/python/conftest.py`).

Invocation flow, all in `core/cli.py:execute`:

1. `--describe` → print the catalog contract and stop (needs no credentials).
2. Required options checked before any access attempt, so a missing
flag is not reported as an access failure.
3. Target resolution (`resolve_target`): the first match in
`catalog.TARGET_PROTOCOL` order wins, and
   `tests/python/test_catalog.py` locks that order. `core/target.py`
validates the result (nonsecret TOML, shape in
   `target.toml.template`). The skill never creates a target file.
4. `core/credentials.py:bind_sources` pins the effective credential
source per authority, the kubeconfig digests, and
   the skill identity from `core/provenance.py`. The identity is a
digest of the bytes that ran plus the git HEAD as
   `revision`. The revision is marked `verified: true` only when the
skill subtree is clean.
5. `core/access.py:check_access` is the base gate over the three
authorities. What each check proves, `--publication`
   included, is in `references/access.md`.
6. On `PASS` the collector runs (`core/collect.py`). `access_check.py`
instead runs the expanded bundle
   (`core/live.py:collect_live_checks`), which records `access_proven`
per check:
   - `storage_report`
   - `event_trace`
   - `cilium_status`
   - `gitlab_job`, only with `--job-url`
7. `core/cli.py:output_mode` picks human output for a TTY and
versioned JSON otherwise (`--json`/`--yaml`/`--human`
   override). `core/report.py:emit` redacts and renders it.
`--receipt-out` writes the committable form via
   `core/portable.py`, which swaps host paths for digests.

Status and exit codes:

- **Tokens and codes.** Defined in `core/status.py`; their meanings
are `catalog.STATUS_MEANING` and
  `catalog.EXIT_CODES`. Exit 2 also covers an input that could not be used.
- **`PARTIAL` is not a gate failure.** A collector that read
successfully but found an unhealthy component is `PARTIAL`
  with `access_proven: true` (`core/live.py:_access_proven`). The
skill has to work on the degraded cluster it is
  diagnosing.
- **Redaction.** Every `gh`/`glab`/`kubectl` call goes through
`core/runtime.py`, which bounds it in time. Its output is
  raw; redaction happens on emission (`report.emit`, `--receipt-out`).

### Single sources of truth

- **`core/catalog.py`** declares every command's purpose and options
as capability tiers (`UNIVERSAL_OPTIONS` and
  `CAPABILITY_OPTIONS`: `--search`, `--namespace`, `--node`, time range).
  - `tools/render_manifest.py` generates `tools.json` from it; never
hand-edit `tools.json`.
  - `tests/python/test_catalog.py` compares the catalog with each
script's real `build_parser()`, so a new option goes
    into both.
- **`core/cilium.py`** is the only definition of "Cilium agent pod"
and "valid health response", shared by the access
  gate and the collector.
- **`tests/acceptance`** declares what each receipt must prove.
  - `tests/acceptance` needs one receipt per declared executor,
matched by `execution_host` and younger than
    `max_receipt_age_days`.
  - A receipt comes only from `access_check.py --publication
--receipt-out ...` on the executor's declared host, never
    from a mock or a hand edit.

## Constraints

These are CI-enforced unless a bullet says otherwise.

- **Skill digest.** The committed receipt is bound to `tree_digest` of
`skills/k8s-admin-diagnostics/`.
  - Any byte change there (`SKILL.md`, `references/` and `tools.json`
included) makes `tools/check_live_acceptance.py`
    report `skill_digest_mismatch`. `validate` then fails until a
fresh receipt from the declared executor is committed.
    Finish every skill edit, then capture.
  - A receipt older than `max_receipt_age_days` fails every PR,
Markdown-only ones included.
  - The checker compares fields and cannot detect a hand edit; only
this rule stops one.
- **Project neutrality.** `tools/check_project_neutrality.py` fails if
any tracked or untracked, non-ignored path or
  file content contains a specific prohibited project name (see the
`marker` in that file). Do not paste config from
  other projects into this repo.
- **No secrets or host paths in commits.** Keep host paths, target
files, credentials and raw traces out of commits. No
  gate catches a host path, so check by hand. Tests use
`*.example.test` targets and mock `gh`/`glab`/`kubectl`
  (`tests/python/conftest.py`).
- **Installed copies.** `tests/python/test_installed_package.py` runs
the installed copy from outside the source tree,
  so skill code must not rely on repo-relative paths. The installer
refuses an existing destination and an unverified
  (dirty) skill subtree; details are in `README.md`.
- **Do not run `make` here (local, not gated).** This checkout has an
untracked `Makefile` whose default goal runs
  `install-hooks`. That sets a repo-local `core.hooksPath .githooks`,
a directory that does not exist, so no hook runs,
  including the global commit-message check. Drop this bullet once
that file is gone.
