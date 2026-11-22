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
tools/live-smoke.py apply --phase ci14 --case mirror --run-id r20261122a \
    --github-token-file ~/.ci-skills/github.com.token.env

# Deletes exactly what apply created; verifies absence by path and by ID.
tools/live-smoke.py cleanup --run-id r20261122a

# Finishes a cleanup a previous run left partial.
tools/live-smoke.py cleanup --run-id r20261122a --reconcile
```

`apply` opens `<state-dir>/<run_id>.json` (default `.internal/scratch/live-smoke`) before creating fixtures.
It saves each created native ID before the next provider request. Cleanup requires the
recorded GitLab server before contacting either provider. Older ledgers without a server binding require manual reconciliation.
`cleanup` re-reads every object, requires the recorded native ID, deletes, proves
absence by path and by native ID, and repeats the absence read-back. GitLab answers
project deletion with HTTP 202 and removes asynchronously; the tool polls to 404.
Exit codes: `0` success, `2` with the stable reason on stderr.

## Credentials

Read order per system: an explicit `--github-token-file` / `--gitlab-token-file`
wins, then the environment (`GH_TOKEN`, `GITHUB_TOKEN`, `GITLAB_TOKEN`), then the
filed defaults `~/.ci-skills/github.com.token.env` and
`~/.ci-skills/gitlab.dcloud.run.token.env`. Token values are never printed or
recorded; the ledger stores identities and IDs only.

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
