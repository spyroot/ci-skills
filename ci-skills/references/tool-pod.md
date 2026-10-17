# Prepare a reusable tool Pod

`bin/prepare_tool_pod.py` prepares tools inside one Kubernetes Pod, returns its identity and keeps it available
for a later operation. It requires only the Kubernetes section of your [target file](access.md); GitLab,
GitHub and Harbor configuration are unrelated. The dispatch host needs the installed skill and `kubectl`.
Downloads and installation happen inside the selected container.

The standalone command creates a namespace, ConfigMap and Pod, reads their state, executes commands in the
Pod, and deletes its owned resources on removal. Resolving a returned run ID lists namespaces, Pods and
ConfigMaps across the cluster. The selected credentials need permission for those operations; having a valid
kubeconfig alone does not grant them. The command creates no RBAC or admission-policy grants.

## Supply the tools

Set `CI_SKILL_DIR` to the installation path reported by the installer, then discover the interface. Replace
the path in the first line; keep inputs in your working directory, outside the installed package:

```bash
CI_SKILL_DIR=/path/from/installer/ci-skills
python "$CI_SKILL_DIR/bin/prepare_tool_pod.py" --help
python "$CI_SKILL_DIR/bin/prepare_tool_pod.py" --describe --json
```

Save the invocation document as `tools.json` in your working directory. Its
[schema](../schemas/tool-pod.schema.json) contains two fields:

| Field | Caller supplies |
| --- | --- |
| `image` | Container image suitable for the required tools and the cluster's admission settings. |
| `dependencies` | Tool names, executable names, optional probe argv and installation routes. |

This example verifies the existing shell and installs a missing `yq` executable in a writable directory:

```json
{
  "image": "registry.access.redhat.com/ubi9/ubi:9.6",
  "dependencies": [
    {
      "name": "shell",
      "commands": ["sh"],
      "probe": ["sh", "-c", "printf 'tool-pod-ready\n'"]
    },
    {
      "name": "yq",
      "commands": ["yq"],
      "probe": ["yq", "--version"],
      "linux": {
        "method": "download",
        "url": "https://github.com/mikefarah/yq/releases/download/v4.47.2/yq_linux_amd64",
        "sha256": "1bb99e1019e23de33c7e6afc23e93dad72aad6cf2cb03c797f068ea79814ddb0",
        "destination": "/tmp/tools/bin/yq"
      }
    }
  ]
}
```

The image and Linux amd64 artifact above are examples, not command defaults. Select inputs for your platform.
Pin the selected image by digest when reproducibility matters. A tool with no installation route must already
be present. The shell takes the `NO_OP` path; if `yq` is missing,
its download and installation records report `APPLIED`, and final read-back contains its version output.

Dependencies reuse [the installed toolchain definition](../schemas/toolchain-dependencies.schema.json).
For a missing tool, supply `apt` or `yum` package names, or a `linux` installation object. An archive route
uses `method: archive`, the provider's `url` and `sha256`, a writable `destination`, and optional `files` and
`stripComponents`. A direct executable uses `method: download` and an exact destination filename.
`probe` is an argv array: provide the actual command supported by that tool, such as
`["oc", "version", "--client", "-o", "json"]` or `["rsync", "--version"]`.

The image needs a shell and `sleep`. Artifact routes additionally need their transfer/checksum/extraction
commands; native package routes need their package manager and its write permissions. Preparation inspects
these prerequisites and reports the failed operation. It does not change the selected image or install
anything on the dispatch host. For an arbitrary container UID, choose a writable
installation directory such as `/tmp/tools/bin` when the selected installation route supports it.

## Plan and prepare

```bash
python "$CI_SKILL_DIR/bin/prepare_tool_pod.py" prepare \
  --spec tools.json --dry-run --json
```

The result contains `plan_digest`, which binds the input, target and functional code. Copy that value into
`DIGEST` below. An offline plan does not contact the cluster. `--live-plan` checks Kubernetes access without
creating a Pod.

```bash
python "$CI_SKILL_DIR/bin/prepare_tool_pod.py" prepare \
  --spec tools.json --apply --confirm-plan DIGEST --json
```

On success, read `run_id`, `pod` and `readback` from the result. `pod` identifies the actual namespace,
name, UID and container. Callback records distinguish newly installed tools (`APPLIED`) from already-present
tools (`NO_OP`), and final probes show what ran. The Pod remains available until explicit removal.

## Reuse and remove

To reuse that Pod, repeat the plan/apply pair with the same input and `--run-id RUN_ID`. The command resolves
the run from live ownership labels, checks the input fingerprint and re-probes tools in the current container.
A previous report alone does not prove that tools survived a container restart.

Python workflows compose the [exported callbacks and handles](../lib/python/core/kubernetes/tool_pod.py)
through the existing shared executor. Downloads, installation and downstream callbacks use the same Pod.

Remove the run when its consumers finish:

```bash
python "$CI_SKILL_DIR/bin/prepare_tool_pod.py" remove \
  --run-id RUN_ID --dry-run --json
python "$CI_SKILL_DIR/bin/prepare_tool_pod.py" remove \
  --run-id RUN_ID --apply --confirm-plan REMOVE_DIGEST --json
```

Use the removal plan's digest for `REMOVE_DIGEST`. Removal verifies absence of the run's owned objects;
repeating it returns `NO_OP`. An existing namespace selected by an internal workflow is never owned or deleted
by preparation. If preparation fails, resources created by that invocation are cleaned up. A reused Pod is
not deleted merely because a later tool operation fails; the report retains its identity for recovery.

The result's `cleanup` distinguishes removed resources, retained resources and cleanup failures. Use
`--receipt-out PATH` to save an apply receipt. `--log-level debug --log-format json --log-file PATH` produces
the shared sanitized execution trace when a diagnostic artifact is needed.
