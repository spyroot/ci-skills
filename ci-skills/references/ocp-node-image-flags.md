# Native OpenShift node-image flags

[OpenShift 4.22, sections 6.4.1–6.4.3][native] defines the YAML and single-node flag procedures, and the
cluster configuration reference. The selected CLI version's `oc adm node-image create --help` gives its
supported native arguments. `ocp_node_onboard.py --help` lists the arguments accepted by ci-skills.
Additional native flags are not automatically wrapper options.

## Node selection

- `--mac-address`: selects the intended single node by its NIC MAC. The wrapper passes the caller's value
  unchanged; it is the alternative to `--spec nodes-config.yaml`.
- `--network-config-path`: selects native NMState YAML for that single node. With the MAC route,
  the wrapper stages the caller's file unchanged and supplies its in-Pod filename.
  The YAML route instead uses the native `networkConfig` inside `--spec`.

Use the [native YAML example](ocp-node-input.md) for multiple hosts or other native configuration fields.

## Output and registry access

- `--dir`: the native asset directory, derived from the build path's parent directory.
  With `buildOutput: destination`, that is the caller's `artifactPath` parent; with `scratch`, it is in
  the run-owned cluster workspace. The wrapper places `nodes-config.yaml` there for the YAML route.
- `--output-name`: the native ISO filename, derived from the selected build path's final component.
  The ci-skills caller sets `artifactPath` in the destination document, not this flag.
- `--registry-config`: the native registry-credentials file option. ci-skills uses OpenShift's alternative
  `REGISTRY_AUTH_FILE` environment variable: ci-skills stages the selected existing Secret, and
  the native invocation exports that file path. Select the Secret with the wrapper's
  `--pull-secret-namespace`, `--pull-secret-name` and `--pull-secret-key`; do not pass secret contents.

The [build flow](ocp-node-image.md#plan-build-and-inspect) shows destination selection, planning, confirmed
build and verification. The ISO stays at the caller's cluster destination; the caller chooses how to boot
it. After boot, use the [join flow](ocp-node-image.md#join-a-booted-node) for the exact node and its IP addresses.

[native]: https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/nodes/working-with-nodes
