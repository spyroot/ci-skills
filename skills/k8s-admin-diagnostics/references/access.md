# Access contract

A skill is local instruction and code. `gh`, `glab`, and `kubectl` supply authenticated live data using credentials configured on the current host. The skill does not include a target file, token, or kubeconfig.

The required `--target PATH` TOML contains exactly `[github]`, `[gitlab]`, and `[kubernetes]`. Supported fields are `github.host` (full FQDN), `github.repository` (`owner/repository`), `gitlab.url` (full HTTPS origin), `kubernetes.context` (exact context), `kubernetes.server` (exact HTTPS API origin), and optional `kubernetes.kubeconfig` (path). No authentication fields are accepted. Without an explicit kubeconfig path, `kubectl` resolves `KUBECONFIG` or its normal default.

## Gate probes

| Surface | Required evidence | On failure |
|---|---|---|
| GitHub | `gh auth status` for the selected host, authenticated `GET /user`, and exact repository read. Publication also checks repository administration before configuring or verifying a required check. | Authenticate or select a repository the identity can read/administer. |
| GitLab | `glab auth status` for the exact selected FQDN, authenticated `GET /user` with `is_admin: true` and matching host, plus successful `GET /runners/all`. Group ownership alone does not satisfy the instance administrator requirement. | Use an instance administrator identity with runner API access on the selected host. |
| Kubernetes | Explicit context resolves to the supplied API server; TLS API `/version` succeeds; `kubectl auth whoami` identifies the effective user; cluster-wide wildcard, cluster role binding administration, cluster-role bind, and `pods/exec` in the discovered Cilium namespace are allowed. | Correct the context/credential or have a cluster administrator grant the required permissions. |

The gate reports `PASS` or `BLOCKED` for each surface, target, identity when verified, observed capabilities, reason, and safe next step. It uses read-only access reviews for RBAC. It never prints tokens, reads credential contents into a report, changes the current context, or falls back to another host. A failed surface stops collection. A dry run reports intended probes as `DRY_RUN` without contacting an authority.
