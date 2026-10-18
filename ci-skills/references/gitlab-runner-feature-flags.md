# GitLab Runner feature flags

Native settings; not ci-skills flags. [Upstream defaults][source] checked
2026-10-18; compare the installed Runner version before selection.

## Scope

| Location | Effect |
| --- | --- |
| `.gitlab-ci.yml` `variables` | Pipeline or individual job. |
| `[[runners]].environment` | Every job on that runner; jobs can override. |
| `[runners.feature_flags]` | Jobs cannot override; required for some manager flags. |

Environment booleans accept `true`/`false` or `1`/`0`. Add these fields inside
an existing runner entry in `config.toml`:

```toml native-config=gitlab-runner/config.toml
# [[runners]] entry, before its nested tables:
environment = ["FF_SCRIPT_SECTIONS=1"]
[runners.feature_flags]
  FF_USE_DIRECT_DOWNLOAD = true
```

## Selected keys

| Key | Default | Effect / condition |
| --- | --- | --- |
| `FF_SCRIPT_SECTIONS` | `false` | Collapsible multiline script logs. |
| `FF_USE_DIRECT_DOWNLOAD` | `true` | Requests artifact-storage redirection; administrator must permit it. |
| `FF_USE_ADAPTIVE_REQUEST_CONCURRENCY` | `true` | Adapts requests below `request_concurrency`. |
| `FF_USE_POD_ACTIVE_DEADLINE_SECONDS` | `true` | Kubernetes Pod deadline follows job timeout. |
| `FF_USE_PARALLEL_CACHE_TRANSFER` | `false` | Parallel cache upload/download; tune `CACHE_*` below. |
| `FF_USE_PARALLEL_ARTIFACT_TRANSFER` | `false` | Parallel direct artifact download; independent of `CACHE_*`. |

Parallel artifacts require storage redirection and HTTP `206` with a
`Content-Range` total; chunk sizing/concurrency are Runner-controlled.

## Related

- [Cache tuning](gitlab-runner-cache.md): units, defaults, backend applicability.
- [Runner inputs](gitlab-runner-config.md): registration and job selection.
- [Operator configuration](gitlab-runner-operator.md): OpenShift manager settings.

[source]: https://docs.gitlab.com/runner/configuration/feature-flags/
