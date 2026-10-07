# ci-skills use cases

This document states, use case by use case, the behaviour we want from ci-skills, in the words we used when we asked
for it. Each use case quotes its source message as (session, date), the session being the first eight characters of
the working-session id, and points to the phase document under `docs/phases/` that owns its exact output shape; this
document never restates a schema. Status is one of shipped, partial, planned (specified in a phase document, not built)
or missing (neither built nor fully planned), and "(inferred)" marks a step that is our reading rather than a quoted
ask. Quotes keep our own spelling; whitespace is normalised, `...` marks a cut, and a link we pasted is named in the
line after the quote. Dates are the UTC date of the message. Some quotes are draft text we pasted into a message and
adopted; they are quoted as ours. Paths are from the repository root, and `core/` is the Python package at
`ci-skills/lib/python/core/`; line numbers were read back against `a2d98c3` on 2026-10-07.

## How an agent uses ci-skills

Commands named in this document are the skill's entrypoints in `ci-skills/bin/`, shipped or planned;
`ci-skills/tools.json` lists the shipped ones and is generated from `ci-skills/lib/python/core/catalog.py` by
`tools/render_manifest.py`. `ci-skills/SKILL.md` is the skill's agent entry point, `README.md` the repository's
user-facing protocol, `target.toml.template` the shape we ship for the access file, `docs/phases/` the phase plans
indexed by `docs/README.md`, and `.coordination/` the tracked coordination notes. Each rule names its owner, or says it
has none yet.

### Using the skill

- **Check ci-skills first.** Before a raw `glab` or `kubectl` call, the agent checks whether ci-skills has the tool,
  picks it and runs it. Owner: `ci-skills/SKILL.md:16-17`.
- **Read the tool before describing it.** The source is each command's real options and behaviour. Owner:
  `ci-skills/SKILL.md:70-75` (`tools.json` and `--describe`); planned: `docs/phases/CI09-REFERENCE.md:376-381`.
- **Small pointers, not dumps.** One level per call, a keyword returns where to look next, `SKILL.md` stays focused
  and references load on demand. Owner: `docs/phases/CI09-REFERENCE.md:376-381` (the navigator), `:364-374`
  (loading), `:1733-1778` (compact output).
- **Each node is a run set and a read set.** Expanding a pointer yields a subset node; a new tool adds rows and so
  extends the graph. Owner: `docs/phases/CI09-REFERENCE.md:393-411` (grammar), `:1715`.
- **Read more only when needed.** Answer from the reference the tool points to; no online search and no crawling of
  local code when a pointer exists. Owner: `docs/phases/CI09-REFERENCE.md:364-374`; no owner yet for the no-search
  rule.
- **Bounded responses.** Past a limit on bytes, entries or summary length, a response returns an explicit continuation
  or narrowing call, never a silent cut. Owner: `docs/phases/CI09-REFERENCE.md:1126-1131`, `:1759-1762`.
- **Reuse what exists.** A pointer invokes an existing command; no new registry, workflow engine or background
  service. Owner: `docs/phases/CI09-REFERENCE.md:1768-1770`.
- **Read at run time.** Only endpoints and their access come from `~/.ci-skills/target.toml`; pods, nodes,
  milestones, merge requests, pipelines and namespaces are read live, with no optional or ad-hoc argument for them. No
  owner yet. CONFLICT, shipped: `ceph_cluster.py` requires `--namespace` (`ci-skills/tools.json:227-228`),
  `access_check.py` takes `--ceph-namespace` (`ci-skills/tools.json:88`) and `cilium_status.py` takes
  `--namespace NAME|auto` (`ci-skills/tools.json:309`); planned: `docs/phases/CI11-TOOLS.md:139-142`
  (`--ceph-namespace`, `--cilium-namespace`) and `docs/phases/CI02-CLI.md:202-203` (`--cilium-namespace`). Open: a
  filter over records a tool already read, such as `storage_report.py --namespace`, narrows output instead of locating
  an object; whether the rule covers filters is not decided.
- **The access file holds pointers.** Each endpoint is paired with a pointer to its access, and
  `gate-ci-skills-endpoints` keeps every other key out (UC-32). Owner: `target.toml.template:14-15`; gate: pull
  request #47 (open); CONFLICT: `target.toml.template:85-103`.
- **One call, one consolidated summary.** The agent does not run many commands and post-process them. Owner:
  `docs/phases/CI11-TOOLS.md:740-745`; `README.md:36-38`.
- **Parallel fan-out.** Reads across many objects run in parallel; async is declared and used only where truly
  needed. Owner: `docs/phases/CI11-TOOLS.md:223-231`.
- **Facts, not decisions.** A tool collects and combines the facts; the agent and we decide what to do next. Owner,
  in part: `ci-skills/SKILL.md:192` (the node commands repair nothing) and `README.md:356-358` (an action code is a
  prompt for investigation, not a root cause).
- **Done means read back.** A combo reports completion and success separately, keeps incomplete reads explicit, and
  never takes pipeline success as proof of a publish. Owner: `docs/phases/CI11-TOOLS.md:343-349`, `:363-374`.
- **Specified is not available.** Planned stays apart from available now; (inferred) required behaviour is
  implemented, not deferred as "lands in". Owner: `docs/phases/CI09-REFERENCE.md:415-416`;
  `docs/phases/CI11-TOOLS.md:21-25`; `docs/README.md:3-4`.

### Building a tool

- **One tool, one repeatable task.** It gives the same result on any target of its kind. Owner:
  `docs/phases/CI11-TOOLS.md:8-10`, `:174`.
- **Thin main over a shared library.** The library may provide an abstraction, one shared library rules Python and
  Bash, there is never a second implementation, and a Python port does not copy the Bash. Owner:
  `docs/phases/CI10-PHASES.md:31-32`, `:145-161`; `docs/phases/CI11-TOOLS.md:175-180`, `:214-215`.
- **Object-oriented Python.** Classes, abstractions and interfaces, with a reasoned choice of method, class method or
  abstract method, and no loose helpers. No owner yet; CONFLICT: `docs/phases/CI10-PHASES.md:169-172` adds an
  abstraction only where two implementations exist.
- **Layout and dependency direction.** Entrypoints in `ci-skills/bin/`, development executables in `scripts/`,
  libraries in `ci-skills/lib/bash/` and `ci-skills/lib/python/`; dependencies flow from executables into libraries,
  nothing local imports CI behaviour, and when code disagrees the code changes. Owner:
  `docs/phases/CI10-PHASES.md:19-32`, `:51-63`; no owner yet for the last clause.
- **Generalize once.** Build one tool and use it instead of repeating ad-hoc steps or scratch scripts. No owner yet
  (closest: `docs/phases/CI10-PHASES.md:31-32`).
- **One argument standard.** Every tool has `--help`, `--json` and `--yaml` and a machine-readable spec, enforced by
  a gate. Owner: `ci-skills/tools.json:857-871` (`universal_options`); `docs/phases/CI02-CLI.md:111-183`; the gate is
  planned at `:184-215`.
- **Versioned output schema.** Each output has a versioned JSON Schema (Draft 2020-12 for new ones) with explicit
  extension rules; schema, spec, examples, validator, implementation and tests change together, and a gate checks
  that each exists. Owner: `docs/phases/CI07-SCHEMA.md:5-19`, `:264-337`; `docs/phases/CI10-PHASES.md:183-188`.
- **Proof by live read-back.** Every phase delivers its delivery, test and live read-back evidence against the test
  project and cluster before QA or a pull request; an exit code is not proof. Owner:
  `docs/phases/CI10-PHASES.md:83-90`, `:236-238`; `docs/phases/CI06-TESTS.md:72-83`.
- **Live systems are test benches.** The skill resolves targets and never provisions a runner, GitLab provider or
  Kubernetes route for this repository. Owner: `README.md:255`; `ci-skills/SKILL.md:67`; no owner in git yet for the
  no-runner rule.
- **Official skill layout.** Layout, scopes, invocation and metadata follow the official Claude and OpenAI skill
  documentation. Owner: `docs/phases/CI01-CATALOG.md:218-224`; `docs/phases/CI09-REFERENCE.md:167-168`.

### Writing the docs

- **Files, not chat.** Deliverables go to `docs/` in git through a pull request, with phase names, one index and no
  second copy that disagrees; the agent always gives the file path. Owner: `docs/README.md:1-25`; no owner yet for
  giving the path.
- **Anchor every named thing.** For each script, function, variable, value or capability, say who produces it, where
  it comes from, and whether and when it must be built. No owner yet.
- **Concrete specs.** Every task says what to implement and where, by full path, with concrete exhibits (inline
  schema, human and JSON renders, render order on expansion) and short comments. Owner:
  `docs/phases/CI11-TOOLS.md:8-10`; `docs/phases/CI09-REFERENCE.md:419-426`; `docs/phases/CI07-SCHEMA.md:68-113`.
- **One authority per rule.** Change the owning document with an exact diff; never add an overlapping specification.
  Owner: `docs/phases/CI09-REFERENCE.md:383-391`; `docs/phases/CI08-ROUTING.md:35-37`.
- **Grounded references only.** Every file, path or schema a document names exists, and an empty document is a
  defect. Owner: `docs/phases/CI07-SCHEMA.md:214-218`; `docs/phases/CI09-REFERENCE.md:414`; no owner yet for links
  between documents, and `README.md:24`, `:65` and `:68` link the absent `ci-skills/lib/core/`.
- **Examples are the specification.** Re-read our earlier answers and generalize from them instead of asking again.
  No owner yet.
- **Merges keep everything.** Map each item we asked for to its place and remove nothing; consolidating copies the
  text into git instead of rewriting it, and corrections go on top in a separate pull request. No owner yet.
- **Open question, not a rule.** Specs for agents carry ordered renders, inline schemas and exact declarations, never
  test lists or file names alone; we asked this with a "?" and have not confirmed it. Practised by
  `docs/phases/CI09-REFERENCE.md:419-426`; no owner.

### Working in this repository

- **Queue in your own lane.** Coordination work is queued and claimed in the agent's own lane of the local
  `.internal/` queue, not in a temporary worktree. Owner: `.coordination/coordination.md:4-5`.
- **Pull request, then QA, then merge.** Every change goes through a pull request handled with the pr-coordinator
  skill and passes QA on its exact head before it merges. Owner: `.coordination/pr-coordinator-policy.md:20-63` (the
  QA remediation loop).
- **Do not overload the machine.** When asked, find runaway processes, stop them and confirm the cleanup. No owner
  yet.
- **No agent attribution.** No agent-instruction files in the repository and no agent attribution in pull requests or
  commits; naming Claude or Codex as skill subject matter is fine. Owner: `.gitignore:19-27`; no owner yet for pull
  request and commit text.
- **Keep work in git.** Work is git-tracked so it is not lost; tokens and time are limited, so lost work is not
  re-derived. No owner yet.

## UC-01. Prove access to the selected authorities before any read

**The ask.**

> access_check.py checks the three selected authorities and runs the storage, event, and Cilium collector reads.
> --publication also requires repository admin permission and read-back of required branch checks. --job-url URL also
> checks that job, pipeline, runner, and trace.

(session 1f71c81b, 2026-10-02), quoting `README.md` as it read that day; at `a2d98c3` the same bullet says "runs the
declared live collector reads" and adds `--ceph-namespace` (`README.md:311-314`).

**What the agent does.**

1. We ask whether the agent can work against the selected GitHub, GitLab and cluster targets, for a publication
   receipt, or for one GitLab job.
2. The agent runs `access_check.py`, which checks the three selected authorities and, on the same run, performs the
   storage, event and Cilium collector reads.
3. For a publication receipt it adds `--publication`, which also requires repository admin permission and reads back
   the required branch checks.
4. For one job it adds `--job-url URL`, which also checks that job, its pipeline, its runner and its trace.
5. (inferred) The agent reports each check from the tool output (`credential_sources`, `surfaces.<name>.identity`,
   `blocking_live_checks`), not from memory.

**What it must not do.**

- Describe or route to `access_check.py` without having read what it does: "did you even read this tools" (session
  1f71c81b, 2026-10-02).

**Served by today.** Shipped: `access_check.py` (`ci-skills/tools.json:83-119`), with the credential chain per
authority in its `access_protocol` table (`ci-skills/tools.json:2-65`) and what each check proves in the skill's
access reference (`ci-skills/references/access.md`). Gap: none for the ask, but the committed receipts no longer match
the skill digest (`docs/phases/CI10-PHASES.md:219-221`, read back 2026-10-06).

**Exact output.** Not locked yet; the committed receipt `tests/acceptance/receipts/operator-laptop.json` shows the
shape, and `ci-skills/references/access.md:80` names the full three-authority receipt.

## UC-02. Resolve which GitLab, cluster and Harbor we work with, and our access to each, from target.toml

**The ask.**

> that file need have only pointer to secrete , k8s cluster and gitlab or any other end point we add paired with it
> access.

(session 279dd962, 2026-10-07)

> that file what project ci-skills point and token what k8s im working and where is access what harbor im working and
> what is my access

(session 279dd962, 2026-10-07)

> that it it access file it provide ci-skills i.e default location ... inside user project can overwrite same key to
> project specifics

(session 279dd962, 2026-10-07)

**What the agent does.**

1. We run any ci-skills command; it needs to know which GitLab project, which Kubernetes cluster and which Harbor we
   work with, and how to reach each.
2. The command reads `~/.ci-skills/target.toml`, the default access file ci-skills provides; inside a project, that
   project's file may override the same keys with its own values.
3. From that file it learns each endpoint and the pointer to its access: the GitLab project and its token, the cluster
   and its kubeconfig, Harbor and its access. The file holds pointers, never secrets.
4. Every other object the command needs (pods, nodes, milestones, merge requests, pipelines, namespaces) is read from
   the live system at run time, so commands take no optional arguments for them.

**What it must not do.**

- Add optional arguments for objects the tool can read at run time: "Never introduce optional args for object we can
  read from run-time and real system" (session 279dd962, 2026-10-07).
- Keep platform routing or diagnostic data in the access file (UC-03).

**Served by today.** Partial: target selection (`target_protocol`, `ci-skills/tools.json:831-856`;
`README.md:226-257`) reads argv, `$CI_SKILLS_TARGET`, `./.ci-skills/target.toml`, then `~/.ci-skills/target.toml`,
and `target.toml.template`, the shape we ship for that file, gives `[github]`, `[gitlab]` and `[kubernetes]` their
`token_file` and `kubeconfig` pointers (`target.toml.template:30-83`). Gap: there is no `[harbor]` table
(`ci-skills/lib/python/core/target.py:373-377` refuses it), a project file replaces the user-tier file whole instead
of overriding single keys (`ci-skills/SKILL.md:32-33`), and the file carries node routing under
`[kubernetes.node_diagnostics]` (`target.toml.template:85-103`), which UC-03 rejects.

**Exact output.** Not locked yet; the file shape is `target.toml.template:1-103` and the selection order is
`ci-skills/tools.json:831-856`.

## UC-03. Find a platform-specific location (OpenShift node journal) without putting it in target.toml

**The ask.**

> read this chat log if ocp has some specification location ... a) two question our workflow can find at runtime ...
> b) or it better include in reference as part of tree

(session 279dd962, 2026-10-07)

**What the agent does.**

1. A diagnostic needs a location specific to one platform, for example where the OpenShift machine-config-daemon
   exposes the node journal.
2. The question is whether OpenShift itself specifies where the node journal is read from.
3. Option (a): the workflow finds the location at run time. Option (b): the location ships in the skill's reference
   tree. Open decision: the ask poses (a) or (b) as a question, and we have not chosen. Read back 2026-10-07 for (a):
   `oc adm node-logs --help` (client 4.22.9) reads a node's systemd journal through the node log endpoint, with no
   Pod, namespace or mount path; the same help text limits node logs to privileged node administrators
   (`system:node-admins`, `nodes/log`).
4. Either way, the location never goes into `~/.ci-skills/target.toml`.

**What it must not do.**

- Put platform routing (namespace, selector, container, directory, `host_path`) into `target.toml`; we rejected the
  pasted `[kubernetes.node_diagnostics.journal]` block (session 279dd962, 2026-10-07).

**Served by today.** Missing: `ceph_kernel.py` (`ci-skills/tools.json:239-266`) and `cilium_node.py`
(`ci-skills/tools.json:267-292`) read their Pod route from `[kubernetes.node_diagnostics]`
(`ci-skills/lib/python/core/target.py:311-355`). Gap: CONFLICT, the committed route in the access file
(`target.toml.template:85-103`, `README.md:151-152`, `ci-skills/SKILL.md:184-186`,
`ci-skills/lib/python/core/target.py:410-422`) contradicts this ask, no run-time discovery exists, and
`docs/phases/CI08-ROUTING.md:117-118` declines platform-specific references, so neither option has an owner yet.

**Exact output.** Not locked yet.

## UC-04. Install the skill at any scope and have the agent pick it from its description

**The ask.**

> so official laylout ... can choose a skill when your task matches the skill `description` ... Indicate our skill
> need work same way in scopes

(session 279dd962, 2026-10-06); the middle fragment is skill documentation text we pasted in that message.

> me need include metadata this one example

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We install ci-skills at any supported scope and invoke it by mention, or the agent picks it because the task
   matches its `description`.
2. The package follows the official skill layout: `SKILL.md`, plus optional `scripts/`, `references/`, `assets/` and
   `agents/openai.yaml`.
3. The skill works the same at each scope of the official table: repo, user, admin and system; the paths are in
   `docs/phases/CI01-CATALOG.md:218-266`.
4. The package carries `agents/openai.yaml` metadata: interface fields, invocation policy and tool dependencies.

**What it must not do.** None recorded.

**Served by today.** Partial: the installers `install.sh` and `tools/install_ci_skills.py` install into
`$CODEX_HOME/skills/ci-skills` or `--skills-dir` (`README.md:177-216`), and the `description` in the
`ci-skills/SKILL.md` front matter (`ci-skills/SKILL.md:1-8`) drives implicit invocation. Gap: there is no `--scope`
flag, no `ci-skills/agents/openai.yaml` and no `ci-skills/assets/`, the metadata schema is missing
(`docs/phases/CI07-SCHEMA.md:37`), and the default scope is open decision D-SCOPE (`docs/phases/CI10-PHASES.md:257`).

**Exact output.** Not locked yet; the metadata field list is `docs/phases/CI01-CATALOG.md:226-261`.

## UC-05. Discover what the skill can do by walking one level per call

**The ask.**

> so it act as blackbox you give you keyword it give where to look next

(session 279dd962, 2026-10-06)

> -> gitlab -> pipeline -> watch

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We give a task such as watching a GitLab pipeline or a Harbor question; the agent routes to ci-skills first.
2. The agent calls one discovery entrypoint with a keyword or path, for example `gitlab`, `pipeline` or `harbor`.
3. Each call returns only that level or the selected resource: its subresources, the commands or combos it can run,
   the references it can read, and the exact next invocation.
4. (inferred) The agent follows one pointer at a time, `gitlab -> pipeline -> watch`, and stops at the node that
   answers the task.
5. When the agent already knows the operation or reference, it addresses it directly instead of walking from the root.
6. The discovery answer has its own versioned schema.
7. Open naming: our exhibits name the CI branch both `reference.py next ci pipeline` and the path `gitlab`,
   `pipeline`; `docs/phases/CI09-REFERENCE.md:1563-1589` declares the groups `gitlab`, `k8s`, `claude` and `harbor`.

**What it must not do.**

- Read large JSON or text to find the next step: "so agent don't read massive json files massive text and more walk"
  (session 279dd962, 2026-10-06).
- Return the whole tree: "One entrypoint does not mean returning the entire tree in one response." (session 279dd962,
  2026-10-06).
- Require reading `tools.json`, a reference's generated `index.json`, the docs tree or complete CLI help to choose the
  next step; those are the discovery tool's inputs (session 279dd962, 2026-10-06).
- Force root-to-leaf navigation when the operation or reference is already known (session 279dd962, 2026-10-06).
- Bind pointers to one GitLab instance; this is our reading of "a do not only point to same gitlab we have" and needs
  confirmation (session 279dd962, 2026-10-06).

**Served by today.** Planned: `ci-skills/bin/reference.py next` (`docs/phases/CI09-REFERENCE.md:376-418`,
`:1717-1731`); today `ci-skills/SKILL.md:70-95` sends the agent to the routing map in `ci-skills/tools.json:802-830`,
the dump this use case replaces. Gap: `reference.py` and `core/navigate.py` do not exist at `a2d98c3`, and
`schemas/reference-next.schema.json` ships with no producer.

**Exact output.** `docs/phases/CI09-REFERENCE.md:419-1048` (Renders 1-14, human and JSON) and
`schemas/reference-next.schema.json`.

## UC-06. Each level says what you can run and what you can read, with the exact next call, within fixed bounds

**The ask.**

> give what you RUN what you can READ but not array massie small pointer

(session 279dd962, 2026-10-06)

> Each includes a short purpose and its exact next invocation.

(session 279dd962, 2026-10-06)

**What the agent does.**

1. At any level, the agent reads one small answer and sees which entries it can execute and which it can read.
2. Pointer actions are typed: `expand`, `describe`, `execute`, `read`, `retrieve-result`.
3. An execute pointer exposes the operation and its required inputs, or points to its compact description; missing
   inputs are explicit.
4. A read pointer carries a short purpose, its anchor, when to read it and how to retrieve it.
5. The same answer is machine-readable and carries its schema version; its fields are owned by
   `schemas/reference-next.schema.json`.
6. Past a limit on serialized bytes, entries per response or summary length, the answer returns an explicit
   continuation or a narrowing call.
7. A planned verb is shown as unavailable and is never callable (`docs/phases/CI09-REFERENCE.md:415-416`).

**What it must not do.**

- Dump large JSON or a large index (session 279dd962, 2026-10-06).
- Recursively embed child resources, contracts, schemas, reference bodies, logs or reports (session 279dd962,
  2026-10-06).
- Silently drop entries: "Never silently omit entries or truncate" (session 279dd962, 2026-10-06).
- Grow the default response with the full contents of new capabilities (session 279dd962, 2026-10-06).
- Bypass the limits through a free-form field: "Do not use an unrestricted extensions object" (session 279dd962,
  2026-10-06).

**Served by today.** Planned: the contract values and limits (`docs/phases/CI09-REFERENCE.md:1069-1133`) and the
compact, versioned, pointer-based output (`:1733-1778`); `schemas/reference-next.schema.json` ships. Gap: its
producer, `ci-skills/bin/reference.py`, is not built, so no shipped command emits typed pointers.

**Exact output.** `docs/phases/CI09-REFERENCE.md:561-698` (Renders 4-6: run, describe, optional inputs) and
`:1050-1133` (draw rules, contract values, limits).

## UC-07. Short, user-friendly names that cover all of our tools

**The ask.**

> we have my tools and you indicate only glab and k8s diagnostic plus it massive name short make it use friendly

(session 1f71c81b, 2026-10-02)

> k8s-diag is ok

(session 1f71c81b, 2026-10-02)

**What the agent does.**

1. We invoke or read about a capability by name; names are short and easy to use, with `k8s-diag` as an
   accepted example.
2. The listing covers our own tools as well as `glab` and the Kubernetes diagnostics.
3. (inferred) The agent asks us which tools "my tools" means instead of guessing the list.

**What it must not do.**

- List only `glab` and the Kubernetes diagnostics (session 1f71c81b, 2026-10-02).
- Use long names (session 1f71c81b, 2026-10-02).

**Served by today.** Partial: one package named `ci-skills` (decided 2026-10-02, `docs/phases/CI10-PHASES.md:225-226`)
and the planned navigator groups `gitlab`, `k8s`, `harbor` and `claude` (`docs/phases/CI09-REFERENCE.md:1563-1589`).
Gap: command file names stay long under the `<domain>_<noun>.py` rule (`docs/phases/CI02-CLI.md:168-170`), for
example `k8s_verify_mtu_consistency.py`, and none of our own scripts is ported yet (`docs/phases/CI11-TOOLS.md:31-69`).

**Exact output.** `docs/phases/CI09-REFERENCE.md:428-532` (Renders 1-2, the short group and subresource names).

## UC-08. See what the skill can do now versus what is only planned

**The ask.**

> it does not give the reader a consolidated answer about what is available now.

(session 279dd962, 2026-10-06)

> Make the existing docs/README.md the authoritative project-and-capability overview. Do not add another phase
> document to solve this.

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We, or an agent, ask which capabilities ci-skills offers now and which are only planned.
2. The answer is one table in `docs/README.md` with the columns
   `Capability | What the agent can accomplish | Available or planned | Details`.
3. The table covers every capability `ci-skills/SKILL.md` routes to (for example `storage_report.py`,
   `event_trace.py`, `cilium_status.py`), not only the rows of `docs/phases/CI11-TOOLS.md`.
4. It also covers skill discovery and installation, vendored upstream skills, references and navigation, and
   selective loading and routing.
5. Each row keeps specified apart from available; a proposed phase is marked planned.

**What it must not do.**

- Solve the gap with another phase document (session 279dd962, 2026-10-06).

**Served by today.** Missing: `README.md:6-30` lists current capabilities in prose and `ci-skills/SKILL.md:77-95`
routes the shipped commands. Gap: `docs/README.md` has no capability table, and `docs/phases/CI11-TOOLS.md:18` points
at a "Capabilities" section of `docs/README.md` that does not exist.

**Exact output.** Not locked yet.

## UC-09. GitLab work goes through the existing glab

**The ask.**

> did you add step by step plan how we consume and integrate existing glab I asked

(session 1f71c81b, 2026-10-02)

**What the agent does.**

1. We ask for GitLab work; the agent routes to ci-skills first.
2. (inferred) The ci-skills command calls the existing `glab` instead of a second GitLab client.
3. GitLab work that no ci-skills command declares is handed to `glab`'s own skill
   (`docs/phases/CI08-ROUTING.md:98-115`).
4. A step-by-step plan in the docs states how ci-skills consumes and integrates `glab`.

**What it must not do.**

- Leave out the step-by-step `glab` plan we asked for (session 1f71c81b, 2026-10-02).

**Served by today.** Partial: every GitLab command runs `glab api` through `GlabAPIClient`
(`ci-skills/lib/python/core/gitlab_api.py:262`, `:305`), the access check runs `glab auth status`
(`ci-skills/lib/python/core/access.py:344`), and the plan is "How we consume the skills that the installed `glab`
ships" (`docs/phases/CI05-VENDOR.md:216-243`). Gap: `glab`'s skills are not vendored (no `vendor/` at `a2d98c3`) and
the hand-off reference `references/conditional/gitlab-writes.md` is planned (`docs/phases/CI08-ROUTING.md:98-115`).

**Exact output.** Not locked yet.

## UC-10. Read one GitLab job by its URL

**The ask.**

> gitlab_job.py --job-url URL reads a selected job, pipeline, runner, and bounded trace. It accepts --search TEXT.

(session 1f71c81b, 2026-10-02), quoting `README.md` as it read that day; at `a2d98c3` it adds "using GitLab-only
access" (`README.md:315-316`).

**What the agent does.**

1. We give a GitLab job URL and ask what happened in that job.
2. The agent runs `gitlab_job.py --job-url URL` with that URL; the tool reads the job, its pipeline, its runner and a
   bounded trace.
3. When we look for a specific message, the agent narrows the trace with `--search TEXT`.
4. (inferred) When the failure needs cluster context, the agent reads `event_trace.py` over the job's own window
   (UC-12, UC-20).

**What it must not do.**

- Describe or route to the tool without having read what it does (session 1f71c81b, 2026-10-02).

**Served by today.** Shipped: `gitlab_job.py` (`ci-skills/tools.json:463-499`, `ci-skills/SKILL.md:82`). Gap: none
for the ask; the `gitlab_job` report kind has no schema file yet (`docs/phases/CI07-SCHEMA.md:149-156`).

**Exact output.** Not locked yet; `ci-skills/tools.json:495` states the returned record.

## UC-11. Find GitLab jobs by state and time, then fetch, track or read their logs

**The ask.**

> I need tool each tool do task as I indicate i.e fetch job, track job , get logs , all machine readble spec

(session 279dd962, 2026-10-06)

> get last job finished, stuck etc all can do interval filter on keyword easy to get dump ...

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We ask for the last failed job, what finished recently, which job is stuck, or a job's logs.
2. The agent calls one ci-skills job tool instead of composing `glab api` calls.
3. The tool selects jobs by state (last failed, last finished, stuck) and narrows by an interval and a keyword.
4. The tool can fetch a job, track it and get its logs.
5. The output follows a machine-readable spec, one dump per call.

**What it must not do.**

- Drive the raw GitLab API by hand with `glab api` (session 279dd962, 2026-10-06).

**Served by today.** Partial: `gitlab_job.py --job-url` fetches one job (`ci-skills/tools.json:463-499`); the verbs
`get`, `list`, `watch` and `logs` are planned (`docs/phases/CI11-TOOLS.md:1039-1056`). Gap: `gitlab_job.py` declares
no subcommands (`ci-skills/tools.json:497`), so nothing lists jobs by state or time, watches a job or reads its logs,
and the stuck state with its `--stuck-after` bound exists only in `docs/phases/CI11-TOOLS.md:83-88`.

**Exact output.** Not locked yet; `docs/phases/CI11-TOOLS.md:83-88` lists the fields.

## UC-12. Correlate the last failed job with what the cluster said during it

**The ask.**

> example get last failed job and then interval

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We ask why the last job failed and what the cluster reported while it ran.
2. The agent reads the last failed job of the project or pipeline.
3. (inferred) It takes the job's start and finish times, widened by a margin, as `docs/phases/CI11-TOOLS.md:761`
   reads "and then interval"; a polling interval also fits the words, so this reading needs confirmation.
4. It reads the cluster events in that window and correlates them by the job's runner node and Pod
   (`docs/phases/CI11-TOOLS.md:761-768`).
5. It returns one record: the job, the window, the events in it and the correlated subset.

**What it must not do.**

- Rely on the default event window when the job failed earlier: "the default window is the previous hour, which is
  the trap when correlating a job that failed earlier" (session 1f71c81b, 2026-10-02, quoting `README.md` as it read
  that day).

**Served by today.** Partial: the agent joins `gitlab_job.py` (`ci-skills/tools.json:463-499`) and `event_trace.py`
with `--from` and `--to` (`ci-skills/tools.json:331-373`) by hand, as `ci-skills/SKILL.md:132-135` and `:157-161`
direct; `ci_failure_trace.py` is planned (`docs/phases/CI11-TOOLS.md:761-768`). Gap: no command finds the last failed
job, and no command joins a job with its events.

**Exact output.** Not locked yet.

## UC-13. Read one pipeline, find the last one, match its jobs by name, and manage schedules

**The ask.**

> all machine readble spec same goes pipeline , pipeline scheduler

(session 279dd962, 2026-10-06)

> same goes to pipeline last pipe some keyword that allow match each job i.e some name *

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We ask for the last pipeline, the jobs in it whose names match a pattern, or about pipeline schedules.
2. The tool returns the last pipeline and matches a keyword or glob such as `some name *` against each of its jobs.
3. The same tool family covers pipeline schedules.
4. The output follows the same machine-readable spec as the job tools.
5. (inferred) The job tool's selectors and filters (interval, keyword) carry over to pipelines.

**What it must not do.**

- Compose raw `glab api` calls by hand, which the job ask rejects and "same goes to pipeline" extends (session
  279dd962, 2026-10-06).

**Served by today.** Partial: `gitlab_pipeline.py --pipeline-id` reads one pipeline (`ci-skills/tools.json:559-593`);
`gitlab_pipeline.py list --name-glob` (`docs/phases/CI11-TOOLS.md:337-341`) and `gitlab_schedule.py`
(`docs/phases/CI11-TOOLS.md:1086-1094`) are planned. Gap: `gitlab_pipeline.py` requires an exact `--pipeline-id`
(`ci-skills/tools.json:582-584`), so nothing finds the newest pipeline, matches jobs by name or reads schedules.

**Exact output.** Not locked yet.

## UC-14. Watch a pipeline and every pipeline it started until all settle

**The ask.**

> user says use ci-skill I just started pipeline watch it progress ... agent check ok do I run glab or ci-skills
> provide tools if does ok which one ok it pipeline_watch another eample

(session 279dd962, 2026-10-07)

> Every pipeline in scope is accounted for; incomplete reads remain explicit; completion and success are reported
> separately.

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We have started a pipeline, for example a deploy-all root that triggers child and component pipelines, and ask
   the agent to watch its progress.
2. The agent checks whether ci-skills provides a tool before using `glab`, walks `gitlab -> pipeline -> watch`, and
   runs the watch.
3. (inferred) Project, ref and token come from the target at run time; with no pipeline id, the newest pipeline is
   read from live state.
4. The watch resolves the pipeline and discovers its jobs, bridges and linked downstream pipelines, across projects
   (`docs/phases/CI11-TOOLS.md:357-364`).
5. It also tracks newer pipelines that the root's jobs started through the API, which carry no bridge.
6. It reports each job, bridge and pipeline once, when it reaches a terminal status (success, failed, canceled,
   skipped, manual), and polls in between within a bounded interval and overall wait.
7. It ends when the root and every pipeline in scope have settled; every pipeline is accounted for, incomplete reads
   stay explicit, and completion and success are reported separately.
8. The result points to failed jobs and pipelines instead of inlining component reports; the agent and we decide
   what to do next.

**What it must not do.**

- Hand-write a per-run scratch watcher with a hard-coded project id and token path; we pasted such a watcher as the
  counter-example (session 279dd962, 2026-10-06).
- Merge completion and success into one verdict (session 279dd962, 2026-10-06).
- Inline every component report (session 279dd962, 2026-10-06).
- Present the watch as implemented: "not claims that the commands already exist" (session 279dd962, 2026-10-06).

**Served by today.** Partial: `gitlab_pipeline.py --pipeline-id` reads one pipeline once, without bridges or polling
(`ci-skills/tools.json:559-593`); `gitlab_pipeline.py watch` is planned (`docs/phases/CI11-TOOLS.md:357-464`) and
`schemas/gitlab-pipeline-watch.schema.json` ships without a producer (`docs/phases/CI07-SCHEMA.md:62-64`). Gap: no
watch verb exists at `a2d98c3`, and the planned watch requires `--pipeline-id` (`docs/phases/CI11-TOOLS.md:383`), so
"I just started pipeline" first needs the planned `gitlab_pipeline.py list` with `--limit 1`
(`docs/phases/CI11-TOOLS.md:337-341`).

**Exact output.** `docs/phases/CI11-TOOLS.md:376-464` (Pipeline watch, exact: interface, human and JSON render) and
`:466-736` (its schema); `docs/phases/CI09-REFERENCE.md:534-698` (Renders 3-6).

## UC-15. Quick runner view: tags, state, running, attached, executor

**The ask.**

> same for runner for example quick get runner tags, state , does it run  ,is ttached, is docker or k8s etc

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We ask whether a GitLab runner is usable.
2. The agent gets one quick view of the runner in one call: its tags, state, whether it is running, whether it is
   attached, and whether its executor is docker or Kubernetes.
3. (inferred) The view is machine-readable with `--json` and `--yaml`, like every other tool.

**What it must not do.** None recorded.

**Served by today.** Planned: `gitlab_runner.py list|get` (`docs/phases/CI11-TOOLS.md:1108-1127`); today
`gitlab_runner.py` assigns, creates, and tags runners (`ci-skills/tools.json`), and `gitlab_job.py` returns a
job's runner (`ci-skills/tools.json:495`). Gap: no runner read verb exists, the executor type needs a new
`GlabAPIClient.graphql_json` (`docs/phases/CI11-TOOLS.md:106-107`), and "attached" maps to the planned `projects`
field.

**Exact output.** Not locked yet.

## UC-16. Check a merge request's checks, like the PR checker in gh-fix-ci

**The ask.**

> here example for pr checker we can add mr checker job cheker pipeline cheker ?

(session 279dd962, 2026-10-06), right after our link to `scripts/inspect_pr_checks.py` in the gh-fix-ci skill of
openai/skills.

**What the agent does.**

1. We ask whether a GitLab merge request's checks pass, or what state a job or pipeline is in.
2. The tool follows the pattern of `inspect_pr_checks.py` from gh-fix-ci, as a merge-request checker for GitLab.
3. Job and pipeline checkers are an open question in the ask, not a decision.
4. (inferred) Job and pipeline checkers reuse the job and pipeline readers instead of a second implementation.

**What it must not do.** None recorded.

**Served by today.** Planned: `gitlab_mr.py check` (`docs/phases/CI11-TOOLS.md:1096-1106`); the pattern is recorded
in `docs/plans/2026-10-06/reference-and-docs-readjust.md:204-206`, and until then merge-request work is handed to
`glab`'s skill (`docs/phases/CI08-ROUTING.md:104-106`). Gap: no merge-request command exists at `a2d98c3`.

**Exact output.** Not locked yet.

## UC-17. Create or update a milestone, label the selected work items, and check their MRs

**The ask.**

> cmd you can run create , update delete

(session 279dd962, 2026-10-06)

> Create or resolve the milestone ... apply the specified associations and labels to the selected work items ...
> Here, “tag” is treated as a label; Git repository tags must remain a separate, explicitly specified operation.

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We ask the agent to create or reuse a GitLab milestone, label the selected issues, and check that the related
   merge requests carry the expected milestone and labels.
2. The milestone node lists the commands it can run: create, update, delete.
3. (inferred) Before labelling, the agent reads only the label family it needs (UC-26).
4. The combo creates the milestone, or resolves it if it exists, and applies the specified associations and labels to
   the selected work items; "tag" means a label.
5. It inspects the related merge requests and checks each one's milestone and labels.
6. Completion evidence: the exact milestone and work-item identities, the changes made, and a check per merge
   request.

**What it must not do.**

- Treat "tag" as a Git repository tag (session 279dd962, 2026-10-06).

**Served by today.** Partial: `gitlab_milestone.py create|update|adjust-time` with read-back
(`ci-skills/tools.json:500-558`) and `gitlab_issue.py open-bug` with `--label` and `--milestone-id` at creation
(`ci-skills/tools.json:412-462`); the combo is planned (`docs/phases/CI11-TOOLS.md:370-374`). Gap: no milestone delete
verb exists in `ci-skills/tools.json:540` or any phase document, no command changes the labels or milestone of an
existing issue (`README.md:87-89` lists it as proposed), and the merge-request check is planned (UC-16).

**Exact output.** Committed results `tests/acceptance/receipts/milestone-create-applied.json` and
`tests/acceptance/receipts/milestone-create-no_op.json`; `docs/phases/CI09-REFERENCE.md:700-739` (Render 7, the
milestone run level).

## UC-18. Check the health of our cluster in one call

**The ask.**

> example use ask agent invoke ci-skills check health of my k8s ... list of CNI and storage provide list machine spec
> list of crds like nvidia etc operator that core system ... 30 k8s cmd reduce to 1 all at runtime

(session 279dd962, 2026-10-07)

> that combo can include ok cni is ok, mtu ok, controller ok, ceph is ok all in one go summary state give consolidate
> view in one go no 20 cmd agent run, parse , jq etc

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We ask the agent to use ci-skills to check the health of our cluster, or a composite question such as whether
   "ceph is ok".
2. The agent invokes one ci-skills entrypoint, a thin `main()` over reusable library APIs.
3. (inferred) The cluster and its access come from `~/.ci-skills/target.toml` (UC-02).
4. At run time the tool finds the CNI, the storage providers, the machine spec, the CRDs (for example NVIDIA) and the
   operators of the core system.
5. It checks each component in one go, for example CNI, MTU, controllers and Ceph, with independent checks in
   parallel (UC-22).
6. It returns one consolidated summary state of the main facts and names any failed component.
7. The agent and we decide the next action; the tool's job ends at reporting facts.

**What it must not do.**

- Require components, nodes, pods or namespaces as arguments when the tool can read them (session 279dd962,
  2026-10-07).
- Fix anything or choose the next action (session 279dd962, 2026-10-07).
- Make the agent run many commands and post-process the output (session 279dd962, 2026-10-06).

**Served by today.** Planned: `cluster_health.py` (`docs/phases/CI11-TOOLS.md:740-760`, `:996-1003`); its parts ship
separately as `cilium_status.py`, `ceph_cluster.py`, `k8s_verify_mtu_consistency.py`, `storage_report.py` and
`event_trace.py`. Gap: `cluster_health.py` is not built, its planned components are a fixed list rather than
discovered (`docs/phases/CI11-TOOLS.md:747-754`), and, CONFLICT, it plans `--ceph-namespace` and `--cilium-namespace`
(`docs/phases/CI11-TOOLS.md:139-142`), optional arguments for objects the cluster can report; the shipped
`ceph_cluster.py` already requires `--namespace` (`ci-skills/tools.json:227-228`).

**Exact output.** Not locked yet; nearest are the catalog entry (`docs/phases/CI11-TOOLS.md:189-206`) and the
component table (`docs/phases/CI11-TOOLS.md:747-754`).

## UC-19. Find why a volume or claim is stuck

**The ask.**

> storage_report.py correlates PVCs, standalone PVs, Pods, attachments, and controllers. Filters: --namespace
> NAME|all, --node NAME, --storage-class NAME, --phase Pending|Bound|Lost|Released|Failed|all, and --search TEXT.

(session 1f71c81b, 2026-10-02), quoting `README.md`, which reads the same at `a2d98c3` (`README.md:318-320`).

**What the agent does.**

1. We ask why a volume or a Pod's storage is stuck, possibly for one namespace, node or storage class.
2. The agent runs `storage_report.py`, which correlates PVCs, standalone PVs, Pods, attachments and controllers.
3. The agent narrows with the filters that match the question: `--namespace`, `--node`, `--storage-class`, `--phase`
   and `--search`.

**What it must not do.**

- Describe or route to the tool without having read what it does (session 1f71c81b, 2026-10-02).

**Served by today.** Shipped: `storage_report.py` (`ci-skills/tools.json:743-781`, `ci-skills/SKILL.md:84`). Gap:
none for the ask; the `storage_report` kind has no schema file yet (`docs/phases/CI07-SCHEMA.md:149-156`).

**Exact output.** Not locked yet.

## UC-20. Read what the cluster said over the right time window

**The ask.**

> event_trace.py reads both Kubernetes event APIs and accepts --last (5m, 90s, 2h, 7d), --from, --to, --namespace,
> --kind, --object, --reason, and --search. --last 15m is shorter than computing an RFC3339 pair; the default window
> is the previous hour, which is the trap when correlating a job that failed earlier.

(session 1f71c81b, 2026-10-02), quoting `README.md` as it read that day; at `a2d98c3` it says the tool prefers
`events.k8s.io/v1` and falls back to core Events (`README.md:321-324`).

**What the agent does.**

1. We ask what happened in the cluster around a failure, often a GitLab job that failed more than an hour ago.
2. The agent runs `event_trace.py`.
3. For a recent window it passes `--last` with a duration (for example `15m`) instead of computing an RFC3339 pair.
4. When correlating with a job that failed earlier, it sets the window to cover the job's time, with `--from` and
   `--to` or a long enough `--last`.
5. It narrows with `--namespace`, `--kind`, `--object`, `--reason` and `--search` as the question requires.
6. Zero records with zero errors means the events aged out, not that the read failed (`ci-skills/SKILL.md:151-153`).

**What it must not do.**

- Rely on the default one-hour window when the failure is older (session 1f71c81b, 2026-10-02).
- Compute an RFC3339 pair when a relative duration answers the question (session 1f71c81b, 2026-10-02).

**Served by today.** Shipped: `event_trace.py` (`ci-skills/tools.json:331-373`, `ci-skills/SKILL.md:132-135`). Gap:
CONTRADICTION on which event APIs are read: the quote and `ci-skills/tools.json:361`, `:369` (rendered from
`ci-skills/lib/python/core/catalog.py:374`, `:387`) say both, deduplicated, while the code reads `events.k8s.io` first
and core Events only as a fallback (`ci-skills/lib/python/core/collect.py:384-426`), as `README.md:321` says.

**Exact output.** Not locked yet.

## UC-21. Check Cilium health across the cluster or on one node

**The ask.**

> cilium_status.py reads Cilium resources and executes non-TTY health on ready agents. It accepts --namespace
> NAME|auto, --node, and --search.

(session 1f71c81b, 2026-10-02), quoting `README.md`, which reads the same at `a2d98c3` (`README.md:325-327`).

**What the agent does.**

1. We ask whether cluster networking (Cilium) is healthy, overall or on one node.
2. The agent runs `cilium_status.py`, which reads Cilium resources and runs a non-TTY health check on ready Cilium
   agents.
3. It passes `--namespace NAME|auto`, `--node` and `--search` as the question requires.
4. For one affected node, the agent follows with `cilium_node.py` (`ci-skills/SKILL.md:228-229`).

**What it must not do.**

- Describe or route to the tool without having read what it does (session 1f71c81b, 2026-10-02).

**Served by today.** Shipped: `cilium_status.py` (`ci-skills/tools.json:293-330`) and `cilium_node.py`
(`ci-skills/tools.json:267-292`). Gap: CONFLICT with "Read at run time": `--namespace NAME|auto` asks for a location
the tool can already discover (`auto`), and `docs/phases/CI02-CLI.md:202-203` plans renaming it to
`--cilium-namespace` rather than removing it.

**Exact output.** Not locked yet.

## UC-22. Collect from many pods or nodes in parallel

**The ask.**

> spec also include async call if needed ONLY if need and it where TRULY needed ... example on it clear task that
> scape collect for many pods MUST alway paraller ... there is not point doing naive tools that does in loop same cmd
> in k8s and ocp.

(session 279dd962, 2026-10-06)

> some tools for example now do serial task many can be done concurently like take k8s state

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We ask for cluster state that needs reads from many pods or nodes in Kubernetes or OpenShift.
2. The tool runs those reads in parallel instead of looping one command per object.
3. Serial tools such as "take k8s state" become concurrent.
4. The tool's spec says when it makes async calls, and it makes them only where truly needed.

**What it must not do.**

- Loop the same command per pod (session 279dd962, 2026-10-06).
- Use async where it is not needed (session 279dd962, 2026-10-06).

**Served by today.** Partial: the shipped collectors already use bounded thread pools
(`ci-skills/lib/python/core/collect.py:92`, `:713`, `:820`; `ci-skills/lib/python/core/live.py:169`;
`ci-skills/lib/python/core/access.py:867`; `ci-skills/lib/python/core/ceph_cluster.py:245`), and the rule is
`docs/phases/CI11-TOOLS.md:223-231` (threads; async only as a separate contract for a tool that holds many open
streams; a per-object loop is a defect). Gap: `k8s_state.py` is not built, and the shared parallel-read helper with
per-read durations is planned (`docs/phases/CI10-PHASES.md:118`).

**Exact output.** Not locked yet.

## UC-23. OpenShift full view (routes, HA, machines) and actions such as building an ISO

**The ask.**

> osp specific need have concrete full view ocp route , ocp ha , ocp machine space, ocp action like build iso .

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We ask for the state of an OpenShift cluster, or for an OpenShift action such as building an ISO.
2. The agent requests one concrete full view covering routes, HA and "machine space".
3. For an action, the agent invokes an OpenShift action tool, for example "build iso".
4. (inferred) Read views and actions are separate verbs, and an action ends with a live read-back (UC-33).

**What it must not do.**

- Leave the implementation open-ended or invented: "make sure we do not have open end implementation or underground
  invention." (session 279dd962, 2026-10-06).

**Served by today.** Planned: `ocp_route.py`, `ocp_ha.py`, `ocp_machine.py` and `ocp_iso.py`
(`docs/phases/CI11-TOOLS.md:892-977`); the shipped OpenShift pieces are `k8s_verify_mtu_consistency.py`
(`ci-skills/tools.json:702-742`) and the `bin/ci-binary-build` plan (`ci-skills/tools.json:162-196`). Gap: none of
the four tools is built, and `docs/phases/CI11-TOOLS.md:932-946` reads "machine space" as a machine view
(MachineSets, Machines, BareMetalHosts), not disk space, which needs confirmation.

**Exact output.** Not locked yet.

## UC-24. Build the toolbox image, push it to Harbor, create a Harbor robot, and verify the publish, on any Harbor

**The ask.**

> action like build toolbox , push to harbor , create harbor robot,  so each small tool agent invoke it does
> repeatable task ... example build pipeline for toolbox need standardized so it skill produce same way for any
> harbor  ~/.ci-skills/ has config entry for harbor tool use agent use python main that does it out of the box.

(session 279dd962, 2026-10-06)

> Selected source revision, pipeline/job identities, produced image digest, and Harbor read-back agree. A successful
> pipeline alone is insufficient.

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We ask the agent to build the toolbox image, push it to Harbor or create a Harbor robot account, or to build and
   publish the toolbox through GitLab and confirm the image is in Harbor.
2. The agent invokes one small tool per repeatable action: build toolbox, push to Harbor, create Harbor robot.
3. The tool takes the Harbor target from its entry in the `~/.ci-skills/` config; the agent does not supply it.
4. The tool is a Python `main` that works out of the box, and the toolbox build is standardized so the result is the
   same for any Harbor.
5. The end-to-end combo starts the declared GitLab build and publish workflow, reuses pipeline watch (UC-14), reads
   the produced image identity and verifies the artifact in Harbor.
6. Completion evidence: source revision, pipeline and job identities, image digest and Harbor read-back agree.

**What it must not do.**

- Define ad-hoc arguments: "no random args define" (session 279dd962, 2026-10-06).
- Treat a successful pipeline as proof of publication (session 279dd962, 2026-10-06).
- (inferred) Re-implement pipeline watching inside the combo; the ask says "reuse pipeline watch" (session 279dd962,
  2026-10-06).

**Served by today.** Planned: `toolbox_build.py`, `harbor_push.py`, `harbor_robot.py`, `harbor_sanity.py`,
`harbor_charts.py` and `harbor_pull_secret.py` (`docs/phases/CI11-TOOLS.md:1164-1285`), the `[harbor]` and
`[toolbox]` target keys (`docs/phases/CI11-TOOLS.md:247-280`) and the combo (`docs/phases/CI11-TOOLS.md:365-369`);
the related shipped `bin/ci-binary-build` only plans an exact-commit BuildConfig (`ci-skills/tools.json:162-196`).
Gap: there is no Harbor authority (`ci-skills/lib/python/core/catalog.py:64` declares GitHub, GitLab and Kubernetes
only, and `ci-skills/lib/python/core/target.py:373-377` refuses a `[harbor]` table), and nothing builds, pushes or
creates a robot.

**Exact output.** Not locked yet.

## UC-25. Load one upstream doc section (GitLab CI trigger:forward) on demand, kept current by a lifecycle

**The ask.**

> propose how I create reference for ... so it provide some way to load on demand in skills,  ground on claude
> documentation and openai.

(session 279dd962, 2026-10-06); the cut is our link to the `trigger:forward` section of GitLab's CI YAML reference.

> how we can add improve reference so we have some lifecycle we fetch collect structure etc

(session 279dd962, 2026-10-06)

**What the agent does.**

1. An agent needs one upstream section, for example GitLab CI `trigger:forward`, while working on a pipeline.
2. (inferred) The agent reaches it as a readable item at its discovery level, under `gitlab pipeline`.
3. The skill loads that one section on demand, not up front, by a concrete algorithm for load, knowledge and memory.
4. References are kept current by a lifecycle: fetch, collect, structure.
5. The reference-building scripts follow the `openai-docs` scripts of openai/skills.
6. The design is grounded on the Claude and OpenAI skill documentation.

**What it must not do.**

- Leave the design ungrounded or vague: "luck of grounded detail , concrete algorithms way , to optimize load ,
  knowledge, memory and skills" (session 279dd962, 2026-10-06).
- Implement a second copy instead of reusing the Python: "note I notice some agent start doing dual implementation
  and never re-using python" (session 279dd962, 2026-10-06).

**Served by today.** Planned: knowledge references (`docs/phases/CI09-REFERENCE.md:162-374`) and the reference
lifecycle (`docs/phases/CI09-REFERENCE.md:1780-1812`); today `ci-skills/references/access.md` and
`ci-skills/references/project-binding.md` ship and are read whole. Gap: there is no `ci-skills/references/vendor/`,
no `reference.py get` and no `tools/update_reference.py` at `a2d98c3`.

**Exact output.** `docs/phases/CI09-REFERENCE.md:253-279` (the index record for `trigger:forward`) and `:852-883`
(Render 10, one section, human and JSON); `schemas/reference-section.schema.json`.

## UC-26. Read only the label family you need

**The ask.**

> here massive doc ... do w eneed to read all this no

(session 279dd962, 2026-10-06)

> it need know types severity priorty i.e clustered information

(session 279dd962, 2026-10-06)

**What the agent does.**

1. Before setting a label, for example a priority on a milestone's issue, the agent needs to know which labels exist.
2. The labels reference is clustered by family (type, severity, priority and the others), so the agent picks the
   family before reading values.
3. The agent expands `tags -> priority` and gets only the Priority labels section: `priority::1` to `priority::4` and
   the pointer to the triage handbook.
4. (inferred) The agent then applies the label with the command its node lists (UC-17).

**What it must not do.**

- Read the whole labels page for one family (session 279dd962, 2026-10-06).

**Served by today.** Planned: the `gitlab-labels` reference and its placement (`docs/phases/CI09-REFERENCE.md:1655`,
`:1670-1672`) and the `meaning_in` relation for priority and severity (`docs/phases/CI07-SCHEMA.md:295-316`). Gap:
`gitlab-labels` is not vendored and the navigator is not built.

**Exact output.** `docs/phases/CI09-REFERENCE.md:768-883` (Render 9, the label cluster with relations; Render 10, the
priority labels).

## UC-27. Answer an MCP question from the declared MCP references, never by searching

**The ask.**

> I ask show me json that enable mcp in gitlab you ar enot allowed search online

(session 279dd962, 2026-10-06)

> i,e. reference for glab mcp could also include nothing to do with glab just point last specification for eample

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We ask a concrete question such as "show me json that enable mcp in gitlab", with online search not allowed.
2. The agent addresses `gitlab -> mcp` instead of searching.
3. The `mcp` node returns small pointers: what the agent can run and what it can read.
4. The agent follows the reference pointer to the concrete section, reads more only if it needs to, and answers from
   it.
5. The same reference can point to the `glab` docs, Claude's MCP documentation and the latest MCP specification, even
   one unrelated to `glab`.
6. (inferred) Each such pointer is a typed read pointer with an anchor and when-to-read, and repeated pointer calls
   keep the context small.

**What it must not do.**

- Search online (session 279dd962, 2026-10-06).
- Crawl local package directories for MCP code; we pasted the agent's `find` loop as the counter-example (session
  279dd962, 2026-10-06).
- Read large amounts of source for a simple question (session 279dd962, 2026-10-06).

**Served by today.** Planned: the nodes `gitlab mcp`, `claude` and `claude mcp` (`docs/phases/CI09-REFERENCE.md:1573`,
`:1585-1589`) and the references `gitlab-mcp-server`, `claude-code-mcp` and `mcp-spec`
(`docs/phases/CI09-REFERENCE.md:1704-1713`). Gap: the MCP nodes stay hidden until their references are vendored
(`docs/phases/CI09-REFERENCE.md:1589`), and an `mcp-spec` that follows the latest version needs a second
`ReferenceSource`, because a specification site is not a file at a commit (`docs/phases/CI09-REFERENCE.md:312-314`).

**Exact output.** `docs/phases/CI09-REFERENCE.md:954-984` (Render 13, "mcp: which one?").

## UC-28. Add a tool: port a source script as a small main over a named shared library

**The ask.**

> so you gal need be concrete point to script indicate how it need indicat epoint full path take this script copy to
> ci-skills adopt and how adopt

(session 279dd962, 2026-10-06)

> example if I read thid doc where it said that what library it need introduce

(session 279dd962, 2026-10-06)

**What the agent does.**

1. We port a Bash script from the source repository (`$SOURCE_REPO`, as the CI11 inventories name it) into
   ci-skills.
2. For each source script, the plan names its full path, the ci-skills entrypoint it becomes, and how it is adopted:
   copy, then adapt.
3. The script is ported to Python behind the unified interface.
4. The tool is a small `main` that calls a library, and the library may provide an abstraction other tools reuse.
5. The tool's plan states which shared Python library it needs or introduces, and what abstraction.
6. The entrypoint goes in `ci-skills/bin/` and the logic in the Bash or Python library core, per the fixed layout.
7. The rule applies to every phase, not only the Harbor toolbox.

**What it must not do.**

- Implement the same behaviour twice instead of reusing the Python library (session 279dd962, 2026-10-06).
- Leave the plan open-ended or invented (session 279dd962, 2026-10-06).

**Served by today.** Planned: the ten-step port recipe (`docs/phases/CI11-TOOLS.md:168-237`), the library module per
tool (`docs/phases/CI11-TOOLS.md:31-75`), the source rows by path relative to the source root
(`docs/phases/CI11-TOOLS.md:859-1285`), and `core/action.py` for plan, apply and read-back
(`docs/phases/CI11-TOOLS.md:218-222`). Gap: the inventory we read for this
(`docs/plans/2026-10-06/ci11-inventories/toolbox-harbor.md`) lists source files only, the library per tool is named
only in the CI11 catalogue table, and `docs/phases/CI10-PHASES.md:169-172` adds an abstraction only where two
implementations exist, so for Harbor the answer today is `core/action.py` alone.

**Exact output.** `docs/phases/CI11-TOOLS.md:189-213` (one full catalog entry and how it renders into `tools.json`).

## UC-29. Extend the command contract: add a tools.json entry and its schema from one full example

**The ask.**

> what is real tools.json you need indicate sample we adding now ? so we need format how we exectend what is contract

(session 279dd962, 2026-10-06)

> how we validate schema, what tool we add , how we promote version, how related to reference do we have any pointer
> how reference relate to tools

(session 1f71c81b, 2026-10-02)

**What the agent does.**

1. We, or an agent, add a capability and need the contract for extending `tools.json` and adding its schema.
2. The owning schema document states what a new schema must include and shows one full example schema, not a
   description of one.
3. It shows a sample of the real `tools.json` entry being added and the format for extending it.
4. It names the validating tool, how a version is promoted, and how a reference points to the tools it describes.
5. Every schema file or path the document names exists at that path.
6. (inferred) A capability the contract requires is implemented, not deferred as "lands in" a later phase.

**What it must not do.**

- Cite schema files or paths that do not exist: "where is schema file you indicate in this table" (session 279dd962,
  2026-10-06).
- Describe the contract without a full example: "put one example full schema" (session 279dd962, 2026-10-06).
- (inferred) Defer required behaviour as "lands in" a later phase: "Lands in not land MUST IMPLEMENTED" (session
  279dd962, 2026-10-06).

**Served by today.** Partial: `tools/render_manifest.py` renders `ci-skills/tools.json` from
`ci-skills/lib/python/core/catalog.py`; `schemas/skill-manifest.schema.json` and
`schemas/command-contract.schema.json` ship; the contract and example are `docs/phases/CI07-SCHEMA.md:68-113`,
versions and promotion `:264-337`, and pointers between references and tools `:192-218`. Gap: the validator
(`tools/check_schemas.py`, `tools/skillkit/schema.py`) is planned (`docs/phases/CI07-SCHEMA.md:220-262`), and the
`uses` pointers between references and tools are not in the catalog yet.

**Exact output.** `docs/phases/CI07-SCHEMA.md:68-113` (contract and example), `docs/phases/CI11-TOOLS.md:189-213` (a
full catalog entry and its `tools.json` render) and `docs/phases/CI11-TOOLS.md:466-736` (one full schema).

## UC-30. Lock the navigator output: inline schema, rendered exhibits for agent and machine, render order on expansion

**The ask.**

> to lock this down as schema , example how it rendered for agent and machine readable format ... you can even
> indicate order how one render and then second render on expansion

(session 279dd962, 2026-10-06)

> and version allow extended add if we miss initially and we will miss soemthig

(session 279dd962, 2026-10-06)

**What the agent does.**

1. An implementing agent reads the spec to build or extend the navigator output, and must produce conforming output
   from the spec alone, without inventing arguments.
2. The spec states the response schema itself, not only a schema file name.
3. The spec shows each concrete object as rendered for the agent (human form) and in machine-readable form.
4. The spec gives the render order: the first render, then the render after one expansion.
5. The schema carries a version and an extension rule, so what we missed at first is added later without changing
   existing meaning.
6. The rule lives in one authoritative place in the docs.
7. Even a low-capability agent can match its output to the exhibit structurally.

**What it must not do.**

- Leave docs open-ended with no example (session 279dd962, 2026-10-06).
- Leave pointers that resolve to nothing: "pointer to nowhere" (session 279dd962, 2026-10-06).
- Leave room for divergent implementations: "now ill get 10 implemenetation 100 args invented" (session 279dd962,
  2026-10-06).
- Replace the concrete example with tests (session 279dd962, 2026-10-06).
- Cite only a schema file name: "schema.json < file name does help in specification example do" (session 279dd962,
  2026-10-06).

**Served by today.** Planned: the authority (`docs/phases/CI09-REFERENCE.md:383-391`), the renders in order
(`:419-1048`), the draw rules and values (`:1050-1133`) and the inline schemas (`:1135-1559`), with
`schemas/reference-next.schema.json` and `schemas/reference-section.schema.json` shipped and the extension rule in
`docs/phases/CI07-SCHEMA.md:280-320`. Gap: the spec is locked but its producer, `ci-skills/bin/reference.py`, is not
built, and Renders 8-13 wait for vendored references (`docs/phases/CI09-REFERENCE.md:423-426`).

**Exact output.** `docs/phases/CI09-REFERENCE.md:419-1048` (Renders 1-14) and `:1135-1559` (schemas).

## UC-31. Fail the check when a tool has no versioned schema or breaks the CLI contract

**The ask.**

> now we do we need add small gate to check for now baseline that all tool in ci-skills have schema

(session 279dd962, 2026-10-07)

> this goal not produce missing schema it goal create that make github red

(session 279dd962, 2026-10-07)

> i.e gate it has version , gate it follow

(session 279dd962, 2026-10-07)

> how we gate interface , common cli consistency

(session 1f71c81b, 2026-10-02)

**What the agent does.**

1. A change is proposed; the gate lists every tool in ci-skills and checks that each has a schema with a version.
2. A missing or unversioned schema fails the gate and turns the GitHub check red.
3. New schemas use JSON Schema Draft 2020-12, and schema, specification, examples, validator, implementation and
   tests change together (the standards text we pasted on 2026-10-07; `docs/phases/CI07-SCHEMA.md:13-19`).
4. The common command-line interface is consistent across tools, and a gate enforces it.
5. (inferred) On its first run the gate is red: `ci-skills/tools.json` declares 17 report kinds and none has its own
   schema file (`docs/phases/CI07-SCHEMA.md:147-156`).

**What it must not do.**

- Turn the gate task into writing the missing schemas (session 279dd962, 2026-10-07).

**Served by today.** Missing: the schemas gate (`tools/check_schemas.py`, `docs/phases/CI07-SCHEMA.md:220-249`) and
the CLI gate (`docs/phases/CI02-CLI.md:184-215`) are planned, and `tests/python/test_catalog.py` already compares the
catalog with each command's parser. Gap: no GitHub workflow exists (`.github/` is absent at `a2d98c3`, decision D-GATE
"no gate for now", `docs/phases/CI10-PHASES.md:227-228`), so nothing can turn GitHub red, and `schemas/` holds no
output schema of a shipped command. Pull request #47 (open) adds the first workflow, for target-file keys only
(UC-32).

**Exact output.** Not locked yet.

## UC-32. Block any commit that adds a key to the access file (gate-ci-skills-endpoints)

**The ask.**

> add gate gate-ci-skills-endpoints that block that ... i.e it block introducing random keys in that file

(session 279dd962, 2026-10-07)

> if we add new capability harbor we add new update gate

(session 279dd962, 2026-10-07)

**What the agent does.**

1. A change adds a key to the access file's shape, for example a diagnostics route.
2. `gate-ci-skills-endpoints` compares the keys with the allowed endpoint keys and their access-pointer keys.
3. A key that is neither an endpoint nor its paired access pointer fails the gate and blocks the commit or pull
   request.
4. A new endpoint capability, such as Harbor, updates the gate in the same change.
5. (inferred) One owner holds the allowed keys, and both the parser and the gate read it.

**What it must not do.**

- Allow random keys in the access file (session 279dd962, 2026-10-07).
- Accept keys like `[kubernetes.node_diagnostics.journal]` (session 279dd962, 2026-10-07).

**Served by today.** Missing at `a2d98c3`, specified in pull request #47 (open, 2026-10-07): at run time
`ci-skills/lib/python/core/target.py:107-116` refuses unknown keys in the selected target file, with the key sets at
`:245`, `:314`, `:326`, `:384` and `:410` and unknown tables refused at `:373-377`. Pull request #47 adds the gate, one
declaration of the allowed keys that the parser reads, and a `--staged` mode for a pre-commit hook; merging it alone
does not block a commit or a merge, because the hook is not installed (CI04-HOOKS) and branch protection does not
require the check. Gap: nothing at `a2d98c3` blocks a commit that widens the allowed
key sets, and those sets already include node routing (`ci-skills/lib/python/core/target.py:314`, `:326`), which
the pull request lists as value keys to remove rather than failing them.

**Exact output.** Not locked yet.

## UC-33. Live smoke proves each action by its read-back, not by an exit code

**The ask.**

> live smoke provide use this laptop collect proove so it not some sort exit code but it live read tool did this and
> collected ... specification for smoke need piece that show live evidence tool output x where x is it read back.

(session 279dd962, 2026-10-06)

> example if I create milestone read back what we posted ... if did test job , show out on read back it run , it
> could hello world we are not testing CI we making sure action executed

(session 279dd962, 2026-10-06)

**What the agent does.**

1. A phase or tool is delivered; its smoke runs the installed tool directly against the live test GitLab project and
   the live cluster.
2. The smoke records live evidence: tool output X, where X is the read-back.
3. Creating a milestone is proven by reading back what was posted; running a test job (hello world is fine) is proven
   by reading back that it ran.
4. Each phase states its delivery, its test and its proof evidence.
5. The tool output is locked by a contract, so the evidence is visibly consistent.
6. Unit and contract tests run on the gate route, and live smoke on the declared laptop executor (decision D-SMOKE,
   `docs/phases/CI06-TESTS.md:10-14`, `:74-83`; `docs/phases/CI10-PHASES.md:236-238`).

**What it must not do.**

- Treat an exit code as proof (session 279dd962, 2026-10-06).
- Turn the smoke into a test of CI itself (session 279dd962, 2026-10-06).
- Use inconsistent output or ad-hoc arguments: "output consistent , no random args define" (session 279dd962,
  2026-10-06).

**Served by today.** Partial: `tools/check_live_acceptance.py` checks the committed receipts against the
`[[gitlab_receipts]]` declared in `tests/acceptance/expected.toml`, with 17 receipts committed (applied and no-op
results for milestone, issue, wiki and runner, plus `gitlab-access` and `operator-laptop`); one `[[smoke_cases]]`
entry per tool is planned (`docs/phases/CI06-TESTS.md:96-147`, `docs/phases/CI11-TOOLS.md:770-857`). Gap: the read
tools (job, pipeline, storage, events, Cilium) have no smoke case, the `[[smoke_cases]]` verification is planned
(`docs/phases/CI03-GATES.md:177-181`), and the committed receipts no longer match the skill digest
(`docs/phases/CI10-PHASES.md:219-221`, read back 2026-10-06).

**Exact output.** `tests/acceptance/receipts/milestone-create-applied.json` (read-back shape) and
`docs/phases/CI06-TESTS.md:125-131` (the fields a receipt carries).

## UC-34. Check what is merged and reviewed before locking the architecture

**The ask.**

> I think most piece merged am I correct asking about our design and other pieces because I need lock final
> architecture and the rest before we move

(session 1f71c81b, 2026-10-02)

> did you check all unmerged PR ?

(session 1f71c81b, 2026-10-02)

**What the agent does.**

1. We ask whether most of the design is merged, so the final architecture can be locked before moving on.
2. (inferred) The agent reads merged and unmerged pull requests at the source when it answers.
3. The agent checks whether the requested review of each piece actually happened.
4. (inferred) The agent answers which design pieces are merged and which remain, with the evidence.

**What it must not do.**

- Answer without checking all unmerged pull requests (session 1f71c81b, 2026-10-02).
- Assume a review happened without checking (session 1f71c81b, 2026-10-02).

**Served by today.** Partial: `bin/ci-api get` reads one caller-selected GitHub or GitLab endpoint
(`ci-skills/tools.json:120-161`), and `.coordination/coordination.md:7-8` says to read the current pull-request head,
review and checks before acting. Gap: no ci-skills command lists open pull requests with their review state, so this
check is agent process (`gh` and the pr-coordinator skill), not a skill capability.

**Exact output.** Not locked yet.

## Index

| UC | Title | Status | Served by |
| --- | --- | --- | --- |
| UC-01 | Prove access before any read | shipped | `access_check.py` |
| UC-02 | Endpoints and access from `target.toml` | partial | `target_protocol`, `target.toml.template` |
| UC-03 | Platform locations outside `target.toml` | missing | open decision, (a) or (b) |
| UC-04 | Install at any scope, pick by description | partial | `install.sh`; CI01-CATALOG |
| UC-05 | Discover one level per call | planned | `reference.py next` (CI09-REFERENCE) |
| UC-06 | Run and read pointers, bounded | planned | `reference.py next` (CI09-REFERENCE) |
| UC-07 | Short names covering our tools | partial | navigator groups (CI09-REFERENCE) |
| UC-08 | Available now versus planned | missing | `docs/README.md`, no table yet |
| UC-09 | GitLab work through `glab` | partial | `GlabAPIClient`; CI05-VENDOR |
| UC-10 | Read one job by URL | shipped | `gitlab_job.py --job-url` |
| UC-11 | Find jobs by state and time | partial | `gitlab_job.py`; CI11-TOOLS |
| UC-12 | Last failed job with its events | partial | `gitlab_job.py` and `event_trace.py` by hand |
| UC-13 | Last pipeline, jobs by name, schedules | partial | `gitlab_pipeline.py --pipeline-id` |
| UC-14 | Watch a pipeline and its downstream | partial | `gitlab_pipeline.py watch` (CI11-TOOLS) |
| UC-15 | Quick runner view | planned | `gitlab_runner.py get` (CI11-TOOLS) |
| UC-16 | MR checks like gh-fix-ci | planned | `gitlab_mr.py check` (CI11-TOOLS) |
| UC-17 | Milestone, labels, MR check | partial | `gitlab_milestone.py`, `gitlab_issue.py` |
| UC-18 | Cluster health in one call | planned | `cluster_health.py` (CI11-TOOLS) |
| UC-19 | Why a volume or claim is stuck | shipped | `storage_report.py` |
| UC-20 | Events over the right window | shipped | `event_trace.py` |
| UC-21 | Cilium health | shipped | `cilium_status.py`, `cilium_node.py` |
| UC-22 | Parallel collection | partial | thread pools in `core/collect.py` |
| UC-23 | OpenShift view and actions | planned | `ocp_*.py` (CI11-TOOLS) |
| UC-24 | Toolbox and Harbor actions | planned | `toolbox_build.py`, `harbor_*.py` (CI11-TOOLS) |
| UC-25 | Upstream section on demand | planned | knowledge references (CI09-REFERENCE) |
| UC-26 | One label family | planned | `gitlab-labels` reference (CI09-REFERENCE) |
| UC-27 | MCP answers from references | planned | MCP nodes (CI09-REFERENCE) |
| UC-28 | Port a script over a shared library | planned | port recipe (CI11-TOOLS) |
| UC-29 | Extend `tools.json` and its schema | partial | `tools/render_manifest.py`; CI07-SCHEMA |
| UC-30 | Lock the navigator output | planned | renders and schemas (CI09-REFERENCE) |
| UC-31 | Red check without a versioned schema | missing | `tools/check_schemas.py`, planned |
| UC-32 | Block new access-file keys | missing | `gate-ci-skills-endpoints`, specified in pull request #47 |
| UC-33 | Smoke proves by read-back | partial | `tools/check_live_acceptance.py` |
| UC-34 | Merged and reviewed before locking | partial | `bin/ci-api get` |
