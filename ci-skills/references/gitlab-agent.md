# GitLab Agent for Kubernetes: connectivity and attach

Status: no command uses this yet. `gitlab_agent.py` is planned in CI14-M2 and is not implemented; its name and flags
are PROPOSED under CI14 D3. This page tells an AI agent, without going online, when a person's cluster can be
attached to a GitLab project, what each side needs, and which target value each input comes from. ci-skills itself is
never attached to a cluster: the person's project is attached to the person's cluster.

Words used here: "person" is the human who owns the GitLab project and the cluster; "AI agent" is the consumer of
this skill; "GitLab Agent" is the GitLab feature, and "agentk" is its program running in the cluster.

## Who opens the connection

agentk, installed in the cluster, opens the connection to the GitLab agent server, KAS (the docs now call it GitLab
Relay), at `config.kasAddress`. The install is one Helm release:

```bash
helm upgrade --install <name> gitlab/gitlab-agent \
  --namespace <namespace> --create-namespace \
  --set config.token=<agent token> \
  --set config.kasAddress=<kas address>
```

The default chart's service account has cluster-admin. Source: docs.gitlab.com `user/clusters/agent/install`.

## KAS address

- GitLab.com: `grpcs://kas.gitlab.com`. Agents installed with `wss://kas.gitlab.com` still work. Sources:
  `user/clusters/agent/install`; GitLab 19.5 release notes.
- Self-managed: `grpcs://<gitlab host>`, or `wss://<gitlab host>/-/kubernetes-agent/` on an instance without HTTPS or
  with a relative URL root. Source: `administration/clusters/kas`.

On a self-managed GitLab the administrator sets up KAS; the Linux package installs it by default. From 19.5, new Linux
package and Helm chart installations advertise `grpcs://`. The planned command will read the address from the selected
GitLab at run time and will not derive it from the target key or host.

## What the cluster side needs

- Egress from the cluster to the KAS address. No inbound path into the cluster is needed, so a privately hosted
  cluster can attach to GitLab.com when it can open an outbound connection to `kas.gitlab.com`.
- Behind a proxy, `HTTPS_PROXY` set through the chart's `extraEnv`.
- For `grpcs://`, HTTP/2 end to end. A WAF or TLS-inspecting proxy that speaks HTTP/1.1 breaks it; use the `wss://`
  address there.

Source: `user/clusters/agent/install`.

## When an attach is possible

| Network between cluster and GitLab | Self-managed GitLab | GitLab.com |
| --- | --- | --- |
| Cluster can open a connection to KAS | normal agent | normal agent via `grpcs://kas.gitlab.com` |
| Only GitLab can reach the cluster | receptive agent (Ultimate, from 17.4) | not possible: no receptive agents |
| Neither direction | not possible | not possible |

A receptive agent is one GitLab connects to, at a URL configured through the cluster agents API. Source:
`user/clusters/agent` (Receptive agents) and `user/clusters/agent/install`.

## What the project defines, and how its CI uses it

The project that registers the GitLab Agent is its configuration project. Its file
`.gitlab/agents/<agent-name>/config.yaml` lists the projects whose CI jobs may use the GitLab Agent. Example, in
`group/app`, file `.gitlab/agents/lab/config.yaml`:

```yaml
ci_access:
  projects:
    - id: group/svc
```

A job in an authorized project gets `$KUBECONFIG` with one context per shared GitLab Agent, named
`<configuration project path>:<agent-name>`. Example, `.gitlab-ci.yml` in `group/svc`:

```yaml
deploy:
  script:
    - kubectl config use-context group/app:lab
    - kubectl get pods
```

Authorized projects must share the configuration project's top-level group unless instance-level authorization is
enabled; `ci_access.projects` and `ci_access.groups` hold up to 500 entries each. Whether GitLab.com allows
instance-level authorization is not verified here. Source: `user/clusters/agent/ci_cd_workflow`.

## Attach flow (planned, CI14-M2)

The flow follows the existing confirmed-action pattern: `--describe`, `--live-plan`, then `--apply --confirm-plan`.

```mermaid
sequenceDiagram
    participant Person
    participant AI as AI agent
    participant Skill as gitlab_agent.py (planned)
    participant Target as ~/.ci-skills/target.toml
    participant GL as GitLab (instance chosen by --gitlab)
    participant K8s as Kubernetes (table named by the routing row)
    Person->>AI: attach group/app to my lab cluster
    AI->>Skill: --describe (flags, modes, result schema; no credentials)
    AI->>Skill: attach --gitlab private --project group/app --agent-name lab --live-plan
    Skill->>Target: [gitlab_routing] row -> instance table + kubernetes table
    Skill->>GL: read KAS address, existing agents (read only)
    Skill->>K8s: preflight: can the cluster reach that KAS address (read only)
    Skill-->>AI: plan + digest, or BLOCKED <missing prerequisite>
    AI->>Person: show plan; Person confirms
    AI->>Skill: same command --apply --confirm-plan <digest>
    Skill->>GL: POST /projects/:id/cluster_agents {"name":"lab"} -> token
    Skill->>GL: commit .gitlab/agents/lab/config.yaml (ci_access.projects)
    Skill->>K8s: helm upgrade --install gitlab/gitlab-agent (config.token, config.kasAddress)
    Skill->>GL: read back: agent connected; context group/app:lab visible to authorized CI
    Skill-->>AI: one result: PASS | BLOCKED <reason> | PARTIAL <what exists> + routing + agent context
```

Where each value comes from:

| Value | Source | Example | Used as |
| --- | --- | --- | --- |
| Instance | `--gitlab`, else `[gitlab_routing].default`, else `gitlab_private` | `private` | API host: its `url` |
| Cluster | `kubernetes` of the selected `[gitlab_routing]` row | `kubernetes` | where agentk runs |
| Configuration project | `--project` | `group/app` | holds `.gitlab/agents/lab/config.yaml` |
| Agent name | `--agent-name` (PROPOSED) | `lab` | registration name and config directory |
| Authorized projects | `--ci-access-project`, repeatable (PROPOSED) | `group/svc` | `ci_access.projects[].id` |
| KAS address | read live from the selected GitLab | `grpcs://kas.gitlab.com` | `config.kasAddress` |
| CI context | derived | `group/app:lab` | `$KUBECONFIG` context in authorized jobs |

The project to attach is the configuration project.

## Pairing a GitLab with a cluster in `[gitlab_routing]`

A `[gitlab_routing]` row is the person's own declaration of which cluster and registry belong to a GitLab
([access reference](access.md#gitlab-private-and-public-targets)). No key, host or code rule decides it; it depends on
where the work runs:

- A GitLab.com project plus a cluster in the person's lab works for a GitLab Agent, or for a self-managed runner in
  that cluster, when the cluster has egress to GitLab.com.
- GitLab.com hosted runners cannot pull from a Harbor behind the person's DMZ, so pairing that Harbor with GitLab.com
  makes no sense for them.
- An empty row, `gitlab_public = {}` in the template, means that GitLab has no cluster and no registry of the person's.
  A command that needs Kubernetes or Harbor then returns BLOCKED `target is missing required authority tables: ...`
  before any provider call, and its `--dry-run` shows the same result. Fill the row only for the first case above.

Whether the cluster reaches KAS is an infrastructure fact. The planned command checks it live before any
registration and returns BLOCKED naming the missing egress. That check is proposed as DEC-14 in the issue #133
ExecPlan and is not implemented.
