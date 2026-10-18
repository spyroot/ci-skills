# Native OpenShift node input

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

Each host supplies its NIC mapping in `interfaces[].name` and `interfaces[].macAddress`. When `networkConfig`
is present, its interface names and MAC addresses must match that mapping, as required by OpenShift.

Replace the disk, NIC, MAC, hostname and MTU with values for the machine being added. Native network settings
follow [NMState](https://nmstate.io/). ci-skills stages this file byte-for-byte as `nodes-config.yaml`; it does
not validate, rewrite or duplicate the OCP/NMState format. A native failure returns the provider's sanitized
error and exit status so the caller can correct its input.

Use either `--spec nodes-config.yaml` or `--mac-address MAC`. The MAC route optionally accepts
`--network-config-path network.yaml`, which is also staged unchanged. These inputs are distinct from the
ci-skills [destination storage wrapper](ocp-node-image.md#choose-where-the-iso-stays).

See the [native flag reference](ocp-node-image-flags.md) for argument ownership and the
[build flow](ocp-node-image.md#plan-build-and-inspect) for planning and confirmed execution.

[native]: https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/nodes/working-with-nodes
