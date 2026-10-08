# Agent capability model

ci-skills is an installed toolbelt for agents working on a selected GitLab, Kubernetes, OpenShift or Harbor target.
This repository develops the toolbelt; its GitHub workflow is not the target's CI system. An agent may use another
tool or stop whenever ci-skills does not answer the task at hand.

## Choose the shortest useful path

The planned `ci-skills/bin/reference.py` navigator advertises capabilities separately from executing them. A call
such as `reference.py next gitlab milestone` returns a small choice of **run** and **read** paths. Following **run**
narrows to a verb, its exact command and required inputs; optional inputs load only on request. Following **read**
leads to a pinned reference section and its subsections. Each choice carries its next invocation. Planned verbs must
remain visibly unavailable until an installed command implements them. [CI09-REFERENCE](phases/CI09-REFERENCE.md)
owns the navigation grammar and output; [CI08-ROUTING](phases/CI08-ROUTING.md) owns routing and lazy loading.

## Work backward from an observed effect

An operational command should compose the usual reads needed to answer one question. For a stuck GitLab job, the
useful path may be job → pipeline and ref → job tags → eligible runner → runner state, project access and protection.
The result identifies each observed fact and its source so an agent can distinguish a supported cause from an
inference. For a chart that cannot find a Secret, the path may be chart version → default values → template reference
→ required Secret name and presence. The planned Harbor chart tool in [CI11-TOOLS](phases/CI11-TOOLS.md) covers
search, extraction, grep and credential-field discovery; it must not expose credential values.

The tool returns the relevant fields and an explicit next pointer when further investigation is useful. It does not
dump a provider response, chart, reference tree or all possible actions. The agent can interleave other commands and
resume at the needed path. These are recurring workflows, not a single command that tries to do everything.

## Keep each capability provable

An installed command owns a bounded behavior, a human view, JSON and YAML output, and a versioned result schema.
Read commands report concrete values observed from the selected live target. Mutations report the before state, the
action and an independent after read. Exit status alone is not evidence that the claimed behavior occurred.
[CI07-SCHEMA](phases/CI07-SCHEMA.md) owns schema rules; [Use cases](use-cases.md) names the expected workflows.
The installed [skill entry point](../ci-skills/SKILL.md) and generated `ci-skills/tools.json` determine what an agent
can actually call today.
