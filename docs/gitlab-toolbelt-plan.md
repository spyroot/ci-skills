# GitLab operations tool belt: delivery plan

## Outcome and boundary

This delivery extends `k8s-admin-diagnostics` with project-neutral GitLab
commands. Its diagnostic commands remain read-only, while
each operation is marked mutating in the generated manifest.
The GitLab target resolution, effective credential selection, response-size
validation, and redaction logic serve both routes through one
implementation. No operation selects a private host, project, group,
runner, or token by a built-in name.

The source inventory for these blocks is `gitlab_auth.sh` (credentialed
execution), `gitlab_util.sh` (bug and milestone creation), and
`gitlab_runner_assign.sh` (runner assignment). The Python core owns the
adapted API behavior. It carries forward exact-title lookup, open-issue reuse,
project enumeration, and independent read-back;
project-specific default hosts, groups, token aliases, and unrelated
deployment commands are excluded.

This is the delivery plan, not a live acceptance receipt. Each stage below is
an independently runnable block and review checkpoint. Code, tests, manifest,
result shape, and user instructions land with the capability.

## Agent invocation and target contract

The agent routes and first calls are in
[SKILL.md](../skills/k8s-admin-diagnostics/SKILL.md). Its generated `tools.json`
is the command catalog. The catalog and parser must accept the same
subcommands and options; `--describe` and `--help` work without credentials.
Agents call installed executables directly; no MCP server or separate provider
deployment is implied.

Target precedence is defined in the [README](../README.md#target-selection).
The access block supports a GitLab-only target profile and permits
`gitlab.project`, `gitlab.group`, and `gitlab.runner_id` in the selected file.
The three-authority diagnostic loader still requires all three tables.
Explicit `--project`, `--group`, or `--runner-id` overrides its matching target
field. A missing or conflicting identifier blocks. Neither the Git remote nor
the `glab` profile supplies a target fallback. The access check resolves a
project/group to a numeric ID;
all later API paths use that ID, never a context-sensitive placeholder. Live
results name the resolved origin, numeric IDs, and target source.

The GitLab-only check reuses effective-source precedence: declared token
file, named token environment variable, then the exact-host `glab` store.
The chosen token is loaded once into a controlled child environment; a store
credential that cannot be pinned noninteractively blocks. The live result
records the source reference and credential digest, never the value.
`GET /user` records the numeric identity; `GET /projects/:id` or group
read-back verifies the exact target. Apply binds one effective credential,
reads back the user and numeric target, then uses that session for the action.
The offline plan does not bind a user ID or credential digest. The existing
instance-admin and `runners/all` checks remain in the diagnostics route.
The operation gate does not precheck each action's API permission; an action
can still return an authorization error. Runner assignment reads the selected
runner when applied.

Mutating commands default to offline `--dry-run`: no GitLab API call, no file
write, and no access claim. The plan prints a SHA-256 fingerprint of the
selected target and desired input. `--apply --confirm-plan DIGEST --timeout
DURATION` must match that fingerprint, then re-resolve the credential and
target and read back the authenticated identity immediately before a write.
An unverified revision is reported but is not a security binding. Apply
rejects changed input or target. A verified no-op sends no write request.

The GitLab transport writes POST/PUT JSON to a mode-0600 temporary file passed
as `glab api --input PATH`. It pins `--hostname` and clears conflicting token
variables. The child runs in the caller's working directory. Capture is
bounded to 8 MiB while the child runs; failures expose a classified reason
instead of raw provider output. Safe reads retry a bounded number of times for
transient failures and honor a bounded `Retry-After`. Writes are sent once.
An uncertain write is reconciled by resource read-back before another create
can be attempted; a 401 or 403 is terminal.

## Delivery blocks

| Block | Agent action | Proof before merge |
| --- | --- | --- |
| 1 | `gitlab.access.check` | Exact host, user, target, source |
| 2 | `milestone.create/update/adjust-time` | GET exact milestone |
| 3 | `bug.create` | GET exact issue IID |
| 4 | `wiki.create/update` | GET exact page slug |
| 5 | `runner.assign` | GET membership for each project |
| 6 | `runner.create` | GET ID, description, scope; `sink_persisted` |

### 1. Access and packaging

`gitlab_access.py check` is the first call before a GitLab operation. The
installer, catalog, target/source/report modules, and package smoke remain in
one skill. The read-back proves exact host, numeric user and project/group
IDs, and effective source; it does not prove action-specific permission.
Wrong host, expired token, and wrong project block. An installed-package smoke
invokes every entrypoint from an unrelated directory, with no source-tree
`PYTHONPATH`, and renders normal and failed JSON/YAML results.

### 2. Milestones

`gitlab_milestone.py create` is for a requested new project milestone;
`update` changes a selected numeric ID; `adjust-time` calls the same update
core with only start/due dates. The input is a title for create, a milestone
ID for update, and optional description file, dates, or state as applicable.
The create lookup covers the selected project or group: one matching title
with identical desired fields is no-op; a differing or ambiguous match blocks
and routes the agent to update. Calendar-invalid dates block before apply.
Independent GET compares ID and requested title, description, dates, and state.

### 3. Bugs

`gitlab_issue.py create-bug` is called for a requested bug. `open-bug` is an
alias to the same implementation. It accepts a title, optional description
file, repeated caller-supplied `--label`, and optional numeric milestone ID.
Apply searches all issue states by exact title. One matching open issue with
the same requested fields is no-op; closed, multiple, or differing matches
block. After creation, GET compares the issue IID, open state, and requested
fields.

### 4. Wiki

`gitlab_wiki.py create` takes a title and content file; `update` takes an
existing slug and content file. Create lists pages by exact title, then reads
the matching page by its returned slug. Equal requested fields produce no-op;
a differing or ambiguous title match blocks. A new page is read back by the
slug returned from POST. Update reads its selected slug before PUT and returns
no-op for equal fields. Create does not check a would-be slug collision when
the existing page has a different title.

### 5. Runner assignment

`gitlab_runner.py assign` takes an existing numeric runner ID and a selected
project or group. Group mode enumerates all direct and subgroup projects with
pagination in a read-only `--live-plan`, then binds their sorted numeric IDs
into the plan digest. Apply re-enumerates the group and refuses a changed set
before a write. The offline plan has no group project set and cannot authorize
apply. Direct project association is read from runner details after assignment;
the project runner list includes inherited availability and cannot prove a
direct assignment. A repeat call skips already assigned projects. If a later
assignment fails, the command attempts to
remove only the assignments made by this call and reports cleanup read-back.

### 6. Runner creation

`gitlab_runner.py create` uses `POST /user/runners` for a requested new
runner record. The offline plan binds the selected project/group reference,
tags, description, and token destination; apply resolves the numeric target.
Any existing runner with that description blocks. The same `--token-out PATH`
is required on plan and apply to bind the destination in the plan digest. Apply
reserves an exclusive mode-0600 destination, posts once, writes the one-time
token, and reads
back runner ID, description, and scope membership. The token is not reported.
Tags and configuration are not compared. If a known newly created runner fails
token persistence or read-back, the command attempts to delete that record and
reports cleanup evidence. An uncertain POST or unresolved runner ID blocks
further creation until independent reconciliation. The result records
`sink_persisted`, not manager readiness. Registration and an online job remain
separate acceptance work.

For an uncertain create, a later complete empty list read clears the local
pending marker and blocks without another POST; a subsequent invocation may
retry the same plan. An existing runner with the description keeps the marker.
The operator must inspect the exact runner ID and scope in GitLab, then decide
whether to remove that record through the GitLab UI. A lost one-time token is
not recoverable from the record. After authorized removal, one apply clears the
marker and a further apply can create. This is a manual recovery step, not a
`gitlab_runner.py recover` subcommand.

The suggested API mapping follows GitLab's project
[milestones](https://docs.gitlab.com/api/milestones/),
[issues](https://docs.gitlab.com/api/issues/),
[wikis](https://docs.gitlab.com/api/wikis/), and
[runner assignment and creation](https://docs.gitlab.com/api/runners/)
contracts. The legacy registration-token `POST /runners` route is not the
creation target.

## One result contract

Extend the existing versioned result envelope rather than define a parallel
schema or exit-code authority. Its truly common fields are:

```text
schema_version, kind, status
```

The diagnostic branch preserves the current `schema_version: "1.0"` fields,
including plural `targets`, `surfaces`, and `credential_sources` in its access
receipt. The operations branch adds `captured_at`, `errors`, `operation`,
`plan_digest`, numeric verified target, `credential_source`, `identity`,
`skill.digest`, `records`, `readback`, and `cleanup`. Action-specific checks
require their own resource IDs and compared fields. JSON/YAML and human
output come from the existing renderer, with one redaction pass.

| Mode | Status | Exit | Meaning |
| --- | --- | --- | --- |
| Offline dry-run | `DRY_RUN` | 0 | Intent only; no API evidence |
| Group live-plan | `PLANNED` | 0 | Read-only projects and identity |
| Apply verified | `PASS` | 0 | `APPLIED` or `NO_OP` |
| Any unresolved step | `BLOCKED` | 2 | No success claim |
| Partial assignment | `PARTIAL` | 2 | Inspect errors and recover |

HTTP success, a returned ID, and an installed binary alone do not mean
`PASS`. Token values, raw response bodies, description/page content, and
credential-bearing files are excluded from shareable evidence; reports keep
IDs, selected fields, digests, and sanitized error classes. A controlled
failure emits a schema-valid JSON/YAML error envelope on stdout with a
nonzero exit status.

## Gates and proof per block

The existing GitHub `validate` workflow is the configured CI route. Each
changed block needs exact-head static checks, catalog/manifest/schema
validation, focused unit tests, and installed-package smoke. The installed
smoke runs all entrypoints from an unrelated directory and checks normal
JSON/YAML, structured failures, and runtime dependencies. If Bash is added,
Bash parse, ShellCheck, shfmt, and Bats must execute in that workflow.
Acceptance requires tests for offline dry-run with zero API calls and
plan/apply input binding; permission denial; terminal 401/403;
408/429/5xx classification; malformed responses; redaction; timeout cleanup;
read-back mismatch; and second-call no-op. Same-host create locking and
post-timeout read-back need exact-head test evidence; cross-host create
serialization remains a limitation.

Stages 2–6 also require protected live smoke against a named disposable
GitLab target, from a named execution host that can reach that instance. A
sanitized receipt binds the exact skill digest, origin and
numeric target IDs, effective source and user ID, plan digest, apply result,
independent GET, repeated no-op, and cleanup. The existing acceptance checker
requires operation receipt profiles alongside the diagnostic receipt; an
empty or incomplete profile inventory blocks. The current expectations file
has no disposable GitLab target or operation receipts, so `validate` remains
blocked until they are supplied. Mocked unit tests alone cannot accept stages
2–6. No production resource is changed merely to satisfy smoke.

## Remaining live acceptance inputs

1. **Token sink for runner creation:** the command takes `--token-out PATH` and
   saves the one-time token with mode 0600. A live smoke still needs the
   operator's selected destination and manager-registration owner.
2. **Live smoke route:** name a disposable project/group and execution host
   for stages 2–6, then record exact-digest receipts from that host. A saved
   login does not identify that target or supply an approved mutation route.
