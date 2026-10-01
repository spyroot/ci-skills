# Per-computer access and evidence

The installed skill contains instructions and code. Each execution host
supplies a nonsecret target file and its own credentials. The target file
identifies one exact GitHub repository, GitLab origin, Kubernetes context, and
API server. Use `--target PATH`; no target location is assumed.

## Effective credential sources

- GitHub uses an explicit `github.token_file` from the target, then the
  effective `GH_TOKEN` or `GITHUB_TOKEN` environment variable on github.com.
  Enterprise hosts use `GH_ENTERPRISE_TOKEN` or
  `GITHUB_ENTERPRISE_TOKEN`. Otherwise, the selected host's `gh` credential
  store is used.
- GitLab uses an explicit `gitlab.token_file`, then the effective
  `GITLAB_TOKEN`, `GITLAB_ACCESS_TOKEN`, or `OAUTH_TOKEN` environment variable.
  Otherwise, the selected host's `glab` credential store is used.
- Kubernetes uses an explicit `kubernetes.kubeconfig`, `KUBECONFIG`, or the
  default kubeconfig. The selected context resolves the user and cluster.
  The user may use an embedded token, `tokenFile`, client certificate and
  key, or an exec provider. No separate token file is assumed.

An explicit missing or unreadable file blocks with no fallback to a different
credential. The gate selects sources once
and passes the same sources to every collector. It records source references,
not token values, private keys, or raw kubeconfig contents. Keep credentials
outside this repository and the installed skill.

Example nonsecret target:

```toml
[github]
host = "github.com"
repository = "owner/repository"
# token_file = "/home/operator/.config/ci-skills/github.token"

[gitlab]
url = "https://gitlab.example.com"
# token_file = "/home/operator/.config/ci-skills/gitlab.token"

[kubernetes]
context = "admin-context"
server = "https://api.cluster.example.com:6443"
# kubeconfig = "/home/operator/.kube/config"
```

## Mandatory live gate

Run `access_check.py --target PATH --json --publication` on each intended
execution host. Supply `--revision SHA` for an installed copy without Git
metadata, and `--job-url URL` when verifying a requested job. The receipt
identifies the execution host, time, revision, sources, targets, identities,
and individual results.

- GitHub: authenticate the selected host, read back the identity and exact
  repository. Publication mode also requires repository administration and
  read-back of nonempty required status checks on the protected main branch.
- GitLab: authenticate the exact origin, read back an identity with
  `is_admin: true` and matching host, and read the instance runner API.
  A selected job also requires the job, pipeline, runner, and trace reads.
- Kubernetes: match the context to the exact HTTPS API server with TLS
  verification, read back the effective identity, check administrator
  permissions, and perform the storage and event resource reads. Cilium
  diagnostics require a real non-TTY `cilium-health` command on a ready agent.

`PASS` requires all selected live checks. Missing, invalid, expired,
wrong-target, or unauthorized credentials block. A mock, file-existence
check, login-status message, dry run, or receipt from another host is not
live acceptance evidence.

## Automated gates

The `validate` workflow checks package layout, all installed entrypoints from
an unrelated working directory, workflow/YAML and Markdown syntax, diff
hygiene, secret scanning, Ruff lint and format, and mocked behavior. CI proves
code behavior at its tested commit. The per-host live receipt proves actual
access and resource reads on that host.
