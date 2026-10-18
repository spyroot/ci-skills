# CI12 portable evidence transformation

The original operation receipts and JSON Lines debug traces remain outside the
repository. `TRANSFORM-MAPPING.json` binds every original file to its published
copy with SHA-256.

The portable copies retain the complete receipt shape and every trace event in
its original order. Event names, result fields, timestamps, elapsed times, and
native return codes are unchanged. The transformation uses the installed
`redact_tree`, `portable`, `portable_command`, and `path_token` helpers. Local
paths and private Kubernetes context, server, target, and identity selectors are
replaced by deterministic SHA-256 tokens.

The pre-create and post-cleanup Kubernetes List observations and acceptance
cleanup report required no redaction and are copied byte-for-byte. The mapping
records their identical original and published hashes. `measurement.json` is a
derived one-sample timing summary; its published copy changes only evidence links
to their repository-relative paths.

No event or provider result is synthesized.
