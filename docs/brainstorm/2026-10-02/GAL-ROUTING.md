# GAL-ROUTING adversarial review

Target: PR #11, head `7b0f9099202aa768bbabf69c569400ed8cfe5d8a`.

## Findings

- **P1 — The receipt executor conflicts with release policy.**
  `GAL-ROUTING.md:109-123` needs a new live receipt after the final skill
  edit. `../../tests/acceptance` declares only `operator-laptop` at
  `mac.lan`, while the current instruction forbids laptop release evidence.
  Name an approved executor and capture route, update expectations, then
  capture and read back a fresh receipt for the exact skill digest.
- **P2 — A routed dependency may not be installed.** `:80` hands GitLab
  writes to the `glab` skill, but GAL-CATALOG's install command is per skill.
  An installed k8s skill can therefore point to an absent sibling. Define
  dependency validation or installation and test an isolated installed copy.
- **P2 — Routing record validity needs a closed-world check.** The planned
  `REFERENCES` entries and `load_when`/`points_to` fields must be checked
  against actual packaged paths and catalog names, then rendered byte-equal
  into `tools.json`. Reject a dangling reference or unknown pointed-to skill
  before a PR can pass; the plan's negative tests should cover both.

## Minimal path

Keep `SKILL.md` as a small router, preserve the existing safety statements in
the moved report reference, and use the current catalog/render path. Finish
all skill edits before taking the receipt. Do not use the existing laptop
receipt or a mocked result as release proof.
