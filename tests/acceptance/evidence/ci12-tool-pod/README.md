# Installed tool Pod live evidence

Captured on 2026-10-18 from installed candidate
`0017df813f5ef0beacc581745c3a16590bfb4229`.

- Package checksum: `33c82b7da68ad46124877127d01e62b4a1e0b03fdd6a47ff95fc682566ed5547`.
- Functional checksum: `e5523516780793f388c33e81cde0747b44571f6f380339a01a04be4bfde5bf9b`.
- PR84 source with identical functional bytes:
  `cfd97942b0dc8d18376cc1b3cbd6816d7ee432c1`.
- Run ID: `a494d409e92d409496e4491518e0fa41`.
- Pod UID: `a9df3e70-c9c6-4539-bfba-79dfa4a8bc6b`; restart count remained zero.

## Installed discovery

The installed [proposition node][proposition] offers the Tool Pod run and read
paths. The [installed help][help] explains the input specification,
plan/apply confirmation and lifecycle commands. The [installed identity][identity]
binds the operation outputs to the installed revision and checksums above.

## Observed lifecycle

| Operation | Observed result | Report | Trace |
| --- | --- | --- | --- |
| Prepare and consume | `PASS`; tools and same-Pod rsync verified | [Report][prepare] | [Trace][prepare-trace] |
| Repeat prepare | `PASS`; scope and Pod `NO_OP`; UID unchanged | [Report][reuse] | [Trace][reuse-trace] |
| Remove | `PASS`; run-owned resources removed | [Report][remove] | [Trace][remove-trace] |
| Independent cleanup | Run-owned selectors empty | [N][after-ns], [P][after-pod], [CM][after-cm] | — |
| Repeat remove | `PASS`; cleanup returned `NO_OP` | [Report][repeat-remove] | [Trace][repeat-remove-trace] |

The selected image already contained `sh`, `oc`, and `rsync`. The installed
workflow downloaded and installed `yq` v4.47.2, then verified all four tools.
The `SamePodRsync` record copied 34 bytes with rsync inside the staged Pod and
verified SHA-256
`94731147f3b1260a8419454105592b0b2ab1de91d4c7cb63bf1636fd0fb28f2c`.
Its before and after Pod UID and restart count are identical.

The first independent scope observation is in the prepare report and trace:
`ResolveRunScopes` returned `VERIFIED` with an empty `scopes` list, then
`OpenRunScope` applied a new owned Namespace. Repeat prepare records `NO_OP` for
the existing scope, Pod and installed tools. Remove independently verified the
exact Pod, ConfigMap and Namespace absent; selector read-back found no remaining
resource with this run ID. Repeat remove returned `NO_OP`.

The bounded acceptance refresh produced 24 existing receipt categories at the
same installed revision and functional checksum. Its [cleanup report][refresh-cleanup]
records `PASS` with no errors. No SCC, RBAC, NFS or PVC object was changed.

[Transformation details](TRACE-TRANSFORM.md) explain selector tokenization.
[Hash mapping](TRANSFORM-MAPPING.json) binds private raw evidence to published
portable copies. [Published hashes](raw-sha256.txt) cover the Tool Pod evidence
and accompanying acceptance receipts. [Timing evidence](measurement.json)
contains one correctness sample and makes no performance comparison.

[identity]: 03-installed-provenance.json
[proposition]: 04-proposition.json
[help]: 05-help.txt
[prepare]: 05-prepare.json
[prepare-trace]: traces/05-prepare.jsonl
[reuse]: 10-repeat-prepare.json
[reuse-trace]: traces/10-repeat-prepare.jsonl
[remove]: 12-remove.json
[remove-trace]: traces/12-remove.jsonl
[after-ns]: 13-absence-namespaces.json
[after-pod]: 13-absence-pods.json
[after-cm]: 13-absence-configmaps.json
[repeat-remove]: 15-repeat-remove.json
[repeat-remove-trace]: traces/15-repeat-remove.jsonl
[refresh-cleanup]: acceptance-refresh-cleanup.json
