# CI Skills

Reusable, self-contained Bash tools for CI agents. `install.sh` links this
repository into the Codex skills directory. No source code from another
repository or skill is loaded at runtime.

The initial commands are `bin/ci-api` for GitHub and GitLab reads, and
`bin/ci-binary-build` for an exact-commit OpenShift Binary BuildConfig plan.
Both accept project-specific values as arguments. Run each command with
`--help` for its exact interface. The build command makes no cluster changes.
No credentials belong in tracked files or published artifacts.

For local `--token-file` use, the operator selects a file under the calling
project's ignored `.internal/` directory, as required by the project agent
instructions. This skill chooses no filename and never copies or prints the
token. In GitLab CI, pass a protected file-variable path; in Kubernetes, pass
a read-only Secret mount path. Without `--token-file`, the selected `gh` or
`glab` CLI uses its existing authentication. The skill does not manage that
credential store. An explicit token file overrides that CLI authentication
for the selected request.

`scripts/check.sh` is the gate for tracked shell, YAML, Markdown, secrets, and
Bats tests. Its `--dry-run` prints the plan locally; live validation runs only
inside a Kubernetes pod.

Runtime commands are Bash, `jq`, `gh` or `glab` for the selected provider,
and, for build planning, `yq`, Git, and a SHA-256 utility. `install.sh` needs
Bash, `jq`, and a SHA-256 utility. The test suite also needs Bats.
The Kubernetes gate additionally needs ShellCheck, shfmt, yamllint,
markdownlint-cli2, and gitleaks.

`standards-binding.yaml` pins the shared Standards contracts. The local agent
and handoff files are ignored.
