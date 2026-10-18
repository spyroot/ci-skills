# Installed tool Pod live evidence

Captured on 2026-10-18 from installed candidate `2c615ffcc0c9ec16e323e8e72c7e6e0962897bca`.

- Package checksum: `e5aaa500d9b9aa69b61b95683595ce54442f9d28c8ad0f3ac25b499f3644de9f`.
- Functional checksum: `856dadd68651a9c393636c97bc9fe9aa51c44470f105148347c774f8fa69fc30`.
- Run ID: `c5639c8c32af4cfab8ca8a3dff4abdc5`.
- Pod UID: `3a4bf056-c9fc-4cad-b833-d1213846c42b`; restart count remained zero.

## Installed discovery

The installed [proposition][proposition] offers `prepare_tool_pod.py` and the
task reference `references/tool-pod.md`. The [installed help][help] explains the
input specification, plan/apply confirmation and lifecycle commands. The
[installed identity][identity] binds both outputs to the candidate revision,
package checksum and functional checksum above.

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

[identity]: 03-installed-provenance.json
[proposition]: 04-proposition.json
[help]: 05-help.txt
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
