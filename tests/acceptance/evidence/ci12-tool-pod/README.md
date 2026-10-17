# Installed tool Pod live evidence

Captured on 2026-10-17 from installed candidate `9fe0fea86d224a58f0b86011a39af1297334f647`.
This is CI12.0 tool preparation evidence; ISO generation and Podman execution are separate deliveries.

- Package checksum: `7e5cf3950d5c5d80b8193e650734899c678703b3e129af8f284196553353a8da`.
- Functional checksum: `a1f62f687d0e8c6392abe1412cbaa10abc03a03815471f55df3b49b56e425fd8`.
- Run ID: `c795c9ba39d8407b9e8220f385d3acad`.
- Pod UID: `386e5762-720e-4282-8ac5-a43167a2b29c`; restart count remained zero.

## Observed behavior

| Operation | Result | Report | Execution trace |
| --- | --- | --- | --- |
| Prepare and consume | PASS; yq downloaded/installed, all tools verified | [Report][prepare] | [Trace][prepare-trace] |
| Repeat prepare | PASS; same Pod, no installation repeated | [Report][reuse] | [Trace][reuse-trace] |
| Remove | PASS; owned resources deleted | [Report](12-remove.json) | [Trace](traces/12-remove.jsonl) |
| Repeat remove | PASS; verified NO_OP | [Report](15-repeat-remove.json) | [Trace](traces/15-repeat-remove.jsonl) |

The selected image already contained `sh`, `oc` and `rsync`. The command downloaded and installed
`yq` v4.47.2, then verified all four tools. An appended consumer callback copied a 34-byte fixture
with rsync in the same Pod and verified SHA-256
`94731147f3b1260a8419454105592b0b2ab1de91d4c7cb63bf1636fd0fb28f2c`.
The consumer's before/after UID and restart count matched. Its fixture was removed.

[BEFORE evidence](00-before-capability.json) records the command's absence at the base revision
and each generated resource name's absence immediately before creation. The label observation
occurred after namespace creation but before Pod staging: one namespace, zero Pods and ConfigMaps.
It does not claim that every run label was absent before the workflow began.

Independent final queries returned empty lists for [namespaces](13-absence-namespaces.json),
[Pods](13-absence-pods.json) and [ConfigMaps](13-absence-configmaps.json).
The existing 24 GitLab/access receipts were refreshed against this same installation; their
[cleanup report](acceptance-refresh-cleanup.json) records no errors. Previous receipts remain in
[history](../../historical/pre-ci12-tool-pod/). No shared storage was touched.

## Capture boundaries

[Transformation notes](TRACE-TRANSFORM.md) explain private-selector tokenization.
[Hash mapping](TRANSFORM-MAPPING.json) binds the original and published receipt/trace bytes;
[raw hashes](raw-sha256.txt) also identify the required acceptance refresh captures.
Every trace event, native return code and timing is preserved. No provider result is synthesized.
[Timings](measurement.json) are one sample, not a performance comparison.

[prepare]: 05-prepare.json
[prepare-trace]: traces/05-prepare.jsonl
[reuse]: 10-repeat-prepare.json
[reuse-trace]: traces/10-repeat-prepare.jsonl
