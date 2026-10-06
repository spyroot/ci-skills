# CI04-HOOKS: local source blessing

Status: implementation PR, local development capability. This phase can land
before a merge gate exists. Its result advises a contributor about the staged
commit; it is not a GitHub or release check.

## Goal and boundary

Install one `pre-commit` hook that checks source bytes in the Git index. The
operator selected `bless.sh` as this repository's pre-hook and rejected the
old `scripts/check.sh` route. The hook calls `bless.sh --staged`; it never runs
tests, touches a cluster, contacts GitLab, or rewrites source. There is no
pre-push hook in this delivery.

The distributable skill under `ci-skills/` remains separate from these
repository maintenance tools. `scripts/dev.sh` is a development installer;
it is not an agent-facing skill command.

## Interface and layout

| Interface | Defined by | Observable behavior |
| --- | --- | --- |
| `make bless` | `Makefile`, `bless.sh` | Check exact staged bytes and refuse a failed check. |
| `make bless-all` | `Makefile`, `bless.sh` | Check tracked working-tree files. |
| `make pretty` | `Makefile` | Format repository Markdown, Python, and Bash; leave staging to the contributor. |
| `make install` | `Makefile`, `scripts/dev.sh` | Install missing tools, Conda environment, and hook. |
| `make build docker` | `Makefile`, `Dockerfile` | Build the Ubuntu image and run its hook smoke. |

The reusable checker, installer, hook, and source graph functions live in
`scripts/bash/core/`. Their executable wrappers remain thin. The argument,
help, result, and toolchain schemas live under the corresponding root
`schemas/` subdirectories. `toolchain-dependencies.json` selects the host and
Conda tools used by this hook; `environment.yml` declares the project Conda
environment. The existing C/CMake dependencies remain in that manifest.

`bless.sh --staged` reads selected files with `git show :PATH`, then runs the
applicable Ruff, JSON/schema, YAML, TOML, Markdown, ShellCheck, or shfmt check.
It also checks staged whitespace, declared Bash source dependencies and
cycles, secret-class paths, and staged secrets. `--dry-run` lists planned
checks; `--json`, `--yaml`, `--help`, and `--describe` expose the paired result
and interface contracts. A missing tool or failed check refuses the commit
with a safe next step.

## Installation and recovery

Direct `scripts/dev.sh install` plans without mutating and prints a
`PLAN_FINGERPRINT` bound to the observed plan and installer inputs. Apply
requires `--apply --confirm-install PLAN_FINGERPRINT`; `make install` plans
first and passes that fingerprint. Changed inputs require a fresh plan.
The installer reads the Git common directory, preserves any foreign hook,
and refuses an incompatible configured hook dispatcher. It never sets the
repository's `core.hooksPath` Git configuration key, which would override a
contributor's global hook route. An identical second install is a no-op. The
installed hook refuses when `bless.sh` is unavailable in a checkout.

To recover from a failed check, repair and stage the named file, then run
`make bless` again. A failed install leaves an existing foreign hook in place;
inspect the reported path before changing it.

## Delivery, test and proof

1. **Delivery:** `bless.sh`, `scripts/dev.sh`, their Bash libraries, `Makefile`,
   root schemas, and the Ubuntu `Dockerfile` in one pull request.
2. **Tests:** `tests/bash/bless.bats` checks plan and refusal modes, secret
   paths, missing/mismatched Bash sources, cycles, foreign-hook preservation,
   missing checker refusal, a real Git commit from staged valid bytes, and a
   real commit refusal for staged invalid bytes.
3. **Smoke:** `make build docker` runs those Bats cases inside the Ubuntu
   image. The commit fixture changes its working-tree JSON after staging;
   success proves the hook read the staged version. It then stages invalid
   JSON and verifies that Git refuses the commit.
4. **Evidence:** Record the exact PR head, `make bless` result, Docker image
   digest, Bats count, and the two Git commit outcomes in the pull request.
   These are local static evidence, not live service receipts.
5. **Verification:** Review the diff and the read-only QA report. After merge,
   run `make install` in the canonical checkout and read back the installed
   hook and unset local `core.hooksPath`; a later staged commit exercises it.

The pull request remains draft until its Standards binding parent, review,
and any required gate policy are reconciled. No local `PASS` substitutes for a
required merge check if one is established later.
