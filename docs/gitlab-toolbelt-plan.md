# GitLab operations tool belt: proposed delivery plan

## Outcome and boundary

Extend the installed `k8s-admin-diagnostics` skill with project-neutral
GitLab operations. Its existing diagnostic commands remain read-only, while
each new command is explicitly marked mutating in the generated manifest.
The current GitLab target resolution, effective credential selection,
bounded output, and redaction logic serve both routes through one
implementation. No operation selects a private host, project, group,
runner, or token by a built-in name.

The source inventory to adopt is `gitlab_auth.sh` (credentialed execution),
`gitlab_util.sh` (bug and milestone creation), and
`gitlab_runner_assign.sh` (runner assignment). Their sourceable helpers own
the actual API behavior. Port exact-title lookup, open-issue reuse, project
enumeration, and independent read-back; exclude project-specific default
hosts, groups, token aliases, and unrelated deployment commands.

This is a plan, not an installed capability or a live acceptance receipt.
Each stage below is one reviewable PR and runnable block. Code, tests,
manifest, schema extension, and user instructions land with the capability.

## Agent invocation and target contract

The existing `SKILL.md` routes an agent by intent. Diagnostics keep
`access_check.py` as their first call; GitLab operations start with
`gitlab_access.py check`. Its single generated `tools.json` adds one record
per `(script, subcommand)` with a tool ID, trigger, required inputs, minimum
permission, mutability, result kind, and read-back. The skill frontmatter
uses `first_call_by_route` and advertises mixed mutability; the README and
skill text stop saying every command is read-only. The catalog and parser
accept the same subcommands and options. `--describe` and `--help` work
without credentials. Agents call installed executables directly; no MCP
server or separate provider deployment is implied.

The target uses the existing four tiers: `--target`, `CI_SKILLS_TARGET`,
`./.ci-skills/target.toml`, then `~/.ci-skills/target.toml`. Stage 1 adds a
GitLab-only target profile and permits `gitlab.project`, `gitlab.group`, and
`gitlab.runner_id` in the selected file; the current loader requires all
three authorities and rejects those keys. Explicit `--project`, `--group`,
or `--runner` overrides its matching target field. A missing or conflicting
identifier blocks. Neither the Git remote nor the `glab` profile supplies a
target fallback. The access check resolves a project/group to a numeric ID;
all later API paths use that ID, never a context-sensitive placeholder. Every
result names the resolved origin, numeric IDs, and target source.

The GitLab-only check reuses effective-source precedence: declared token
file, named token environment variable, then the exact-host `glab` store.
The chosen token is loaded once into a controlled child environment; a store
credential that cannot be pinned noninteractively blocks. The plan records
the source reference and token digest, never the value. `GET /user` records
the numeric identity; `GET /projects/:id` or group read-back verifies the
exact target. Apply checks source digest, user ID, origin, and numeric target
again immediately before a write. The existing instance-admin and
`runners/all` checks remain in the diagnostics route. GitLab operations use
the API permissions their action needs; they do not silently inherit the
diagnostic admin policy or require GitHub/Kubernetes access. Runner actions
also check the selected runner and scope.

Mutating commands default to offline `--dry-run`: no GitLab API call, no file
write, and no access claim. `--plan-out PATH` explicitly performs read-only
API checks and atomically writes a sanitized plan at the caller's path.
`--apply --plan PATH --confirm-<action> --timeout DURATION` consumes that
plan. The plan binds the action, SHA-256 of desired input, origin and numeric
target IDs, source and token digests, user ID, observed resource ID, and
verified installed skill digest. An unverified revision is reported but is
not a security binding. Apply rejects changed inputs, source, identity,
target, or digest. A verified no-op sends no write request.

The shared GitLab transport sends POST/PUT bodies through `glab api --input -`
on stdin, pins `--hostname`, clears conflicting host/protocol variables,
and runs outside a Git repository. It captures bounded status, headers,
stdout, and stderr before classification. Authentication failures are
terminal. A timed-out POST is never retried blindly: read back the operation's
idempotency key first, then return the discovered record or a blocked,
uncertain outcome. Each action defines its own lookup key below.

## Delivery blocks

| PR | Agent action | Proof before merge |
| --- | --- | --- |
| 1 | `gitlab.access.check` | Exact host, user, target, source |
| 2 | `milestone.create/update/adjust-time` | GET exact milestone |
| 3 | `bug.create` | GET exact issue IID |
| 4 | `wiki.create/update` | GET exact page slug |
| 5 | `runner.assign` | GET membership for each project |
| 6 | `runner.create` | GET record and token-sink receipt |

### 1. Access and packaging

`gitlab_access.py check` is the first call before a GitLab operation. Extend
the current installer, catalog, target/source/report modules, and package
smoke rather than introduce a second skill. The read-back proves exact host,
numeric user and project/group IDs, effective source, and the permissions
needed by the selected action. Wrong host, expired token, and wrong project
block. An installed-package smoke invokes every entrypoint from an unrelated
directory, with no source-tree `PYTHONPATH`, and renders normal and failed
JSON/YAML results.

### 2. Milestones

`gitlab_milestone.py create` is for a requested new project milestone;
`update` changes a selected numeric ID; `adjust-time` calls the same update
core with only start/due dates. The input is a title for create, a milestone
ID for update, and optional description file, dates, or state as applicable.
The create lookup covers project milestones only: exactly one matching title
with identical desired fields is no-op; a differing or ambiguous match blocks
and routes the agent to update. Calendar-invalid dates block before apply.
Independent GET compares ID, title, description digest, dates, and state.

### 3. Bugs

`gitlab_issue.py create-bug` is called for a requested bug. `open-bug` is an
alias to the same implementation. It accepts a title, description file,
configured bug label, and optional exact project milestone. The lookup key is
project ID, exact title, and bug label across open and closed issues. One open
match with identical desired fields is no-op; a closed or differing match and
multiple matches block instead of silently creating another issue. Independent
GET compares issue IID, title, label, description digest, and milestone ID.

### 4. Wiki

`gitlab_wiki.py create` takes a title and content file; `update` takes an
existing slug and content file. Create on an existing slug is no-op only if
the content digest matches; otherwise it blocks and points to update. Update
on an unchanged page is no-op. Independent GET by slug compares title,
format, and content digest. A missing update target blocks.

### 5. Runner assignment

`gitlab_runner.py assign` takes an existing numeric runner ID and a numeric
project ID or selected group. Group mode enumerates all direct and subgroup
projects with pagination, records the exact project IDs in the plan, and
requires every selected ID to read back as assigned. Replanning after a
partial run targets only missing assignments; it never reports partial work
as PASS. Recovery can unassign only associations created by this run when
the operator selects rollback.

### 6. Runner creation

`gitlab_runner.py create` uses `POST /user/runners` for a requested new
runner record. Scope, numeric project/group ID, tags, and a caller-supplied
unique runner key are plan inputs. An existing record with that key blocks
with its ID because the one-time token cannot be read back. Apply requires
an explicit token sink chosen by the operator; the token never enters a
report or log. If sink delivery fails after creation, delete only that newly
created runner ID and verify absence; failed cleanup blocks. GET verifies
scope, tags, and configuration. A successful record creation reports
`readiness: UNREGISTERED`; registering a manager on a host and proving it
online with a real job is a separate capability and acceptance claim.

The suggested API mapping follows GitLab's project
[milestones](https://docs.gitlab.com/api/milestones/),
[issues](https://docs.gitlab.com/api/issues/),
[wikis](https://docs.gitlab.com/api/wikis/),
[runner assignment](https://docs.gitlab.com/api/runners/), and
[runner creation](https://docs.gitlab.com/api/users/) contracts. The legacy
registration-token `POST /runners` route is not the creation target.

## One result contract

The access PR extends the existing report factory and adds one
`tool-result/v1` JSON Schema. Its minimum common envelope matches the
current diagnostics report:

```text
schema_version, kind, status, captured_at, errors[]
```

One diagnostic branch preserves the current `schema_version: "1.0"` fields,
including plural `targets`, `surfaces`, and `credential_sources` in its access
receipt; the existing acceptance checker continues to validate it. An
operations branch requires `operation`, `mode`, `outcome`, numeric `target`,
`credential_source`, `identity`, `skill.digest`, `plan.digest`, `resource`,
`readback`, `cleanup`, and `evidence_sanitized`. Action-specific schema
branches require their own resource IDs and compared fields. JSON/YAML and
human output come from the existing renderer, with one redaction pass.

| Mode | Status | Exit | Meaning |
| --- | --- | --- | --- |
| Offline dry-run | `DRY_RUN` | 0 | Intent only; no API evidence |
| Live plan | `PLANNED` | 0 | Read-only plan, never acceptance |
| Apply verified | `PASS` | 0 | `APPLIED` or `NO_OP` |
| Any unresolved step | `BLOCKED` | 2 | No success claim |
| Partial assignment | `PARTIAL` | 2 | Resume or rollback needed |

HTTP success, a returned ID, and an installed binary alone do not mean
`PASS`. Token values, raw response bodies, description/page content, and
credential-bearing files are excluded from shareable evidence; reports keep
IDs, selected fields, digests, and sanitized error classes. A controlled
failure emits a schema-valid JSON/YAML error envelope on stdout with a
nonzero exit status.

## Gates and proof per PR

The existing GitHub `validate` workflow is the configured CI route. Each PR
needs exact-head static checks, catalog/manifest/schema validation, focused
unit tests, and installed-package smoke. The installed smoke runs all
entrypoints from an unrelated directory and checks normal JSON/YAML plus
structured failures and runtime dependencies. If Bash is added, Bash parse,
ShellCheck, shfmt, and Bats must actually execute in that workflow. Tests
assert offline dry-run makes zero API calls; plan/apply input binding;
permission denial; terminal 401/403; bounded 408/429/5xx handling;
post-timeout create read-back; malformed responses; redaction; timeout/signal
cleanup; read-back mismatch; and second-call no-op.

Stages 2–6 also require protected live smoke against a named disposable
GitLab target, from a named execution host that can reach that instance. A
sanitized receipt binds exact skill digest and candidate commit, origin and
numeric target IDs, effective source and user ID, plan digest, apply result,
independent GET, repeated no-op, and cleanup. Add an operations receipt
profile to the existing acceptance checker; keep the diagnostic receipt
profile unchanged. The current GitHub workflow has no configured GitLab
mutation credentials or disposable target, so its mocked `validate` result
alone cannot accept stages 2–6. Those PRs stay draft until an approved live
route and exact-head receipt check are wired. No production resource is
changed merely to satisfy smoke.

## Decisions to close before implementation

1. **Token sink for runner creation:** select a secret destination and the
   manager-registration owner before stage 6. The API returns its token once;
   stage 6 is not callable until that sink is proven.
2. **Meaning of `adjust-time`:** this plan defines milestone schedule dates.
   Issue estimate/spent-time APIs would be a separate requested operation.
3. **Live smoke route:** name a disposable project/group and execution host
   for stages 2–6, then wire exact-head receipt validation. A saved login
   does not identify that target or supply an approved mutation route.
