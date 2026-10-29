# GitLab runner inputs

`gitlab_runner.py` manages the GitLab runner record. Creating a record returns
its ID and persists its one-time token; it does not install or start a runner.
Use the installed root and Python environment from [SKILL.md](../SKILL.md).

## Record fields

| ci-skills input | Value |
| --- | --- |
| `--project` or `--group` | Selected path or numeric ID; choose one scope. |
| `--runner-type` | `project` or `group`, matching the selected scope. |
| `--description` | Caller-selected record identity used during recovery. |
| `--tag` | One scheduling tag; repeat for more; no commas or duplicates. |
| `--token-out` | Caller-selected token file; required in plan and apply. |
| `--runner-id` | Returned numeric ID for later get, tag, assign or delete. |

Plan a project record, then repeat the same inputs with its `plan_digest`:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_runner.py" create \
  --project GROUP/PROJECT --runner-type project --description build-linux \
  --tag linux --token-out runner.token --dry-run --json
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_runner.py" create \
  --project GROUP/PROJECT --runner-type project --description build-linux \
  --tag linux --token-out runner.token \
  --apply --confirm-plan PLAN_DIGEST --json
```

The token file is private output. Successful record creation is not evidence
that a runner manager is online or has executed a job.

## Native manager configuration

These are upstream `gitlab-runner` settings, not `gitlab_runner.py` options.
Check the installed runner version before selecting version-specific fields.

| Location | Exact key | Purpose |
| --- | --- | --- |
| `config.toml` | `concurrent` | Process-wide job concurrency. |
| `[[runners]]` | `executor`, `shell` | Job execution mechanism and shell. |
| `[[runners]]` | `limit`, `request_concurrency` | Per-runner job and request limits. |
| `[[runners]]` | `environment` | Array of `KEY=value` entries injected into jobs. |
| `[[runners]]` | `builds_dir`, `cache_dir` | Executor-visible working paths. |

Native registration maps `--url` to `CI_SERVER_URL`, `--token` to
`CI_SERVER_TOKEN`, `--name` to `RUNNER_NAME`, and `--non-interactive` to
`REGISTER_NON_INTERACTIVE=true`. `--config`/`CONFIG_FILE` selects `config.toml`.
`gitlab-runner register --help` lists the installed binary's complete mapping.
Do not pass the runner authentication token as the GitLab API access token.

The following native example selects the URL/name/config file and reads the
`runner.token` produced above. Replace the example URL with the same GitLab
origin used to create the record. Run with shell tracing disabled:

```bash
CONFIG_FILE="$PWD/runner-config.toml"
RUNNER_TOKEN_FILE="$PWD/runner.token"
(
  set +x
  export CI_SERVER_URL="https://gitlab.example.com"
  export RUNNER_NAME="build-linux"
  CI_SERVER_TOKEN="$(cat "$RUNNER_TOKEN_FILE")" || exit
  export CI_SERVER_TOKEN
  gitlab-runner register --non-interactive \
    --executor shell --config "$CONFIG_FILE"
)
gitlab-runner run --config "$CONFIG_FILE"
```

Registration writes the token into private `config.toml`; `run` starts the
manager. These native commands do not consume `target.toml`. Repeating
registration adds another configuration entry.

Sources: [configuration][configuration], [native commands][commands]; rolling
GitLab documentation checked 2026-10-18.

## Job variables

In `.gitlab-ci.yml`, `variables` selects job behavior. For example, this job
requires a runner with the `linux` tag and uses a shallow checkout:

```yaml
smoke:
  tags: [linux]
  variables:
    GIT_STRATEGY: fetch
    GIT_DEPTH: "20"
  script:
    - printf 'runner=%s\n' "$CI_RUNNER_ID"
```

`GIT_STRATEGY` accepts `clone`, `fetch`, `none`, or `empty`.
`RUNNER_SCRIPT_TIMEOUT` and `RUNNER_AFTER_SCRIPT_TIMEOUT` accept durations,
for example `10m` and `2m`, within the job's total timeout.
Every job tag must match the selected runner; `run_untagged` controls jobs
without tags. Paused state, scope and protected-ref access also affect selection.
See [job configuration][jobs].

## Related

- [Feature flags](gitlab-runner-feature-flags.md): exact keys, defaults and precedence.
- [Cache tuning](gitlab-runner-cache.md): chunk sizes, concurrency and backend limits.
- [OpenShift Operator](gitlab-runner-operator.md): Runner properties and ConfigMaps.
- `gitlab_job.py`: read the actual job's runner and outcome.
- `gitlab_pipeline.py`: read pipeline progress.
- [Git tags, runner tags and labels](gitlab-milestones.md#tags-and-labels): separate identities.

[configuration]: https://docs.gitlab.com/runner/configuration/advanced-configuration/
[commands]: https://docs.gitlab.com/runner/commands/
[jobs]: https://docs.gitlab.com/ci/runners/configure_runners/
