# Installed tool Pod live evidence

Captured on 2026-10-18 from installed candidate `9aa288084328a9c44d38d78bd86b5e8785d810b3`.

- Package checksum: `1cb30887851e9edb58fbb0d08c9679d8dd736d82314b53bf1c7aff5e33ef6cc3`.
- Functional checksum: `1bd593cf493b854d921cb2d08eea333d6f08c3f3033fa474e09f28e921c1e052`.
- Run ID: `a4d6162b88ea446c899e53ccaa2cd792`.
- Pod UID: `ef739f71-d4b2-40d3-8349-6bb7db0b683d`; restart count remained zero.

## Observed lifecycle

Resource links abbreviate Namespace as N, Pod as P, and ConfigMap as CM.

| Operation | Observed result | Report | Trace |
| --- | --- | --- | --- |
| Before staging | No run-owned resources | [N][before-ns], [P][before-pod], [CM][before-cm] | — |
| Prepare and consume | `PASS`; tools and same-Pod rsync verified | [Report][prepare] | [Trace][prepare-trace] |
| Repeat prepare | `PASS`; scope and Pod `NO_OP`; UID unchanged | [Report][reuse] | [Trace][reuse-trace] |
| Remove | `PASS`; run-owned resources were removed | [Report][remove] | [Trace][remove-trace] |
| After removal | No run-owned resources | [N][after-ns], [P][after-pod], [CM][after-cm] | — |
| Repeat remove | `PASS`; cleanup returned `NO_OP` | [Report][repeat-remove] | [Trace][repeat-remove-trace] |

The selected image already contained `sh`, `oc`, and `rsync`. The installed
workflow downloaded and installed `yq` v4.47.2, then verified all four tools.
The `SamePodRsync` record in the prepare report copied 34 bytes with rsync inside
the staged Pod and verified SHA-256
`94731147f3b1260a8419454105592b0b2ab1de91d4c7cb63bf1636fd0fb28f2c`.
Its before and after Pod UID and restart count are identical.

The bounded GitLab/access refresh produced its existing receipt set at the same
installed revision and functional checksum. Its [cleanup report][refresh-cleanup]
records `PASS` with no errors. No shared storage or RBAC object was touched.

[Transformation details](TRACE-TRANSFORM.md) explain selector tokenization.
[Hash mapping](TRANSFORM-MAPPING.json) binds original and published evidence.
[Receipt hashes](raw-sha256.txt) identify the accompanying acceptance receipts.
[Timing evidence](measurement.json) contains one correctness sample and makes no
performance comparison.

[before-ns]: 04-before-namespaces.json
[before-pod]: 04-before-pods.json
[before-cm]: 04-before-configmaps.json
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
