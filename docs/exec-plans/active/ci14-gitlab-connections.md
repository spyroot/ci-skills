# Finish the GitLab Agent connection after the delivered mirror workflow

This living ExecPlan follows [docs/PLANS.md](../../PLANS.md) and derives only from [CI14][phase].
Source baseline: `16255b1c51bb867d7cf4b83ef4ec9d3141211c3a`. The mirror delivery is complete; this phase remains active
for the Agent workflow. Progress and [recorded evidence][baseline] distinguish completed mirror work from the
undelivered Agent.

## Purpose / Big Picture

A user can synchronize a selected GitLab repository to GitHub, or connect a selected Kubernetes cluster to a
GitLab project through the GitLab Agent. These are separate workflows. The mirror command already exists;
the remaining implementation is the Agent workflow after its original design decisions are resolved.

## Progress

- [x] CI14-R1: mirror command, reusable services, schemas and tests exist at the baseline.
- [x] PR #125 merged as `c25d9b3`; installed mirror creation, matching refs, NO_OP and exact-owned cleanup passed.
- [x] PR #129 delivered named GitLab profiles, portability and explicit mirror evidence requirements.
- [x] DEC-CI14-02: retain the approved official pinned Helm chart, existing provider services, owned-token handling
  and selected-project access requirement.
- [ ] Resolve the exact chart pin and remaining Agent interface/ownership details within those settled choices.
- [ ] Reconcile mirror follow-ups [#130](https://github.com/spyroot/ci-skills/issues/130) before new capability work.
- [ ] Deliver PR-002/W-002 Agent registration, installation, connection and selected-project access read-back.
- [ ] Complete the original Agent gates and qualify the affected candidate; retain mirror regression coverage.

## Surprises & Discoveries

The phase still labels both workflows absent and mirror receipt enforcement unregistered. Current source contains
`ci-skills/bin/gitlab_mirror.py`, `core/gitlab/mirror/cli.py` and `workflow.py`, shared Git ref comparison in
`core/git/mirroring.py`, GitHub repository access and installed input/result schemas. The candidate mirror receipts
identify tested revision `bcf7d598`; their existence is recorded evidence, not execution on a new candidate.
No `gitlab_agent.py`, `core/gitlab/cluster_agent/` or `core/kubernetes/gitlab_agent.py` implementation exists.

## Decision Log

DEC-CI14-01: credit the delivered mirror slice and preserve its current command contract. The older proposed D1/D3
text is a reconciliation discrepancy, not an instruction to reopen or overwrite working behavior. The remaining D2
details are prerequisites for the Agent slice; the implementation mechanism is settled below.
No new target keys, permission defaults or gate are introduced.

DEC-CI14-02: the approved closure scope selects the official pinned Helm chart, existing provider sessions,
run-owned token handling and a consumer job using the selected project's Agent. Reuse the locked
[smoke allocations][assets] and runtime Harbor, GitLab, GitHub and Kubernetes configuration. The exact chart pin,
installation inputs and schema/flag details still need a concrete delta before implementation; do not reopen
the selected installer or create a new provider.

## Outcomes & Retrospective

PR-001's mirror capability is merged and qualified; [the recorded evidence][baseline] retains its tested revision,
required CI and cleanup evidence. CI14 remains active because PR-002's Agent capability and project-through-Agent
proof are absent. Next: reconcile #130, then prepare the exact Agent chart/interface delta without reimplementing
the mirror command or repeating its completed smoke.

## Context and Orientation

`MirrorCommand` in `core/gitlab/mirror/cli.py` calls the mirror workflow; `MirrorEngine` in its `workflow.py` uses
shared provider services and independently compares source/destination refs. Tests in
`tests/python/test_gitlab_mirror.py` cover stale confirmation, NO_OP, synchronization read-back, bounded polling,
private creation and exact-ID failed-mirror replacement. `inventory/ci/live-acceptance.json` registers the mirror
operation requirements. The new Agent workflow must reuse provider sessions, Kubernetes resources,
`WorkflowExecutor`, reporting and schema validation already used by installed commands.

## Plan of Work

### CI14-M1: preserve and qualify the delivered mirror slice

Do not implement the mirror command a second time. Retain its current flags, input/result schemas, confirmation,
credential handling, divergence policy and failed-mirror recovery. Use the existing mirror tests and candidate
receipts as the starting evidence. When the functional candidate changes, evaluate it through the existing receipt
policy rather than changing a receipt's tested revision or treating this document as proof.

### CI14-M2: resolve D2 and deliver the Agent workflow

Use the approved official Helm chart and pin its exact version. Reuse existing provider services and keep newly
created tokens tied to their native Agent identity. Preserve pre-existing installations and credentials. Resolve
only the remaining installation inputs, permission/ownership details and exact flags/result fields under D3;
the mirror interface does not supply an Agent schema. Then introduce the phase's
`ci-skills/bin/gitlab_agent.py`, reusable `core/gitlab/cluster_agent/` services and
`core/kubernetes/gitlab_agent.py` callbacks. Discover the requested project, cluster, registrations/installations
and KAS prerequisites. KAS is the GitLab service through which the cluster Agent connects. Plan and apply the
approved work through the existing confirmation interface, then independently read registration, cluster
installation, connection and selected-project access. Reuse shared ownership/recovery behavior.

### CI14-M3: complete the original installed acceptance

Register the Agent command in the existing catalog, generate its manifest entry and pair the approved schemas
with their producers. Extend the existing checks and receipt mechanisms for the original G-001/G-003/G-004
requirements. A registered Agent or ready Pod alone does not prove project access. Keep mirror G-002 and Agent
G-003 distinct; the workflows do not acquire a runtime dependency merely because PR-001 precedes PR-002.

## Concrete Steps

Working directory: repository root, in the existing GitHub validation environment. Run the current mirror/catalog
coverage before and after the Agent delivery and expect exit 0; Agent-specific tests accompany that capability.

    python -W error -m pytest -q tests/python/test_gitlab_mirror.py tests/python/test_catalog.py
    python tools/render_manifest.py --check

Use the existing pipeline and receipt route; no new CI job or gate is proposed here. The workflow supplies its
candidate revision, and the [smoke inventory][smoke] owns required steps and evidence paths.

| Variable Name | Type | Default / Required | Source / Scope | Description & Invariants |
| --- | --- | --- | --- | --- |
| `CI_SOURCE_SHA` | string | Required | [Workflow][workflow] | Exact candidate under validation |

| Pipeline / Job Name | Type / Trigger | Stage / Order | File Path | Required Inputs & Artifacts |
| --- | --- | --- | --- | --- |
| `validate` | PR/push/manual/scheduled | Parallel checks | [Workflow][workflow] | Candidate; validate evidence |
| `gate-ci-skills-endpoints` | Same workflow | Parallel checks | [Workflow][workflow] | Candidate; endpoint evidence |
| `test-install` | Same workflow | Parallel checks | [Workflow][workflow] | Candidate; package evidence |
| `required-result` | Same workflow | After required jobs | [Workflow][workflow] | Exact-candidate job evidence |

| Gate ID | Gate Type / Class | Pass Condition | Fail-Closed Condition | Producer / Owner |
| --- | --- | --- | --- | --- |
| `ci-skills/required` | Required CI | All required evidence | Missing/failed evidence | [Aggregator][aggregate] |
| CI14 acceptance | Phase proof | Original scope proven | Original gaps remain | [Phase gates][phase-gates] |

The second row refers to the phase's existing acceptance, not a newly registered automated gate. Its named checks,
including historical or proposed routes, retain the status recorded by their owner.

## Validation and Acceptance

The existing mirror retains independent ref verification, confirmed optional creation, redacted blockers and
APPLIED/NO_OP behavior. Agent acceptance must show registration, actual cluster installation, connected GitLab
state and the selected project's access, all for the chosen target. Schema validity, registration acceptance or
Pod readiness alone does not establish that outcome. Use CI14's original requirement/gate map and preserve open
D2 choices until resolved. No current Agent command invocation is advertised because it does not exist yet.

## Idempotence and Recovery

Preserve mirror NO_OP and exact observed-identity recovery. For Agent delivery, D2 must settle ownership and
credential/install recovery before mutation code is written; reuse existing resource read-back and cleanup.
An uncertain apply is reconciled against provider state before retry. Preserve pre-existing installations.

## Artifacts and Notes

[PR #125](https://github.com/spyroot/ci-skills/pull/125) merged as `c25d9b3` after
[required workflow 41286903984](https://github.com/spyroot/ci-skills/actions/runs/41286903984) and its exact-head
live-acceptance deployment passed. Installed source `1f2b6f5` proved optional creation, equal branch/tag refs,
NO_OP and deletion of only GitHub repository `1441597088` and mirror `13`, with independent repeated absence.
The retained source and refs remained.

The later installed `bcf7d598` also qualified the unchanged mirror path: repository `1441633602` and mirror `15`
were created, verified, reused without mutation and removed by exact IDs. Selected originals are
[APPLIED][mirror-applied] and [NO_OP][mirror-noop]; the [policy][policy] registers both operation requirements.
Foundation [PR #129](https://github.com/spyroot/ci-skills/pull/129) delivered named profiles and required coverage.
These completed subtasks do not establish Agent registration, connection or project-through-Agent access.
These records retain their captured revision. The missing Agent slice has no live receipt. The
[plan map](../../plans/index.md) links both outcomes to CI14 and the existing repository checks.

## Interfaces and Dependencies

Reuse the current target, provider sessions, `WorkflowExecutor`, resource ownership, catalog and report envelope.
The phase names the Agent modules; DEC-CI14-02 settles the installer mechanism. Remaining credential, permission
and ownership details must reuse the selected runtime target and the existing confirmation/reporting boundary. Exact
schemas and flags precede implementation
through that original decision boundary.

Revision DEC-CI14-01: derived the remaining Agent delivery from the unchanged phase while crediting mirror code
and its recorded receipts, avoiding a duplicate mirror implementation.

[mirror-applied]: ../../../tests/acceptance/candidate/mirror-applied.json
[mirror-noop]: ../../../tests/acceptance/candidate/mirror-no-op.json
[policy]: ../../../inventory/ci/live-acceptance.json

[phase]: ../../phases/CI14-GITLAB-CONNECTIONS.md
[phase-gates]: ../../phases/CI14-GITLAB-CONNECTIONS.md#gates
[workflow]: ../../../.github/workflows/validate.yml
[smoke]: ../../../inventory/ci/smoke-tests.yaml
[aggregate]: ../../../tools/ci_required_result.py

Revision DEC-CI14-02: recorded merged mirror qualification and the previously approved Agent mechanism so a
restart resumes the undelivered slice rather than reopening settled choices.

[baseline]: #artifacts-and-notes
[assets]: ../../2026-10-31/live-smoke-inventory.md
