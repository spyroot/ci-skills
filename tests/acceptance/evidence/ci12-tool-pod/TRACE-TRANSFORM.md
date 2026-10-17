# CI12 portable evidence transformation

The raw operation receipts and JSON Lines debug traces remain in the task scratch
directory. Their SHA-256 identities and the corresponding portable-file hashes
are recorded in TRANSFORM-MAPPING.json.

The portable copies retain the complete receipt shape and every trace event in
its original order. Event names, result fields, timestamps, elapsed times, and
native return codes are unchanged. The transformation applies the installed
redact_tree, portable, portable_command, and path_token helpers. It tokenizes
kubeconfig paths, operator-local absolute paths, and private Kubernetes context,
server, combined target, and access identity selectors. Tokens contain the first
twelve hexadecimal characters of the original value's SHA-256 digest.

No event or provider result is synthesized.
