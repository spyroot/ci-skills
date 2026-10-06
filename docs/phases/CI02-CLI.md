# CI02-CLI: one command-line contract

Status: proposed. Order and dependencies: CI10-PHASES, Phases. Applies to
every command in this repository.

## Goal

Every command that an agent or a person runs here behaves the same way:
one flag name has one meaning, results have one shape, and exit codes have
one meaning. A gate checks it, so a new command cannot drift.

## Two styles today

| Part | `ci-skills/bin/*.py` mains | `ci-skills/bin/ci-*` tools (shipped in #16) |
| --- | --- | --- |
| Contract | `--describe` (JSON) | none |
| Result format | `--json`, `--yaml`, `--human` | `--output json\|text` (`ci-api` only) |
| Diagnostics | stderr; `--log-*` on the 13 catalog mains only | stderr, with `--log-*` flags |
| Plan only | `--dry-run` | `--dry-run`, the default of `ci-binary-build` |
| Exit codes | 0 and 2 (`core/status.py:11-13`) | 0, 64, 65, 66, 69 (`lib/bash/core/runtime.bash:6-9`) |

Both have `--help`. The `--log-*` flags are `--log-format`, `--log-level`,
`--log-file` and `--run-id`; the catalog declares them universal
(`core/catalog.py:40-43`) and `core/cli.py:218-239` adds them to the 13
mains in `COMMANDS`, while the two node-local mains lack them
(`core/catalog.py:444-454`). `ci-binary-build` has no `--output` and returns
its JSON plan (`core/catalog.py:521-534`). Three exit-code tables disagree:
`catalog.EXIT_CODES` (`core/catalog.py:550-553`, 0 and 2, restating
`core/status.py:11-13`), `ci-skills/lib/bash/core/runtime.bash:6-9`
(`CI_EXIT_BLOCKED=69`) and `scripts/bash/core/exit_codes.bash:9-23`
(`CI_EXIT_BLOCKED=2`). Only the Python mains can describe themselves, and
none of the 17 entrypoints answers `--help` on its own today: a main with an
empty environment fails on `import core` (observed 2026-10-06 for
`gitlab_job.py:6`, exit 1; all 15 mains import `core` at the top level) until
block 0's locator lands (CI10-PHASES, Order 0), and `ci-api` and
`ci-binary-build` source
`lib/ci/...` and `lib/automation/...` under `ci-skills/` (`ci-api:6`,
`ci-binary-build:6`), paths that block 0 re-points to `lib/bash/`.

### The Python mains, read from `core/catalog.py`

`ci-skills/bin/` holds 15 Python mains and the two Bash tools (listed
2026-10-06); `tools.json` lists all 17. The 13 mains in `catalog.COMMANDS`
(`core/catalog.py:196-441`) share the 13 universal options
(`core/catalog.py:30-44`): `--target`, `--binding`, `--json`, `--yaml`,
`--human`, `--dry-run`, `--revision`, `--output-dir`, `--describe`,
`--log-format`, `--log-level`, `--log-file` and `--run-id`. The two
node-local mains, `cilium_node.py` and `ceph_kernel.py`
(`core/catalog.py:456-476`), take the nine `NODE_LOCAL_OPTIONS` instead
(`core/catalog.py:444-454`): `--search` without a tier, no `--output-dir`
and no `--log-*`.

Tiers, by their catalog keys (`core/catalog.py:47-62`): `filters_records`
(`--search`), `namespaced` (`--namespace`), `node_scoped` (`--node`) and
`time_ranged` (`--last`, `--from`, `--to`). A cell that would not fit a row
gives the option count and the catalog lines instead of the names.

| Command | Tiers | Subcommands | Own options |
| --- | --- | --- | --- |
| `access_check.py` | none | none | `--publication`, `--job-url`, `--ceph-namespace`, `--receipt-out` |
| `gitlab_job.py` | `filters_records` | none | `--job-url` (required) |
| `gitlab_pipeline.py` | none | none | `--project`, `--pipeline-id` (required) |
| `gitlab_access.py` | none | `check` | `--project`, `--group`, `--receipt-out` |
| `gitlab_milestone.py` | none | `create`, `update`, `adjust-time` | 12, `core/catalog.py:264-275` |
| `gitlab_issue.py` | none | `open-bug`, `create-bug` | 9, `core/catalog.py:292-300` |
| `gitlab_wiki.py` | none | `create`, `update` | 8, `core/catalog.py:316-323` |
| `gitlab_runner.py` | none | `assign`, `create` | 12, `core/catalog.py:339-350` |
| `storage_report.py` | `filters_records`, `namespaced`, `node_scoped` | none | `--storage-class`, `--phase` |
| `event_trace.py` | `filters_records`, `namespaced`, `time_ranged` | none | `--kind`, `--object`, `--reason` |
| `cilium_status.py` | `filters_records`, `namespaced`, `node_scoped` | none | none |
| `ceph_cluster.py` | `namespaced`, `node_scoped` | none | `--operator`, `--conf`, `--ready`, `--condition` |
| `k8s_verify_mtu_consistency.py` | `node_scoped` | none | 8, `core/catalog.py:426-433` |
| `cilium_node.py` | node-local | none | none |
| `ceph_kernel.py` | node-local | none | `--classification` |

The four mutating GitLab mains share `--apply`, `--confirm-plan`,
`--timeout` and `--receipt-out` among their own options
(`core/catalog.py:272-275`, `297-300`, `320-323`, `347-350`);
`k8s_verify_mtu_consistency.py` restates `--dry-run`, `--log-format`,
`--log-level`, `--log-file` and `--run-id` (`core/catalog.py:426-433`).
`ceph_cluster.py` requires `--namespace` (`core/catalog.py:410`).
`tools.json` renders `mutates` as `read_only: false` (`core/catalog.py:671`);
five commands carry it (`tools.json:440`, `:531`, `:625`, `:677`, `:727`;
`core/catalog.py:262`, `:290`, `:314`, `:337`, `:424`).

Reading the parsers shows two defects:

- **One option name, three meanings.** `--namespace` filters records in
  `storage_report.py` and `event_trace.py` (default `all`;
  `ci-skills/bin/storage_report.py:18-21`, `event_trace.py:27-30`), names
  where Cilium is installed in `cilium_status.py` (default: discover;
  `cilium_status.py:18-21`, `core/collect.py:623`), and is the required Ceph
  namespace in `ceph_cluster.py` (`ceph_cluster.py:13`,
  `core/catalog.py:410`), the value `access_check.py` calls
  `--ceph-namespace` (`core/catalog.py:209`). The catalog covers all of them
  with one description, which itself lists three values: a name, `all` and
  `auto` (`core/catalog.py:52`). Whether `ceph_cluster.py` follows the Cilium
  rename below is not decided here; this phase's pull request decides it
  under the one-meaning rule.
- **Help text declared twice.** Each option is described in
  `core/catalog.py` (`:30-44` for the universal tier), which `--describe`
  prints, and again in the parser's `help=` (`core/cli.py:181-243`,
  `core/node_local_cli.py:79-108`, and each main for its own options). The
  two have already drifted: `--publication` reads "also require repository
  administration and the declared required checks" in the catalog
  (`core/catalog.py:207`) and "also require GitHub repository
  administration before configuring checks" in the parser
  (`ci-skills/bin/access_check.py:21`). `tests/python/test_catalog.py`
  compares option names, not their text (`test_catalog.py:59-75`).

## The contract

Every command follows the pinned agent-grade checklist, as:

1. **Help.** `--help` lists every argument and every output mode, needs no
   dependency, and names the audience: agent, human or both.
2. **Contract.** `--describe` prints the command's contract (CI07-SCHEMA,
   `command-contract`, planned; today's record carries
   `kind: command_contract`, `core/catalog.py:592`): its purpose, options,
   output modes and exit codes, whether it mutates, and the tool operations
   it uses (`uses`, planned, CI07-SCHEMA).
3. **Results.** One JSON result on stdout; fields per
   `schemas/command-result.schema.json` (CI07-SCHEMA, planned); diagnostics
   on stderr. A terminal gets a human summary unless `--json` is given;
   `--yaml` and `--human` select the other formats.
4. **Diagnostics.** On stderr only, controlled by the `--log-*` flags that
   the `ci-*` tools and the 13 catalog mains already have
   (`core/cli.py:218-239`).
5. **Exit codes.** Proposed one table (D-EXIT, open; CI10-PHASES, Open
   decisions). Today's owner is `core/status.py` (`exit_code`, lines 11-13:
   0 for `PASS`, `DRY_RUN` and `PLANNED`, 2 otherwise); `catalog.EXIT_CODES`
   (`core/catalog.py:550-553`) restates it for `--describe` and
   `tools.json`. CI10-PHASES, Refactor, renders the one table to
   `lib/bash/core/exit_codes.bash` (planned) and removes the two Bash
   tables.

   | Code | Meaning |
   | --- | --- |
   | 0 | success, or a dry-run plan |
   | 64 | usage error |
   | 65 | invalid data |
   | 66 | missing input |
   | 69 | blocked or failed |

6. **Mutating commands.** Every mutating command plans by default and
   writes only with a confirmation that carries the digest of the plan its
   default run printed, so a plan whose input fingerprint changed is refused
   and a second run with the same inputs is a no-op (pinned `unit-testing`
   contract: "apply requires valid plan", "same-commit or same-input
   fingerprint"). A bare `--confirm` without that digest does not meet it.
   - Skill actions write only with `--apply --confirm-plan DIGEST`
     (`core/gitlab_actions.py:403-406`; `core/mtu_consistency.py:569`).
     Five commands declare `mutates: True` today (`core/catalog.py:262`,
     `:290`, `:314`, `:337`, `:424`): `gitlab_milestone.py`,
     `gitlab_issue.py`, `gitlab_wiki.py`, `gitlab_runner.py` and
     `k8s_verify_mtu_consistency.py`.
   - The installer binds the same way today: `--apply` with
     `--confirm-install FINGERPRINT` or `--confirm-upgrade FINGERPRINT`
     (`tools/install_ci_skills.py:486-492`).
   - The maintenance verbs of `bin/ci-skills` (planned, CI05-VENDOR),
     `install` (CI01-CATALOG) and `update` (CI05-VENDOR), the hook
     installer (CI04-HOOKS) and `tools/update_reference.py`
     (CI09-REFERENCE) take the same binding. The phase documents write it
     `--apply --confirm-plan DIGEST`, the form the mutating commands in the
     tree already use; its spelling is open decision D-CONFIRM (below).

   The `cli` gate checks every mutating command for the plan-bound form.
7. **Names.** Python mains are `ci-skills/bin/<domain>_<noun>.py`
   (`gitlab_job.py`, `ceph_cluster.py`); the Bash tools keep `ci-<noun>`
   until ported (CI10-PHASES, Refactor). Verbs are short words (`list`,
   `get`, `install`, `update`, `verify`), and options are lowercase
   `--kebab-case`. One option name means one thing everywhere:
   - `--target`, `--dry-run`;
   - `--apply` with `--confirm-plan DIGEST` on every mutating command
     (item 6, D-CONFIRM);
   - `--receipt-out PATH` on every command that has a `[[smoke_cases]]`
     entry (CI06-TESTS, Live smoke); it writes the sanitized receipt
     (`core/portable.py`). `access_check.py`, `gitlab_access.py` and the
     four mutating GitLab mains have it today (`core/catalog.py:210`,
     `:249`, `:275`, `:300`, `:323`, `:350`);
   - `--json`, `--yaml`, `--human`;
   - `--search`, `--namespace`, `--node`, `--last`, `--from`, `--to`.

## Interface gate

The `cli` gate of `./scripts/check.sh` (CI03-GATES, G1) runs on the gate
route (CI03-GATES, G0; none as of 2026-10-06, D-GATE).

- **Which commands.** Every entrypoint `core/catalog.py` declares, plus
  `scripts/check.sh` and `bin/ci-skills` (planned, CI05-VENDOR); never a
  glob. `bin/reference.py` (planned, CI09-REFERENCE) enters through the
  catalog's `OFFLINE_COMMANDS` (CI10-PHASES, Modify). `scripts/check.sh`
  has no `--describe` and no contract schema today, and CI03-GATES is silent
  on one; that gap is CI03-GATES G1's.
- **What it checks**, for each command:
  - `--help` and `--describe` exit 0 with an empty environment and no
    network;
  - `--describe` validates against `command-contract`;
  - every option the command accepts is described, and every described
    option is accepted, by parser introspection, not help-text scraping
    (CI10-PHASES, "Gates that make every tool conform");
  - a shared option name keeps one meaning across commands, so the Cilium
    namespace moves to `--cilium-namespace`;
  - each option's help text comes from one declaration, the catalog, which
    the parser reads; the gate fails when the two differ;
  - for a mutating command, the default run writes nothing (a temporary
    working directory and a fake `PATH`);
  - `--json` and `--yaml` outputs validate against the kind's
    `command-result` schema on a mocked run;
  - exit codes are a subset of the one table.
- **Existing pattern.** `tests/python/test_catalog.py` already compares the
  catalog's `COMMANDS` with each main's real parser (`test_catalog.py:59-75`)
  and checks the two node-local mains separately (`:152`); the Bash tools
  are declared as option lists only (`core/catalog.py:478-539`), with no
  parser to compare. The gate extends that check to every command.

## Steps

1. Add the `command-contract` and `command-result` schemas (CI07-SCHEMA).
2. The `ci-*` tools become Python mains (CI10-PHASES, Refactor): `ci-api`
   over `core/gitlab_api.py` plus a GitHub GET owner extracted from
   `core/access.py`, and `ci-binary-build` over `core/binary_build.py`
   (planned); `--describe` arrives with the port. CI05-VENDOR adds it to
   `bin/ci-skills` when that command is created. The Python mains already
   have it (`core/cli.py:203-207`, `core/node_local_cli.py:108`,
   `core/mtu_consistency.py:557-566`).
3. Add the contract test and the `cli` gate.
4. Bring the two styles together inside the phase that touches each
   command, not in a separate refactor:
   - result flags on the `ci-*` tools, with the port;
   - the `--log-*` flags on the two node-local mains (the 13 catalog mains
     have them, `core/cli.py:218-239`);
   - the one exit-code table everywhere (D-EXIT).
5. Read back: the gate lists every command with its contract version.

## Delivery, test and proof

1. *Delivery.* Created: `schemas/command-contract.schema.json` and
   `schemas/command-result.schema.json` (planned, CI07-SCHEMA; `schemas/`
   exists and is empty). Changed: `ci-skills/lib/core/catalog.py` (one help
   text per option; `cilium_status.py` leaves `namespaced` and gains
   `--cilium-namespace`), `ci-skills/lib/core/cli.py` and
   `ci-skills/lib/core/node_local_cli.py` (the parsers read the catalog's
   text), `ci-skills/bin/cilium_status.py` and
   `ci-skills/lib/core/collect.py:623` (the rename), `ci-skills/tools.json`
   (regenerated), `tests/python/test_cli_contract.py`,
   `tests/python/test_catalog.py`, `tests/bash/check.bats`, and
   `scripts/check.sh` with its library for the `cli` gate. The command that
   runs: `./scripts/check.sh` with the `cli` gate selected, which runs
   `pytest -q tests/python/test_cli_contract.py`. Two gaps, both
   CI03-GATES G1's: the library `scripts/check.sh:6` sources,
   `lib/ci/check.bash`, left the tree in `88bd9f6` (#26), so
   `check.sh --help` exits 1 today; and the argument that selects one gate
   is not defined.
2. *Tests*, written with the block; run status UNVERIFIED (CI03-GATES, G0).
   The cases of CI06-TESTS, "CI02-CLI", each with its file:
   - `tests/python/test_cli_contract.py` (exists):
     - `--help` and `--describe` exit 0 with an empty environment and no
       network, for every entrypoint the catalog declares (today
       `test_help_lists_common_and_script_specific_flags` covers five mains
       with a populated environment, `:31-63`, and `test_catalog.py:103-119`
       runs `--describe` with `PATH=/nonexistent`);
     - `--describe` validates against `command-contract`, with the
       `jsonschema` library that `check-jsonschema` installs (CI07-SCHEMA,
       Validation; pinned in `requirements.txt` by CI03-GATES, G2);
     - a shared option name keeps one meaning: every parser's help text
       equals the catalog's for every option, and `--namespace` no longer
       appears on `cilium_status.py`;
     - the default run of a mutating command writes nothing: each of the
       five `mutates: True` commands, run without `--apply` in a temporary
       working directory with a fake `PATH`, leaves `call_journal` empty
       (`conftest.py:64-75`, `:135-139`);
     - exit codes come only from the shared table: every exit the file's
       cases observe is a key of `catalog.EXIT_CODES` (today the cases
       assert 0 and 2: `:99`, `:120`, `:147`, `:169`, `:237`);
     - `--json` and `--yaml` outputs validate against the kind's
       `command-result` schema on a mocked run.
   - `tests/python/test_catalog.py` (exists): options accepted equal options
     described, `test_declared_options_are_the_options_the_command_accepts`
     (`:69-75`), extended from `COMMANDS` to the node-local mains and, after
     the port, the former Bash tools;
     `test_mutating_commands_are_identified_in_the_manifest` (`:221-236`)
     covers the maintenance verbs' plan-bound form (item 6) when
     `bin/ci-skills` lands.
   - `tests/python/test_schema.py` (CI07-SCHEMA, Delivery, test and proof):
     for each of the two schemas, one valid record, one record per missing
     required field and one per broken conditional rule.
   - `tests/bash/check.bats` (exists): `--dry-run` lists the `cli` gate, and
     a failing command fails the run and names the gate (CI06-TESTS,
     "CI03-GATES").

   `tests/python/conftest.py:18` still points at `skills/ci-skills/scripts`;
   block 0 re-points it (CI10-PHASES, Order 0). Until then no case above can
   find a main.
3. *Smoke.* This phase changes no live behaviour, so it declares no
   `[[smoke_cases]]` entry. The read-back is static, on this laptop, for
   every entrypoint the catalog declares:
   - `env -i <python> ci-skills/bin/<main> --help` exits 0, `<python>` being
     the `ci-skills` conda environment's interpreter (CI10-PHASES, "Publish
     and install"). Observed 2026-10-06 for `gitlab_job.py`: exit 1,
     `ModuleNotFoundError: No module named 'core'` (`gitlab_job.py:6`); with
     `PYTHONPATH=ci-skills/lib` it prints its usage and exits 0. Block 0's
     locator removes the variable (CI10-PHASES, Order 0).
   - `<python> ci-skills/bin/<main> --describe > <out>`, then
     `check-jsonschema --schemafile schemas/command-contract.schema.json <out>`
     reports no error (`check-jsonschema [OPTIONS] INSTANCEFILES...`, from
     the `--help` of version 0.38.0; the tool CI07-SCHEMA names, declared in
     the toolchain contract and `environment.yml:16`).
   - `ci-skills/bin/ci-api --help` and `ci-skills/bin/ci-binary-build --help`
     exit 0. Observed 2026-10-06: both exit 1, sourcing `lib/ci/api.bash`
     and `lib/automation/binary_build.bash` under `ci-skills/` (`ci-api:6`,
     `ci-binary-build:6`), which live under `ci-skills/lib/bash/`; block 0
     re-points them.
4. *Evidence.* No receipt: the static evidence is the `--help` and
   `--describe` output of part 3 and the `CURRENT` line of
   `tools/render_manifest.py --check` (`render_manifest.py:69-71`). Every
   byte this phase changes under `ci-skills/` changes the skill digest, so
   the committed receipts, already stale (CI10-PHASES, "Pull request
   status"), need a fresh `access_check.py --publication --receipt-out`
   from `mac.lan` after the phase's last skill byte; that receipt is
   CI03-GATES G5's evidence, not this phase's. That refresh cannot pass
   today: `--publication` reads `main`'s branch protection and its
   `required_status_checks` (`core/access.py:227`, `:263`;
   `required_checks_missing` at `:277`), protection on `main` is disabled
   (CI10-PHASES, "Pull request status"), and `expected.toml:26` declares
   `required_checks = ["validate"]`, the workflow deleted in #27
   (`1108cca`), which `tools/check_live_acceptance.py:311-313` compares with
   the receipt. Whether the expectation changes or the receipt is captured
   without `--publication` is CI03-GATES G0's decision (D-GATE); until it
   is made, a fresh publication receipt is not `PASS`.
5. *Verification.*
   - `tools/render_manifest.py --check` prints `CURRENT` and exits 0
     (`render_manifest.py:69-72`). Observed 2026-10-06: `FileNotFoundError:
     no catalog module under <root>/skills/ci-skills/scripts`
     (`render_manifest.py:27`, `:32-34`; block 0).
   - `./scripts/check.sh` with the `cli` gate: one `PASS` per command. The
     line's format is not defined in the tree; CI03-GATES G1 owns it, and
     the route that runs it is D-GATE.
   - `tools/check_live_acceptance.py --root . --expected tests/acceptance/expected.toml
     --receipts tests/acceptance/receipts --skill ci-skills --json` prints
     `"status": "PASS"` and exits 0 (`check_live_acceptance.py:617`, `:684`)
     once the receipt of part 4 is committed. Observed 2026-10-06:
     `ModuleNotFoundError: No module named 'core'`
     (`check_live_acceptance.py:479`; block 0), and with its defaults
     `expectations_unreadable`, because the default paths (`:630-644`)
     predate the layout.

## Open decisions

- **D-EXIT.** The five-code table in the contract against today's two codes
  (CI10-PHASES, "Machine-readable output, one shape").
- **D-CONFIRM.** The spelling of the plan-bound confirmation on the
  maintenance verbs (item 6). Recommended: `--apply --confirm-plan DIGEST`,
  the form the skill actions use and the installer uses as
  `--confirm-install`, so one option name means one thing (item 7).
  Alternative: `--confirm DIGEST`. Either way the digest is that of the
  printed plan.
- **The `PARTIAL` exit** (CI10-PHASES, Open decisions, D-EXIT). `PARTIAL`
  (the read worked, a component is unhealthy) exits 2 today, like `BLOCKED`
  (`core/status.py:13`). It needs either its own code, or exit 0 with
  `status: PARTIAL`.
- **`bin/ci-k8s`** (CI10-PHASES, Open decisions, D-EXIT). Proposed: one
  `bin/ci-k8s` command with short verbs, after the `ci-skills` package
  delivery (#21, merged, `d6bba3d`). Options keep their names and meanings.
  `k8s_verify_mtu_consistency.py` has no proposed verb yet.

  | Today | Proposed |
  | --- | --- |
  | `access_check.py` | `ci-k8s access` |
  | `gitlab_job.py` | `ci-k8s job` |
  | `storage_report.py` | `ci-k8s storage` |
  | `event_trace.py` | `ci-k8s events` |
  | `cilium_status.py` | `ci-k8s cilium` |
  | `cilium_node.py` | `ci-k8s cilium-node` |
  | `ceph_kernel.py` | `ci-k8s ceph-kernel` |
  | `ceph_cluster.py` | `ci-k8s ceph` |
