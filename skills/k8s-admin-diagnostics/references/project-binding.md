# Project target and credential binding

API commands resolve the target in this order and report the selected tier
in `target_selection`:

1. Explicit `--target PATH` or `--binding PATH`: `cli:` or `binding:`.
2. `CI_SKILLS_TARGET=PATH` or `K8S_ADMIN_DIAGNOSTICS_BINDING=PATH`:
   `env:CI_SKILLS_TARGET -> file:` or `binding:`.
3. `./.ci-skills/target.toml` in the working directory: `project:`.
4. `~/.ci-skills/target.toml` for the current user: `user:`.

Each receipt reference includes the resolved absolute path. Portable receipts
replace that path with a stable digest token.

Two environment selectors set together are an error. A selected file that is
missing or invalid blocks; the command never silently falls through to another
project or user's target. The project and user tiers are considered only when
the higher tier is absent. Every target must declare an exact Kubernetes
context and API server. A plain target must also declare its kubeconfig; the
skill never adopts a global current context or ambient `KUBECONFIG`.

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

If a project declares several explicit kubeconfig files, replace
`kubeconfig` with `kubeconfigs = ["/absolute/first", "/absolute/second"]`.
The skill reads each declared file and selects the **sole** file whose named
context resolves to the declared API server. No match, more than one match,
or an unreadable/malformed candidate blocks. The receipt identifies the
selected candidate index and file source. The list does not use the ambient
current context or `KUBECONFIG`.

Credential values do not belong in the target. GitHub and GitLab select their
effective token file, named environment variable, or host-specific CLI store
as described in [access.md](access.md). Kubernetes authenticates through the
selected kubeconfig's actual user entry: embedded token, `tokenFile`, client
certificate and key, or exec provider. Keep tokens, kubeconfigs, and private
keys out of tracked files.

## Project-specific kubeconfig resolver

A project with a kubeconfig-producing command can supply `--binding PATH` or
`K8S_ADMIN_DIAGNOSTICS_BINDING=PATH`. The binding names its nonsecret target
file and ordered kubeconfig sources. The target it names must omit
`kubernetes.kubeconfig`, because the binding owns that selection.

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
