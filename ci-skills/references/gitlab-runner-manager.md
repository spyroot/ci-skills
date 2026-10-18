# GitLab runner manager

Use this command after `gitlab_runner.py create` returns a runner ID and writes
its one-time token. The selected toolbox image must be pinned by digest and
contain both `gitlab-runner` and `podman`. Supply the exact worker already
verified by `image_build_readiness.py probe`.

## Deploy

Use the installed root and Python environment described in `SKILL.md`. The
default command reads live provider state and returns `plan_digest`; `--dry-run`
returns offline intent without an apply-ready confirmation. A missing run ID
is generated only after confirmation and returned by successful apply:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_runner_manager.py" deploy \
  --project GROUP/PROJECT \
  --runner-id RUNNER_ID \
  --description DESCRIPTION \
  --label JOB_TAG \
  --image REGISTRY/PROJECT/TOOLBOX@sha256:DIGEST \
  --token-file TOKEN_PATH \
  --node READY_NODE \
  --cpu-request 250m --cpu-limit 1 \
  --memory-request 256Mi --memory-limit 1Gi \
  --storage-size 8Gi \
  --json
```

For a group runner, replace `--project GROUP/PROJECT` with `--group GROUP`.
Repeat the same inputs with the confirmation:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_runner_manager.py" deploy \
  --project GROUP/PROJECT \
  --runner-id RUNNER_ID \
  --description DESCRIPTION \
  --label JOB_TAG \
  --image REGISTRY/PROJECT/TOOLBOX@sha256:DIGEST \
  --token-file TOKEN_PATH \
  --node READY_NODE \
  --cpu-request 250m --cpu-limit 1 \
  --memory-request 256Mi --memory-limit 1Gi \
  --storage-size 8Gi \
  --apply --confirm-plan PLAN_DIGEST --json
```

If the caller selects `--run-id RUN_ID`, repeat it in both invocations; an
explicit identity is part of confirmation. Use the same five resource values
that the successful `image_build_readiness.py` probe used. The defaults shown
above match that command's defaults.

Add `--namespace EXISTING_NAMESPACE` to retain a caller-owned namespace. If it
is omitted, confirmed apply creates and returns a run-owned namespace and run
ID. Successful apply proves the exact Deployment placement, ServiceAccount, privilege,
resources, Podman storage, immutable image ID, and exact GitLab runner online
with contact evidence.

## Prove a real job

The linked job proof depends on the CI11 pipeline start/watch delivery. Use
`gitlab_pipeline.py start` to plan and confirm the selected test ref, then
`gitlab_pipeline.py watch` with the returned pipeline ID. Select `JOB_URL` from
the job's `web_url` returned by
`gitlab_job.py list --project GROUP/PROJECT --pipeline-id PIPELINE_ID --name-glob JOB_NAME`.
Read it with `gitlab_job.py get --job-url JOB_URL`; require success and the exact
`RUNNER_ID` in that result. Deployment success alone is not job execution proof.

## Remove

Plan removal using the exact project or group, runner, and returned run ID:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_runner_manager.py" delete \
  --project GROUP/PROJECT \
  --runner-id RUNNER_ID \
  --description DESCRIPTION \
  --run-id RUN_ID \
  --json
```

Repeat those inputs with the deletion plan's confirmation:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_runner_manager.py" delete \
  --project GROUP/PROJECT \
  --runner-id RUNNER_ID \
  --description DESCRIPTION \
  --run-id RUN_ID \
  --apply --confirm-plan DELETE_PLAN_DIGEST --json
```

Deletion needs GitLab and Kubernetes configuration; it does not read Harbor,
the toolbox image, or the one-time token. It removes only the owned workload,
owned temporary SCC RoleBinding when one exists, and owned generated namespace.
It reads the exact runner again and reports its current cached status without
claiming that workload deletion makes the record offline. Delete the provider
runner record separately with confirmed `gitlab_runner.py delete`.
