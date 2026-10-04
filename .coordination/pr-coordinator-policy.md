# ci-skills PR Coordination Policy

This local policy applies only to `spyroot/ci-skills`. Its forge and required
check are configured in `.coordination/pr-coordinator.toml`; verify the current
GitHub PR head and its `validate` check before any integration decision.

Use `.internal/queue/codex/` for Codex work and
`.internal/queue/claude/` for Claude work. Use the `shared` lane for an explicit
handoff. The `ci` and `provider-escalation` lanes remain inactive because
`standards-binding.yaml` declares no providers.

Do not infer live deployment or Kubernetes authority from the GitHub workflow.
Preserve the existing checkout and follow the pinned Standards contracts for
queue, gate, review, and merge decisions.
