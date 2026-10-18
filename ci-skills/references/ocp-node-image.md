# Build an OpenShift node ISO

`bin/ocp_node_onboard.py` builds a node ISO inside your OpenShift cluster and verifies it at the cluster
storage path you choose. The ISO stays there; the command returns its location, size, checksum and run ID.
You decide how to boot the intended node. This command does not boot a machine or approve its certificates.

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

The [OpenShift 4.22 node-image procedure][native] owns the native format and command behavior. For a simple
single-node configuration, supply `--mac-address` with that node's actual NIC address. For additional settings
or multiple nodes, save native YAML as `nodes-config.yaml`. This single-node example uses DHCP:

```yaml
hosts:
  - hostname: worker-01
    rootDeviceHints:
      deviceName: /dev/sda
    interfaces:
      - name: ens3
        macAddress: 02:00:00:10:00:01
    networkConfig:
      interfaces:
        - name: ens3
          type: ethernet
          state: up
          mac-address: 02:00:00:10:00:01
          mtu: 9000
          ipv4:
            enabled: true
            dhcp: true
          ipv6:
            enabled: false
```

Replace the disk, NIC, MAC, hostname and MTU with values for the machine being added. Native network settings
follow [NMState](https://nmstate.io/). ci-skills stages this file byte-for-byte as `nodes-config.yaml`; it does
not validate, rewrite or duplicate the OCP/NMState format. A native failure returns the provider's sanitized
error and exit status so the caller can correct its input.

Use either `--spec nodes-config.yaml` or `--mac-address MAC`. The MAC route optionally accepts
`--network-config-path network.yaml`, which is also staged unchanged. These inputs are distinct from the
ci-skills storage wrapper below.

## Accepted node input shape

Every key `oc adm node-image create` reads from `nodes-config.yaml`, from the installer's node-image types. The
comment on each key is its rule. Values are examples; these are the keys documented for node-image.

```yaml
cpuArchitecture: x86_64              # optional; architecture of the nodes being added
sshKey: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAwvoRVg9tj/8eE9Re9lY1R34rgiKjCQAXokfwY22gJJ example@bastion   # optional; the public key text itself, not a file path
bootArtifactsBaseURL: https://pxe.example.internal/assets   # optional; only with --pxe
hosts:                               # required; at least one host
  - hostname: worker-01              # optional; overrides DHCP or reverse DNS
    interfaces:                      # required; at least one; MAC unique across the whole file
      - name: eno1                   # NIC name, one entry per MAC
        macAddress: 02:00:00:10:00:01   # aa:bb:cc:dd:ee:ff
    rootDeviceHints:                 # optional; any of these eight; every hint given must match
      deviceName: /dev/disk/by-path/pci-0000:01:00.0-scsi-0:2:0:0   # exact; /dev/<name> or /dev/disk/by-path/<link>
      hctl: "0:2:0:0"                # exact SCSI address; quote it
      model: PERC H740P              # substring of the reported model
      vendor: DELL                   # substring of the reported vendor
      serialNumber: 00abc123         # exact
      minSizeGigabytes: 480          # integer; the device is at least this size
      wwn: "0x5000c500a1b2c3d4"      # exact; quote it
      rotational: false              # true = spinning disk, false = SSD or NVMe, omitted = either
    networkConfig:                   # optional; one NMState document; every interface in it is also listed above
      interfaces: []
```

The installer rejects these inputs with the quoted text:

- `rootDeviceHints.wwnWithExtension`: "WWN extensions are not supported in root device hints".
- `rootDeviceHints.wwnVendorExtension`: "WWN vendor extensions are not supported in root device hints".
- `deviceName` not under `/dev/`: "Device Name of root device hint must be a /dev/ path".
- `deviceName` under another directory, such as `/dev/disk/by-id/` or `/dev/mapper/`: "Device Name of root device
  hint must be path in /dev/ or /dev/disk/by-path/". Claim by WWN with the `wwn` hint, not with a `by-id` link.
- a host without `interfaces`: "at least one interface must be defined for each node"; an interface without
  `macAddress`: "each interface must have a MAC address defined"; a repeated MAC: "duplicate MAC address found".

## Claim the install disk

The node examines its disks in discovery order and installs to the first one matching every hint given. Read the
real values on a booted host with `lsblk -o NAME,HCTL,MODEL,VENDOR,SERIAL,WWN,SIZE,ROTA` and
`ls -l /dev/disk/by-path/`. Each block below is a complete `rootDeviceHints` value for one host.

```yaml
rootDeviceHints:                     # stable path; does not move when kernel names change
  deviceName: /dev/disk/by-path/pci-0000:01:00.0-scsi-0:2:0:0
```

```yaml
rootDeviceHints:                     # kernel name; may change between boots on multi-disk hosts
  deviceName: /dev/sda
```

```yaml
rootDeviceHints:                     # SCSI address host:channel:target:lun
  hctl: "0:2:0:0"
```

```yaml
rootDeviceHints:                     # world wide name
  wwn: "0x5000c500a1b2c3d4"
```

```yaml
rootDeviceHints:                     # serial number
  serialNumber: S4EMNX0N123456
```

```yaml
rootDeviceHints:                     # model substring and a size floor; both must match
  model: MZ7LH960
  minSizeGigabytes: 800
```

```yaml
rootDeviceHints:                     # first non-rotating disk of that vendor
  vendor: DELL
  rotational: false
```

Through ci-skills the disk claim travels in `--spec nodes-config.yaml`; the `--mac-address` route of
`ocp_node_onboard.py` carries no disk hint. Native `oc adm node-image create --mac-address MAC` accepts one hint as
`--root-device-hint <hint name>:<value>` and stores the value as text, so use it for the text hints and the YAML
route for `minSizeGigabytes` and `rotational`.

### Where the claim lands in the ISO

The ISO's embedded Ignition carries one directory per host under `/etc/assisted/hostconfig/`, named after
`hostname` or `host-<index>` when no hostname is given. The hints are written with the same keys as the input.
This input:

```yaml
hosts:
  - hostname: worker-01
    interfaces:
      - name: eno1
        macAddress: 02:00:00:10:00:01
    rootDeviceHints:
      wwn: "0x5000c500a1b2c3d4"
      rotational: false
```

produces these files, which the build's embedded-Ignition read-back can be checked against:

```text
/etc/assisted/hostconfig/worker-01/root-device-hints.yaml
/etc/assisted/hostconfig/worker-01/mac_addresses
```

```yaml
# /etc/assisted/hostconfig/worker-01/root-device-hints.yaml
rotational: false
wwn: "0x5000c500a1b2c3d4"
```

```text
# /etc/assisted/hostconfig/worker-01/mac_addresses   (lowercase, one per line)
02:00:00:10:00:01
```

A host without `rootDeviceHints` gets no `root-device-hints.yaml`.
`networkConfig` is rendered to NetworkManager keyfiles under `/etc/assisted/network/`.

## Network cases

Complete files for the cases the single-NIC DHCP example above does not cover. Every interface named in
`networkConfig` is also listed under `interfaces` with its MAC.

Two NICs, DHCP management and a static jumbo-frame storage link:

```yaml
hosts:
  - hostname: worker-02
    rootDeviceHints:
      deviceName: /dev/disk/by-path/pci-0000:01:00.0-scsi-0:2:0:0
    interfaces:
      - name: eno1
        macAddress: 02:00:00:10:00:11
      - name: eno2
        macAddress: 02:00:00:10:00:12
    networkConfig:
      interfaces:
        - name: eno1
          type: ethernet
          state: up
          mac-address: 02:00:00:10:00:11
          ipv4:
            enabled: true
            dhcp: true
          ipv6:
            enabled: false
        - name: eno2
          type: ethernet
          state: up
          mac-address: 02:00:00:10:00:12
          mtu: 9000
          ipv4:
            enabled: true
            dhcp: false
            address:
              - ip: 192.0.2.11
                prefix-length: 24
          ipv6:
            enabled: false
```

Two NICs bonded with LACP, jumbo frames, static address, DNS and default route:

```yaml
hosts:
  - hostname: worker-03
    rootDeviceHints:
      wwn: "0x5000c500a1b2c3d4"
    interfaces:
      - name: eno1
        macAddress: 02:00:00:10:00:21
      - name: eno2
        macAddress: 02:00:00:10:00:22
    networkConfig:
      interfaces:
        - name: bond0
          type: bond
          state: up
          mtu: 9000
          link-aggregation:
            mode: 802.3ad
            options:
              miimon: "100"
            port:
              - eno1
              - eno2
          ipv4:
            enabled: true
            dhcp: false
            address:
              - ip: 192.0.2.12
                prefix-length: 24
          ipv6:
            enabled: false
        - name: eno1
          type: ethernet
          state: up
          mac-address: 02:00:00:10:00:21
          mtu: 9000
        - name: eno2
          type: ethernet
          state: up
          mac-address: 02:00:00:10:00:22
          mtu: 9000
      dns-resolver:
        config:
          server:
            - 192.0.2.1
      routes:
        config:
          - destination: 0.0.0.0/0
            next-hop-address: 192.0.2.1
            next-hop-interface: bond0
```

Several hosts in one file with an architecture override and an SSH public key:

```yaml
cpuArchitecture: x86_64
sshKey: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAwvoRVg9tj/8eE9Re9lY1R34rgiKjCQAXokfwY22gJJ example@bastion
hosts:
  - hostname: worker-04
    rootDeviceHints:
      serialNumber: S4EMNX0N123456
    interfaces:
      - name: eno1
        macAddress: 02:00:00:10:00:31
  - hostname: worker-05
    rootDeviceHints:
      hctl: "0:2:0:0"
    interfaces:
      - name: eno1
        macAddress: 02:00:00:10:00:32
```

Source of the shape and rules: the installer's `pkg/types/agent`,
`pkg/asset/agent/agentconfig` and `pkg/types/baremetal/validation` packages and `oc adm node-image create --help`
at release 4.22.

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

## Verify or remove a run

Read the build result's `run_id` into `RUN_ID`. Verification resolves its live artifact record, reads the ISO
in-cluster and compares its checksum and size before inspecting the embedded configuration. For persistent
storage, it creates and cleans a temporary reader Pod; the selected credentials need those Pod permissions.

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

[native]: https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/nodes/working-with-nodes
