# Project target and credential binding

The [access reference](access.md#where-the-target-file-comes-from) defines target
selection, smoke filenames, Optional provider sections and credential precedence.
Use this binding only when the calling project supplies a kubeconfig resolver.

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
