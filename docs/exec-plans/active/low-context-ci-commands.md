# Make installed CI commands discoverable and quiet

Maintain this ExecPlan under [docs/PLANS.md](../../PLANS.md). It covers the four public command families
`ci-api`, `ci-mr`, `ci-pipeline` and `ci-job`. The last three names are proposed launchers.
Follow [CI02][cli], [CI09][ref] and [CI11][tools] for command, reference and callback behavior.

## Purpose / Big Picture

From a consumer repository, a caller should be able to ask what a command does, find the required inputs, and run
one bounded operation. First, make help work with an unconfigured GitLab client and without the Kubernetes Python
package. Then make it possible to inspect five merge requests (MRs), request just their SQL changes, distinguish
runner wait from execution, and read a failed job's trace without a shell pipeline. A trace is a job's text log.
A successful machine read should spend its output budget on the requested data.

## Progress

- [x] BASE. Mapped the reported calls to source at `fd757b83bbe3e664216b145446f37615f562c536` and existing issues.
- [ ] H1. Reproduce the installed help failure, establish the discovery contract and deliver offline help.
- [ ] H2. Deliver quiet machine reads, actionable errors and the target migration procedure.
- [ ] H3. Deliver packaged commands and native client lookup in a non-interactive shell.
- [ ] H4. Deliver callback-backed job and pipeline reads with their existing output and exit behavior.
- [ ] H5. Deliver compact MR listing and filtered diffs.
- [ ] H6. Deliver per-job queue visibility and failed-job trace retrieval.
- [ ] H7. Deliver a head-checked MR merge using the existing confirmation and read-back workflow.
- [ ] H8. Finish the short skill guide and replay the consumer tasks against the installed candidate.

## Surprises & Discoveries

The reported `gitlab_mr.py --help` traceback came from installed revision `12ddee80`. The same imports exist at
the baseline. The MR parser imports `gitlab_actions`, which imports `runner_options`, `runner` and `podman_runtime`.
The last module imports `kubernetes.utils.quantity` before help can run. [Issue 139][i139] records the pipeline case.

`ci-api -h --json` prints human help. `ci-api --json` with no verb writes generic usage to stderr and exits 64.
`core/read_cli.py` discards the known missing-argument reason. To locate the original inventory entry, run
`git show 3c41eed:docs/plans/2026-10-06/ci11-inventories/core-libs.md`. It records
`--help --json` and `schemas/cli-help-v1.schema.json` for the source CLI, including the Helm push tool. CI02 assigns
`--describe` to the full command contract. The historical inventory names a schema absent from this checkout.

The API can already read a plain-text trace with `--output text --lines 2:2`. `tests/bash/api.bats` contains that
case. The 1 MiB provider-response cap applies before line slicing. For larger logs the job reader already streams a
bounded, redacted tail. MR creation with a description file and milestone update already have callbacks. The top-level
`pyproject.toml` configures repository checks. The installer copies the skill and `bin/_bootstrap.py` adjusts imports.

The baseline skill guide is 418 lines and 23,958 bytes. Consumers reported about 80% log boilerplate across six GETs,
15–20 seconds to inspect four or five MRs, and one 900-second watch timeout. Those figures describe the incident.
The [context-load study][study] measured a separate workload and found that wrong command guesses cost extra calls.

## Decision Log

- DEC-01, existing contract. Discovery reads the catalog offline. A live verb builds a typed action and runs
  provider callbacks through `WorkflowExecutor`. A callback owns one operation and consumes the previous result it
  needs. The executor handles failure and reverse cleanup. Preserve CI11's version 3 reference node and CI02's
  full `--describe` record. Governing sources are [CI11][tools] and [the command schema][command-schema].
- DEC-02, proposed interface. `-h --json` requests machine help. Bare `ci-api --json` remains a usage error and
  returns a short machine-readable explanation. This preserves exit 64 for scripts that omitted required inputs.
  Put the runnable example first in human help. Keep every accepted option and its mode restrictions discoverable.
- DEC-03, proposed logging change. Make successful JSON reads quiet by default. Still expose target-selection and
  deprecation events at an explicitly selected level. Preserve native values on successful API GETs. The relevant
  existing contracts are [target selection][targets] and the `bin/ci-api` entry in the catalog.
- DEC-04, delivery sequence. Review exact changes to contracts and JSON before implementing them. H1 can fix the
  proven import failure independently of new machine-help fields. H2 and H3 can follow independently. Land H4
  before composing job reads for H6. Land H5's exact-head reader before H7. Use one runnable change per PR.

## Outcomes & Retrospective

The baseline audit and delivery plan are available. Runtime fixes, installed-candidate measurements, remote checks
and live consumer receipts are pending. Update this section with the candidate revision and each observed outcome.

## Context and Orientation

Repository-root `bin/ci-api` forwards to `ci-skills/bin/ci-api`. The shipped Python entrypoints for the other three
families are `ci-skills/bin/gitlab_mr.py`, `ci-skills/bin/gitlab_pipeline.py` and `ci-skills/bin/gitlab_job.py`.
Below, `core/` denotes `ci-skills/lib/python/core/`. `core/catalog.py` declares commands, `core/help.py` renders
human help, and `tools/render_manifest.py` generates `ci-skills/tools.json`. `ci-skills/bin/reference.py` offers one
node of related tools and reading pointers. `core/target.py` resolves the selected authority and credential source.

A plan digest binds the inputs a caller reviewed to the operation they confirm. An MR IID is its project-local
number. Its head SHA is the exact source commit. A provider read-back independently queries the identified object
after a write. The [contribution guide][contribution] maps these concepts to existing code.

## Plan of Work

### H1. Make offline help the first installed delivery

Capture separate stdout, stderr and exit status on the old install for `gitlab_mr.py --help`, `ci-api --json`,
`ci-api -h --json` and `ci-api --json -h`. Use a supported base interpreter without the Kubernetes package,
credentials or network. First establish the accepted records and caller-selected target fixture. Read the exact
historical help schema and a conforming example through the source listed in the port inventory before claiming
compatibility. If the original is unavailable, present the proposed compact record in Concrete Steps for review
under its own schema identity. The independent import fix has its own before/after check and can ship first.

The first PR addresses [#139][i139]. Isolate lightweight argument declarations and defer workflow/provider imports until
an operational verb needs them. Fix the transitive `REGISTRATION_SECRET_SUFFIX` dependency at its owning module, or
defer its consumer, so shared parser options can load without Podman. Exercise both MR and pipeline discovery.

The second PR handles help intent in the shared parser before argparse's human help action exits. Read raw arguments
only to identify help and output format. Treat `-h` and `--help` identically in either flag order. A command with a
selected verb should show its required arguments, its own options and one runnable example. `--describe` keeps
its full record. Teach `ReadArgumentParser.error` to identify missing input names without echoing argument values.
The four families currently declare nine verbs, none with a per-verb optional-argument map. Record each verb’s
options and mode restrictions in `core/catalog.py`, including the CI09 `watch` options. Derive the help from those
declarations. Use each example as a parser test and check option membership against the parser for every verb.

### H2. Keep successful reads quiet and make failures diagnosable

Resolve the effective log level once in `core/logging.py`. Give explicit CLI settings precedence over the environment,
then the effective output-mode default. `-q/--quiet` selects error-only stderr. Explicit conflicting CLI settings
produce a usage error. With explicit `--log-file`, preserve the requested diagnostic detail there. Dry runs retain
their current persistence rules. Preserve the existing structured success and deprecation events for explicit
diagnostic use. One failed machine read emits a bounded classified diagnostic. Distinguish an absent executable,
DNS resolution failure, timeout, HTTP 401 and 403, a demonstrated network denial and an unknown provider failure.
Reuse `runtime.error_class` and `cli_api._failure`. Treat unclassified transport errors as such.

Publish a redacted migration example using `target.toml.template`. Match the active target by existing precedence
before changing it. For the reported private default, rename the legacy table to `[gitlab_private]` and, if an
explicit default is wanted, set `default = "gitlab_private"` under `[gitlab_routing]`. Preserve relative token
paths, any public instance and Kubernetes/Harbor routes. The old and new shapes must select the same private
authority and credential reference without `--gitlab private`. An actual personal config edit is a separate
caller-selected operation with a backup. The installer leaves consumer target files under their owners' control.

### H3. Install four direct commands and resolve their native clients

Add distribution metadata and four `[project.scripts]` entries to `pyproject.toml`. Use importable CLI functions
for the implementations. Use the prepared interpreter and base dependencies. Stage a revision-specific wheel runtime
and its copied skill data together for each installation scope. An editable install remains a development option.
Both the short names and legacy adapters resolve the same active bundle. Remove their sibling `_bootstrap` import.

Let `core/paths.py` locate the bundle’s skill tree from the installed distribution, independent of the caller’s
working directory. `core/schema.py` and `core/navigate_references.py` use that root. Bind the installed wheel contents
and copied data to the same revision in `core/provenance.py` and the installer’s `INSTALLATION.json`. Stage and
verify both before one switch of the active revision. Keep the previous bundle until recovery succeeds. A second
installation scope can keep its own revision even when both scopes use the same base interpreter.

Extend `tools/install_ci_skills.py` to plan optional launchers in a caller-selected `--bin-dir` such as
`~/.local/bin`. Bind their destination, interpreter and installed revision to its existing plan/confirmation.
Prefer an explicit `CI_SKILLS_PYTHON`, then the prepared project environment. Verify Python 3.11+ and the supported
base dependencies before installing. Bind the launcher to that interpreter so an older system Python earlier in
`PATH` cannot take over. Legacy paths must reach the same interpreter before importing modern Python modules.
For standalone installations without a prepared environment, check Homebrew Python, then versioned `python3.12` and
`python3.11` on PATH. Verify its version and base imports at install time. For an invalid explicit interpreter,
report how to correct it. A launcher collision identifies the occupied path.

Resolve `glab`, `gh`, `kubectl`, `oc` and `skopeo` once through the shared command runtime, when a selected operation
needs them. Honor the caller's PATH first. On macOS, check executable files under `/opt/homebrew/bin` and then
`/usr/local/bin` if PATH misses. Keep the Linux lookup unchanged. Use the resolved executable in the actual child
process. Four commands should work directly from a consumer directory without shell prefixes, pipe scripts, or
interpreter activation. Sandbox authorization still belongs to the calling environment.

### H4. Bring the existing reads under the callback executor

Move the orchestration in `gitlab_pipeline.py:_get` and `core/gitlab/cli.py:execute_gitlab_job` and
`execute_gitlab_job_list` into the existing GitLab authentication and domain callback sequence. Keep
`read_pipeline`, `collect_gitlab_job` and `list_jobs` as the implementations. Use the session established by
`GitLabAuth` and the exact project identity it verified. Preserve bounded reads, partial results, redaction,
receipt eligibility and exit codes. Keep the existing API, MR create/update and pipeline start/watch callbacks.

### H5. Return compact MR status and only the requested diffs

Extend `core/gitlab/gitlab_merge_requests.py`. `ci-mr list --state opened --with-pipeline` lists one bounded page
window and enriches only the selected IIDs. Use bounded concurrency, preserve stable order and distinguish
unavailable detail from a verified empty pipeline. Return a pipeline status only when its SHA matches the MR’s
source head. Use the newest matching pipeline when selection is needed. Bound additional requests and mark coverage
incomplete when the page or request limit prevents verification.
`ci-mr diff IID --path '*.sql'` matches either side of a rename before rendering. Share the existing target,
authentication, pagination and response limits. Summarize omissions and upstream truncation even when the matching
patch cannot be retrieved. Keep [#134][i134]'s broader incremental evidence work separately identifiable.

### H6. Explain pending jobs and retrieve failed-job evidence

Extend `WatchJob` and the existing graph poll to retain the job's status, assigned runner and available timing.
Show the first pending observation and subsequent status or runner changes once. Reuse the existing stderr progress
sink. For machine callers, `--json --progress` opts into bounded JSON Lines on stderr while stdout contains one final
report. Plain `--json` and `--quiet` suppress progress. Report timeout counts and a bounded list of unsettled jobs.
Prefer provider queue duration when present.
For a job already observed pending, track elapsed time since that observation and label the source. Created, manual
and dependency-waiting jobs retain their own status. Unknown queue time or runner identity stays unknown.

In a second PR expose `ci-pipeline failures --pipeline-id ID` and `ci-job logs --job-id ID`. Use failed-job
identities from the bounded graph. Fetch excerpts only for selected failed jobs when requested, using
`core/gitlab/job_read.py`'s redacted tail. Declare partial or truncated evidence by job. Keep generic API text
reads available through `--output text`. Offer `--raw` as an alias to this explicit text mode. Preserve JSON
validation, the 1 MiB cap and the 200-line slice limit. The focused log command handles the larger-trace case.
These reads deliver the GitLab trace excerpt. [CI11 C4][tools] still owns the full MR `check` and event correlation.

### H7. Merge one verified head through the existing MR workflow

Expose existing `create` and milestone `update` under `ci-mr`. For `merge`, the caller supplies the IID, expected
source SHA and target branch. Bind all three and the resolved GitLab authority/project to the confirmation digest.
Immediately before the write, read back the MR and compare both head and destination. A mismatch requires a fresh
plan and sends zero merge writes. GitLab’s [merge API][merge-api] guards only the source SHA atomically. A retarget
between the final read and the write remains possible. Independently verify the merged MR, source SHA and actual
target branch afterward. Report a mismatch, deferred merge or ambiguous result explicitly and reconcile before retry.

### H8. Shorten the common path and measure it end to end

In `ci-skills/SKILL.md` lead with the four installed names, one target-selection paragraph, exit/status semantics and
the focused navigator. Move detailed workflows into existing `ci-skills/references/` nodes and retain reachability.
Update each command's examples as soon as it ships. [Issue 150][i150] proposes roughly 120 lines for the final
guide. Measure bytes, calls and wrong guesses as well as line count. Compare the six-read batch, five-MR listing,
filtered SQL diff, pending watch and failed-job trace from the same consumer setup before and after.

## Concrete Steps

Use the repository root in the existing GitHub validation environment. For installed-consumer checks change to an
unrelated repository, clear source-tree imports, and run the installed command. A fake native executable used for an
offline failure case must record zero invocations during help, description and dry-run discovery.

### Implementation and interface changes for review

Paths beginning with `core/` have the expansion given in Context and Orientation. The proposed field lists below
define the review surface. Compare each with the shipped schema and submit the exact versioned contract diff before
changing a published record. An optional field added to a closed record still requires compatibility review.

**REFACTOR H1.** `ci-skills/bin/gitlab_mr.py`, `ci-skills/bin/gitlab_pipeline.py`,
`core/gitlab/gitlab_actions.py`, `core/runner_options.py`. Parse help before importing operational
factories. Import Kubernetes only on the path that needs it.

**MODIFIED H1.** `core/cli.py:MachineArgumentParser`, `core/read_cli.py:ReadArgumentParser`,
`core/help.py:CommandHelp`, `core/catalog.py`. One declaration supplies human help, machine help, accepted
options and examples. Existing `--describe` validates against `schemas/command-contract.schema.json`.

**INTRODUCED H1.** Compact `--help --json` projection, emitted by `core/help.py`. Prefer the recovered source
shape if approved. For a new shape propose `schema_version:string`, `command:string` and `verbs:array`. Each
verb has `name`, `purpose`, `usage`, `example` strings, `required_options:string[]`,
`options:map<string,string>` and `mutates:boolean`. Use `schemas/command-help.schema.json` only for that
distinct approved shape.

**MODIFIED H1/H2.** `core/read_cli.py`, `core/read_tools.py`, `core/runtime.py`, `core/cli_api.py`,
`core/logging.py`. API usage failure with `--json` writes one stdout record with `status="BLOCKED"`,
`error_class="invalid_arguments"`, `missing:string[]`, `usage:string` and exit 64. A provider failure keeps
stdout free of partial provider data and writes one diagnostic to stderr with `error_class:string` and a
bounded sanitized `detail:string`, serialized as JSON for machine callers.

**MODIFIED H3.** `pyproject.toml`, `install.sh`, `tools/install_ci_skills.py`, `ci-skills/bin/`. Console
names resolve to one installed distribution. Map `ci-api` to `lib.python.core.read_cli:api_main`. Move the
three domain mains to `core/gitlab/entrypoints.py` as `mr_main`, `pipeline_main` and `job_main`, each
accepting `argv=None` and returning the existing exit code. Old and new launchers delegate to those functions.
Include package data and verify discovery after a wheel install outside the checkout.

**REFACTOR H4.** `ci-skills/bin/gitlab_pipeline.py`, `core/gitlab/cli.py`, `core/gitlab/gitlab_pipelines.py`,
`core/gitlab/job_read.py`, `core/gitlab/gitlab_jobs.py`.
Compose `GitLabAuth` → one `GitlabCallback.run()`
for pipeline get, job get or job list. Retain output and independent access read-back. Compare the same
fixtures before and after.

**INTRODUCED H5.** `core/gitlab/gitlab_merge_requests.py`, `core/catalog.py`, proposed
`ci-skills/schemas/results/gitlab-mr-read.schema.json`. List records carry `iid:positive integer`,
`title:string`, `head_sha:string or null`, `detailed_merge_status:string or null` and nullable
`head_pipeline` with positive `id`, verified `sha` and `status:string`. Diff records carry `old_path`,
`new_path`, `patch` strings. Carry `truncated:boolean` and per-IID/file errors in the enclosing result.

**MODIFIED H6.** `core/gitlab/gitlab_pipeline_graph.py`, `gitlab_pipeline_watch_types.py` and
`gitlab_pipeline_watch_runtime.py` in the same directory,
`ci-skills/schemas/results/gitlab-pipeline-watch.schema.json`. Retain existing totals. For each retained
unsettled job expose `id`, `name`, `status`, nullable positive `runner_id`, nullable nonnegative
`queue_seconds` and `run_seconds`, and `queue_time_source` of `provider`, `observed_pending` or `unknown`.
Report truncation explicitly.

**INTRODUCED H6.** `core/gitlab/job_read.py`, `core/gitlab/gitlab_pipeline_graph.py`, `core/catalog.py`,
proposed `ci-skills/schemas/results/gitlab-failure-summary.schema.json`. Failures return job `id`, `name`,
`status`, nullable `failure_reason` and optional bounded `trace_tail:string` with truncation or a per-job
error. `--trace-lines N` requests excerpts. Zero trace fetches without it. `ci-job logs` uses the same bounded
tail.

**MODIFIED H7.** `core/gitlab/gitlab_merge_requests.py`, `core/gitlab/gitlab_action.py`, `core/catalog.py`
and, if required, `ci-skills/schemas/results/gitlab-action.schema.json`. `merge` requires a positive IID,
full source SHA and target branch. Read-back identifies `iid`, `state`, `source_sha`, `target_branch` and
nullable `merge_commit_sha`. A deferred/unknown state cannot produce a verified merge.

**MODIFIED H8.** `ci-skills/SKILL.md`, `core/catalog.py` and the affected files in `ci-skills/references/`.
Regenerate `ci-skills/tools.json` from the catalog. One node offers the relevant installed command and
immediate reading pointers.

The successful API `get` value remains native JSON or explicitly selected text. For `--raw`/`--output text`,
preserve line numbering with `--lines`. Reject contradictory JSON/text or raw-pagination selections. An invalid
JSON endpoint response still exits 65. For diagnostics, keep established classes where they distinguish causes,
including `missing_tool` with detail `glab` and `authentication` with detail `HTTP 401`. Classify DNS or policy denial
only from positive evidence. Never echo tokens, response bodies, arbitrary stderr or full environments.

### Variables

| Variable Name | Type | Default / Required | Source / Scope | Description & Invariants |
| --- | --- | --- | --- | --- |
| `CI_SKILLS_LOG_LEVEL` | Enum | Mode default | CLI environment | `debug`, `info`, `warning`, `error`. CLI wins. |
| `CI_SKILLS_PYTHON` | Path | Prepared interpreter | Installer | Explicit value must pass base import/version checks. |
| `PATH` | Path list | Caller-supplied | Process | Search native clients here first. |
| `CI_SKILLS_TARGET` | Path | Optional | Target resolver | Preserve existing selection precedence. |
| `CI_SOURCE_SHA` | SHA | Required | GitHub workflow | Exact candidate revision. |

No credential variable is introduced. Use the existing configured target for live reads and an explicitly selected
disposable project and MR for write acceptance. The example variables select the fixture objects and reviewed digest.

| Variable Name | Type | Default / Required | Source / Scope | Description & Invariants |
| --- | --- | --- | --- | --- |
| `PROJECT` | Positive ID | Required | Fixture project | GitLab project ID for the API and workflow examples. |
| `PIPELINE_ID`, `JOB_ID`, `IID` | Positive IDs | Per verb | GitLab read-back | Pipeline, job and project-local MR. |
| `HEAD_SHA`, `TARGET_BRANCH`, `DIGEST` | Strings | Merge only | MR and plan | Source SHA, destination, confirmation. |

### CI pipelines and jobs

All rows use [.github/workflows/validate.yml][workflow].

| Pipeline / Job Name | Type / Trigger | Stage / Order | File Path | Required Inputs & Artifacts |
| --- | --- | --- | --- | --- |
| `validate` | PR, push, schedule, manual | Parallel | [Workflow][workflow] | Candidate, schema and test results. |
| `gate-ci-skills-endpoints` | Same | Parallel | [Workflow][workflow] | Endpoint inventory and evidence. |
| `test-install` | Same | Parallel | [Workflow][workflow] | Installed package and installer results. |
| `required-result` | Same | After all three | [Workflow][workflow] | Exact-head `ci-skills/required` status. |

### Verification gates

Use the [repository gate table][gates] and its existing owners. The gate table currently lives in
`docs/plans/index.md`. The `docs/gates.md` location mentioned in the plan template is absent at this baseline.

| Gate ID | Gate Type / Class | Pass Condition | Fail-Closed Condition | Producer / Owner |
| --- | --- | --- | --- | --- |
| `validate` | Static/behavior | Catalog, schemas and tests pass | Drift, failure, missing evidence | [CI][workflow] |
| `test-install` | Installation | External invocation succeeds | Wrong revision or missing proof | [Install][install] |
| `gate-ci-skills-endpoints` | Endpoint | Valid authority and evidence | Invalid or missing evidence | [Gate][ep] |
| `validate.live_acceptance` | Receipts | Selected operations verified | Invalid or missing receipt | [Checker][ac] |
| `ci-skills/required` | GitHub status | Exact head passes | Failed, skipped or mismatched evidence | [Result][res] |

At baseline `inventory/ci/live-acceptance.json` requires access, mirror and agent operations. Its green status cannot
prove MR, watch, trace or merge acceptance. `test-install` owns offline discovery and upgrade recovery. `validate` owns
parser, callback and stream assertions. For H5–H7, each capability PR supplies installed-candidate evidence for its
claimed live action on the selected fixture. H7 records the intended head and branch before the write and reads both
back after merge. Cite the receipt and its exact revision separately unless that operation is selected by the inventory.

### Commands and expected observations

In the GitHub validation environment, use the existing `ci-skills` Conda environment. These focused selections guide
before/after comparison. The existing required workflow still runs its full selection and lint checks.

    python tools/render_manifest.py --check
    python -W error -m pytest -q tests/python/test_cli_contract.py tests/python/test_catalog.py tests/python/test_propositions.py
    python -W error -m pytest -q tests/python/test_installed_package.py tests/python/test_installer.py tests/python/test_target_profiles.py
    python -W error -m pytest -q tests/python/test_callbacks.py tests/python/test_gitlab_actions.py tests/python/test_gitlab_transport_retry.py
    python -W error -m pytest -q tests/python/test_gitlab_job_cli.py tests/python/test_gitlab_jobs.py tests/python/test_gitlab_pipeline.py tests/python/test_gitlab_pipeline_watch.py
    bats tests/bash/api.bats tests/bash/install.bats

In an installed consumer directory replay these calls as the corresponding milestones land. Quote the glob so the
consumer's shell passes it intact. Supply the selected fixture values in place of shell variables.

    ci-api -h --json
    ci-api --json -h
    ci-api --json
    ci-mr --help
    ci-pipeline watch --help
    ci-job --describe
    ci-api get --provider gitlab --endpoint "projects/$PROJECT/jobs/$JOB_ID/trace" --output text --lines 1:40
    ci-mr list --project "$PROJECT" --state opened --with-pipeline --json
    ci-mr diff "$IID" --project "$PROJECT" --path '*.sql' --json
    ci-pipeline watch --project "$PROJECT" --pipeline-id "$PIPELINE_ID" --timeout 30 --json --progress
    ci-pipeline failures --project "$PROJECT" --pipeline-id "$PIPELINE_ID" --trace-lines 20 --json
    ci-job logs --project "$PROJECT" --job-id "$JOB_ID" --trace-lines 20
    ci-mr merge "$IID" --project "$PROJECT" --sha "$HEAD_SHA" --target-branch "$TARGET_BRANCH" --dry-run --json

Use a numeric project ID for the raw API endpoint above. The high-level commands accept the existing project selector.
For the H7 write fixture alone, repeat the planned merge with `--apply --confirm-plan "$DIGEST"` in place of
`--dry-run`. Use the candidate's installed help to verify the mode. Read back the IID, source SHA, target branch
and merged state.

## Validation and Acceptance

For H1, add an installed subprocess case in `tests/python/test_installed_package.py` under `test-install`.
The job’s Conda environment normally includes Kubernetes. In the subprocess, make that import unavailable and prove
the restriction with an import probe. From an unrelated directory with no credentials or network, show the old MR
help failing and the candidate passing under identical conditions. Run `--help` and `--describe` for each family.
Both JSON-help flag orders decode to the same record and write zero stderr bytes. The
invalid `ci-api --json` case exits 64 and names the missing verb/provider in JSON. Before H3 use the installed
legacy paths. After H3 repeat with the short names. Validate `--describe` against the existing command schema and
navigate one version 3 reference node. Make sure `watch` advertises its own timeout, interval and related-name flags.

For H2/H3, six successful GETs produce six independently parseable values and zero stderr bytes with default JSON
settings. Explicit info restores selection evidence. Warning restores the legacy notice. Test missing `glab`, DNS,
HTTP 401/403 and a tool error containing a sentinel secret. Check the expected exit class, diagnostic and absence of
the sentinel. Repeat installation from an unrelated directory with an older Python first on PATH, Conda inactive
and native clients initially outside PATH. Verify each launcher, installation identity and unmodified caller config.
Interrupt the A→B upgrade before and after activation. Recover, then load one schema and reference with both launcher
forms and verify their installed digests. A second scope pinned to A must still load A.

For H4/H5, compare pre/post job and pipeline results on identical deterministic fixtures, excluding independently
changing timestamps. Include success, partial, blocked and dry run. Record callback order, reuse of the authorized
session, and zero dependent reads after failed authentication. Apply this assertion to every new live verb in H5–H7,
including merge, which must send zero writes on auth failure. Verify each new verb’s dry run makes zero provider
calls.
For five MRs measure calls and wall time on identical inputs.
Plain listing issues zero detail reads. Enrichment fetches at most one MR detail per selected IID per pass.
Count any paged exact-head lookups separately from retries and access checks. Include an older matching failed pipeline
and a newer unrelated green one. Return the matching SHA and status. Also exercise missing and unverified pipelines.
A SQL patch late in a large diff must survive filtering. Flag a provider-omitted hunk as incomplete.

For H6/H7, test pending → running → settled, pending through timeout, missing timing, and mixed manual/running jobs.
For a pending-only watch, capture its first machine progress event before timeout. Observe the transition to running
in the second fixture. Unchanged polls add zero lines. Plain JSON stays quiet and returns its expected exit status.
For failures, request two selected tails including a large trace and verify bounded, redacted output and per-job
errors. For merge, retarget an MR between plan and apply while keeping its IID and head fixed. Assert zero merge writes.
Also exercise stale SHA, unmet provider requirements, success, a lost write response and a changed branch at read-back.
Only the verified merged head and branch yield repeat success with zero extra mutations.

For H8, rerun the consumer tasks with identical selectors and record skill bytes loaded, stdout/stderr bytes, provider
requests, elapsed time, wrong commands and whether the requested hunk/log line arrived. Separate cold and warm runs.
Require six parseable GET results and zero default stderr bytes. For each SQL and trace replay, retain the requested
hunk or line. Count total skill, help and tool-output bytes through delivery. Claim a context saving only when those
bytes fall and wrong calls do not increase on the same task. Record a trade-off otherwise. Two seconds for MR listing
is a measurement target. Store short redacted receipts for the exact candidate.

Budget the replay for one disposable project, five MRs and one pipeline with pending, running and failed jobs.
A 30-second timeout fixture exercises the reported 900-second case. Retain the existing 1 MiB API response limit,
200-line text window and watch graph caps. For H5/H6, begin with four concurrent detail reads, 20 returned MRs,
20 lines per requested excerpt and a shared 16 KiB inline patch/trace budget. Keep invocation limits
caller-selectable within the published bounds.
Expose omission counts or a narrower next selection. Record measurements before adopting new defaults.

## Idempotence and Recovery

Repeated discovery and reads leave the target untouched. Honor existing installer fingerprint, upgrade backup and
recovery behavior. Restore only launchers owned by the failed install. An explicit target migration compares
sanitized routing and credential references before/after, restores its backup on failure and becomes a no-op on
repeat. Keep credentials and raw traces out of receipts. For an ambiguous merge response, query the
same project, IID, head and target branch before retry. A changed head or destination requires a fresh plan.

## Artifacts and Notes

Keep the initial stderr/exit and final result side by side for each failing invocation. For example, the
pre-change MR evidence ends in `ModuleNotFoundError: No module named 'kubernetes'`. Replace full transcripts
with the installed revision, command, exit, stream sizes, request count and the bounded observed fact.
Record old/new catalog or schema diffs in their owning capability PR.

## Interfaces and Dependencies

All four families use shared `--gitlab private|public` for GitLab selection, `--json` for machine output,
`--quiet` for diagnostics and `--dry-run` for offline planning according to each verb's declared modes.
`ci-api` retains `--provider github|gitlab` and native success values. Use the existing `glab`/`gh` transport,
authenticated project resolution, `GitlabCallback`, `WorkflowExecutor`, catalog help, report renderer and
installer. Implement each new read once in the domain library. Branch protection and merge policy are enforced
by the selected GitLab project.

## Out of Scope

Broader CI09 tool compatibility inventory, Helm deployment changes, fleet-wide config rewrites, generic API
writes, replacement CI jobs and automatic sandbox approvals remain separate work. [Issue 78][i78] owns MR create
history. Verify existing behavior before filing a duplicate. [Issue 134][i134] also covers approvals, artifacts,
cleanup evidence and incremental refresh beyond the bounded list/diff scope here.

Revision BASE recorded the consumer reports and source baseline. The November 25 review added the exact-head,
destination, install-recovery and offline-help cases, observable queue progress, proof owners and replay criteria.

[cli]: ../../phases/CI02-CLI.md
[ref]: ../../phases/CI09-REFERENCE.md
[tools]: ../../phases/CI11-TOOLS.md
[study]: ../../2026-11-21/consumer-context-load.md
[targets]: gitlab-private-public-targets.md
[contribution]: ../../contribution_guide.md
[command-schema]: ../../../schemas/command-contract.schema.json
[workflow]: ../../../.github/workflows/validate.yml
[gates]: ../../plans/index.md#repository-gates
[install]: ../../../tests/python/test_installed_package.py
[ep]: ../../../gates/gate-ci-skills-endpoints.py
[ac]: ../../../tools/check_live_acceptance.py
[res]: ../../../tools/ci_required_result.py
[merge-api]: https://docs.gitlab.com/api/merge_requests/#merge-a-merge-request
[i78]: https://github.com/spyroot/ci-skills/issues/78
[i134]: https://github.com/spyroot/ci-skills/issues/134
[i139]: https://github.com/spyroot/ci-skills/issues/139
[i150]: https://github.com/spyroot/ci-skills/issues/150
