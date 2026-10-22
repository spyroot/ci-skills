# Build and join an OpenShift node

`bin/ocp_node_onboard.py` builds a node ISO inside your OpenShift cluster and verifies it at the cluster
storage path you choose. The ISO stays there; the command returns its location, size, checksum and run ID.
You decide how to boot the intended node. After boot, the join action can approve that exact node's client and
serving certificate requests and verify its Ready state. The command never boots or deletes a machine.

## Prepare access and choose inputs

Install the skill as described in its [entrypoint](../SKILL.md). The dispatch computer needs Python 3.11+
with PyYAML and jsonschema, `kubectl`, and `oc`. Default tool discovery uses local `curl` to read Red Hat's
published checksum manifest. Tool archives, package installation, ISO generation and ISO inspection run
inside the cluster Pod.

Configure only Kubernetes in your [target file](access.md), normally `~/.ci-skills/target.toml`:

```toml
[kubernetes]
context = "your-context"
server = "https://your-cluster-api:6443"
kubeconfig = "/absolute/path/to/your/kubeconfig"
```

Use your actual context, API URL and kubeconfig path. GitLab, GitHub and Harbor are not prerequisites.
The selected credentials must permit native node-image creation and access to the selected destination.
OpenShift's native command creates its temporary builder resources, including a namespace and RBAC objects;
ci-skills does not grant permissions to make a failed authorization check pass.

Set `CI_SKILLS_ROOT` to the installed skill directory created by your installer, then read the interface:

```bash
CI_SKILLS_ROOT="/path/from/installer/ci-skills"
python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" --help
python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" --describe --json
```

The caller supplies the intended machine's MAC/NIC and final storage location. Supply network and disk
overrides when needed; OpenShift retrieves omitted settings from the cluster. Obtain unknown values from
the user. Example hostnames, devices and addresses below are not defaults.

## Native node input

Use the [native OCP YAML example](ocp-node-input.md) for the caller-supplied MAC/NIC and network settings.
The [native flag reference](ocp-node-image-flags.md) separates OpenShift flags from ci-skills inputs.

## Choose where the ISO stays

Save `destination.json` with the exact existing namespace, native volume source, mount path and final filename.
This example uses an existing PVC named `iso-store` in an existing namespace named `node-build`:

```json
{
  "namespace": "node-build",
  "volumeSource": {"persistentVolumeClaim": {"claimName": "iso-store"}},
  "mountPath": "/output",
  "artifactPath": "/output/worker-01/node.iso",
  "buildOutput": "destination"
}
```

The [destination schema](../schemas/artifact-destination.schema.json) describes these five ci-skills fields;
Kubernetes receives `volumeSource` unchanged. An existing NFS export can use its native `nfs` volume source
instead. Select your real server and export path; the command does not discover or substitute an export.

Before building, `CheckDestinationStorage` reads the mounted protocol/source, checks that the output is
absent, and verifies a uniquely named write/read/delete probe. It preserves existing files and storage.
Native `oc` uses the output's parent directory as its asset directory; choose a directory without other ISO
files because native output renaming traverses that directory. A conflict is reported rather than overwritten.

`buildOutput: destination` writes directly to the selected path. `buildOutput: scratch` builds in run-owned
cluster scratch, copies exclusively to the same final path, and independently compares size and checksum.
For deliberately temporary output, use `volumeSource: {"emptyDir": {}}`; its Pod is retained until `remove`.
The result identifies that Pod-bound lifetime. Persistent volumes retain the ISO after Pod removal.

## Plan, build and inspect

The live plan reads cluster access, release and compatible tools; it creates no Pod. It binds native input
hashes, destination, resolved tools, target and functional code to `plan_digest`.

```bash
python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" build \
  --spec nodes-config.yaml --destination destination.json --live-plan --json
```

Read `plan_digest` into `DIGEST`, then apply exactly those inputs:

```bash
python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" build \
  --spec nodes-config.yaml --destination destination.json \
  --apply --confirm-plan "$DIGEST" --json
```

For the single-node flag route, replace `--spec nodes-config.yaml` in both calls with
`--mac-address 02:00:00:10:00:01`, using the actual MAC. Add `--network-config-path network.yaml` when supplying
native network overrides. `--dry-run` shows an offline plan and performs no
provider discovery; use the live plan's digest when default tools need discovery.

Default tool selection reads the cluster release and provider-published artifacts. It prepares `oc`, `rsync`
and `coreos-installer` through the [shared tool Pod callbacks](tool-pod.md). Supply `--tool-pod-spec FILE` on
both calls only when supplying your own [tool-Pod input document](tool-pod.md#supply-the-tools). That override is
retained exactly; choose tools and an image suitable for your cluster and workload.

Workload credentials are staged privately inside the Pod and removed after confirmed completion. Registry
configuration comes from OpenShift's existing `openshift-config/pull-secret`, data key `.dockerconfigjson`.
Use `--pull-secret-namespace`, `--pull-secret-name` and `--pull-secret-key` to select another existing Secret.
Secret values never belong in command arguments or reports.

A successful build reports native completion, embedded-Ignition inspection, final ISO size/checksum and
verified destination read-back. `records` gives each callback's actual result; `cleanup` distinguishes removed
and retained resources. Add `--receipt-out result.json` for a saved report, or
`--log-level debug --log-format json --log-file trace.jsonl` for a sanitized execution trace.
If transport ends without confirmed native completion, the report says so and retains the exact Pod/resources;
it does not claim cleanup or a successful build.

## Join a booted node

Boot the intended machine from the generated ISO and read its build result's `run_id` into `RUN_ID`. Select the
exact Kubernetes Node hostname and every IP address passed to OpenShift's native monitor. These are caller
choices; ci-skills does not discover or replace them. Repeat `--ip-address` for a multi-address node.

```bash
python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" join --run-id "$RUN_ID" \
  --node-name worker-01 --ip-address 192.0.2.10 --live-plan --json
```

Set `JOIN_DIGEST` to the `plan_digest` returned by that live plan, then apply it unchanged:

```bash
python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" join --run-id "$RUN_ID" \
  --node-name worker-01 --ip-address 192.0.2.10 \
  --apply --confirm-plan "$JOIN_DIGEST" --json
```

Replace the example hostname and address with the booted node's values. The plan binds the run ID, hostname,
addresses, two fixed Kubernetes signers, target and installed functional code. It never binds provider-generated
CSR names.

The selected credentials need permission to list and read certificate signing requests, update the CSR approval
subresource, read the selected Node, and remove the run-owned build resources after successful join. The command
does not grant those permissions.

Apply reads CSRs explicitly even without reverse DNS. It approves only the supplied hostname's client-kubelet
and kubelet-serving requests, in that order, and reads each approval back by UID. Other signers remain untouched.
The report distinguishes observed client issuance from bootstrap completion inferred from a serving request.

After approval, native `oc adm node-image monitor` runs and the exact Node is independently checked for Ready.
Roles and addresses are reported; monitor and Node addresses may differ across NAT or multiple networks.
An already Ready Node returns `NO_OP` without claiming unobserved certificate issuance.

Successful join cleans run-owned build resources last. Persistent storage and its ISO remain; temporary output
is removed with its backing resources. Cleanup never deletes joined Nodes or issued certificates.

## Verify or remove a run

Verification resolves the selected run's live artifact record, reads the ISO in-cluster and compares its checksum
and size before inspecting the embedded configuration. For persistent storage, it creates and cleans a temporary
reader Pod with a read-only destination mount; the selected credentials need those Pod permissions.
New build records include the actual build node. A later reader prefers that node while preserving the recorded
placement constraints; older records remain readable without that preference. The shared tool-Pod callback
handles bounded mount recovery and reports attempted nodes and failure events.

Embedded configuration readback includes native IPv4/IPv6 addressing, routes and DNS settings plus decoded
root-device hints. Unreadable or oversized observations remain explicitly unobserved with file digests;
ci-skills does not validate or rewrite the native OCP input.

```bash
python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" verify --run-id "$RUN_ID" --json
python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" remove --run-id "$RUN_ID" --live-plan --json
python "$CI_SKILLS_ROOT/bin/ocp_node_onboard.py" remove --run-id "$RUN_ID" \
  --apply --confirm-plan "$REMOVE_DIGEST" --json
```

Set `REMOVE_DIGEST` from the removal plan. Persistent output is read back before and after removing the
original build resources; its storage and final ISO are preserved. Removal of temporary output deletes its
backing Pod and verifies absence. Once the run record is removed, repeating removal is `NO_OP`; the preserved
persistent ISO can still be consumed directly from its selected storage path.
