# GitLab wiki inputs

`gitlab_wiki.py` creates or updates a project wiki page, then reads the returned
slug to verify title/content. Use the installed root and Python environment
from [SKILL.md](../SKILL.md).

## Fields and format

| ci-skills input | Supply |
| --- | --- |
| `--project` | Project path or numeric ID. |
| `--title` | Required for create; optional rename during update. |
| `--content-file` | Required UTF-8 file containing the entire requested page. |
| `--slug` | Exact existing slug returned by GitLab; update only. |

GitLab's native API accepts `format=markdown`, `rdoc`, `asciidoc` or `org`.
The wrapper exposes no `--format` and sends no format field; create uses the
API's Markdown default. Do not infer markup from the content filename.
List, delete, file uploads and group wikis are native API operations, not
verbs of this wrapper. Source: [project wikis API][api], rolling documentation
checked 2026-10-18.

## Markdown page example

Save the caller's requested content as `release-notes.md`. Replace `PIPELINE_URL`
with the observed result URL; `deploy` must be an existing selected wiki slug:

```markdown
# Release 1.0

- Pipeline: [Observed result](PIPELINE_URL).
- Deployment procedure: [Deploy](deploy).
```

Plan and apply with identical title, project and file contents:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_wiki.py" create \
  --project GROUP/PROJECT --title release-notes \
  --content-file release-notes.md --dry-run --json
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_wiki.py" create \
  --project GROUP/PROJECT --title release-notes \
  --content-file release-notes.md --apply --confirm-plan PLAN_DIGEST --json
```

`PLAN_DIGEST` comes from the plan. Supply the actual returned slug to update;
do not reconstruct it from the title:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/gitlab_wiki.py" update \
  --project GROUP/PROJECT --slug RETURNED_SLUG \
  --content-file release-notes.md --dry-run --json
```

Repeat with the plan's confirmation to apply. Identical content/title gives
`NO_OP`; differing existing content at create requires update. A rename can
change the slug, so use the newest returned value. Nonverified or uncertain
writes remain explicit in the result rather than being reported successful.

## Read an existing page

`bin/ci-api` provides the existing bounded GET route. `PROJECT_ID` is the numeric
project ID; `ENCODED_SLUG` URL-encodes the complete slug, including any `/`.
This read helps select content/format before an update:

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/ci-api" get \
  --provider gitlab --endpoint "projects/PROJECT_ID/wikis/ENCODED_SLUG" --json
```

## Native format change

For a format the wrapper does not select, use the existing native API.
`GITLAB_HOST` selects the authenticated GitLab hostname; `CONTENT_FILE` contains
the requested AsciiDoc. `PROJECT_ID` and `ENCODED_SLUG` select the exact page:

```bash
glab api --hostname "$GITLAB_HOST" --method PUT \
  "projects/$PROJECT_ID/wikis/$ENCODED_SLUG" \
  --raw-field format=asciidoc --field "content=@$CONTENT_FILE"
glab api --hostname "$GITLAB_HOST" \
  "projects/$PROJECT_ID/wikis/$ENCODED_SLUG"
```

The PUT changes the page; the GET reads its resulting format/content. Native
`glab` uses its own authentication, not ci-skills target resolution.
See [glab API fields][glab-api].

## Related

- [Wiki links and Markdown][markdown]: page-relative links and wiki syntax.
- [Milestones](gitlab-milestones.md): dates and closing state.
- `gitlab_job.py`: collect the actual job outcome before writing a claim.

[api]: https://docs.gitlab.com/api/wikis/
[markdown]: https://docs.gitlab.com/user/project/wiki/markdown/
[glab-api]: https://docs.gitlab.com/cli/api/
