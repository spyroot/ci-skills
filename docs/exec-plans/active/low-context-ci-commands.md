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
- [x] REPLAY. Recorded a paired consumer-guide trial on installed revision `12ddee80`.
  See [replay results](#paired-consumer-replay).
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
  Follow the pinned [help standard][help-standard]. Human help opens with a summary and optional description, then
  shows explained examples, options, output modes and usage. Keep accepted options and their restrictions discoverable.
- DEC-03, proposed logging change. Make successful JSON reads quiet by default. Still expose target-selection and
  deprecation events at an explicitly selected level. Preserve native values on successful API GETs. The relevant
  existing contracts are [target selection][targets] and the `bin/ci-api` entry in the catalog.
- DEC-04, delivery sequence. Review exact changes to contracts and JSON before implementing them. H1 can fix the
  proven import failure independently of new machine-help fields. H2 and H3 can follow independently. Land H4
  before composing job reads for H6. Land H5's exact-head reader before H7. Use one runnable change per PR.

## Outcomes & Retrospective

The [paired consumer replay](#paired-consumer-replay) measures the effect of revising the guide on the installed
baseline. The candidate still needs the runtime changes, the same consumer replay, remote checks and live receipts.

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
compatibility. Compare the recovered example with the pinned six-field help requirement. Record any incompatibility.
If the original is unavailable, present the compact record below for review under its own schema identity.
The independent import fix has its own before/after check and can ship first.

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
patch cannot be retrieved. Identify the base and source SHA of the diff returned. Optional `--sha` pins the source
seen in the MR list. If the requested revision is unavailable, return `head_changed` and omit the replacement patch.
Keep [#134][i134]'s broader incremental evidence work separately identifiable.

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

Make the first screen of `ci-skills/SKILL.md` answer “what can I do?” with one task and one installed invocation per
row. Put common target selection and exit conventions below the index. Link to verb help and one focused reference
for optional detail. Use the release examples below only when the corresponding launcher and verb ship. Keep older
installations on their working commands. [Issue 150][i150] proposes roughly 120 lines for the final guide. Judge
it by whether a new consumer can obtain the requested evidence and how much context it uses along the way.

### Consumer tasks and the first command to show

These are tasks the consumer agent brought back from real repositories. Put the task and one runnable example
together at the top of `SKILL.md` as its launcher ships. Substitute the caller's project and object IDs.
Keep the shared resolver, interpreter setup and reference links in one place. The case names also identify the replays.

**List open MRs (H5).** The list omitted detailed merge status and head pipeline. Four or five MRs cost one list call
plus one sequential detail call per MR, taking 15–20 seconds. Show one row per IID with its verified pipeline SHA.

    ci-mr list --project "$PROJECT" --state opened --with-pipeline --json

**Review a SQL patch (H5).** On a large MR, unrelated patches filled the tool output before the SQL hunk arrived.
Filter before rendering. Return the diff's base and source SHAs and its completeness. A caller may bind the source
SHA seen in the MR list. If that revision is unavailable, return `head_changed` with zero replacement hunks.

    ci-mr diff "$IID" --project "$PROJECT" --path '*.sql' --sha "$HEAD_SHA" --json

**Open an MR (H3/H7).** The agent tried `ci-api post`, got exit 64 and wrote an HTTP client. Manual token-file
parsing failed twice on file location or format. Lead with the existing create workflow under its installed name.

    ci-mr create --project "$PROJECT" --source-branch "$SRC" --target-branch "$DST" --title "$TITLE" --dry-run --json

**Merge a reviewed head (H7).** The consumer also fell back to an HTTP client to merge. A short SHA failed in
one recorded attempt. Show the offline plan with the full source SHA and intended branch. It returns the digest
for a separately confirmed apply. The generic API reader stays read-only.

    ci-mr merge "$IID" --project "$PROJECT" --sha "$HEAD_SHA" --target-branch "$DST" --dry-run --json

**Watch a queued pipeline (H6).** The agent waited 900 seconds while the pipeline stayed pending. It needed the
first per-job queue observation while still watching, followed by status or runner changes. Supply the caller's limit.

    ci-pipeline watch --project "$PROJECT" --pipeline-id "$PIPELINE_ID" --timeout "$SECONDS_LIMIT" --json --progress

**Find a failed job and read its trace (H6).** A watcher exited 1 without identifying what failed. The agent then
looked up failed jobs and fetched logs separately. Lead with the failed-job summary and a small requested excerpt.
When the job ID is already known, go directly to its log.

    ci-pipeline failures --project "$PROJECT" --pipeline-id "$PIPELINE_ID" --trace-lines 20 --json
    ci-job logs --project "$PROJECT" --job-id "$JOB_ID" --trace-lines 20

**Read plain text from an API (H6).** A trace returned exit 65 on the default JSON path. The consumer switched helpers.
The installed GET supports explicit text and line selection for traces and raw repository blobs. Keep that example
runnable on older installations. H6 adds the shorter `--raw` spelling.

    ci-api get --provider gitlab --endpoint "projects/$PROJECT/jobs/$JOB_ID/trace" --output text --lines 1:20
    ci-api get --provider gitlab --endpoint "$RAW_BLOB_ENDPOINT" --output text --lines 1:20

**Pipe clean JSON and preserve the default target (H2).** A six-GET batch spent about 80% of its output on success
and deprecation logs. Lead with ordinary JSON, which H2 makes quiet by default. After migrating the selected legacy
table, the same check should resolve the same authority without repeating `--gitlab private` on every invocation.
H2 specifies `[gitlab_routing].default = "gitlab_private"` and the explicit `--quiet` and log-level overrides.

    ci-api get --provider gitlab --endpoint "$ENDPOINT" --json
    ci-api check --provider gitlab --json

**Discover one operation from a consumer shell (H1/H3).** `ci-api -h --json` printed human help. MR help imported
Kubernetes and crashed. System Python 3.9, the sibling bootstrap import and missing PATH entries forced interpreter
prefixes or inline scripts that broke approval-prefix matching. Lead with the installed command and verb. Use compact
JSON help for discovery and one expanded reference for details. Bare `ci-api --json` must explain the missing verb.

    ci-api -h --json
    ci-pipeline watch -h --json
    ci-mr --help

**Diagnose a blocked GET (H2/H3).** A missing native client and a DNS or sandbox failure both surfaced as
`PROVIDER_FAILED`. The same GET should report its bounded, sanitized cause. Resolve GitLab's native client from
the supported install locations even when Homebrew is absent from PATH. Apply H3 lookup tests to each declared client.

    ci-api get --provider gitlab --endpoint "$ENDPOINT" --json

**Two later discovery examples.** To check one job now, show `ci-job get --job-url "$JOB_URL" --json` once H3
installs the alias. For continued observation, point to the pipeline watch above and state its scope. To check
available MR operations, show `ci-mr --describe`. Closing an MR without merging and watching one job continuously
are outside the declared verbs. Give that answer directly when asked. These examples do not add write or watch verbs.

H4's callback requirement applies behind these commands. Verify it through the executor and authenticated session.
Publishing an example requires checking its installed verb help. Record source reads separately from CLI execution.

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

**INTRODUCED H1.** `core/help.py` renders the same six fields for `--help --json` at command or verb scope.
The pinned help standard names `summary`, `description`, `examples`, `options`, `output_modes` and `usage`.
Use the existing catalog and help declarations for both formats. Preserve option applicability and runnable examples.
If a new schema is needed, propose strings for summary, description and usage, description/command pairs for examples,
and maps of names to explanations for options and output modes. Add `schema_version` and command identity. Each
verb entry retains its name, purpose, usage, runnable example, required and optional arguments, and mutation flag.
Review the proposed `schemas/command-help.schema.json` before implementation.

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
`new_path`, `patch` strings. Include the MR IID and the diff revision’s `base_sha` and `source_sha` in the enclosing
result, with `truncated:boolean` and per-IID/file errors. Record `head_changed` if the requested head is unavailable.

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

From outside the checkout replay the [consumer tasks](#consumer-tasks-and-the-first-command-to-show) as each
milestone ships. Use a numeric project ID in raw endpoints. High-level commands accept the existing project
selector. Check both help-flag orders, the missing-verb diagnostic and the separate full contract.

    ci-api -h --json
    ci-api --json -h
    ci-api --json
    ci-job --describe

For the H7 write fixture alone, repeat the planned merge with `--apply --confirm-plan "$DIGEST"` in place of
`--dry-run`. Use the candidate's installed help to verify the mode. Read back the IID, source SHA, target branch
and merged state.

## Validation and Acceptance

For H1, add an installed subprocess case in `tests/python/test_installed_package.py` under `test-install`.
The job’s Conda environment normally includes Kubernetes. In the subprocess, make that import unavailable and prove
the restriction with an import probe. From an unrelated directory with no credentials or network, show the old MR
help failing and the candidate passing under identical conditions. Run `--help` and `--describe` for each family.
Both JSON-help flag orders must return identical, schema-valid records and write zero stderr bytes. Assert all six
keys and compare their values with the same scope’s human help, including examples, option names and output modes.
Omit a required key in a negative fixture and verify validation fails. Bare `ci-api --json` exits 64 and names the
missing verb/provider in JSON. Before H3 use the installed legacy paths. After H3 repeat with the short names.
Validate `--describe` against the existing command schema and
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
A SQL patch late in a large diff must survive filtering. Flag a provider-omitted hunk as incomplete. List head A,
advance the MR to B and request the diff for A. Return A with its base SHA or `head_changed`, with zero hunks from B.

For H6/H7, test pending → running → settled, pending through timeout, missing timing, and mixed manual/running jobs.
For a pending-only watch, capture its first machine progress event before timeout. Observe the transition to running
in the second fixture. Unchanged polls add zero lines. Plain JSON stays quiet and returns its expected exit status.
For failures, request two selected tails including a large trace and verify bounded, redacted output and per-job
errors. For merge, retarget an MR between plan and apply while keeping its IID and head fixed. Assert zero merge writes.
Also exercise stale SHA, unmet provider requirements, success, a lost write response and a changed branch at read-back.
Only the verified merged head and branch yield repeat success with zero extra mutations.

For H8, repeat each paired task on the installed candidate from a fresh consumer directory. Record the exact
revision and guide digest, first and peak input context, cumulative input including cache, source reads and their
specific reason, and whether scoped help supplied the answer. Verify requested evidence before comparing costs.
Use identical selectors and record skill bytes loaded, stdout/stderr bytes, provider
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

### Paired consumer replay

On 2026-11-25, `gpt-6-luna` replayed ten incident tasks and the two later discovery examples. Each pair used
fresh consumer sessions, the same prompt, installed runtime `12ddee804361390b5c35f007a0251661dc663f15` and
the same read-only GitLab fixture. Before used the installed guide (364 lines, 21,249 bytes). After used a staged
task index (31 lines, 3,025 bytes) with the working interpreter and legacy commands for that installation.
The staged guide is an experiment. H1–H8 still need validation against their released commands.

Each cell shows **before / after**. Growth is peak sampled model-input tokens minus the first sample in that
session. Total is all input tokens reported for the turn, including cached input. Calls count shell invocations.
One shell invocation can make several provider requests. Each pair is one observation. Workspace lookups and
failed attempts are included in the measurements.

| Consumer task | Context growth | Total input tokens | Shell calls |
| --- | ---: | ---: | ---: |
| List five MRs | 23,200 / 5,591 | 316,856 / 188,600 | 6 / 8 |
| Filter SQL diff | 7,421 / 2,612 | 270,984 / 141,448 | 6 / 3 |
| Open MR dry run | 7,965 / 1,997 | 231,757 / 140,096 | 5 / 3 |
| Merge head dry run | 16,792 / 9,478 | 587,154 / 309,085 | 12 / 7 |
| Watch pending pipeline | 11,748 / 3,801 | 249,442 / 216,076 | 5 / 5 |
| Failed job and trace | 14,625 / 3,749 | 289,332 / 218,348 | 6 / 5 |
| Read trace and raw blob | 10,573 / 3,105 | 203,329 / 178,661 | 5 / 5 |
| Six JSON reads | 8,510 / 2,452 | 442,212 / 176,093 | 10 / 9 |
| Discover four CLIs | 18,772 / 16,097 | 395,060 / 429,621 | 8 / 28 |
| Diagnose GET failures | 19,236 / 3,538 | 551,990 / 253,701 | 11 / 6 |
| Watch individual job | 9,478 / 4,918 | 319,982 / 183,967 | 7 / 4 |
| Discover MR close | 7,394 / 13,058 | 191,212 / 270,811 | 4 / 6 |

**Evidence returned.** The failed-job pair returned the same job, `script_failure` and four trace lines. Provider
requests fell from nine to two. Both text runs returned the same requested trace and file lines. Both six-GET runs
returned parseable JSON and emitted six INFO lines. Neither verified migration or reproduced the deprecation warning.
The task-first guide got five MR rows through six GETs and produced the create digest offline. The baseline list
stopped on a workspace lookup and MR create failed on a Kubernetes import. Neither guide could return the filtered
SQL hunk or a guarded merge plan. Both merge runs verified the source SHA. No writes were applied.

**Watch and discovery.** With the staged guide the five-second watch timed out. A follow-up job-list GET identified
job 7001, pending with no runner and 47 queued seconds. The pipeline watcher did not emit that per-job evidence
during the wait. The individual-job replay also used the pipeline watch and a snapshot. Both baseline watch attempts
stopped at import. For failure diagnosis the baseline agent supplied an extra `api/v4/` prefix and saw 404s.
The revised guide led to valid endpoints and distinct authorization, connectivity and missing-client causes.
Structured error detail, Homebrew fallback and target migration still need candidate evidence. Four-family discovery
took 28 calls after versus eight before. Close-MR discovery returned the same unsupported result with higher growth.
A shorter guide alone cannot supply missing verbs or repair their help.

**Why Python entered context.** Count source lines returned by searches as well as files explicitly opened.
The baseline create search returned two MR launcher lines while looking for the command. Baseline pipeline watch
returned 29 launcher lines while looking for watch and runner options. Baseline close discovery searched the MR
launcher and opened `bin/reference.py` to find its discovery interface. The staged merge run searched for
confirmation and head binding across `bin/` and returned 50 matches from eight Python entrypoints. The MR
reference and `--describe` already identified the supported verbs. In a discarded watch run, Luna passed
`--help` to `sed` and requested 240 lines of the launcher. The focused watch reference and `--describe`
supplied the flags. Count actual tool output even when the agent says it only ran the CLI. These cases motivate
a runnable example for a supported operation and a short capability answer for an unsupported one.

**Replay controls.** Use the same model (low reasoning effort), prompt, runtime and fixture in each pair.
Record both guide digests. Fixture inputs included five MRs, a late SQL patch, a pending and a failed pipeline,
a four-line trace, a raw blob and controlled failure responses.
Supply a valid fixture identity URL and support both numeric and encoded project paths.
Earlier attempts that lacked these inputs and a guide pilot with
a literal `PY BIN/...` placeholder were excluded. The discovery pair exercised the non-interactive shell and missing
Kubernetes import. The remaining comparison is the same replay on installed candidate binaries, including per-stream
bytes, wrong guesses, direct PATH resolution, action receipts and live progress before timeout.

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

Revision BASE recorded the consumer reports and source baseline. The November 25 consumer replay added command examples
for each incident and the measured guide comparison.
The November 25 review added the exact-head,
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
[help-standard]: https://github.com/spyroot/standards/blob/5bd31f1/docs/agents/contracts/documentation-contract.md#help
[merge-api]: https://docs.gitlab.com/api/merge_requests/#merge-a-merge-request
[i78]: https://github.com/spyroot/ci-skills/issues/78
[i134]: https://github.com/spyroot/ci-skills/issues/134
[i139]: https://github.com/spyroot/ci-skills/issues/139
[i150]: https://github.com/spyroot/ci-skills/issues/150
