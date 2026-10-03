# Project target and credential binding

Commands resolve the target in this order and report the selected tier
in `target_selection`:

1. Explicit `--target PATH` or `--binding PATH`: `cli:` or `binding:`.
2. `CI_SKILLS_TARGET=PATH` or `K8S_ADMIN_DIAGNOSTICS_BINDING=PATH`:
   `env:CI_SKILLS_TARGET -> file:` or `binding:`.
3. `./.ci-skills/target.toml` in the working directory: `project:`.
4. `~/.ci-skills/target.toml` for the current user: `user:`.

Each receipt reference includes the resolved absolute path. Portable receipts
replace that path with a stable digest token.

Two environment selectors set together are an error. An explicit target or a
declared environment target path that is absent blocks without substituting
the project or user target. Kubernetes commands require an exact context and
API server in their selected target; GitLab commands require a GitLab origin.
The full access receipt requires all three sections. When a plain target omits its
kubeconfig, credential resolution uses `KUBECONFIG`, then `~/.kube/config`.
The configured context and server still have to match the selected files.

Example nonsecret `./.ci-skills/target.toml`:

```toml
[github]
host = "github.com"
repository = "owner/repository"
# For --publication, replace the example with actual protected branch checks.
# required_checks = ["actual-required-check"]

[gitlab]
url = "https://gitlab.example.com"

[kubernetes]
context = "selected-admin-context"
server = "https://api.cluster.example.com:6443"
kubeconfig = "/absolute/path/to/project/kubeconfig"
```

If context, cluster, and user entries live in separate kubeconfig files,
replace `kubeconfig` with
`kubeconfigs = ["/absolute/first", "/absolute/second"]`. This is kubectl's
ordered, combined KUBECONFIG path, not a list of alternative targets. Every
file must be readable. The selected context and exact API server are verified
against the combined configuration. The list does not use the ambient current
context or `KUBECONFIG`.

Credential values do not belong in the target. GitHub and GitLab select their
effective token file, named environment variable, or host-specific CLI store
as described in [access.md](access.md). Kubernetes authenticates through the
selected kubeconfig's actual user entry: embedded token, `tokenFile`, client
certificate and key, or exec provider. Keep tokens, kubeconfigs, and private
keys out of tracked files.

## Project-specific kubeconfig resolver

A project with a kubeconfig-producing command can supply `--binding PATH` or
`K8S_ADMIN_DIAGNOSTICS_BINDING=PATH`. The binding names its nonsecret target
file and ordered kubeconfig sources. The target it names must omit both
`kubernetes.kubeconfig` and `kubernetes.kubeconfigs`, because the binding owns
that selection.

```toml
schema_version = "1.0"
target = "./target.toml"

[[kubernetes.sources]]
kind = "file"
path = "./.private/cluster-kubeconfig"

[[kubernetes.sources]]
kind = "environment"
name = "PROJECT_KUBECONFIG"

[[kubernetes.sources]]
kind = "command"
argv = ["./scripts/resolve-cluster", "--kubeconfig-path"]
```

Relative target, file, and command paths resolve from the binding's directory;
the command runs there without a shell. It must print exactly one absolute
path to a readable, nonempty kubeconfig and exit successfully. A missing file
or unset environment variable advances to the next declared source. A source
that exists but is unreadable, invalid, or fails blocks without trying another
source. A command source never falls through after it runs. `--dry-run` plans
a command source without invoking it or claiming access.

The command-returned file is borrowed from the project. The skill reads it but
does not delete it; a helper that creates a temporary file needs an external
owner to clean that file after the diagnostic command finishes. The receipt
records the resolved executable and a digest of its full argument vector, so
different project selections cannot share indistinguishable provenance.

The local receipt records the selected source and absolute path, never token
values or kubeconfig contents. The access gate verifies that the context
resolves to the selected server with TLS verification; collectors receive the
same kubeconfig and context. The skill does not assume a project helper name,
token file, or global profile.
