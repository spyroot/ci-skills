# CI Skills

Reusable tools for CI agents. `install.sh` links this repository into the
Codex skills directory. `SKILL.md` tells agents when to use the commands.

The initial commands are `bin/ci-api` for GitHub and GitLab reads, and
`bin/ci-binary-build` for an exact-commit OpenShift Binary BuildConfig plan.
Both accept project-specific values as arguments. Run each command with
`--help` for its exact interface. The build command makes no cluster changes.
No credentials belong in this repository.

`standards-binding.yaml` pins the shared Standards contracts. The local agent
and handoff files are ignored. The operator creates the remote and commits.
