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

| The user says | The agent runs | Status |
| --- | --- | --- |
| "Can you reach my GitLab and cluster?" | `access_check.py`, `gitlab_access.py check` | current |
| "What happened in this job?" (URL) | `gitlab_job.py --job-url URL` | current |
| "How is this pipeline doing?" (ID known) | `gitlab_pipeline.py --pipeline-id ID` | current |
| "Watch the pipeline I started" | `gitlab_pipeline.py watch` (root and descendants) | under development |
| "Create a milestone, a bug or a wiki page" | `gitlab_milestone.py`, `gitlab_issue.py`, `gitlab_wiki.py` | current |
| "Assign or create a runner" | `gitlab_runner.py` | current |
| "Is my cluster healthy?" | `cluster_health.py` | planned in next delivery |
| "Why is this volume stuck?" | `storage_report.py` | current |
| "What did the cluster say around the failure?" | `event_trace.py --last 2h` | current |
| "Is Cilium ok?" | `cilium_status.py`, then `cilium_node.py` for one node | current |
| "Is Ceph ok?" | `ceph_cluster.py`, `ceph_kernel.py` | current |
| "Are the node MTUs consistent?" | `k8s_verify_mtu_consistency.py` | current |
| "Build the toolbox and push it to Harbor" | `toolbox_build.py`, `harbor_push.py` | planned in next delivery |
| "What does `trigger:forward` do?" | `reference.py next` | planned in next delivery |

## Rules the agent follows

- ci-skills first; raw `glab` or `kubectl` only when no command covers the task.
- Never a new argument for something the tool can read itself.
- Read the command's `--describe` or `--help`, not the source, before using it.
- A write (milestone, issue, wiki, runner) shows a plan first and applies only with `--apply --confirm-plan`.
- Under-development and next-delivery rows are not available yet; say so instead of improvising them.
