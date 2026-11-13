# Enable an OLM operator on OpenShift and apply its shipped resources from one ci-skills command

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and
`Outcomes & Retrospective` must be kept up to date as work proceeds. It is maintained in accordance with
[docs/PLANS.md](../../PLANS.md) and delivers the phase [CI16][phase]. Source baseline: `92ce002`.

## Purpose / Big Picture

OpenShift installs add-on software through the Operator Lifecycle Manager (OLM). OLM is the cluster service that
reads a request object named a Subscription and then downloads, installs and upgrades the requested package. An
"operator" in this document always means such an OLM-installed package, for example the NVIDIA Network Operator.
OpenShift ships without it.

An administrator tells an agent "enable the network operator". The cluster may already have it, may have it on
another channel, or may not have it at all. Without ci-skills the agent finds out by trial and error whether OLM is
present, which catalog and channel to use, whether an OperatorGroup exists, when the install is finished, and what
to create afterwards so the operator does anything.

After this change one command, `ocp_operator.py`, answers on a cluster in any state. It either does the step, or
reports that the step is already done, or reports what is needed and how the caller supplies it. It never guesses a
value for the cluster and never changes an object that already exists.

The operator works after two calls. A third kind of call applies optional pieces. Package and namespace are
abbreviated `P` and `N` here; for the NVIDIA Network Operator both are `nvidia-network-operator`.

    ocp_operator.py install --package P --namespace N --live-plan
    ocp_operator.py install --package P --namespace N --apply --confirm-plan <sha256>

    ocp_operator.py apply --package P --namespace N --kind NicClusterPolicy \
        --shipped-default --wait-for status.state=ready --live-plan
    ocp_operator.py apply --package P --namespace N --kind NicClusterPolicy \
        --shipped-default --wait-for status.state=ready --apply --confirm-plan <sha256>

    ocp_operator.py apply --package P --namespace N --kind MacvlanNetwork --spec my-network.yaml --live-plan

The first pair is the step named enable: it creates the Namespace, the OperatorGroup and the Subscription and waits
until the package reports it is installed. The second pair is the step named install: it applies the custom resource
the package itself ships as its example, so the operator deploys its components. The last call is the optional step
named use: the caller supplies a document with values for their site.

On a cluster that already has the package, the first call prints this and writes nothing:

    kind: olm_operator
    action: install
    status: NO_OP
    package: nvidia-network-operator
    channel: v26.7
    source: certified-operators / openshift-marketplace
    installed_csv: nvidia-network-operator.v26.7.0-1   phase=Succeeded
    created_by_ci_skills: false

## Progress

- [x] (DEC-CI16-01 to DEC-CI16-21) Design decisions recorded in the Decision Log.
- [x] (CI16-P1) This ExecPlan, the phase document and the plan index row are written.
- [ ] (CI16-M1) Target table `[registry.<name>]`, result schema, models, the read-only resolve callbacks and
      `--live-plan` for `install` and `apply`; one live plan of each.
- [ ] (CI16-M2) `install` apply chain, qualified resource names in `ResourceSet`, `wait_phase` refactor, catalog
      entry, `tools.json`; live run on a cluster that already has the package (`NO_OP`).
- [ ] (CI16-M3) `apply` chain with `--shipped-default`, `--spec`, `--wait-for` and the registry Secret callback; live
      apply of the shipped `NicClusterPolicy` and read-back of its status.
- [ ] (CI16-M4) `remove` for a custom resource and for the package; live remove of what CI16-M3 created.
- [ ] (CI16-M5) Two references, installed-copy proof, evidence files and renewed skill-digest receipts.

## Surprises & Discoveries

These observations come from read-only reads of one OpenShift 4.19.26 cluster.

- Observation: the package was already installed on the cluster, on a channel that is not the default.
  Evidence: Subscription `nvidia-network-operator`, channel `v26.7`, source `certified-operators`, approval
  `Automatic`, installed CSV `nvidia-network-operator.v26.7.0-1`; PackageManifest default channel `stable`, channels
  `stable,v25.10,v25.7,v26.1,v26.4,v26.7`. Effect: DEC-CI16-06.
- Observation: two API groups define a kind named `Subscription`.
  Evidence: `kubectl api-resources` lists `subscriptions operators.coreos.com/v1alpha1` and
  `subscriptions postgresql.cnpg.noobaa.io/v1`. On this cluster `kubectl get subscription/<name>` resolved to the OLM
  group. Effect: DEC-CI16-13.
- Observation: the package ships example custom resources, and they are readable before install.
  Evidence: the CSV annotation `alm-examples` holds `NicClusterPolicy`, `NicNodePolicy`, `MacvlanNetwork`,
  `HostDeviceNetwork` and `IPoIBNetwork`; the PackageManifest channel entry carries the same annotation. Effect:
  DEC-CI16-03.
- Observation: the shipped `NicClusterPolicy` has no site value; the shipped network examples do.
  Evidence: the policy sets `ofedDriver` and `rdmaSharedDevicePlugin` only. `MacvlanNetwork` sets `master: ens2f0`
  and an address range `192.168.2.225/24` with `ipam.type: whereabouts`; `IPoIBNetwork` sets `master: ibs3f1`.
  Effect: DEC-CI16-08 and DEC-CI16-17.
- Observation: the shipped policy enables no address plugin, and the shipped `MacvlanNetwork` asks for one.
  Evidence: same two documents. Effect: the reference states the dependency; the command holds no such rule.
- Observation: a CSV that belongs to another package sits in the install namespace.
  Evidence: `tetragon-operator.v1.18.3   Succeeded   Copied` in namespace `nvidia-network-operator`. Effect:
  DEC-CI16-14.
- Observation: OLM labels the Subscription and the CSV with package and namespace.
  Evidence: label `operators.coreos.com/nvidia-network-operator.nvidia-network-operator`. Effect: DEC-CI16-14. Not
  verified for names long enough to exceed the label length limit.
- Observation: the CSV declares no required CRDs, and no login for `nvcr.io` is configured.
  Evidence: `spec.customresourcedefinitions.required` is empty; the cluster pull secret lists `cloud.openshift.com`,
  `quay.io`, `registry.connect.redhat.com`, `registry.redhat.io`; the controller Pod image is on `nvcr.io/nvidia`
  and the Pod is `Running`. Effect: DEC-CI16-07 and DEC-CI16-18. Whether the driver image the policy pulls needs a
  login is not verified; no policy has been applied.
- Observation: the status of `NicClusterPolicy` has a field `state` with the values `ignore`, `notReady`, `ready`
  and `error`.
  Evidence: the CRD schema of `nicclusterpolicies.mellanox.com`. Effect: DEC-CI16-12. Whether the operator acts only
  on a policy named `nic-cluster-policy` is not verified.
- Observation: `docs/PLANS.md` requirement 7 asks every ExecPlan to link `docs/gates.md`, and that file does not
  exist. Effect: this plan links the [repository gates][repo-gates] section of the plan index instead.
- Observation: `ci-skills/references/ocp-node-image.md` is not registered in `PROPOSITION_REFERENCES`, so
  `reference.py get ocp-node-image` returns `reference_unknown`. Effect: none in this phase; recorded for the owner
  of that reference.

## Decision Log

Every decision below has the Governing Ref "team approval recorded with the CI16 phase" unless another is named.

- Decision DEC-CI16-01: three steps in order. Enable is Namespace, OperatorGroup and Subscription. Install is the
  shipped default custom resource that makes the operator deploy its components. Use is any further shipped kind.
  Status: ACCEPTED
  Rationale: OpenShift has no network operator until the Subscription exists, and the operator does nothing until
  its policy exists.
- Decision DEC-CI16-02: enable and install are required for "the operator works"; use is optional.
  Status: ACCEPTED
  Rationale: a network needs site values and an address plugin that not every caller wants.
- Decision DEC-CI16-03: ci-skills does not re-implement the operator. It applies the document the package ships in
  `alm-examples` and reads the status the operator's controller writes.
  Status: ACCEPTED
  Rationale: the operator already knows what to deploy and reports its own state.
- Decision DEC-CI16-04: the library holds no cluster value. Channel, catalog source, install mode, shipped examples
  and object names are read from the cluster at plan time.
  Status: ACCEPTED
  Rationale: a hardcoded value that does not match another cluster returns the agent to trial and error.
- Decision DEC-CI16-05: the caller may override channel, catalog source, catalog source namespace, approval,
  starting CSV and install mode. Each override is checked against what the PackageManifest lists.
  Status: ACCEPTED
  Rationale: these are the caller's choices; the default for each is what the cluster states.
- Decision DEC-CI16-06: an existing Subscription for the package counts as enabled, whatever its channel. The
  command reports a difference only when the caller passed an override that conflicts with it.
  Status: ACCEPTED
  Rationale: the cluster used for discovery has the package on `v26.7` while the default channel is `stable`.
- Decision DEC-CI16-07: ci-skills checks no prerequisite packages.
  Status: ACCEPTED
  Rationale: OLM installs the dependencies a package declares.
- Decision DEC-CI16-08: `apply` creates nothing unless the caller passes exactly one of `--shipped-default` or
  `--spec <file>`. With neither, the answer shows the shipped example and the choices, and issues no plan digest.
  Status: ACCEPTED
  Rationale: a shipped example can hold a placeholder such as `ens2f0`; creation must be a stated choice.
- Decision DEC-CI16-09: object names are never typed. They come from the shipped document or from the `--spec`
  file. The Subscription is named after the package and the OperatorGroup after the namespace.
  Status: ACCEPTED
  Rationale: every shipped example carries its own name.
- Decision DEC-CI16-10: an existing object is never overwritten. Same name and same spec is `NO_OP`. Same name and a
  different spec stops the command with both specs shown.
  Status: ACCEPTED
  Rationale: the caller decides between keeping and replacing.
- Decision DEC-CI16-11: nothing is refused because of who created it. `remove` works on any Subscription for the
  package. The plan states `created_by_ci_skills` and lists what will be deleted and what will be left without a
  controller, and proceeds on a matching plan digest.
  Status: ACCEPTED
  Rationale: caller's choice, with the impact shown first. This supersedes an earlier draft that blocked on
  ownership.
- Decision DEC-CI16-12: the caller states when an applied resource is done, with `--wait-for <field>=<value>`. The
  field is checked against the CRD schema on the cluster before any write.
  Status: ACCEPTED
  Rationale: generic code cannot know which status field means done for an arbitrary kind.
- Decision DEC-CI16-13: every OLM object and every applied custom resource is addressed by its qualified resource
  name, for example `subscriptions.operators.coreos.com`.
  Status: ACCEPTED
  Rationale: where the bare kind resolves to another API group, a read returns nothing and the following apply
  overwrites an existing Subscription that the plan said was absent.
  Governing Ref: `ci-skills/lib/python/core/kubernetes/resources.py`, method `apply`.
- Decision DEC-CI16-14: the installed CSV is found through `Subscription.status.installedCSV`, then through the OLM
  label. A CSV whose `status.reason` is `Copied` is ignored.
  Status: ACCEPTED
  Rationale: a remove interrupted halfway must still find the CSV, and a copied CSV belongs to another package.
- Decision DEC-CI16-15: every answer that is not finished carries a `needs` list. Each entry has `need`, `evidence`
  and `supply`.
  Status: ACCEPTED
  Rationale: the agent's next call is in the answer.
- Decision DEC-CI16-16: needs are inferred only from what the cluster states: the PackageManifest, Subscription
  conditions, CSV requirement status, the CRD schema through a server-side dry run, the applied object's status, and
  a named object that does not exist. The command does not inspect Pods, logs or node hardware.
  Status: ACCEPTED
  Rationale: DEC-CI16-03.
- Decision DEC-CI16-17: for a caller's `--spec`, ci-skills enforces the interface only. Shape is validated by the
  cluster through a server-side dry run. Site values are not judged.
  Status: ACCEPTED
  Rationale: only the caller knows the site.
- Decision DEC-CI16-18: a registry credential is a file named in the target file, in a new table
  `[registry.<name>]` with the same keys and rules as `[harbor]`. The callback that creates a pull Secret runs only
  when a matching table is present, and writes only inside the package namespace. The cluster-wide pull secret is
  never edited.
  Status: ACCEPTED
  Rationale: credentials are stored one way in this repository; presence of the entry is the caller's consent.
  Governing Ref: `target.toml.template`, table `[harbor]`.
- Decision DEC-CI16-19: the three steps are separate calls, each with its own plan and digest. `remove --kind`
  deletes one applied object by UID, so the steps can be undone in reverse order. `remove` without `--kind` removes
  the package and deletes the Namespace only when ci-skills created it and it is empty. CRDs always stay.
  Status: ACCEPTED
  Rationale: the install step loads drivers and may drain nodes, so it needs its own confirmation.
- Decision DEC-CI16-20: the "use" step ends at what the operator reports. ci-skills starts no test Pod.
  Status: ACCEPTED
  Rationale: a traffic test needs its own image, node selection and cleanup and is a later phase.

- Decision DEC-CI16-21: the reference shows one golden path, the exact sequence of calls that was run live and
  reached a working operator. What the live install step finds out (whether the shipped policy reaches `ready`,
  whether the driver image needs a login, which policy name the operator accepts) is written into that path before
  the phase closes.
  Status: ACCEPTED
  Rationale: a skill shows the path that works; an agent copies it and does not rediscover it.

## Outcomes & Retrospective

Nothing is implemented yet. This section is filled at the end of each milestone.

## Context and Orientation

ci-skills is a set of Python command-line tools under `ci-skills/bin/`. Each file there is a short entrypoint that
imports `build_parser` and `main` from a module under `ci-skills/lib/python/core/`. The generated file
`ci-skills/tools.json` lists every command; it is produced from `ci-skills/lib/python/core/catalog.py` by
`tools/render_manifest.py` and is never edited by hand.

A command that changes a cluster is built from callbacks. A callback is one class with up to four methods, `collect`,
`run`, `update` and `cleanup`, defined in `ci-skills/lib/python/core/abstract/callback.py`. `WorkflowExecutor` in
`ci-skills/lib/python/core/executor.py` runs a list of callbacks in order and then runs every `cleanup` in reverse
order, on success as well as on failure. The callbacks share one state object, a dataclass; an earlier callback
writes a field and a later callback reads it. Kubernetes callbacks subclass `K8SCallback` in
`ci-skills/lib/python/core/kubernetes/kubernetes.py`. The first callback of every chain is `K8SAuth`, which checks
the selected cluster and puts a `KubernetesService` into the state. `KubernetesService` is the only place that runs
`kubectl`; it has `read`, `apply`, `delete` and `json` methods. A callback joins a chain only when its input is
present; for example `ci-skills/lib/python/core/kubernetes/image_build_readiness.py` adds its writing callbacks only
in apply mode.

`ResourceSet` in `ci-skills/lib/python/core/kubernetes/resources.py` applies one object and proves it. Its `apply`
method adds two labels, `app.kubernetes.io/managed-by=ci-skills` and `ci-skills.spyroot.dev/operation=<operation>`,
compares the desired object with the cluster, applies only when they differ, reads the object back, and returns
`{kind, name, namespace, uid, action}` where `action` is `APPLIED` or `NO_OP`. It raises
`resource_not_owned_by_operation` when the object already exists without those labels, so a callback in this plan
reads first and calls `apply` only for an object that is absent. Its `cleanup_created` method deletes only the
objects the current run created.

A changing command has modes, defined in `ci-skills/lib/python/core/action_modes.py`. `--live-plan` reads the cluster
and prints a plan and its plan digest, which is the SHA-256 of the plan computed by `plan_digest` in
`ci-skills/lib/python/core/report.py`. `--apply --confirm-plan <digest>` recomputes the plan and applies it only when
the digest matches. A mode policy, an `ActionModePolicy` instance, names the default mode and the largest accepted
`--timeout`.

The cluster a command talks to comes from the `[kubernetes]` table of `~/.ci-skills/target.toml`. The accepted keys
of that file are declared once, in `TARGET_CONTRACT` in `ci-skills/lib/python/core/endpoints.py`, parsed in
`ci-skills/lib/python/core/target.py`, and shown in `target.toml.template`. The `[harbor]` table accepts either
`credential_file`, or `username_file` together with `password_file`; a relative path resolves beside the target
file; the function `_parse_harbor` enforces that exactly one form is given.

A reference is a Markdown file under `ci-skills/references/` that an agent reads through
`ci-skills/bin/reference.py`. A command lists its references by display name in its catalog entry, and each display
name is registered in `PROPOSITION_REFERENCES` in `catalog.py`. `reference.py get <reference>` lists the `##`
sections; `reference.py get <reference> <section>` returns that section's text and pointers to its `###`
subsections. Detail therefore goes under deeper headings. A reference has exactly one `#` title.

The OLM objects this plan uses are these. A PackageManifest (API group `packages.operators.coreos.com`) is a
read-only catalog entry for one package; its `status` names the catalog source that serves the package, the default
channel, and for each channel the install modes and the example custom resources the package ships, in an annotation
named `alm-examples`. A catalog source is the cluster object that serves a set of packages. A channel is a named
stream of versions of one package. An install mode says which namespaces the installed package may watch. An
OperatorGroup (`operators.coreos.com/v1`) declares the watched namespaces for a namespace; OLM refuses to install
when a namespace has none or more than one. A Subscription (`operators.coreos.com/v1alpha1`) asks OLM to install one
package from one channel of one catalog source. An InstallPlan is the object OLM creates to carry out a
Subscription; with manual approval it waits until someone approves it. A ClusterServiceVersion, abbreviated CSV, is
the object that represents one installed version; its `status.phase` is `Succeeded` when the install is complete. A
CustomResourceDefinition, abbreviated CRD, is a cluster-wide type definition that the installed package adds. A
custom resource is one object of such a type; `NicClusterPolicy` is the custom resource that tells the NVIDIA
Network Operator which components to deploy. A pull Secret is a Kubernetes Secret that holds a registry login.

A receipt is a JSON file under `tests/acceptance/` that records one live run of an installed command. The skill
digest is a checksum of every byte under `ci-skills/`; the committed receipts are bound to it, so any change under
`ci-skills/` makes `tools/check_live_acceptance.py` fail until receipts are captured again.

## Plan of Work

The work adds one command with three actions, four library modules, one target table and two references. It extends
two existing shared pieces.

The configuration lock comes first, as `docs/PLANS.md` requires. In `ci-skills/lib/python/core/endpoints.py` add a
table `registry` to `TARGET_CONTRACT` with the endpoint field `url`, the access fields `credential_file`,
`username_file` and `password_file`, and `named_profiles=True`, which is the existing way to allow one named child
table. In `ci-skills/lib/python/core/target.py` move the either-or file check out of `_parse_harbor` into a helper
that both `_parse_harbor` and a new `_parse_registry` call. Add the commented example to `target.toml.template`. Add
the result schema.

In `ci-skills/lib/python/core/kubernetes/olm_models.py` define the action and state dataclasses, the qualified
resource names, and the pure functions that build the Namespace, OperatorGroup and Subscription documents.

In `ci-skills/lib/python/core/kubernetes/olm_callbacks.py` write the enable chain. `ResolveOperatorPackage` checks
that the OLM API exists with the raw API discovery call that `_scc_api_available` uses in
`ci-skills/lib/python/core/kubernetes/scc_access.py`. It lists PackageManifests in all namespaces, keeps those named
after the package, and groups them by catalog source name and catalog source namespace. It reads the existing
Namespace, OperatorGroups, Subscription and CSV. It applies the caller's overrides, checks each against the
PackageManifest, and writes the plan or the `needs` list into the state. `ConfirmOperatorPlan` compares
`--confirm-plan` with the digest. `EnsureOperatorNamespace`, `EnsureOperatorGroup` and `EnsureSubscription` each
create their object only when it is absent. `ApproveInstallPlan` joins the chain only with `--approval Manual`.
`WaitOperatorInstalled` waits for the CSV.

In `ci-skills/lib/python/core/kubernetes/olm_resources.py` write the apply chain. `ResolveShippedResource` reads the
shipped examples from the installed CSV, or from the PackageManifest when no CSV exists, selects the one whose kind
equals `--kind`, or loads the `--spec` file. It lists existing objects of that kind, sends the document to the
cluster as a server-side dry run, and checks the `--wait-for` field against the CRD schema. `EnsureRegistrySecret`
joins the chain only when the document names a pull Secret that does not exist and a `[registry.<name>]` table
matches the image registry host. `EnsureCustomResource` creates the object. `WaitCustomResource` polls the one
status field.

In `ci-skills/lib/python/core/kubernetes/resources.py` add an optional qualified resource name to
`ResourceSet.apply` and `ResourceSet.delete`. In `ci-skills/lib/python/core/kubernetes/kubernetes.py` move the
polling loop of `KubernetesService.wait_build` into a method that both it and the new waits call.

In `ci-skills/lib/python/core/kubernetes/olm_cli.py` write `build_parser`, `workflow` and `main` following
`ci-skills/lib/python/core/kubernetes/image_build_readiness.py`, and add the entrypoint
`ci-skills/bin/ocp_operator.py`. The chains are exported as functions that return callback lists, in the way
`prepare_tool_pod_callbacks` is, so another command can compose them.

Register the command in `ci-skills/lib/python/core/catalog.py`, regenerate `ci-skills/tools.json`, add the two
references, add one row to the routing table in `ci-skills/SKILL.md` and one line to the command list in
`README.md`, and add the new modules to the pydoclint path list in `.github/workflows/validate.yml`.

### CI16-M1: plans without writes

This milestone is a prototype that proves the cluster reads before any writing code exists. At its end the target
table, the schema, the models and the two resolve callbacks exist, and `--live-plan` works for `install` and
`apply`. The live proof is `install --live-plan` on a cluster that already has the package, which must report the
channel and CSV that `kubectl get subscription` shows, and `apply --kind NicClusterPolicy --shipped-default
--live-plan`, which must print the document that the CSV annotation holds. The prototype is promoted when both
match; it is revised when the lookup returns zero or several catalog sources for a package the web console can
install.

### CI16-M2: enable

At the end of this milestone `install --apply` creates the three objects on a cluster that lacks them and returns
`NO_OP` on a cluster that has them. The live proof is the `NO_OP` run on the cluster that already has the package,
with the same UIDs that `kubectl` shows.

### CI16-M3: install the shipped default

At the end of this milestone `apply --kind <Kind>` works with `--shipped-default` and with `--spec`. The live proof
is one apply of the shipped `NicClusterPolicy` with `--wait-for status.state=ready`, and the result carries the
status the operator wrote, whether or not it reached `ready`. This run also settles the three unverified items in
Surprises: the driver image login, the required policy name, and whether the shipped default reaches `ready` on the
nodes of that cluster.

### CI16-M4: remove

At the end of this milestone `remove --kind <Kind>` deletes one applied object and `remove` deletes the package.
The live proof removes the policy that CI16-M3 created and reads back its absence. Removing the package itself is
proved against the in-memory fake only, because the cluster's install is in use.

### CI16-M5: references, installed copy and receipts

At the end of this milestone the two references are registered and readable through `reference.py`, the live runs
have been repeated from an installed copy, their JSON output is stored under
`tests/acceptance/evidence/olm-operator/`, and the receipts bound to the skill digest are renewed so that the
`validate` job passes on the pull request head.

## Concrete Steps

All commands run from the repository root unless a step says otherwise.

### Command interface

INTRODUCED: `ci-skills/bin/ocp_operator.py`, kind `olm_operator`.

| Action | Required | Optional |
| --- | --- | --- |
| `install` | `--package`, `--namespace` | the six overrides in the next table |
| `apply` | `--package`, `--namespace`, `--kind`, and one of `--shipped-default` or `--spec` | `--wait-for` |
| `remove` | `--package`, `--namespace` | `--kind` |

Every action also takes `--live-plan`, `--apply`, `--confirm-plan`, `--timeout` and `--receipt-out`.

### Where each value comes from

| Value | When the caller passes nothing | Caller override | Check on the override |
| --- | --- | --- | --- |
| channel | `PackageManifest.status.defaultChannel` | `--channel` | listed in the PackageManifest |
| catalog source | the one source serving the package | `--source`, `--source-namespace` | serves the package |
| install mode | `OwnNamespace` when supported | `--install-mode` | in the supported list |
| approval | `Automatic` | `--approval Automatic\|Manual` | enum |
| starting CSV | not set | `--starting-csv` | a CSV of the chosen channel |
| custom resource document | none; the caller must choose | `--shipped-default` or `--spec` | server-side dry run |
| done condition | none; one read-back | `--wait-for field=value` | field exists in the CRD schema |
| object names | from the document | inside `--spec` | none |

### Objects the enable step creates

INTRODUCED, built in `olm_models.py`. Values in angle brackets come from the PackageManifest or an override.
`ResourceSet.managed` adds the two ownership labels.

    apiVersion: v1
    kind: Namespace
    metadata:
      name: <namespace>

    apiVersion: operators.coreos.com/v1
    kind: OperatorGroup
    metadata:
      name: <namespace>
      namespace: <namespace>
    spec:
      targetNamespaces:
        - <namespace>

    apiVersion: operators.coreos.com/v1alpha1
    kind: Subscription
    metadata:
      name: <package>
      namespace: <namespace>
    spec:
      name: <package>
      channel: <channel>
      source: <catalog source>
      sourceNamespace: <catalog source namespace>
      installPlanApproval: <approval>

Invariants: `spec.targetNamespaces` holds exactly the install namespace for the install mode `OwnNamespace` and is
omitted for `AllNamespaces`. `spec.startingCSV` is present only when the caller passed `--starting-csv`. The
Namespace is applied through a `ResourceSet` built with an empty namespace, because `ResourceSet.managed` writes
`metadata.namespace` whenever its set has one.

### Target table

INTRODUCED in `TARGET_CONTRACT` and `target.toml.template`. One table per registry.

    [registry.nvcr]
    url = "nvcr.io"
    credential_file = "nvcr.credentials"
    # or, the same alternative [harbor] accepts:
    # username_file = "nvcr.identity"
    # password_file = "nvcr.secret"

Invariants: exactly one form per table. A relative path resolves beside the target file. The name after `registry.`
is a label; the command matches on `url`. No value from these files appears in any output.

### Result schema

INTRODUCED: `ci-skills/schemas/results/olm-operator.schema.json`. Every object sets `additionalProperties: false`.

| Field | Type | Values and invariants |
| --- | --- | --- |
| `kind` | string | constant `olm_operator` |
| `action` | string | `install`, `apply` or `remove` |
| `mode` | string | `live_plan` or `apply` |
| `status` | string | `PASS`, `APPLIED`, `NO_OP`, `PARTIAL` or `BLOCKED` |
| `reason` | string | present with `PARTIAL` or `BLOCKED`; a code from the behavior table |
| `needs` | array | required with `BLOCKED`; items `need`, `evidence`, `supply`, all strings |
| `package`, `namespace` | string | equal to the inputs |
| `channel` | string | the channel in effect |
| `source` | object | `name` and `namespace` |
| `install_mode` | string | `OwnNamespace` or `AllNamespaces` |
| `created_by_ci_skills` | boolean | whether the Subscription carries the ownership labels |
| `plan` | object | documents to create and objects to delete; absent with `BLOCKED` |
| `plan_digest` | string | 64 hexadecimal characters; absent with `BLOCKED` |
| `impact` | array | items `kind`, `name`, `effect`; what the apply or remove changes or leaves |
| `existing` | array | `apply` only; objects of the asked kind already on the cluster |
| `resources` | array | items `kind`, `name`, `namespace`, `uid`, `action`, `owned` |
| `resources[].action` | string | `APPLIED`, `NO_OP` or `DELETED` |
| `installed_csv` | object | `name`, `phase`, `found_by` (`subscription` or `label`) |
| `resource_status` | object | `apply` only; the applied object's `status` as the operator wrote it |
| `retained` | array | `remove` only; `kind`, `name`, `uid` of what stays |

The JSON form of a blocked answer:

    {
      "kind": "olm_operator",
      "action": "apply",
      "mode": "live_plan",
      "status": "BLOCKED",
      "reason": "source_not_chosen",
      "package": "nvidia-network-operator",
      "namespace": "nvidia-network-operator",
      "existing": [],
      "needs": [
        {
          "need": "a MacvlanNetwork document",
          "evidence": "neither --shipped-default nor --spec was passed; shipped example sets master ens2f0",
          "supply": "add --shipped-default, or add --spec <file>, or stop"
        }
      ]
    }

### Behavior table

Each row is one observable outcome of the command. A `BLOCKED` row always carries `needs`.

| Action | State found | Result |
| --- | --- | --- |
| any | OLM API group absent | `BLOCKED`, `olm_api_unavailable` |
| `install` | no PackageManifest for the package | `BLOCKED`, `package_manifest_missing`; catalog sources listed |
| `install` | package served by several catalog sources, no `--source` | `BLOCKED`, `package_source_ambiguous` |
| `install` | an override is not in the PackageManifest | `BLOCKED`, `override_not_in_package`; valid values listed |
| `install` | no Subscription | plan; on confirm `APPLIED`, CSV `Succeeded` |
| `install` | Subscription exists, no conflicting override | `NO_OP`; actual channel, source and CSV reported |
| `install` | Subscription exists, an override conflicts | `BLOCKED`, `subscription_differs`; both values shown |
| `install` | two or more OperatorGroups in the namespace | `BLOCKED`, `operator_group_conflict`; neither is deleted |
| `install` | `--approval Manual`, InstallPlan waiting | plan names the InstallPlan and CSV; on confirm it is approved |
| `install` | CSV not `Succeeded` before the deadline | `PARTIAL`, `csv_wait_timeout`; objects stay |
| `apply` | package not installed | `BLOCKED`, `operator_not_installed`; next is `install` |
| `apply` | neither `--shipped-default` nor `--spec` | `BLOCKED`, `source_not_chosen`; example shown; no digest |
| `apply` | both `--shipped-default` and `--spec` | usage error, exit 2 |
| `apply` | `--kind` not among the shipped kinds | `BLOCKED`, `kind_not_shipped`; shipped kinds listed |
| `apply` | package ships no example, `--shipped-default` | `BLOCKED`, `no_shipped_example`; next is `--spec` |
| `apply` | dry run rejects the document | `BLOCKED`, `spec_rejected_by_cluster`; the cluster's message |
| `apply` | `--wait-for` field not in the CRD schema | `BLOCKED`, `wait_field_unknown`; status fields listed |
| `apply` | same name exists, same spec | `NO_OP`; its status reported |
| `apply` | same name exists, different spec | `BLOCKED`, `custom_resource_differs`; differing fields shown |
| `apply` | named pull Secret missing, no matching target table | `BLOCKED`, `credential_needed` |
| `apply` | named pull Secret missing, matching target table | plan lists the Secret; created on confirm |
| `apply` | field does not reach the value before the deadline | `PARTIAL`, `resource_wait_timeout`; status shown |
| `remove --kind` | object exists | plan; on confirm deleted by UID |
| `remove` | custom resources of the package exist | plan lists them under `impact`; proceeds on confirm |
| `remove` | Subscription exists | Subscription, CSV and owned OperatorGroup deleted by UID |
| `remove` | Subscription absent, CSV found by label | CSV deleted by UID |
| `remove` | nothing found | `NO_OP` |
| any | `--confirm-plan` differs from the digest | `BLOCKED`, `confirm_plan_mismatch_rerun_plan` |
| any | a create raises after an earlier one succeeded | `BLOCKED`; objects this run created are deleted |

### Refactoring

REFACTOR in `ci-skills/lib/python/core/kubernetes/kubernetes.py`: extract the loop of
`KubernetesService.wait_build` into `KubernetesService.wait_field`, which takes the qualified resource, the name, the
namespace, a field path, the accepted values, the failed values and a deadline in seconds. `wait_build` calls it with
its current values and keeps its build-log trace. The CSV wait and the custom resource wait call it. The shared
service timeout stays 30 seconds per command; the deadline is a separate argument.

REFACTOR in `ci-skills/lib/python/core/kubernetes/resources.py`: `ResourceSet.apply` and `ResourceSet.delete` take
an optional `resource` argument, the qualified resource name used for reads and deletes. Existing callers pass
nothing and keep today's behavior.

REFACTOR in `ci-skills/lib/python/core/target.py`: extract the either-or credential file check from `_parse_harbor`
into `_credential_files`, called by `_parse_harbor` and `_parse_registry`.

The tests that cover these today must pass with no edit, before and after:

    conda run -n ci-skills python -m pytest -q tests/python/test_callbacks.py \
        tests/python/test_image_build_chain.py tests/python/test_target_profiles.py \
        tests/python/test_target_resolution.py tests/python/test_endpoints.py

### Catalog entry

MODIFIED: `ci-skills/lib/python/core/catalog.py`, new key `"ocp_operator.py"` in `COMMANDS` with
`kind: "olm_operator"`, `requires: (KUBERNETES_AUTHORITY,)`, `capabilities: ("namespaced",)`, `mutates: True`, the
options of the interface table, and subcommands `install`, `apply` and `remove`, each with
`result_schema: "schemas/results/olm-operator.schema.json"` and
`references: ("OLM package install", "NVIDIA Network Operator")`. Add the command to the routing dictionary in
`manifest()` and both display names to `PROPOSITION_REFERENCES`. Then regenerate the manifest:

    conda run -n ci-skills python tools/render_manifest.py
    conda run -n ci-skills python tools/render_manifest.py --check

The second command prints nothing and exits 0 when `ci-skills/tools.json` matches the catalog.

### Golden path

The golden path is the sequence an agent copies. It is proven live in CI16-M3 and stored in the worked-example
reference. Package and namespace are both `nvidia-network-operator`.

| Order | Call | Ends when | Proven |
| --- | --- | --- | --- |
| 1 | `install` | CSV phase `Succeeded`, or `NO_OP` when already enabled | CI16-M2 |
| 2 | `apply --kind NicClusterPolicy --shipped-default --wait-for status.state=ready` | status field `ready` | CI16-M3 |
| 3 | optional: `apply --kind <network kind> --spec <file>` | the caller's `--wait-for` | not in this phase |

Three facts are open until call 2 runs live, and each is written into the reference with the raw line that settles
it:

| Open fact | Read from | Written into the reference as |
| --- | --- | --- |
| the shipped policy reaches `ready` | the policy's `status.state`, `status.appliedStates` | expected status |
| the driver image pulls without a login | the same status; a missing named Secret | registry table needed or not |
| the operator accepts only one policy name | `status.state: ignore` on any other name | the name to use |

If call 2 does not reach `ready`, the golden path records the operator's own message and the step the caller takes,
and the phase stays open until a sequence that works is recorded.

### References

INTRODUCED: `ci-skills/references/olm-package.md`, display name "OLM package install". Sections `## Enable`,
`## Install`, `## Use`, `## Overrides`, `## Needs`, `## Remove`, each with `###` subsections for the detail: what
is read from the cluster, each flag, each reason code.

INTRODUCED: `ci-skills/references/nvidia-network-operator.md`, display name "NVIDIA Network Operator". It holds the
golden path above; the five shipped kinds with their shipped names; the status field to read after
each step; an excerpt of the shipped `NicClusterPolicy` with a comment per key in the form `type | default | effect`,
labeled with the CSV version it was read from; and one line stating that the shipped network examples hold a
placeholder interface name and address range and need `--spec`, and that a `MacvlanNetwork` with
`ipam.type: whereabouts` needs the address plugin enabled in the policy.

### Variables

| Variable Name | Type | Default / Required | Source / Scope | Description & Invariants |
| --- | --- | --- | --- | --- |
| `--package` | string | Required | command line | OLM package name |
| `--namespace` | string | Required | command line | install namespace |
| `--channel` | string | cluster default | command line | must be listed for the package |
| `--source` | string | the single source | command line | used with `--source-namespace` |
| `--approval` | enum | `Automatic` | command line | `Automatic` or `Manual` |
| `--starting-csv` | string | not set | command line | a CSV of the channel |
| `--install-mode` | enum | `OwnNamespace` if supported | command line | supported by the channel |
| `--kind` | string | Required for `apply` | command line | a kind the package ships |
| `--shipped-default` | flag | none | command line | exclusive with `--spec` |
| `--spec` | path | none | command line | caller's document; used exactly as given |
| `--wait-for` | `field=value` | none | command line | field exists in the CRD schema |
| `--timeout` | integer, seconds | 600 | command line | deadline of a wait; 1 to 600 |
| `--confirm-plan` | SHA-256 text | Required with `--apply` | output of `--live-plan` | equals the plan digest |
| `[kubernetes]` | TOML table | Required | `~/.ci-skills/target.toml` | context, server, kubeconfig |
| `[registry.<name>]` | TOML table | Optional | `~/.ci-skills/target.toml` | `url` and one credential form |

### CI Pipelines & Jobs

No pipeline or job is introduced or changed in behavior. The only edit to the workflow file is MODIFIED: the new
module paths in its pydoclint list.

| Pipeline / Job Name | Type / Trigger | Stage / Order | File Path | Required Inputs & Artifacts |
| --- | --- | --- | --- | --- |
| `validate` | pull request, push to `main` | existing order | `.github/workflows/validate.yml` | pydoclint path list |

### Verification Gates

These gates are proposed. A gate is added to the repository only after team approval. The repository-wide gates are
listed in the [plan index][repo-gates]. `G-CI16-01` runs in the `validate` job. The other three run the installed
`ocp_operator.py` against the cluster selected in the target file and store their JSON output as evidence.

| Gate ID | Gate Type / Class | Pass Condition | Fail-Closed Condition | Producer / Owner |
| --- | --- | --- | --- | --- |
| `G-CI16-01` | `unit-contract` | every behavior row passes on the fake cluster | a row has no test | unit tests |
| `G-CI16-02` | `live-read-back` | `install` reports what `kubectl` shows | evidence missing | `install` |
| `G-CI16-03` | `live-read-back` | shipped policy applied; operator status returned | evidence missing | `apply` |
| `G-CI16-04` | `live-read-back` | applied object absent after `remove --kind` | object remains | `remove` |

### Out Of Scope

This phase starts no test Pod and sends no traffic. It does not edit the cluster-wide pull secret. It does not
configure mirrored or disconnected catalog sources. It does not delete CRDs. It holds no table of which package
needs which other package, label or plugin. It does not judge the values in a caller's `--spec`. It does not add the
command to the required operations in `inventory/ci/live-acceptance.json`.

### Budget

ci-skills starts no Pod of its own in this phase. The installed package starts its controller, and the shipped
policy makes it start driver and device-plugin Pods on the nodes. A wait is at most 600 seconds. One live proof is
at most eight command runs and takes under 40 minutes. The evidence files are under 200 KB in total.

## Validation and Acceptance

Unit tests run in the `validate` job on the pull request. The new tests build the real callback chains and replace
only the cluster with an in-memory fake, the `MemoryCluster` class in `tests/python/test_tool_pod.py`, injected the
way `bind_provider` does in `tests/python/test_ocp_node_onboard.py`. Each test starts from one cluster state of the
behavior table, runs the real entrypoint, and asserts the calls that reached the fake and the emitted result. Test
targets use `*.example.test` hosts so the portability gate in `tools/skillkit/portability_gate.py` does not report a
literal namespace as a fixed selector.

Live acceptance uses the installed copy against a cluster that already has the package. The observable behavior is:

1. `install --live-plan` ends `NO_OP` and reports the channel, source and CSV that
   `kubectl get subscriptions.operators.coreos.com -n <namespace>` shows.
2. `apply --kind NicClusterPolicy` with neither source flag ends `BLOCKED`, `source_not_chosen`, shows the shipped
   document and issues no digest.
3. `apply --kind NicClusterPolicy --shipped-default --wait-for status.state=ready --apply --confirm-plan <digest>`
   creates `nic-cluster-policy`, and the result's `resource_status` equals
   `kubectl get nicclusterpolicies.mellanox.com nic-cluster-policy -o jsonpath='{.status}'`.
4. The same apply again ends `NO_OP`.
5. `remove --kind NicClusterPolicy --apply --confirm-plan <digest>` ends `APPLIED`, and
   `kubectl get nicclusterpolicies.mellanox.com` returns nothing.

## Idempotence and Recovery

Every action can be repeated. A repeated `install` or `apply` returns `NO_OP`; a repeated `remove` returns `NO_OP`.

`SkillExecutor.run` in `ci-skills/lib/python/core/abstract/executor.py` calls `cleanup` on every callback that was
entered and sets `state.failed` to true before cleanup when a callback raised. Each creating callback's `cleanup`
therefore deletes its created objects, through `ResourceSet.cleanup_created`, only when `state.failed` is true. On a
run that reaches the end it returns an empty result and the objects stay.

A wait that ends at the deadline is not a raised failure. The waiting callback catches the error, records the
reason, and the result is `PARTIAL` with the actual status. `state.failed` stays false, so the created objects
stay, and the same call can be repeated to wait again.

`remove` deletes by the UID read during planning, so a different object with the same name is not deleted. `remove`
leaves the CRDs, and leaves a Namespace that ci-skills did not create or that is not empty; an administrator who
wants them gone deletes them by hand after checking that nothing else uses them.

Known limit: the command cannot tell that a shipped example holds a placeholder value. `ens2f0` is a string to it.
The reference carries that knowledge. Known limit: if the operator does not report a missing address plugin in the
network object's status, the command returns that status as written, and the first failure appears when a Pod
attaches.

## Artifacts and Notes

Evidence files, written in CI16-M5, all under `tests/acceptance/evidence/olm-operator/`:

    install-plan.json
    apply-source-not-chosen.json
    apply-policy-plan.json
    apply-policy-apply.json
    apply-policy-repeat.json
    remove-policy-apply.json

A remove plan when a policy exists, human form:

    action: remove
    created_by_ci_skills: false
    deletes:
      Subscription           nvidia-network-operator
      ClusterServiceVersion  nvidia-network-operator.v26.7.0-1
    impact:
      NicClusterPolicy  nic-cluster-policy   stays; its DaemonSets keep running with no controller
    plan_digest: <sha256>

## Interfaces and Dependencies

No new third-party library is used. Cluster access goes through `KubernetesService` and `kubectl` only.

In `ci-skills/lib/python/core/kubernetes/olm_models.py`, define:

    OPERATION: Final = "olm-operator"
    SUBSCRIPTIONS: Final = "subscriptions.operators.coreos.com"
    OPERATOR_GROUPS: Final = "operatorgroups.operators.coreos.com"
    CLUSTER_SERVICE_VERSIONS: Final = "clusterserviceversions.operators.coreos.com"
    INSTALL_PLANS: Final = "installplans.operators.coreos.com"
    PACKAGE_MANIFESTS: Final = "packagemanifests.packages.operators.coreos.com"
    MAX_OPERATOR_TIMEOUT: Final = 600
    OLM_OPERATOR_MODE_POLICY: Final = ActionModePolicy(
        default_mode=ActionMode.LIVE_PLAN,
        receipt_modes=frozenset((ActionMode.APPLY,)),
        allow_offline_output=False,
        maximum_timeout=MAX_OPERATOR_TIMEOUT,
    )

    @dataclass(frozen=True)
    class Need:
        need: str
        evidence: str
        supply: str

    @dataclass(frozen=True)
    class OperatorAction(SkillAction):
        target: Target
        operation: str                 # "install", "apply" or "remove"
        package: str
        namespace: str
        mode: ActionMode
        timeout: int
        confirm_plan: Optional[str]
        channel: Optional[str]
        source: Optional[str]
        source_namespace: Optional[str]
        approval: Optional[str]
        starting_csv: Optional[str]
        install_mode: Optional[str]
        kind: Optional[str]
        shipped_default: bool
        spec: Optional[Path]
        wait_for: Optional[tuple[str, str]]

    @dataclass
    class OperatorState(DeliveryState):
        plan: Optional[JSONObject] = field(default=None, init=False)
        plan_digest: Optional[str] = field(default=None, init=False)
        needs: MutableSequence[Need] = field(default_factory=list, init=False)
        resources: MutableSequence[JSONObject] = field(default_factory=list, init=False)
        installed_csv: Optional[JSONObject] = field(default=None, init=False)
        resource_status: Optional[JSONObject] = field(default=None, init=False)

    def namespace_document(namespace: str) -> JSONObject: ...
    def operator_group_document(namespace: str, install_mode: str) -> JSONObject: ...
    def subscription_document(action: OperatorAction, source: PackageSource) -> JSONObject: ...

In `ci-skills/lib/python/core/kubernetes/olm_callbacks.py`, define the classes `ResolveOperatorPackage`,
`ResolveInstalledOperator`, `ConfirmOperatorPlan`, `EnsureOperatorNamespace`, `EnsureOperatorGroup`,
`EnsureSubscription`, `ApproveInstallPlan`, `WaitOperatorInstalled`, `RemoveSubscription`, `RemoveInstalledCSV`,
`RemoveOperatorGroup`, `RemoveOperatorNamespace`, and the functions:

    def operator_install_callbacks(action: OperatorAction) -> Sequence[K8SCallback[OperatorAction]]: ...
    def operator_remove_callbacks(action: OperatorAction) -> Sequence[K8SCallback[OperatorAction]]: ...

In `ci-skills/lib/python/core/kubernetes/olm_resources.py`, define the classes `ResolveShippedResource`,
`EnsureRegistrySecret`, `EnsureCustomResource`, `WaitCustomResource`, `RemoveCustomResource`, and the functions:

    def operator_apply_callbacks(action: OperatorAction) -> Sequence[K8SCallback[OperatorAction]]: ...
    def operator_resource_remove_callbacks(action: OperatorAction) -> Sequence[K8SCallback[OperatorAction]]: ...

In `ci-skills/lib/python/core/kubernetes/kubernetes.py`, add to `KubernetesService`:

    def wait_field(
        self,
        resource: str,
        name: str,
        namespace: str,
        path: Sequence[str],
        accepted: frozenset[str],
        failed: frozenset[str],
        deadline_seconds: float,
    ) -> JSONObject: ...

In `ci-skills/lib/python/core/kubernetes/resources.py`, change in `ResourceSet`:

    def apply(self, document: JSONObject, *, resource: Optional[str] = None) -> JSONObject: ...
    def delete(self, kind: str, name: str, *, resource: Optional[str] = None, ...) -> ...: ...

In `ci-skills/lib/python/core/target.py`, define `RegistryTarget` with the fields `name`, `url`, `host`,
`credential_file`, `username_file` and `password_file`, add `registries: Mapping[str, RegistryTarget]` to `Target`,
and define `_parse_registry` and `_credential_files`.

In `ci-skills/lib/python/core/kubernetes/olm_cli.py`, define:

    def build_parser() -> argparse.ArgumentParser: ...
    def workflow(action: OperatorAction, state: OperatorState) -> WorkflowExecutor[DeliveryState]: ...
    def main(argv: Optional[Sequence[str]] = None) -> int: ...

`workflow` returns `K8SAuth` followed by the callback list of the selected action.

Revision notes:

- First version covered the Subscription only, with a fixed default channel and an ownership block on remove.
- Second version, this one. A read of a live cluster showed the package already installed on a non-default channel,
  two kinds named `Subscription`, and examples shipped by the package. The scope now covers enable, install and
  optional use; values are read from the cluster; nothing is created or deleted without a stated choice; an
  unfinished answer carries `needs`; and a registry credential comes from a new target table. Decisions
  DEC-CI16-01 to DEC-CI16-20 record each change and its reason.

[phase]: ../../phases/CI16-OLM-OPERATOR.md
[repo-gates]: ../../plans/index.md#repository-gates
