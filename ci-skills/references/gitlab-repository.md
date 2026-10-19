# GitLab repository commits

Source: [GitLab Commits API](https://docs.gitlab.com/api/commits/#create-a-commit),
checked 2026-10-19. The installed command supports native `create` and `update`
file actions. GitLab's API also offers delete, move and chmod; those actions
are outside this command.

## Native file actions

`--actions-file` reads a JSON array. Each action supplies `action`, `file_path`
and `content`; optional `encoding` is `text` (default) or `base64`.
`last_commit_id` is GitLab's optimistic-concurrency field for an updated file.
Content is committed unchanged and independently read back as bytes.

Example `actions.json` creates one Go source file:

```json
[
  {
    "action": "create",
    "file_path": "main.go",
    "content": "package main\n\nimport \"fmt\"\n\nfunc main() { fmt.Println(\"hello\") }\n"
  }
]
```

Use `update` for an existing file. Add another action to commit a Dockerfile
or `.gitlab-ci.yml` in the same commit. Their contents belong to the caller;
the command does not generate a pipeline or run a job.

## Plan and commit

The caller selects `PROJECT`, `BRANCH`, and existing `BASE` branch or commit:

```bash
gitlab_repository.py commit --project "$PROJECT" \
  --branch "$BRANCH" --start-branch "$BASE" \
  --commit-message "Add consumer example" --actions-file actions.json --json
```

Repeat that invocation with `--apply --confirm-plan DIGEST`, replacing `DIGEST`
with the returned `plan_digest`. Existing branches do not need `--start-branch`.
The result contains `branch`, `commit_sha`, `verified` and each file's
`content_sha256`. Repeating matching message/content returns `NO_OP`.

A commit can trigger GitLab's existing project rules. To run only a separately
requested API pipeline, the caller's native YAML can select
`CI_PIPELINE_SOURCE == "api"` in `workflow.rules`; see GitLab's
[workflow reference](https://docs.gitlab.com/ci/yaml/workflow/).

## Related commands

Select `reference.py gitlab_pipeline.py` for start/watch guidance or
`reference.py gitlab_mr.py` to open an MR for the committed branch.
