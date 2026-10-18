# GitLab Runner cache tuning

Native `.gitlab-ci.yml` variables; not ci-skills options. [Upstream][source]
checked 2026-10-18. Select values for the installed Runner and cache backend.

## Keys

| Key | Units | Upload default | Download default |
| --- | --- | --- | --- |
| `CACHE_CHUNK_SIZE` | Bytes per chunk | `16777216` (16 MiB) | `0`: sequential. |
| `CACHE_CONCURRENCY` | Concurrent chunks | `16` | `0`: sequential; `1` also sequential. |

`FF_USE_PARALLEL_CACHE_TRANSFER=true` enables parallel transfers; its default
is `false`. See [flag scope and precedence](gitlab-runner-feature-flags.md).
For parallel download with chunk size unset, Runner uses 16 MiB.

Uploads: GoCloud backends only, including S3 with `RoleARN`, Azure and GCS.
Downloads: presigned HTTP ranges or GoCloud. Respect the selected backend's
part-size and part-count limits; values are not interchangeable across backends.

## Tuning example

This is a caller-selected experiment, not a default recommendation:

```yaml
variables:
  FF_USE_PARALLEL_CACHE_TRANSFER: "true"
  CACHE_CHUNK_SIZE: "33554432"
  CACHE_CONCURRENCY: "32"
```

32 MiB × 32 chunks implies approximately 1 GiB of transfer-buffer memory,
plus other job/archiving memory. Compare measured transfer time and memory
before retaining it. Artifact transfer has separate controls.

## Other controls

- `CACHE_REQUEST_TIMEOUT`: minutes, default `10`; avoids timeouts, not a speed setting.
- `CACHE_TRANSFER_BUFFER_SIZE`: bytes, default `4194304` (4 MiB).
- [Compression, distributed caches and registry mirrors][source]: further tuning choices.
- [Runner inputs](gitlab-runner-config.md): native configuration and job selection.

[source]: https://docs.gitlab.com/runner/configuration/speed_up_job_execution/
