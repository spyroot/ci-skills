# Installed progressive discovery evidence

## Current v3 consumer proof

Installed source `8fea10f7bba9a84b09f211ae8c0ee14b67ff8867` returns the existing v3
contract: an ordered reference array with `name`, `summary` and `pointer`.
Its functional checksum is
`d4c527c90862055ad91b9c01313e20505fd10b8eea6173f4f3851a220e07ce21`.

The [root](v3-8fea10f/root.json), [runner node](v3-8fea10f/command.json),
[returned reference](v3-8fea10f/sections.json) and
[selected section](v3-8fea10f/section.json) were captured from a fresh installation
outside the checkout and validated against the existing v3 schemas.
The [human renderer](v3-8fea10f/command-human.txt) exited successfully.
[Checksums](v3-8fea10f/sha256.txt) bind those outputs and the
[ordinary acceptance cleanup](v3-8fea10f/acceptance-cleanup.json).
All 24 ordinary acceptance receipts passed and are saved unchanged in their existing paths.
The matching [Podman receipt](https://github.com/spyroot/ci-skills/blob/4374a39932a2151463db3c32f476a30dd40d5afd/tests/acceptance/podman-readiness/image-build-readiness.json)
passed all eight runtime checks.
The Pod exited 0; [independent cleanup](v3-8fea10f/podman-cleanup.json) verified all five owned resources absent.
The existing SCC and ClusterRole remained present. GitHub checks the complete receipt set before merge.

## Historical pre-v3 capture

Captured from installed revision `0017df813f5ef0beacc581745c3a16590bfb4229` on 2026-10-18.
The functional checksum is `e5523516780793f388c33e81cde0747b44571f6f380339a01a04be4bfde5bf9b`;
the package checksum is `33c82b7da68ad46124877127d01e62b4a1e0b03fdd6a47ff95fc682566ed5547`.
Commands ran from a separate consumer directory, without a source-tree import path.

`reference.py`, the installed discovery command, returned these captured outputs.
Each path lists only the selected node's connections. References are returned as
pointers until the caller explicitly reads a section.

| Request after `reference.py` | Captured output |
| --- | --- |
| `next --json` | [Root](01-root.json) |
| `next k8s --json` | [Kubernetes](02-k8s.json) |
| `next k8s prepare_tool_pod --json` | [Tool Pod](05-tool-pod.json) |
| `next k8s prepare_tool_pod run --json` | [Operations](06-tool-pod-run.json) |
| `next k8s prepare_tool_pod run prepare --json` | [Capability and required input](08-required.json) |
| `next k8s prepare_tool_pod run prepare options --json` | [Optional inputs](09-options.json) |
| `next k8s read tool-pod-preparation --json` | [Reference sections](07-tool-pod-sections.json) |
| `get tool-pod-preparation supply-the-tools --json` | [Selected section](10-section.json) |
| `next k8s read tool-pod-preparation --search tools --json` | [Search](11-search.json) |
| `next` with captured stdout | [Default JSON](12-pipe-default.json) |
| `next --human` | [Explicit human output](13-human.txt) |

All listed successful invocations returned exit 0. The [oversized lookup](14-oversized.json)
used a 5,000-character UTF-8 path, returned exit 2, and retained the machine-readable
error envelope within 4,096 bytes including the final newline. It is intentional
failure evidence, not a successful capability lookup.

[SHA-256 identities](sha256.txt) bind the published bytes. JSON outputs are unchanged.
The human output has only its final blank line removed for repository whitespace rules;
its original SHA-256 was
`0078c9ba0e23450ee343625699bbedba2e9085e006f7019da5edabd79ccb0590`.
The accompanying
GitHub tests check schema conformance, bounded output, valid pointers, search and
continuation behavior. This directory records installed command execution, not
provider operations or a performance comparison.
