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
