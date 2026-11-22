# Live smoke fixture lifecycle

Date: 2026-11-22. Operator direction: live smoke creates its own disposable fixtures;
a deterministic tool owns that lifecycle, phase-scoped names give every phase a pool
of repositories to work with, and renaming is always preferred over deleting where
deletion is not required.

`tools/live-smoke.py` is that tool. It creates, renames and removes only run-owned
fixtures on GitHub and the GitLab named by the selected target, records a JSON
ledger per run, and refuses to touch anything whose identity changed since the run
created it. The operator-specified verbs live in `tools/skillkit/live_smoke.py`:
`create_fine_grained`, `attach_fine_grained_to_repo`, `detach_fine_grained_to_repo`,
`create_test_gitlab_repo` and `create_test_github_repo`.

## Naming

Both systems share one deterministic name per run:

```text
smoke-<phase>-<case>-<run_id>
```

Example: `smoke-ci14-mirror-r20261122a` is the GitHub repository
`spyroot/smoke-ci14-mirror-r20261122a` and the GitLab project
`ci-skills-smoke/smoke-ci14-mirror-r20261122a`. Segments are lowercase letters,
digits and dashes; underscores fold to dashes. The GitLab fixture additionally
lives under the `ci-skills-smoke` group, which is created when absent and is
never deleted by the tool.

## Commands

```bash
# No credentials needed; prints the names and mutates nothing.
tools/live-smoke.py plan --phase ci14 --case mirror

# Creates both fixtures; aborts when either name already exists.
tools/live-smoke.py --github-token-file ~/.ci-skills/github.com.token.env \
    apply --phase ci14 --case mirror --run-id r20261122a

# Deletes exactly what apply created; verifies absence by path and by ID.
tools/live-smoke.py cleanup --run-id r20261122a

# Finishes a cleanup a previous run left partial.
tools/live-smoke.py cleanup --run-id r20261122a --reconcile
```

One mutation at a time holds the lock for `<state-dir>/<run_id>.json`, which
`apply` creates in `.internal/scratch/live-smoke` by default. The ledger records
intent before each create request and saves the returned native ID before the
next provider request. An unknown create outcome keeps the ledger open for
manual investigation. Cleanup requires the recorded GitLab server before
contacting either provider.

`cleanup` verifies absence twice by path and by native ID, including with
`--reconcile`. If a self-managed GitLab project enters delayed deletion, cleanup
can request permanent removal after verifying the recorded ID and the path GitLab
assigned to that run-owned project. Cleanup also verifies that path is absent.
Exit status `0` indicates success. Status `2` carries a stable reason on stderr.

## Credentials

GitHub reads `--github-token-file`, then `GH_TOKEN` or `GITHUB_TOKEN`, then
`~/.ci-skills/github.com.token.env`. The token login must match `--owner`
before GitHub can create a repository.

GitLab reads its server and configured token file from the same `--target-file`
(default `~/.ci-skills/target.toml`). That file's `[gitlab_routing]` section
selects the route, and the legacy `[gitlab]` table is supported. Relative token
paths resolve beside the selected target file. `--gitlab-token-file` overrides
the selected token file. If no file is selected, GitLab reads `GITLAB_TOKEN` or
`GITLAB_PRIVATE_TOKEN`. An explicit `--gitlab-url` uses the explicit token file
or environment credential. Token values stay out of output and ledgers.

- GitHub token: a fine-grained PAT (resource owner `spyroot`, all repositories)
  with **Administration: write** and **Contents: write**. GitHub offers no API to
  create or edit PATs, so creation stays a one-time web step;
  `create_fine_grained` verifies the filed token instead of minting one.
- GitLab token: one that can create projects under the smoke group on the target
  GitLab (default group `ci-skills-smoke`, override with `--group`).

In PAT-manage mode `attach_fine_grained_to_repo` verifies that the filed PAT
reaches one repository, and `detach_fine_grained_to_repo` is a documented no-op:
a PAT has no per-repository attachment to remove. A future GitHub App mode can
implement real attach and detach by minting installation tokens; the verb names
already match that design.

## Safety rules

- Stop-if-present: `apply` aborts, creating nothing, when either fixture name
  already exists on its provider.
- Stop-if-identity-changed: `cleanup` re-reads each object by path and deletes
  only when the native ID still matches the ledger; otherwise it aborts with
  `identity_changed` and deletes nothing.
- Run-owned only: the tool never adopts or deletes pre-existing objects, and the
  smoke group is infrastructure, not a fixture.
- This tool manages fixtures; capability evidence still comes from the installed
  skill runs (for example `tools/qualify_mirror.py`) against these fixtures.

See `docs/2026-10-31/live-smoke-inventory.md` for the operator-allocated smoke
assets and their protection rules; this tool automates only the disposable
per-run fixtures.
