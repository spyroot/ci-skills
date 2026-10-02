# GAL-CLI: one command-line contract

Status: proposed. Depends on: GAL-SCHEMA and GAL-GATES. Applies to every
command in this repository.

## Goal

Every command that an agent or a person runs here behaves the same way:
one flag name has one meaning, results have one shape, and exit codes have
one meaning. A gate checks it, so a new command cannot drift.

## Two styles today

| Part | k8s skill commands | `bin/ci-*` tools (PR #2) |
| --- | --- | --- |
| Contract | `--describe` (JSON) | none |
| Result format | `--json`, `--yaml`, `--human` | `--output json\|text` |
| Diagnostics | stderr | stderr, with `--log-*` flags |
| Plan only | `--dry-run` | `--dry-run` |
| Exit codes | 0 pass, 2 otherwise | 0, 64, 65, 66, 69 |

Both have `--help`. The `--log-*` flags are `--log-format`, `--log-level`,
`--log-file` and `--run-id`. The two exit-code tables disagree, and only the
k8s commands can describe themselves.

## The contract

Every command follows the shared standards' agent-grade checklist: it is
non-interactive, deterministic and idempotent, gives machine-readable
results with diagnostics on stderr, names the next step on failure, keeps no
hidden state, and bounds its output.

1. **Help.** `--help` lists every argument and every output mode, needs no
   dependency, and names the audience: agent, human or both.
2. **Contract.** `--describe` prints the command's contract (GAL-SCHEMA,
   `command-contract`): its purpose, options, output modes and exit codes,
   whether it mutates, and the tool operations it uses.
3. **Results.** One JSON result on stdout (GAL-SCHEMA, `command-result`):
   `kind`, `schema_version` and `status`, plus `reason` and `safe_next_step`
   on failure. A terminal gets a human summary unless `--json` is given;
   `--yaml` and `--human` select the other formats.
4. **Diagnostics.** On stderr only, controlled by the `--log-*` flags that
   the `bin/ci-*` tools already have.
5. **Exit codes.** One table for every command:

   | Code | Meaning |
   | --- | --- |
   | 0 | success, or a dry-run plan |
   | 64 | usage error |
   | 65 | invalid data |
   | 66 | missing input |
   | 69 | blocked or failed |

6. **Mutating commands.** These are `update`, `install` and the hook
   installer. They plan by default, write only with `--confirm`, and refuse
   to apply a plan whose input fingerprint changed. A second run with the
   same inputs is a no-op (pinned standards, `unit-testing` contract).
7. **Names.** New commands are named `bin/ci-<noun>`, verbs are short words
   (`list`, `get`, `install`, `update`, `verify`), and options are lowercase
   `--kebab-case`. One option name means one thing everywhere:
   - `--target`, `--dry-run`, `--confirm`;
   - `--json`, `--yaml`, `--human`;
   - `--search`, `--namespace`, `--node`, `--last`, `--from`, `--to`.

## Interface gate

The `cli` gate of `./scripts/check.sh` (GAL-GATES) runs in CI.

- **Which commands.** It covers every entrypoint that discovery finds
  (GAL-CATALOG), as a closed world: `bin/ci-*`, `bin/ci-skills`,
  `scripts/check.sh`, and each skill's `scripts/*.py`.
- **What it checks**, for each command:
  - `--help` and `--describe` exit 0 with an empty environment and no
    network;
  - `--describe` validates against `command-contract`;
  - every option the command accepts is described, and every described
    option is accepted;
  - a shared option name keeps one meaning across commands;
  - for a mutating command, the default run writes nothing.
- **Existing pattern.** `tests/test_catalog.py` already compares the k8s
  skill's declared options with each script's real parser. The gate extends
  that check to every command.

## Steps

1. Add the `command-contract` and `command-result` schemas (GAL-SCHEMA).
2. Add `--describe` to the `bin/ci-*` tools and to `bin/ci-skills`; the k8s
   commands already have it.
3. Add the contract test and the `cli` gate.
4. Bring the two styles together inside the phase that touches each
   command, not in a separate refactor:
   - result flags on the `bin/ci-*` tools;
   - the `--log-*` flags on the k8s commands;
   - the shared exit-code table everywhere.
5. Read back: the gate lists every command with its contract version.

## Open decisions

- **`PARTIAL` under the shared table.** The k8s skill's `PARTIAL` (the read
  worked, a component is unhealthy) exits 2 today, like `BLOCKED`. It needs
  either its own code, or exit 0 with `status: PARTIAL`.
- **k8s command names.** Whether the k8s commands keep their
  `scripts/*.py` names or move behind one `bin/ci-k8s` command with verbs,
  together with the `k8s-diag` rename.
