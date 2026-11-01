# NOT_IMPLEMENTED: CI14 — Connect a GitLab project to GitHub and Kubernetes

- grounding: GROUNDED
- verification: UNVERIFIED
- planningStage: PLANNED

## Authority

Main at `864f0059c818ad1066e5e58c0f10a35e5dc74a14` has shared provider foundations but neither workflow. The operator
requests (A1) GitLab-to-GitHub synchronization, with an existing destination by default, an explicit create-if-absent
flag and redacted JSON blockers for missing access or key material; and (A2) registration and installation of a GitLab
Agent in the selected Kubernetes cluster for the selected GitLab project. [README](../../README.md),
[the capability model](../capability-model.md), [the contribution guide](../contribution_guide.md),
[CI07-SCHEMA](CI07-SCHEMA.md) and `standards-binding.yaml` govern installed consumption, reusable Python ownership and
schemas (A3). Existing live acceptance inventory and checker govern receipt registration (A4). The phase identity is
approved; exact command and schema interfaces remain open decisions.

## Consumer

A user installs CI-Skills, selects the required authorities through `~/.ci-skills/target.toml`, and asks an agent for
either outcome. The agent discovers the command, reviews its plan, confirms a mutation through the existing interface
and receives a bounded machine-readable report. Project, cluster, existing resources and installation facts come from
the request, target and live system. Credential values stay out of reports.

## Motivation

Each workflow packages recurring provider calls into one operation with independent read-back. The agent can report
completion, missing prerequisites or a partial result without paging through raw API responses.

## Diagram

```text
User request + selected target
  → catalog → thin Python command → WorkflowExecutor callbacks
      ├─ GitLab + GitHub → mirror → destination ref read-back
      └─ GitLab + Kubernetes → agent installation → project access read-back
  → existing report renderer → bounded result
```

Provider services own API operations; callbacks own sequencing and evidence. The existing executor owns lifecycle and
cleanup dispatch.

## Implementation Plan

### IMPLEMENTED: shared foundation

Current main contains target and credential resolution, provider services, callbacks, `WorkflowExecutor`, reports,
catalog generation and installed schema validation. This planning pass verified source presence, not runtime behavior.
Reuse `core/target.py`, `core/executor.py`, `core/abstract/`, `core/gitlab/gitlab_session.py`, `core/github/github.py`,
`core/kubernetes/kubernetes.py`, `core/kubernetes/resources.py` and `core/schema.py` under `ci-skills/lib/python/`.

### NOT_IMPLEMENTED: GitLab-to-GitHub synchronization

Main has GitLab repository-file operations and GitHub runner operations, but no Git repository mirror command. Proposed
`ci-skills/bin/gitlab_mirror.py` calls shared services in `core/gitlab/mirror/` and `core/github/repository.py`.
Callbacks discover both repositories and existing mirrors, plan and apply confirmed work, request synchronization, then
independently read mirror status and approved destination refs. Request acceptance alone is not sync proof.

Current `GitHubAuth` assumes the destination exists and reads its runners. Extract reusable identity/session access
before repository lookup or optional creation; preserve runner verification for existing callers. Missing auth,
permissions or mirror key material returns redacted structured `BLOCKED` evidence.

### NOT_IMPLEMENTED: GitLab Agent connection

Existing Kubernetes runner deployment is distinct from GitLab Agent registration. Proposed
`ci-skills/bin/gitlab_agent.py` uses shared `core/gitlab/cluster_agent/` and `core/kubernetes/gitlab_agent.py`
callbacks. Discover the selected project, cluster, existing registrations/installations and KAS prerequisites; plan the
approved registration and installation; then read back cluster installation, GitLab connection and selected-project
access. Reuse Kubernetes access and owned-resource handling. Credential delivery, installation mechanism and permissions
remain decisions, not assumed defaults.

### Contracts and open decisions

Declare each command in `core/catalog.py`, regenerate `ci-skills/tools.json`, and pair its inputs/results with installed
schemas. Reuse the existing modes, status and report envelope. Schemas define accepted intent and report shape; live
read-back proves behavior. No new target key is authorized by this phase.

Before implementation, resolve D1: mirror credential/key mechanism, destination visibility and supported ref/divergence
policy; D2: agent installer, permissions, credential delivery and ownership of newly created resources; D3: exact
command flags, schemas and receipt registration. Discover existing names and prerequisites live. Missing prerequisites
yield a structured blocker.

## PR Order

Planning PR IDs are not forge PR numbers.

| PR / wave | Coherent deliverable | Dependency |
| --- | --- | --- |
| PR-001 / W-001 | Mirror command, shared auth, callbacks, schema, docs and receipt | Foundation; D1/D3 |
| PR-002 / W-002 | Agent install command, callbacks, schema, docs and receipt | Foundation; D2/D3; after PR-001 |

Merge PR-001 before PR-002, following operator priority. The workflows have no runtime dependency on each other. Each PR
preserves existing callers and uses focused checks. Root Bash tooling, Git hooks and repository CI design are outside
this phase.

## Gates

G-001 is the existing catalog/schema/static route with new command checks to add. G-002 is the new mirror live receipt:
source, mirror state and destination refs independently read under the approved policy, including explicit creation and
a redacted blocked case. G-003 is the new agent live receipt: registration, installation, connection and
selected-project access independently read through the agent. G-002/G-003 are not registered or enforced yet. G-004 is
the existing required-result route in `.github/workflows/validate.yml` for the exact PR head; it has not run for this
phase.

| Requirement / authority | Status | Delivery / dependency | Gate and gap |
| --- | --- | --- | --- |
| R-001 installed / A3 | NOT_IMPLEMENTED / UNVERIFIED | both; PR-001/002 W-001/002; foundation | G-001/004; absent |
| R-002 mirror / A1 | NOT_IMPLEMENTED / UNVERIFIED | mirror; PR-001/W-001; D1/D3 | G-002; ref receipt absent |
| R-003 create / A1 | NOT_IMPLEMENTED / UNVERIFIED | GitHub; PR-001/W-001; D1/D3 | G-002; create proof absent |
| R-004 blocker / A1 | NOT_IMPLEMENTED / UNVERIFIED | auth/report; PR-001/W-001; D1/D3 | G-001/002; JSON absent |
| R-005 agent / A2 | NOT_IMPLEMENTED / UNVERIFIED | agent; PR-002/W-002; D2/D3 | G-003; access proof absent |
| R-006 discovery / A3 | NOT_IMPLEMENTED / UNVERIFIED | both; PR-001/002 W-001/002; target | G-002/003; IDs absent |
| R-007 reuse / A3 | NOT_IMPLEMENTED / UNVERIFIED | libraries; PR-001/002 W-001/002; foundation | G-001/004; absent |
| R-008 schemas / A3 | NOT_IMPLEMENTED / UNVERIFIED | catalog; PR-001/002 W-001/002; D3 | G-001/004; schemas absent |
| R-009 proof / A4 | NOT_IMPLEMENTED / UNVERIFIED | both; PR-001/002 W-001/002; commands | G-002/003/004; absent |

Completion requires both installed workflows and their applicable gates. A mirror sync request, agent registration or
ready Pod alone does not prove the outcome. Additional project sharing, user-access integration and unrelated phase
corrections remain outside this delivery.

Provider references: [GitLab mirrors](https://docs.gitlab.com/api/remote_mirrors/),
[GitHub repositories](https://docs.github.com/en/rest/repos/repos),
[GitLab Agent installation](https://docs.gitlab.com/user/clusters/agent/install/) and
[project CI access](https://docs.gitlab.com/user/clusters/agent/ci_cd_workflow/).

### Proposed D1/D3 resolution for PR-001 — pending operator approval

This section is a proposal, not an approved replacement for the open decisions.

Use GitLab's native HTTPS password-authenticated push mirror. Bind the selected GitHub credential through the existing
credential provider and private request-body transport. GitLab retains that credential for future pushes; never expose
it in argv, plans, URLs, reports, errors or receipts. No new target key or credential source is introduced. SSH key
provisioning is not part of PR-001.

Mirror all branches and tags with `only_protected_branches=false` and `keep_divergent_refs=true`. Divergence preserves
destination changes and fails verification. Native GitLab branch-deletion behavior still applies; preserving divergent
refs does not promise that merged source-branch deletions will never propagate. Existing conflicting mirror policy is
reported in the live plan and must not be silently relaxed. No force-push or unrelated mirror/repository mutation.

The destination must exist unless `--create-if-absent` is confirmed. Optional creation produces an empty private
repository under the explicitly selected owner. Never infer an owner mapping from the source namespace or change an
existing repository's visibility. Validate owner type and creation permissions through provider read-back.

`gitlab_mirror.py sync` accepts existing common modes/output/log controls and `--project`, `--destination`,
`--create-if-absent`, `--live-plan`, `--apply`, `--confirm-plan`, `--timeout` and `--receipt-out`. Source and destination
selectors default only from existing `gitlab.project` and `github.repository`; missing selectors or missing authority
configuration produce structured blockers. Default/`--dry-run` is offline. `--live-plan` reads state without mutation.
Apply requires the digest of that live plan, binding selected repositories, observed refs, mirror policy, optional
creation and functional revision. An uncertain write is reconciled before any retry.

Reuse `WorkflowExecutor`, provider sessions, shared transport/modes/reporting and constants. Extract GitHub identity
session authentication before optional repository creation while preserving existing runner authentication behavior.
Introduce shared Git ref reading through `run_command_bounded`: native `git ls-remote` reads only branch/tag object IDs,
uses private credential environments and no prompts, and never clones or downloads repository objects. Verify all
source refs against destination object IDs; retain destination-only refs. Read source stability and report digests/counts.
A sync request or a finished mirror without matching refs cannot report PASS. Matching verified state is NO_OP without
another sync request. Preserve intended repositories/mirrors and report partial ownership after uncertain mutation;
clean private temporary request/ref-read resources on every exit. The result schema's `before` observation retains the
fresh identities and refs checked before apply, including a null destination ID when absent. Behavioral qualification
requires connected APPLIED and NO_OP receipts from the same installed candidate, with nonempty complete refs,
consistent native identities, and proof that APPLIED created the destination; ordinary empty-repository sync remains
valid. Verification polls at 30-second intervals, capped by the remaining operation deadline.

The qualification caller records independent `smoke_cleanup` observations in a copy of the NO_OP receipt after
removing the run-created mirror configuration and disposable GitHub destination. The existing mirror result schema
defines this optional supplement; acceptance requires its exact IDs, candidate identity, deletion outcomes, retained
GitLab source, independent absence, and repeated no-op cleanup. Preserve original command outputs unchanged.

Normalized intent and result shapes are defined by the accompanying exact schema proposal. A new targeted receipt must
be produced from installed candidate execution; its generated JSON diff requires separate exact-content approval.
Register the new kind in the existing acceptance checker and existing required-result path after approval. Never run
an aggregate status-publishing check against main as a substitute for this feature's own candidate proof.

Required implementation paths in addition to the queue's current source scope are `tests/python/test_gitlab_mirror.py`,
`tools/check_live_acceptance.py` and generated `ci-skills/tools.json`; these are acceptance/catalog consumers of the same
capability, not separate feature scope. No Git hooks, repository CI redesign or new target keys are proposed.

Provider semantics: [remote mirrors API](https://docs.gitlab.com/api/remote_mirrors/) and
[push mirroring](https://docs.gitlab.com/user/project/repository/mirror/push/).

### Confirmed failed-mirror credential recovery

The native remote-mirror update API does not accept the credential-bearing `url` field. Recovering a selected failed
password mirror with a replacement credential therefore requires replacing that mirror configuration. The live plan
must expose `replacement_required` and `replaced_mirror_id`, binding this decision and the exact selected mirror ID
into confirmation. Only a native failed mirror is eligible; healthy verified state remains a write-free no-op.

Confirmed recovery deletes only that selected failed mirror, independently proves its absence, creates its replacement
with the selected credential and confirmed policy, and verifies the new native state and destination refs. The mirror
ID changes. Repository contents and unrelated mirrors are preserved. An interruption may leave the selected mirror
absent; partial output retains known old/new identities and ownership, and a new live plan is required before retry.
No replacement occurs without the digest of a live plan that explicitly identifies this replacement.
