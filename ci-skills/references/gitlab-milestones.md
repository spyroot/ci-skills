# GitLab milestone inputs

`gitlab_milestone.py` creates or updates project/group milestones and verifies
the result with a separate read. Use the installed root and Python environment
from [SKILL.md](../SKILL.md).

## Fields and dates

| ci-skills input | Provider field / meaning |
| --- | --- |
| `--project` or `--group` | Selected milestone scope; choose one. |
| `--title` | `title`; required for create, optional for update. |
| `--milestone-id` | Returned `id`, not scope-local `iid`; needed for updates. |
| `--description-file` | UTF-8 description passed as `description`. |
| `--start-date` | `start_date`, `YYYY-MM-DD`. |
| `--due-date` | `due_date`, `YYYY-MM-DD`. |
| `--state active` | Update maps to `state_event=activate`. |
| `--state closed` | Update maps to `state_event=close`. |

Dates are calendar dates, not start/stop timestamps. Omitted fields are not
requested changes. `adjust-time` accepts dates only; supply at least one.
When both dates are supplied, start must not follow due. This wrapper has no
date-clearing flag. State changes require `update`, not `create`.
Sources: [project API][milestones] and [group API][groups]; rolling GitLab
documentation checked 2026-10-18.

## Create and adjust

Caller-selected example dates/title follow; replace them with the requested
milestone values. Plan first, then repeat with the returned `plan_digest`:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_milestone.py" create \
  --project GROUP/PROJECT --title release-1.0 \
  --start-date 2026-11-01 --due-date 2026-11-15 --dry-run --json
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_milestone.py" create \
  --project GROUP/PROJECT --title release-1.0 \
  --start-date 2026-11-01 --due-date 2026-11-15 \
  --apply --confirm-plan PLAN_DIGEST --json
```

Use the returned numeric milestone ID for these plans; repeat the chosen
command with `--apply --confirm-plan PLAN_DIGEST` instead of `--dry-run`:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_milestone.py" adjust-time \
  --project GROUP/PROJECT --milestone-id MILESTONE_ID \
  --due-date 2026-11-20 --dry-run --json
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_milestone.py" update \
  --project GROUP/PROJECT --milestone-id MILESTONE_ID \
  --state closed --dry-run --json
```

Matching state returns `NO_OP`; changed state is `APPLIED` only after verified
read-back. A differing existing title requires update rather than another
create. A failed or uncertain write must be resolved from the returned evidence.

## Tags and labels

- **Milestone**: groups issues/merge requests around a goal and optional dates.
  Associate an issue through `gitlab_issue.py --milestone-id`.
- **Git tag**: names a repository commit. `CI_COMMIT_TAG` identifies tag
  pipelines; it is not a milestone field. See [repository tags][tags].
- **Runner tag**: schedules jobs with matching `.gitlab-ci.yml` `tags`.
  `gitlab_runner.py tag --tag` adds these; see [runner inputs](gitlab-runner-config.md).
- **Label**: categorizes issues/merge requests. `gitlab_issue.py --label` accepts
  repeated labels. See [GitLab labels][labels].

## Related

- `gitlab_issue.py`: associate an issue with the selected milestone.
- `gitlab_pipeline.py`: inspect execution before closing the milestone.
- [Wiki inputs](gitlab-wiki.md): publish the resulting project notes.

[milestones]: https://docs.gitlab.com/api/milestones/
[groups]: https://docs.gitlab.com/api/group_milestones/
[tags]: https://docs.gitlab.com/user/project/repository/tags/
[labels]: https://docs.gitlab.com/user/project/labels/
