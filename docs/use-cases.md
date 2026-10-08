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

| The user says | The agent runs | Today |
| --- | --- | --- |
| "Can you reach my GitLab and cluster?" | `access_check.py`, `gitlab_access.py check` | shipped |
| "What happened in this job?" (URL) | `gitlab_job.py --job-url URL` | shipped |
| "How is this pipeline doing?" (ID known) | `gitlab_pipeline.py --pipeline-id ID` | shipped |
| "I just started a pipeline, watch it" | `gitlab_pipeline.py watch` (root and every pipeline it starts) | planned |
| "Create a milestone, a bug or a wiki page" | `gitlab_milestone.py`, `gitlab_issue.py`, `gitlab_wiki.py` | shipped |
| "Assign or create a runner" | `gitlab_runner.py` | shipped |
| "Is my cluster healthy?" | `cluster_health.py` | planned |
| "Why is this volume stuck?" | `storage_report.py` | shipped |
| "What did the cluster say around the failure?" | `event_trace.py --last 2h` | shipped |
| "Is Cilium ok?" | `cilium_status.py`, then `cilium_node.py` for one node | shipped |
| "Is Ceph ok?" | `ceph_cluster.py`, `ceph_kernel.py` | shipped |
| "Are the node MTUs consistent?" | `k8s_verify_mtu_consistency.py` | shipped |
| "Build the toolbox and push it to Harbor" | `toolbox_build.py`, `harbor_push.py` | planned |
| "What does `trigger:forward` do?" | `reference.py next` | planned |

## Rules the agent follows

- ci-skills first; raw `glab` or `kubectl` only when no command covers the task.
- Never a new argument for something the tool can read itself.
- Read the command's `--describe` or `--help`, not the source, before using it.
- A write (milestone, issue, wiki, runner) shows a plan first and applies only with `--apply --confirm-plan`.
- Planned rows are not available yet; say so instead of improvising them.
