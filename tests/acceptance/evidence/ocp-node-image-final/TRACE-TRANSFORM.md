# Portable evidence transformation

The original captures remain private and unchanged. `TRANSFORM-MAPPING.json`
records each published capture's raw SHA-256, published path and published
SHA-256, with exact event counts for JSON Lines traces. `raw-sha256.txt` also
identifies six private Pod/PVC and native-inventory read-backs whose full contents
are omitted; the bounded assessment is published in `cleanup/scoped-readback.json`.
`sha256.txt` covers every published file except itself.

The export reuses the existing evidence transformer's `body_for`, `sha` and
`write_hashes` helpers with the final installation's `redact_tree`, `portable`,
`portable_command` and `path_token` helpers. Observed private context, API server,
identity, namespace, storage selectors, host paths and addresses receive stable
SHA-256 tokens using the first twelve hexadecimal characters. In-cluster test
artifact paths, public image digests, supplied test MACs and resource UIDs remain
visible. Plan and input hashes identify the original input bytes.

Results, reasons, source revision, package and functional hashes, run IDs, sizes,
content hashes, timestamps and process exits are preserved. JSON serialization
is normalized and trace event order is unchanged. No result is restamped or
synthesized by export. Initial blocked plans and historical captures are excluded.
Removal has no JSON Lines trace because those invocations did not request one;
its actual receipts and process exits are retained.
