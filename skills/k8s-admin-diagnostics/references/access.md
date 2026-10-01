# Per-computer access and storage plan

The installed skill contains instructions and executable code. It contains no target, token, or kubeconfig. Every computer or runner supplies its own configuration and credentials. A CLI/keychain profile or an explicit private token file may authenticate GitHub and GitLab; when a token file is selected, a missing or unreadable file blocks access without fallback.

| File or store | Location | Purpose | Gate |
|---|---|---|---|
| `SKILL.md` and `scripts/` | `~/.codex/skills/k8s-admin-diagnostics/` after installation | Codex instructions and reusable diagnostics | Package validation in GitHub Actions checks frontmatter, name, references, entrypoints, and `--help`. |
| `target.toml` | Operator-selected `--target PATH`; recommended `~/.config/ci-skills/target.toml`, outside the installed skill | Nonsecret exact GitHub, GitLab, and Kubernetes authorities and optional credential paths | Target parser rejects unknown fields, credentials in TOML, invalid hosts/URLs, and paths inside the skill. |
| GitHub token | `gh` host profile/keychain, or optional `github.token_file` outside the skill | Authenticates the selected GitHub host | File mode reads a nonempty token into the child process only; both modes require host auth, identity API, and exact repository API reads. |
| GitLab token | `glab` host profile, or optional `gitlab.token_file`; recommended `~/.config/ci-skills/credentials/gitlab.token` | Authenticates the exact GitLab FQDN | File mode reads a nonempty token into the child process only; both modes require host auth, admin identity, and runner API reads. |
| Kubeconfig | Optional `kubernetes.kubeconfig` path outside the skill; otherwise `KUBECONFIG` or kubectl default | Selects Kubernetes credentials and context | Explicit file must be readable and nonempty; API context, TLS, identity, and authorization checks prove effective access. |

A private token file can be provisioned once from an existing secure store into the per-computer credential directory with user-only file permissions. Never copy credentials into the GitHub repository or installed skill. The skill reads the named file at runtime; no private project path is embedded in its code or documentation. Token values are never placed in reports or command arguments.

File mode passes the GitLab token to `glab` as `GITLAB_TOKEN` in the child process environment. An explicit missing, empty, or unreadable token file has no fallback to a CLI profile. Keep every token file outside this repository and the installed skill.

Example nonsecret target file:

```toml
[github]
host = "github.com"
repository = "owner/repository"
# token_file = "/home/operator/.config/ci-skills/credentials/github.token"

[gitlab]
url = "https://gitlab.example.com"
token_file = "/home/operator/.config/ci-skills/credentials/gitlab.token"

[kubernetes]
context = "admin-context"
server = "https://api.cluster.example.com:6443"
kubeconfig = "/home/operator/.config/ci-skills/credentials/kubeconfig"
```

## Gates

| Gate | Implementation | Pass condition |
|---|---|---|
| Package | `tests/test_skill_package.py`, `.github/workflows/validate.yml` | Valid metadata and package layout; every script accepts `--help`. |
| Project neutrality | `tools/check_project_neutrality.py` | Every tracked or pending path and byte, including dotfiles, is free of the prohibited project marker. |
| Target and storage | `scripts/core/target.py`, `scripts/core/access.py` | Explicit TOML parses; selected token and kubeconfig files are readable, nonempty, and outside the installed skill. |
| GitHub read | `scripts/core/access.py` | `gh auth status` for selected host, authenticated identity, exact repository read. |
| GitHub publication | `access_check.py --publication` | GitHub read gate plus repository admin permission, before configuring or verifying a required check. |
| GitLab admin | `scripts/core/access.py` | `glab auth status` for exact FQDN, `GET /user` with `is_admin: true` and matching host, successful `GET /runners/all`. Group Owner alone does not pass. |
| Kubernetes admin | `scripts/core/access.py` | Named context resolves to exact HTTPS server; TLS verification and `/version` work; effective user, cluster wildcard, role-binding administration, role bind, and Cilium `pods/exec` checks pass. |
| Collector | `scripts/core/cli.py` | All three access surfaces pass before any live diagnostic collection. Partial data returns a nonzero status. |
| Exact-head CI | `.github/workflows/validate.yml` and protected `main` | Package, neutrality, Ruff, and mocked denial/filter/CLI tests pass on the exact pull request head; required `validate` check is enforced. |
| Per-host receipt | `access_check.py --target PATH --json` | A real `PASS` is recorded on each intended host; dry run and another host's receipt do not count. |

The gate never logs in, grants roles, changes context, or silently falls back to another host. Failed checks stop live collection. It reports each surface's `PASS` or `BLOCKED`, identity when verified, target, observed capabilities, and safe next step. `--dry-run` reports planned probes as `DRY_RUN` and never proves access.
