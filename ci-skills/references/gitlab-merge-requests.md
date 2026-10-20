# GitLab merge requests

Source: [GitLab Merge requests API](https://docs.gitlab.com/api/merge_requests/),
checked 2026-10-19. The installed command creates MRs and assigns milestones;
GitLab's other MR capabilities remain available through its native API.

## Select the correct identities

| Command input | Native field | Meaning |
| --- | --- | --- |
| `--source-branch` | `source_branch` | Existing branch containing the requested changes. |
| `--target-branch` | `target_branch` | Existing destination branch. |
| `--title` | `title` | Requested MR title. |
| `--mr-iid` | `merge_request_iid` | Project-local MR number shown as `!N`. |
| `--milestone-id` | `milestone_id` | Global milestone `id`, not its project-local `iid`. |

Use the `id` returned by `gitlab_milestone.py create` for the milestone. Use
`iid` returned by `gitlab_mr.py create` for subsequent MR updates.

## Open an MR

The caller supplies `PROJECT`, existing `SOURCE` and `TARGET` branches:

```bash
gitlab_mr.py create --project "$PROJECT" \
  --source-branch "$SOURCE" --target-branch "$TARGET" \
  --title "Consumer example" --json
```

Add `--milestone-id "$MILESTONE_ID"` to attach it during creation. Apply by
repeating the planned invocation with `--apply --confirm-plan DIGEST`, using
the returned digest. The response includes verified MR `id`, `iid` and
`milestone_id`. An exact existing open MR is reused.

## Attach an existing MR to a milestone

```bash
gitlab_mr.py update --project "$PROJECT" \
  --mr-iid "$MR_IID" --milestone-id "$MILESTONE_ID" --json
```

Confirm the returned plan with the same `--apply --confirm-plan DIGEST` pair.
This update changes only the selected MR's milestone and reads it back.

## Related commands

Select `reference.py gitlab_milestone.py` for milestone operations,
`reference.py gitlab_repository.py` for file commits, or
`reference.py gitlab_pipeline.py` for the separately selected pipeline.
