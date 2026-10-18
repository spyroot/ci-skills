# CI12 portable evidence transformation

The raw operation receipts, acceptance receipts, provider observations and JSON
Lines debug traces remain in the task scratch directory. Their SHA-256 identities
and corresponding portable-file hashes are recorded in
`TRANSFORM-MAPPING.json`.

The portable copies retain every provider result and trace event in its original
order. Event names, result fields, timestamps, elapsed times and native return
codes are unchanged. The transformation applies the installed `redact_tree`,
`portable`, `portable_command` and `path_token` helpers to operation evidence.
It tokenizes kubeconfig paths, operator-local absolute paths, and private
Kubernetes context, server, combined target and access identity selectors. The
same path-token algorithm removes local report paths from acceptance receipts.
Tokens contain the first twelve hexadecimal characters of the original value's
SHA-256 digest. Structured access targets preserve their composition, and
duplicate identity values receive the same token, so equality remains testable
after publication.

The installed proposition and help outputs contain only public catalogue values,
so they are copied without a selector transform. The prepare receipt and trace
contain the first run-scope observation. The `13-absence-*` paths contain the
independent post-cleanup empty-list observations. No event or provider result is
synthesized.
