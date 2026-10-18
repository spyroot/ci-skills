# Podman evidence transformation

Raw operation receipts, debug traces and provider observations remain in the
private task scratch directories. `TRANSFORM-MAPPING.json` records each raw
SHA-256 identity and corresponding published hash.

Receipts and trace events pass through the installed `redact_tree`, `portable`,
`portable_command` and `path_token` helpers. Operator-local paths, Kubernetes
context, server, combined target, access identity and selected node are replaced
with stable 12-hex SHA-256 tokens. Equal source values receive equal tokens.
Event order, timestamps, callback names, results, native return codes and runtime
rows are unchanged.

The probe marker document, probe log and installed discovery output already
contain no credential or local path and are copied byte for byte. The independent
cleanup document is derived only from four captured provider reads: empty lists
for the exact run selector and successful reads of the existing privileged SCC
and ClusterRole. No provider result is synthesized.
