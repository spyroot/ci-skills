# Start and watch GitLab pipelines

`bin/gitlab_pipeline.py` reads, starts and watches pipelines for one configured GitLab project. It requires only the
GitLab section of the [target file](access.md#where-the-target-file-comes-from). Start uses the shared confirmed-action
lifecycle; watch is a bounded read that returns compact graph evidence.

Start reports use the [GitLab action result schema](../schemas/results/gitlab-action.schema.json); watch reports use the
[pipeline watch result schema](../schemas/results/gitlab-pipeline-watch.schema.json).

## Discover and read

Set `CI_SKILLS_ROOT` to the installed skill root as described by [CI Skills](../SKILL.md). Use its prepared Python 3.11
environment, `--describe` for the compact command contract, and `--help` for arguments:

```bash
CI_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/ci-skills"
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_pipeline.py" --describe --json
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_pipeline.py" --help
```

Read one exact pipeline and its bounded jobs:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_pipeline.py" get \
  --project GROUP/PROJECT --pipeline-id 123 --json
```

`--project` overrides `gitlab.project` from the target; it does not select credentials for another GitLab host.

## Plan and start

An offline plan checks arguments without provider contact. A live plan then resolves the project, selected ref and
exact commit SHA and returns the digest accepted by apply:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_pipeline.py" start \
  --project GROUP/PROJECT --ref main --dry-run --json
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_pipeline.py" start \
  --project GROUP/PROJECT --ref main --live-plan --json
```

Repeat the same inputs and use the live plan's `plan_digest`:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_pipeline.py" start \
  --project GROUP/PROJECT --ref main \
  --apply --confirm-plan DIGEST --receipt-out pipeline-start.json --json
```

When `--ref` is absent, live planning reads the project's current default branch. `--expected-sha` requires one full
lowercase commit SHA; `--require-protected-ref` requires the resolved branch to be protected. Start requests default to
25 seconds and reject `--timeout` values above 300 seconds. Apply creates one pipeline, then independently reads that
pipeline ID and requires the same project, ref and SHA.

Supply a variable with repeatable `--variable KEY=VALUE` arguments on every phase. Variable values are kept in the
private provider request and plan digest; reports, receipts and logs expose only the selected field names and body
digest.

## Watch to settlement

Watch the root pipeline, its bounded jobs and bridge/downstream pipelines:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_pipeline.py" watch \
  --project GROUP/PROJECT --pipeline-id 123 \
  --interval 10 --timeout 3600 --receipt-out pipeline-watch.json --json
```

The defaults are a 10-second polling interval and a 3,600-second total deadline. `--related-name REGEX` additionally
selects newer same-ref pipelines in the root project whose provider name matches. Watch does not create, retry or cancel
a pipeline.

`PASS` means every observed pipeline settled successfully. `PARTIAL` means the bounded graph settled unsuccessfully,
timed out or could not be proven complete. `BLOCKED` means authentication or the required root read failed. `DRY_RUN`
describes the planned reads without contacting GitLab. Failed-job and unsuccessful-pipeline choices point to the
existing job or pipeline read commands for narrower evidence.
