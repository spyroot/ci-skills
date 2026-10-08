# ci-skills use cases

How an agent uses ci-skills, at a high level. Commands are in `ci-skills/bin/`; `ci-skills/tools.json` lists them.

## How it works

- The user asks the agent in plain words; the agent checks ci-skills first and runs one command, not 20 raw
  `kubectl`, `oc` or `glab` calls.
- Which GitLab and cluster, and where their access is: `~/.ci-skills/target.toml` (a project's
  `.ci-skills/target.toml` overrides it). Nothing else goes there.
- Commands read live resources at run time. Give an exact ID or URL when a shipped command requires one; the selected
  project defaults to the target file when the command supports it.
- A command returns facts in one compact record (`--json`, `--yaml`, or a human summary); the agent and the user
  decide what to do next.
- When the agent does not know where to look, it follows small pointers one level at a time instead of reading big
  files (planned: `reference.py next`).

## Use cases

The route column names a command family or owning plan. Read a current command's `--help` or `--describe` for its exact
arguments. An under-development route may expose only part of the use case; next-delivery routes are not installed.

| ID | Use case | Route or owner | Status |
| --- | --- | --- | --- |
| UC-01 | Prove access | GitLab: `gitlab_access.py check`; all three: `access_check.py` | under development |
| UC-02 | Select endpoints and access | `~/.ci-skills/target.toml` | current |
| UC-03 | Discover platform locations beyond the target | CI11-TOOLS | planned in next delivery |
| UC-04 | Install at the requested skill scope | `install.sh`; CI01-CATALOG | under development |
| UC-05 | Discover one reference level per call | `reference.py next`; CI09-REFERENCE | planned in next delivery |
| UC-06 | Follow bounded reference pointers | `reference.py next`; CI09-REFERENCE | planned in next delivery |
| UC-07 | Find tools by short names | CI09-REFERENCE navigator | under development |
| UC-08 | See what exists and what is planned | `ci-skills/tools.json`; this table | current |
| UC-09 | Run GitLab work through glab | `core/gitlab_api.py` | under development |
| UC-10 | Read one job by URL | `gitlab_job.py --job-url URL` | current |
| UC-11 | Find jobs by state and time | `gitlab_job.py list` | current |
| UC-12 | Correlate the last failed job and events | CI11-TOOLS | under development |
| UC-13 | Select last pipeline, named jobs and schedules | `gitlab_pipeline.py`; CI11-TOOLS | under development |
| UC-14 | Watch a pipeline and descendants | `gitlab_pipeline.py watch`; CI11-TOOLS | under development |
| UC-15 | Read a quick runner view | CI11-TOOLS | planned in next delivery |
| UC-16 | Check an MR like gh-fix-ci | `gitlab_mr.py check`; CI11-TOOLS | planned in next delivery |
| UC-17 | Combine milestone, labels and MR checks | CI11-TOOLS | under development |
| UC-18 | See cluster health in one record | `cluster_health.py`; CI11-TOOLS | planned in next delivery |
| UC-19 | Diagnose a stuck volume or claim | `storage_report.py` | current |
| UC-20 | Read events over a chosen window | `event_trace.py --last 2h` | current |
| UC-21 | Read Cilium health | `cilium_status.py`, `cilium_node.py` | current |
| UC-22 | Collect independent facts in parallel | `core/collect.py`; CI11-TOOLS | under development |
| UC-23 | View and act on OpenShift state | `ocp_*.py`; CI11-TOOLS | planned in next delivery |
| UC-24 | Build toolbox; push to Harbor | `toolbox_build.py`, `harbor_push.py`; CI11-TOOLS | planned in next delivery |
| UC-25 | Read one upstream section on demand | CI09-REFERENCE | planned in next delivery |
| UC-26 | Explain one label family | CI09-REFERENCE | planned in next delivery |
| UC-27 | Answer from MCP reference nodes | CI09-REFERENCE | planned in next delivery |
| UC-28 | Port a script onto a shared library | CI11-TOOLS | planned in next delivery |
| UC-29 | Extend the tool manifest and schema | `tools/render_manifest.py`; CI07-SCHEMA | under development |
| UC-30 | Lock reference navigator output | CI09-REFERENCE | planned in next delivery |
| UC-31 | Require versioned schemas | `tools/check_schemas.py`; CI07-SCHEMA | planned in next delivery |
| UC-32 | Reject new target keys without approval | `gate-ci-skills-endpoints`; CI11-TOOLS | under development |
| UC-33 | Prove live smoke by read-back | `tools/check_live_acceptance.py` | under development |
| UC-34 | Read merge and review state before locking | pr-coordinator; CI11-TOOLS | under development |

No shipped command checks GitLab and Kubernetes together. `access_check.py` currently also requires GitHub.

Current individual writes use `gitlab_milestone.py create`, `gitlab_issue.py open-bug`, `gitlab_wiki.py create`, and
`gitlab_runner.py assign|create`. They plan first and require `--apply --confirm-plan` to write. The current MTU
command also plans first; its confirmed run uses temporary debug Pods.

## Rules the agent follows

- ci-skills first; raw `glab` or `kubectl` only when no command covers the task.
- Never a new argument for something the tool can read itself.
- Read the command's `--describe` or `--help`, not the source, before using it.
- A write (milestone, issue, wiki, runner) shows a plan first and applies only with `--apply --confirm-plan`.
- For an under-development route, name the missing part. Do not run a next-delivery route as if it were installed.
