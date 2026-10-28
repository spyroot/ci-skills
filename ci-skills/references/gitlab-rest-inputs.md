# GitLab REST inputs behind the skill's commands

`gitlab_runner.py`, `gitlab_milestone.py`, `gitlab_pipeline.py` and `gitlab_wiki.py` wrap the write endpoints
below with a dry-run plan, a confirmed apply and an independent read-back; `bin/ci-api get` reads any GET
endpoint. This reference is for the inputs those commands do not expose, or for a call the agent assembles
itself. Each block is one request body with every accepted attribute as a trailing comment in the form
`type | default | effect`; send it as form fields or as a JSON document with `Content-Type: application/json`.
`:id` is a numeric ID or a URL-encoded path such as `group%2Fproject`.

## Runner

`gitlab_runner.py create` calls the first endpoint and saves the returned token; `tag` and the record fields go
through the second; `assign` through the third. Creation needs the `create_runner` scope and an Owner or administrator
whose group or project allows runner registration. Update, assign and unassign need the `manage_runner` scope with
the Maintainer or Owner role for a project runner, Owner for a group runner, administrator for an instance runner;
only an administrator unassigns a locked runner.

```yaml
# POST /user/runners
runner_type: project_type        # instance_type, group_type, project_type | required | scope of the runner; instance_type needs an administrator
group_id: 42                     # int | unset | required with group_type, omit otherwise
project_id: 7                    # int | unset | required with project_type, omit otherwise
description: build-runner        # string | unset | shown in the runner list; ci-skills uses it as the recovery key
paused: false                    # bool | false | ignore new jobs
locked: false                    # bool | false | lock the runner to the current project
run_untagged: true               # bool | true | take jobs without tags
tag_list: "openshift,build"      # comma-separated string (array of strings in the OpenAPI document) | unset | runner tags
access_level: not_protected      # not_protected, ref_protected | not_protected | ref_protected runs protected refs only
maximum_timeout: 3600            # int seconds | unset | upper bound on job duration for this runner
maintenance_note: "rebuilt weekly"   # string, up to 1024 characters | unset | free-form note
token_expires_at: "2030-01-01T00:00:00Z"   # ISO 8601 | unset | Premium; 5 minutes to 21 days ahead and within instance, group or project limits
token_rotation_deadline: "2030-01-01T00:00:00Z"   # ISO 8601 | unset | Premium; needs token_expires_at and must not exceed it; equal values disable rotation
```

The response carries `id`, `token` and `token_expires_at`; the token is shown once.

```yaml
# PUT /runners/:id
description: build-runner        # string | unchanged | description
paused: false                    # bool | unchanged | ignore new jobs (active is the deprecated inverse)
tag_list: [openshift, build]     # array of strings | unchanged | replaces the whole tag list
run_untagged: true               # bool | unchanged | take jobs without tags
locked: false                    # bool | unchanged | lock to the current project
access_level: not_protected      # not_protected, ref_protected | unchanged | protected-ref restriction
maximum_timeout: 3600            # int seconds | unchanged | upper bound on job duration
maintenance_note: "rebuilt weekly"   # string, up to 1024 characters | unchanged | free-form note
```

```yaml
# POST /projects/:id/runners      assign an existing runner to another project
runner_id: 1234                  # int | required | runner to share with project :id
# DELETE /projects/:id/runners/:runner_id   unassigns it again
# DELETE /runners/:id                        deletes the runner record
# POST /runners/:id/reset_authentication_token   rotates the token; the response holds the new token and token_expires_at
```

## Milestone

`gitlab_milestone.py create`, `update` and `adjust-time` use these; `--group` switches to the group endpoints,
which take the same body. `--state active|closed` becomes `state_event`.

```yaml
# POST /projects/:id/milestones      or   POST /groups/:id/milestones
title: "Release 2.0"             # string | required | title; unique inside the project or group
description: "Scope of 2.0"      # string | unset | Markdown description
start_date: "2030-01-06"         # YYYY-MM-DD | unset | start date
due_date: "2030-03-31"           # YYYY-MM-DD | unset | due date; at or after start_date
```

```yaml
# PUT /projects/:id/milestones/:milestone_id      or   PUT /groups/:id/milestones/:milestone_id
title: "Release 2.0"             # string | unchanged | title
description: "Scope of 2.0"      # string | unchanged | description
start_date: "2030-01-06"         # YYYY-MM-DD | unchanged | start date
due_date: "2030-03-31"           # YYYY-MM-DD | unchanged | due date
state_event: close               # close, activate | unchanged | state transition; the read-back state is active or closed
```

## Pipeline

`gitlab_pipeline.py start --ref REF --variable KEY=VALUE` wraps this endpoint with a plan, a confirmed apply and a
read-back; the raw body is for `inputs` or `variable_type: file`, which the command does not expose. Merge request
pipelines use the merge requests endpoint instead.

```yaml
# POST /projects/:id/pipeline
ref: main                        # string | required | branch or tag to run on
variables:                       # array of objects | unset | pipeline variables; as form fields variables[][key]=A&variables[][value]=1
  - key: UPLOAD_TO_S3            # string | required | variable name
    value: "true"                # string | required | variable value
    variable_type: env_var       # env_var, file | env_var | file writes the value to a temporary file and exposes its path
inputs:                          # object | unset | spec:inputs values as key: value; strings, numbers, booleans or arrays
  environment: staging
  level: 3
```

The response is the pipeline record: `id`, `iid`, `sha`, `ref`, `status`, `web_url`.

## Pipeline schedule

```yaml
# POST /projects/:id/pipeline_schedules
description: "Nightly build"     # string | required | shown in the schedule list
ref: main                        # string | required | branch or tag; refs/heads/main or refs/tags/v1 when a name could be both
cron: "0 1 * * *"                # cron string | required | when it runs
cron_timezone: "UTC"             # string | UTC | time zone name such as America/Los_Angeles
active: true                     # bool | true | false creates it paused
inputs:                          # array of objects | unset | spec:inputs for the scheduled pipeline
  - name: deploy_strategy        # string | required | input name
    value: blue-green            # string, number, boolean or array | required | input value
```

```yaml
# PUT /projects/:id/pipeline_schedules/:pipeline_schedule_id      same attributes, all optional
# POST /projects/:id/pipeline_schedules/:pipeline_schedule_id/play             runs it now
# POST /projects/:id/pipeline_schedules/:pipeline_schedule_id/take_ownership   makes the caller its owner
inputs:
  - name: deploy_strategy        # string | required | input to change or remove
    destroy: true                # bool | false | remove this input from the schedule
```

```yaml
# POST /projects/:id/pipeline_schedules/:pipeline_schedule_id/variables
key: DEPLOY_TARGET               # string, up to 255 characters of A-Z a-z 0-9 _ | required | variable name
value: production                # string | required | variable value
variable_type: env_var           # env_var, file | env_var | file exposes the value through a file path
# PUT .../variables/:key takes value and variable_type; DELETE .../variables/:key removes it; the read-back adds raw (bool, expansion off)
```

## Wiki

`gitlab_wiki.py create` and `update` call these for a project wiki. Group wikis use `/groups/:id/wikis` with the
same body. Send Markdown with special characters URL-encoded or as JSON.

```yaml
# POST /projects/:id/wikis
title: "Runbook/Deploy"          # string | required | page title; a slash creates a directory in the slug
content: "# Deploy\n..."         # string | required | page body
format: markdown                 # markdown, rdoc, asciidoc, org | markdown | markup of content
front_matter:                    # object | unset | YAML front matter stored with the page (OpenAPI document)
  title: "Deploy"                # string | unset | front matter title
```

```yaml
# PUT /projects/:id/wikis/:slug      :slug URL-encoded, such as Runbook%2FDeploy; at least one attribute
title: "Runbook/Deploy"          # string | unchanged | new title; also moves the page when the slash path changes
content: "# Deploy\n..."         # string | unchanged | new body
format: markdown                 # markdown, rdoc, asciidoc, org | unchanged | markup of content
```

The response carries `slug`, `title`, `format`, `content` and `encoding`; `DELETE /projects/:id/wikis/:slug`
removes a page.

## Harbor integration

Links a GitLab project to one Harbor project so jobs receive `HARBOR_URL`, `HARBOR_HOST`, `HARBOR_OCI`,
`HARBOR_PROJECT`, `HARBOR_USERNAME` and `HARBOR_PASSWORD` variables. The same body on
`/groups/:id/integrations/harbor` sets a group default.

```yaml
# PUT /projects/:id/integrations/harbor      creates or updates
url: https://harbor.example.com  # string | required | base URL of the Harbor instance
project_name: platform           # string | required | Harbor project name
username: robot$platform+ci      # string | required | Harbor user or robot account
password: from-a-secret          # string | required | that account's password or robot secret
use_inherited_settings: false    # bool | false | inherit the group or instance default instead of these values
# GET /projects/:id/integrations/harbor     reads the settings without the password
# DELETE /projects/:id/integrations/harbor  disables and resets the integration
```

## Agent for Kubernetes configuration

The agent reads `.gitlab/agents/<agent-name>/config.yaml` from its configuration project. `ci_access` gives CI/CD
jobs of the listed projects a kubeconfig context for the agent in `$KUBECONFIG`; `user_access` gives their
members cluster access through GitLab. Authorization takes one or two minutes to propagate.

```yaml
# .gitlab/agents/<agent-name>/config.yaml
ci_access:
  projects:                                  # list, up to 500 | unset | projects whose jobs may use the agent; same top-level group as the agent project unless instance authorization is on
    - id: group/project-1                    # string | required | project path
      environments:                          # list of names or globs | all environments | only jobs in these environments; review/* matches review/anything
        - staging
        - review/*
      protected_branches_only: true          # bool | false | only jobs on protected branches; the most specific entry wins
  groups:                                    # list, up to 500 | unset | every project under the group and its subgroups
    - id: group/subgroup
      environments: [production]
  instance: {}                               # object | unset | every project of the instance; Self-Managed with organization_cluster_agent_authorization_enabled on
  access_as:                                 # object | agent: {} | identity the agent presents to the Kubernetes API; one key; also accepted inside one projects or groups entry
    agent: {}                                # default; the agent's service account
    # ci_job: {}                             # Premium; impersonate gitlab:ci_job:<job id> with groups gitlab:ci_job, gitlab:group:<id>, gitlab:group_env_tier:<id>:<tier>, gitlab:project:<id>, gitlab:project_env:<id>:<slug>, gitlab:project_env_tier:<id>:<tier>
    # impersonate:                           # Premium; a static identity
    #   username: ci-deployer                # string | required | impersonated user
    #   uid: ""                              # string | unset | impersonated UID
    #   groups: [deployers]                  # list of strings | unset | impersonated groups
    #   extra:                               # object of string lists | unset | extra impersonation fields
    #     team: [platform]
user_access:
  projects:                                  # list, up to 500 | unset | projects whose members get access
    - id: group/project-1
  groups:                                    # list, up to 500 | unset | groups whose members get access, descendants included
    - id: group/subgroup
  access_as:                                 # object | agent: {} | one key
    agent: {}                                # requests use the agent service account
    # user: {}                               # Premium; impersonate gitlab:user:<username> with groups gitlab:user, gitlab:project_role:<project id>:<role> and gitlab:group_role:<group id>:<role>
```

With `user: {}` instead of `agent: {}`, bind Kubernetes roles to those group names, for example a
`ClusterRoleBinding` whose subject is the group `gitlab:project_role:123:maintainer`. Only projects and groups
listed directly under `user_access` produce role groups.

## Sources

GitLab documentation at tag v19.5.0-ee in `gitlab-org/gitlab`: `doc/api/users.md`, `doc/api/runners.md`,
`doc/api/milestones.md`, `doc/api/group_milestones.md`, `doc/api/pipelines.md`, `doc/api/pipeline_schedules.md`,
`doc/api/wikis.md`, `doc/api/project_integrations.md`, `doc/user/clusters/agent/ci_cd_workflow.md`,
`doc/user/clusters/agent/user_access.md`. The generated OpenAPI document `doc/api/openapi/openapi_v2.yaml`
carries the bodies `postApiV4UserRunners`, `postApiV4ProjectsIdPipeline`, `postApiV4ProjectsIdPipelineSchedules`,
`postApiV4ProjectsIdWikis` and `putApiV4ProjectsIdIntegrationsHarbor`; it does not describe the milestone
endpoints.
